"""人工小实例验证隔离边界；不调用模型、不依赖FrontierOR数据。

python -m unittest test_sandbox -v
CI设置DOCKER_TESTS=1：缺少Docker或镜像也算失败，不能悄悄跳过。
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sandbox
from collect import read_solution
from evaluator import evaluate


class OutputTest(unittest.TestCase):
    """这些输出交接检查无需Docker。"""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="orpi-output-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.solution = self.root / "solution.json"
        self.solution.write_text('{"objective_value": 3}', encoding="utf-8")

    def test_regular_json(self):
        self.assertEqual(read_solution(self.solution), {"objective_value": 3})

    def test_directory_and_large_file(self):
        with self.assertRaises(ValueError):
            read_solution(self.root)
        with self.solution.open("wb") as output:
            output.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            read_solution(self.solution)

    def test_hardlink(self):
        linked = self.root / "linked.json"
        os.link(self.solution, linked)
        with self.assertRaises(ValueError):
            read_solution(linked)

    def test_symlink(self):
        linked = self.root / "linked.json"
        try:
            linked.symlink_to(self.solution)
        except OSError as error:
            self.skipTest("当前Windows权限不允许创建symlink：" + str(error))
        with self.assertRaises(ValueError):
            read_solution(linked)

    def test_ci_requires_docker(self):
        with patch("test_sandbox.shutil.which", return_value=None):
            with patch.dict(os.environ, {"DOCKER_TESTS": "1"}):
                with self.assertRaises(RuntimeError):
                    DockerBoundaryTest.setUpClass()

    def test_large_integer_objective_is_rejected(self):
        (self.root / "instance").mkdir()
        (self.root / "instance" / "tiny.json").write_text('{"value": 7}', encoding="utf-8")
        (self.root / "instance_schema.json").write_text('{"value": "integer"}', encoding="utf-8")
        goal = SimpleNamespace(task_dir=self.root, instance="tiny.json", time_limit=1)
        with patch("evaluator.execute", return_value={"objective_value": 10 ** 1000}):
            result = evaluate(goal, self.root)
        self.assertFalse(result["valid"])
        self.assertIn("error", result)
        self.assertFalse((self.root / "checker.json").exists())


class DockerBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            if not shutil.which("docker"):
                raise RuntimeError("未安装Docker")
            for arguments in (("info",), ("image", "inspect", sandbox.IMAGE)):
                result = subprocess.run(["docker", *arguments], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=20)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1000:])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            message = "真实沙箱测试需要Docker daemon和已构建镜像：" + str(error)
            if os.environ.get("DOCKER_TESTS") == "1":
                raise RuntimeError(message) from error
            raise unittest.SkipTest(message) from error

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="orpi-sandbox-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.number = 0

    def candidate(self, body, instance=None):
        self.number += 1
        folder = self.root / f"candidate_{self.number}"
        folder.mkdir()
        code = "def solve(instance, time_limit):\n" + textwrap.indent(textwrap.dedent(body), "    ")
        (folder / "candidate.py").write_text(code, encoding="utf-8")
        (folder / "input.json").write_text(json.dumps(instance or {"value": 7}), encoding="utf-8")
        return folder

    def test_python_and_solvers(self):
        programs = {
            "python": "return {'objective_value': instance['value'] + 1}",
            "gurobi": """
                import gurobipy as gp
                model = gp.Model()
                model.Params.OutputFlag = 0
                model.Params.Threads = 1
                x = model.addVar(lb=0, ub=8)
                model.setObjective(x, gp.GRB.MAXIMIZE)
                model.optimize()
                return {'objective_value': x.X}
            """,
            "copt": """
                import coptpy as cp
                environment = cp.Envr()
                model = environment.createModel()
                model.setParam(cp.COPT.Param.Logging, 0)
                model.setParam(cp.COPT.Param.Threads, 1)
                x = model.addVar(lb=0, ub=8)
                model.setObjective(x, cp.COPT.MAXIMIZE)
                model.solve()
                return {'objective_value': x.x}
            """,
        }
        for name, body in programs.items():
            with self.subTest(solver=name):
                result = sandbox.execute(self.candidate(body), 10)
                self.assertAlmostEqual(result["objective_value"], 8)

    def test_files_and_secrets(self):
        private = self.root / ".env"
        private.write_text("synthetic secret; never mount this file", encoding="utf-8")
        folder = self.candidate("""
            import os
            from pathlib import Path
            readonly = []
            for name in ('/input.json', '/candidate.py', '/worker.py'):
                try:
                    Path(name).write_text('tampered')
                    readonly.append(False)
                except OSError:
                    readonly.append(True)
            try:
                Path(instance['private_path']).read_text()
                private_hidden = False
            except OSError:
                private_hidden = True
            return {'objective_value': 0, 'readonly': readonly,
                    'private_hidden': private_hidden,
                    'secret_present': 'ORPI_TEST_SECRET' in os.environ,
                    'api_key_present': 'DEEPSEEK_API_KEY' in os.environ}
        """, {"private_path": str(private)})
        original = (folder / "candidate.py").read_text(encoding="utf-8")
        with patch.dict(os.environ, {"ORPI_TEST_SECRET": "synthetic-test-value"}):
            result = sandbox.execute(folder, 5)
        self.assertEqual(result["readonly"], [True, True, True])
        self.assertTrue(result["private_hidden"])
        self.assertFalse(result["secret_present"])
        self.assertFalse(result["api_key_present"])
        self.assertEqual((folder / "candidate.py").read_text(encoding="utf-8"), original)
        self.assertEqual(private.read_text(encoding="utf-8"), "synthetic secret; never mount this file")

    def test_network_and_resource_limits(self):
        folder = self.candidate("""
            import os
            import socket
            from pathlib import Path
            denied = []
            for host, port in [('1.1.1.1', 443), ('host.docker.internal', 2375)]:
                try:
                    with socket.create_connection((host, port), timeout=1):
                        denied.append(False)
                except OSError:
                    denied.append(True)
            root = Path('/sys/fs/cgroup')
            output = os.statvfs('/output')
            return {'objective_value': 0, 'denied': denied,
                    'memory': (root / 'memory.max').read_text().strip(),
                    'swap': (root / 'memory.swap.max').read_text().strip(),
                    'pids': (root / 'pids.max').read_text().strip(),
                    'cpu': (root / 'cpu.max').read_text().split(),
                    'output_bytes': output.f_blocks * output.f_frsize,
                    'output_inodes': output.f_files}
        """)
        result = sandbox.execute(folder, 5)
        self.assertEqual(result["denied"], [True, True])
        self.assertEqual(int(result["memory"]), 1024 ** 3)
        self.assertEqual(int(result["swap"]), 0)
        self.assertEqual(int(result["pids"]), 64)
        quota, period = map(int, result["cpu"])
        self.assertEqual(quota, period)
        self.assertEqual(result["output_bytes"], 64 * 1024 * 1024)
        self.assertEqual(result["output_inodes"], 1024)

    def test_pid_limit_rejects_bounded_children(self):
        folder = self.candidate("""
            import errno
            import subprocess
            import sys
            children = []
            rejected = False
            try:
                for _ in range(80):
                    try:
                        children.append(subprocess.Popen(
                            [sys.executable, '-c', 'import time; time.sleep(30)']))
                    except OSError as error:
                        if error.errno != errno.EAGAIN:
                            raise
                        rejected = True
                        break
                return {'objective_value': 0, 'rejected': rejected, 'children': len(children)}
            finally:
                for child in children:
                    child.kill()
                for child in children:
                    child.wait(timeout=5)
        """)
        result = sandbox.execute(folder, 15)
        self.assertTrue(result["rejected"])
        self.assertGreater(result["children"], 0)
        self.assertLess(result["children"], 64)

    def test_memory_limit_rejects_at_most_two_gib(self):
        folder = self.candidate("""
            blocks = []
            for _ in range(128):
                blocks.append(bytearray(16 * 1024 * 1024))
            return {'objective_value': len(blocks)}
        """)
        with self.assertRaises(ValueError):
            sandbox.execute(folder, 15)

    def test_file_size_limit(self):
        folder = self.candidate("""
            import errno
            try:
                with open('/output/large.bin', 'wb') as output:
                    for _ in range(17):
                        output.write(b'x' * (1024 * 1024))
            except OSError as error:
                if error.errno != errno.EFBIG:
                    raise
                return {'objective_value': 0, 'limited': True}
            return {'objective_value': 0, 'limited': False}
        """)
        self.assertTrue(sandbox.execute(folder, 5)["limited"])

    def test_total_output_space_is_limited(self):
        folder = self.candidate("""
            import errno
            limited = False
            try:
                for number in range(9):
                    with open(f'/output/fill_{number}.bin', 'wb') as output:
                        for _ in range(8):
                            output.write(b'x' * (1024 * 1024))
            except OSError as error:
                if error.errno != errno.ENOSPC:
                    raise
                limited = True
            # 只截断本测试写出的首个文件，给返回解留出空间。
            with open('/output/fill_0.bin', 'wb'):
                pass
            return {'objective_value': 0, 'limited': limited}
        """)
        self.assertTrue(sandbox.execute(folder, 5)["limited"])

    def test_timeout_removes_container_and_child(self):
        folder = self.candidate("""
            import subprocess
            import sys
            import time
            from pathlib import Path
            subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                             start_new_session=True)
            Path('/output/child_started').write_text('yes')
            time.sleep(120)
            return {'objective_value': 0}
        """)
        identifier = uuid.uuid4()
        original_docker = sandbox.docker
        observed = []

        def observe_cleanup(*arguments, **kwargs):
            if arguments[:2] == ("rm", "--force"):
                try:
                    probe = original_docker(
                        "exec", "--user", "0:0", arguments[2], "python", "-I", "-c",
                        "from pathlib import Path; print(Path('/output/child_started').is_file())")
                    observed.append(probe.stdout.strip())
                finally:
                    removed = original_docker(*arguments, **kwargs)
                return removed
            return original_docker(*arguments, **kwargs)

        with patch("sandbox.uuid.uuid4", return_value=identifier):
            with patch("sandbox.docker", side_effect=observe_cleanup):
                with self.assertRaises(subprocess.TimeoutExpired):
                    sandbox.execute(folder, 1)
        self.assertEqual(observed, ["True"])
        remaining = sandbox.docker("ps", "-a", "--filter", "name=^/orpi-" + identifier.hex + "$",
                                   "--format", "{{.Names}}").stdout.strip()
        self.assertEqual(remaining, "")

    def test_container_symlink_is_not_imported(self):
        folder = self.candidate("""
            from pathlib import Path
            Path('/output/solution.json').symlink_to('payload.json')
            return {'objective_value': 1}
        """)
        with self.assertRaises(ValueError):
            sandbox.execute(folder, 5)
        self.assertFalse((folder / "solution.json").exists())

    def test_fifo_solution_is_rejected_without_blocking(self):
        folder = self.candidate("""
            import os
            os.mkfifo('/output/solution.json')
            os._exit(0)
        """)
        original_docker = sandbox.docker
        durations = []

        def time_collection(*arguments, **kwargs):
            started = time.monotonic()
            try:
                return original_docker(*arguments, **kwargs)
            finally:
                if "/collect.py" in arguments:
                    durations.append(time.monotonic() - started)

        with patch("sandbox.docker", side_effect=time_collection):
            with self.assertRaisesRegex(ValueError, "普通文件"):
                sandbox.execute(folder, 5)
        self.assertEqual(len(durations), 1)
        self.assertLess(durations[0], 5)

    def test_candidate_cannot_write_root_collector_pipe(self):
        attacker = textwrap.dedent("""
            import time
            from pathlib import Path
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                for process in Path('/proc').iterdir():
                    if not process.name.isdecimal():
                        continue
                    try:
                        status = (process / 'status').read_text()
                        uid = next(line for line in status.splitlines() if line.startswith('Uid:'))
                        if int(uid.split()[1]) != 0:
                            continue
                    except (OSError, StopIteration):
                        continue
                    try:
                        with (process / 'fd' / '1').open('wb', buffering=0) as pipe:
                            pipe.write(b'FORGED_COLLECTOR_OUTPUT\\n')
                    except PermissionError:
                        Path('/output/fd_denied').write_text('yes')
                    except OSError:
                        pass
                time.sleep(0.02)
        """)
        folder = self.candidate(f"""
            import subprocess
            import sys
            subprocess.Popen([sys.executable, '-c', {attacker!r}], start_new_session=True)
            return {{'objective_value': 7}}
        """)
        original_docker = sandbox.docker
        observed = []

        def probe_collector(*arguments, **kwargs):
            if "/collect.py" in arguments:
                name = arguments[2]
                probe = original_docker(
                    "exec", "--user=0", name, "python", "-I", "-c",
                    "import time; print('TRUSTED_COLLECTOR_PROBE', flush=True); time.sleep(1)")
                self.assertEqual(probe.stdout, "TRUSTED_COLLECTOR_PROBE\n")
                marker = original_docker(
                    "exec", "--user=0", name, "python", "-I", "-c",
                    "from pathlib import Path; print(Path('/output/fd_denied').read_text())")
                observed.append(marker.stdout.strip())
            return original_docker(*arguments, **kwargs)

        with patch("sandbox.docker", side_effect=probe_collector):
            self.assertEqual(sandbox.execute(folder, 5)["objective_value"], 7)
        self.assertEqual(observed, ["yes"])

    def test_forged_checker_cannot_accept_bad_solution(self):
        task = self.root / "trusted_task"
        (task / "instance").mkdir(parents=True)
        (task / "instance" / "tiny.json").write_text('{"value": 7}', encoding="utf-8")
        (task / "instance_schema.json").write_text('{"value": "integer"}', encoding="utf-8")
        checker = textwrap.dedent("""
            import argparse
            import json
            from pathlib import Path
            parser = argparse.ArgumentParser()
            for name in ('instance_path', 'solution_path', 'result_path'):
                parser.add_argument('--' + name)
            args = parser.parse_args()
            instance = json.loads(Path(args.instance_path).read_text(encoding='utf-8'))
            solution = json.loads(Path(args.solution_path).read_text(encoding='utf-8'))
            valid = solution['objective_value'] == solution.get('witness') == instance['value']
            result = {'feasible': valid, 'violations': [] if valid else ['wrong witness']}
            Path(args.result_path).write_text(json.dumps(result), encoding='utf-8')
        """)
        (task / "feasibility_check.py").write_text(checker, encoding="utf-8")
        goal = SimpleNamespace(task_dir=task, instance="tiny.json", time_limit=5)
        body = """
            from pathlib import Path
            Path('/output/checker.json').write_text('{"feasible": true}')
            return {'objective_value': 999, 'witness': 999}
        """
        folder = self.candidate(body)
        result = evaluate(goal, folder)
        self.assertFalse(result["valid"], result)
        self.assertEqual(result["violations"], ["wrong witness"])
        self.assertFalse(json.loads((folder / "checker.json").read_text(encoding="utf-8"))["feasible"])
        self.assertEqual((task / "feasibility_check.py").read_text(encoding="utf-8"), checker)
        # 即使宿主目录中已有旧的“通过”结果，也不得复用。
        folder = self.candidate(body)
        (folder / "checker.json").write_text('{"feasible": true}', encoding="utf-8")
        self.assertFalse(evaluate(goal, folder)["valid"])


if __name__ == "__main__":
    unittest.main()

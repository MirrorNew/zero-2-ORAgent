"""离线检查中文进度与计时；不请求模型、网络或Docker。"""
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent
import sandbox
from terminal_ui import TerminalUI


def records(folder):
    return [json.loads(line) for line in (folder / "progress.jsonl").read_text(encoding="utf-8").splitlines()]


def action(name, identifier="call", **arguments):
    return {"role": "assistant", "tool_calls": [{"id": identifier, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class TerminalUITest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.goal = agent.Goal("测试任务", "tiny.json", self.folder)
        self.ui = TerminalUI()

    def test_stage_round_and_total_timings(self):
        now = [0.0]
        with redirect_stdout(io.StringIO()), patch("terminal_ui.time.perf_counter", side_effect=lambda: now[0]):
            with self.ui.session(self.goal, self.folder, 2):
                now[0] = 2.0
                self.ui.begin_round(1)
                now[0] = 3.0
                with self.ui.stage("模型请求"):
                    now[0] = 8.125
                now[0] = 9.0
                self.ui.end_round()
                now[0] = 10.0
        events = {item["event"]: item for item in records(self.folder)}
        self.assertEqual(events["模型请求：结束"]["seconds"], 5.125)
        self.assertEqual(events["本轮结束"]["seconds"], 7.0)
        self.assertEqual(events["运行结束"]["seconds"], 10.0)
        self.assertEqual(events["模型请求：结束"]["total_seconds"], 8.125)
        self.assertEqual(events["模型请求：结束"]["round"], 1)

    def test_plain_output_redacts_key_and_control_characters(self):
        stream = io.StringIO()
        with redirect_stdout(stream), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-private-key"}):
            with self.ui.session(self.goal, self.folder, 1):
                with self.ui.stage("工具调用", "test-private-key\x1b[2J\r换行\n中文\n[第1/8轮] 伪造验收通过"):
                    pass
        for text in (stream.getvalue(), (self.folder / "progress.jsonl").read_text(encoding="utf-8")):
            self.assertNotIn("test-private-key", text)
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\r", text)
            self.assertIn("[密钥已隐藏]", text)
            self.assertIn("中文", text)
        self.assertIn("\n  | [第1/8轮] 伪造验收通过", stream.getvalue())
        self.assertNotIn("\n[第1/8轮] 伪造验收通过", stream.getvalue())

    def test_exceptions_close_round_log_and_refresh_thread(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True

        for error, label in ((RuntimeError("测试错误"), "运行异常"), (KeyboardInterrupt(), "用户中止")):
            with self.subTest(error=type(error).__name__):
                threads, stops = [], []
                real_thread = threading.Thread

                def create_thread(*args, **kwargs):
                    thread = real_thread(*args, **kwargs)
                    threads.append(thread)
                    return thread

                def refresh(stop, *_):
                    stops.append(stop)
                    stop.wait()

                with redirect_stdout(TTY()), patch("terminal_ui.threading.Thread", side_effect=create_thread):
                    with patch.object(self.ui, "_refresh", side_effect=refresh), self.assertRaises(type(error)):
                        with self.ui.session(self.goal, self.folder, 1):
                            self.ui.begin_round(1)
                            with self.ui.stage("等待执行"):
                                raise error
                events = [item["event"] for item in records(self.folder)]
                self.assertIn("等待执行：中断或异常", events)
                self.assertIn(label, events)
                self.assertEqual(events[-2:], ["本轮结束", "运行结束"])
                self.assertTrue(stops and all(stop.is_set() for stop in stops))
                self.assertTrue(threads and all(not thread.is_alive() for thread in threads))
                self.assertFalse(self.ui.active)
                self.assertTrue(self.ui.log.closed)

    def test_display_failures_do_not_prevent_container_cleanup(self):
        info = dict(ostype="linux", cgroupversion="2", memorylimit=True,
                    swaplimit=True, cpucfsquota=True, pidslimit=True)
        for failure in ("isatty", "start"):
            with self.subTest(failure=failure):
                stream = io.StringIO()
                display_failure = (patch.object(stream, "isatty", side_effect=OSError("终端不可用"))
                                   if failure == "isatty" else
                                   patch("terminal_ui.threading.Thread.start", side_effect=RuntimeError("线程不可用")))
                with redirect_stdout(stream), patch.object(stream, "isatty", return_value=True), display_failure:
                    with patch("sandbox.ui", self.ui), patch("sandbox.shutil.which", return_value="docker"):
                        with patch("sandbox.docker", return_value=SimpleNamespace(stdout=json.dumps(info))) as docker:
                            with patch("sandbox.subprocess.run", side_effect=RuntimeError("求解失败")) as execute:
                                with self.assertRaisesRegex(RuntimeError, "求解失败"):
                                    with self.ui.session(self.goal, self.folder, 1):
                                        sandbox.execute(self.folder, 30)
                execute.assert_called_once()
                self.assertEqual(docker.call_args.args[:2], ("rm", "--force"))
                self.assertFalse(self.ui.active)

    def test_agent_rejections_feedback_and_step_limit_are_visible(self):
        multiple = action("search", "first", query="不会执行")
        multiple["tool_calls"] += action("search", "second", query="不会执行")["tool_calls"]
        actions = iter([
            {"role": "assistant", "content": "继续考虑"}, multiple,
            action("finish", candidate_id=1, summary="尚未验收"),
            action("solve", code="测试代码", formulation="测试模型"),
            {"role": "assistant", "content": "读取失败反馈"},
        ])
        snapshots = []

        def fake_model(_, messages):
            snapshots.append(json.loads(json.dumps(messages)))
            return next(actions)

        with redirect_stdout(io.StringIO()), patch("agent.ui", self.ui), patch("agent.policy", return_value="测试题目"):
            with patch("agent.model", side_effect=fake_model), patch("agent.search") as search:
                with patch("agent.solve", return_value={"valid": False, "violations": ["缺少变量"]}) as solve:
                    with self.ui.session(self.goal, self.folder, 5):
                        state = agent.run(self.goal, self.folder, None, max_steps=5)
        self.assertEqual((state.status, state.step), ("step_limit", 5))
        search.assert_not_called()
        solve.assert_called_once()
        self.assertFalse(state.results[4]["valid"])
        latest = json.loads(snapshots[-1][-1]["content"].removeprefix("当前状态："))
        self.assertFalse(latest["results"]["4"]["valid"])
        feedback = [json.loads(item["content"]) for item in snapshots[-1] if item["role"] == "tool"]
        self.assertEqual(sum("error" in item for item in feedback), 3)
        self.assertEqual(feedback[-1]["candidate_id"], 4)
        events = records(self.folder)
        self.assertEqual([item["round"] for item in events if item["event"] == "本轮结束"], [1, 2, 3, 4, 5])
        labels = [item["event"] for item in events]
        self.assertEqual(labels.count("未选择工具"), 2)
        self.assertEqual(labels.count("反馈写回：结束"), 4)
        self.assertIn("达到轮数上限", labels)
        self.assertEqual(json.loads((self.folder / "state.json").read_text(encoding="utf-8"))["status"], "step_limit")


if __name__ == "__main__":
    unittest.main()

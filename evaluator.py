"""Evaluator / Guardrails：执行限时、完整解检查和任务自己的官方验收。"""
import json
import math
import os
import subprocess
import sys
import time
from sandbox import execute


def evaluate(goal, folder):
    started = time.monotonic()
    # 保留求解器许可证配置；不把模型API密钥传给候选程序。
    clean_env = {key: value for key, value in os.environ.items()
                 if not any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}
    clean_env["PYTHONIOENCODING"] = "utf-8"
    try:
        instance_path = goal.task_dir / "instance" / goal.instance
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
        schema = json.loads((goal.task_dir / "instance_schema.json").read_text(encoding="utf-8"))
        # 只给候选约定的输入字段，避免暴露数据附带的参考答案等元信息。
        instance = {key: instance[key] for key in schema if key in instance}
        (folder / "input.json").write_text(json.dumps(instance), encoding="utf-8")
        solution = execute(folder, goal.time_limit)
        # schema还含可选字段；必填/条件字段由该任务的官方checker判断。
        if not isinstance(solution, dict):
            raise ValueError("解必须是JSON对象")
        objective = solution.get("objective_value")
        if type(objective) not in (float, int) or not math.isfinite(objective):
            raise ValueError("objective_value 必须是有限数字")
        # 容器已经停止；重新落盘到它从未能访问的位置，独立生成验收结果。
        (folder / "solution.json").write_text(json.dumps(solution, allow_nan=False), encoding="utf-8")
        if (folder / "checker.json").exists():
            raise ValueError("验收结果路径必须是新的")
        command = [sys.executable, str(goal.task_dir / "feasibility_check.py"),
                   "--instance_path", str(instance_path), "--solution_path", str(folder / "solution.json"),
                   "--result_path", str(folder / "checker.json")]
        checked = subprocess.run(command, cwd=goal.task_dir, env=clean_env, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=30)
        if checked.returncode:
            raise ValueError("checker失败：" + checked.stderr[-2000:])
        check = json.loads((folder / "checker.json").read_text(encoding="utf-8"))
        result = {"valid": check.get("feasible") is True, "objective": objective,
                  "violations": check.get("violations", [])[:5]}
    except (ValueError, KeyError, OSError, RecursionError, OverflowError, subprocess.TimeoutExpired) as error:
        result = {"valid": False, "error": str(error)}
    result["seconds"] = round(time.monotonic() - started, 3)
    (folder / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

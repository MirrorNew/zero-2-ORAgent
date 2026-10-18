"""独立进程运行候选。进程隔离/超时不等于操作系统安全沙箱。"""
import importlib.util
import json
import sys
from pathlib import Path

folder = Path(sys.argv[1])
time_limit = int(sys.argv[2])
spec = importlib.util.spec_from_file_location("candidate", folder / "candidate.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
instance = json.loads((folder / "input.json").read_text(encoding="utf-8"))
solution = candidate.solve(instance, time_limit)
(folder / "solution.json").write_text(
    json.dumps(solution, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")

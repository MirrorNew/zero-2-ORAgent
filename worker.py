"""只在容器中运行；读取两个只读输入，把解写到专用输出目录。"""
import importlib.util
import json
import os
import sys
from pathlib import Path

log = open("/output/solver.log", "w", encoding="utf-8")
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
time_limit = int(sys.argv[1])
spec = importlib.util.spec_from_file_location("candidate", "/candidate.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
instance = json.loads(Path("/input.json").read_text(encoding="utf-8"))
solution = candidate.solve(instance, time_limit)
Path("/output/solution.json").write_text(
    json.dumps(solution, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")

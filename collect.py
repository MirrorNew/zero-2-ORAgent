"""容器内可信收集器：不同UID，只取固定文件，拒绝链接及无界输出。"""
import json
import os
import stat
from pathlib import Path


def read_file(path, limit, tail=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("输出必须是普通文件，不能是链接")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("输出必须是普通文件，不能是链接")
        if tail:
            source.seek(max(0, info.st_size - limit))
        elif info.st_size > limit:
            raise ValueError("沙箱输出超过8 MiB")
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("沙箱输出超过读取上限")
    return data.decode("utf-8", errors="replace" if tail else "strict")


def read_solution(path):
    return json.loads(read_file(path, 8 * 1024 * 1024))


if __name__ == "__main__":
    payload = {"log": ""}
    try:
        payload["log"] = read_file(Path("/output/solver.log"), 32000, tail=True)
        payload["solution"] = read_solution(Path("/output/solution.json"))
        result = json.dumps(payload, allow_nan=False)
    except (OSError, ValueError, RecursionError) as error:
        result = json.dumps({"log": payload["log"], "error": str(error)})
    print(result)

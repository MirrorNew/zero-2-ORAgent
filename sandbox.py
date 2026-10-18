"""只隔离候选执行：文件、网络、资源；可信验收留在宿主。"""
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

IMAGE = "orpi-sandbox:1"


def docker(*arguments, timeout=30):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise ValueError("Docker沙箱失败：" + result.stderr[-2000:])
    return result


def execute(folder, time_limit):
    if not shutil.which("docker"):
        raise ValueError("需要Docker沙箱；安装并启动Docker，然后按README构建镜像。不会在宿主执行候选。")
    info = {key.lower(): value for key, value in json.loads(docker("info", "--format", "{{json .}}").stdout).items()}
    required = ("memorylimit", "swaplimit", "cpucfsquota", "pidslimit")
    if info.get("ostype") != "linux" or info.get("cgroupversion") != "2" or not all(info.get(key) for key in required):
        raise ValueError("需要Linux容器和cgroup v2的内存、swap、CPU与PID限制支持")
    folder = Path(folder).resolve()
    name = "orpi-" + uuid.uuid4().hex
    command = ["create", "--name", name, "--pull=never", "--network=none",
               "--read-only", "--user=65534:65534", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--cpus=1", "--memory=1g",
               "--memory-swap=1g", "--pids-limit=64", "--ipc=none",
               "--ulimit=fsize=16777216:16777216", "--ulimit=nofile=128:128",
               "--log-driver=none", "--workdir=/output",
               "--tmpfs=/output:rw,noexec,nosuid,nodev,size=64m,nr_inodes=1024,mode=1777"]
    # 阻止Docker客户端配置把宿主代理凭证自动注入容器。
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY"):
        command.extend(["--env", key + "=", "--env", key.lower() + "="])
    for filename in ("candidate.py", "input.json"):
        command.extend(["--mount", f"type=bind,source={folder / filename},target=/{filename},readonly"])
    command.extend([IMAGE, "sleep", "infinity"])
    # create不执行代码；start之后，任何退出路径都必须先确认整个容器被移除。
    docker(*command)
    try:
        deadline = time.monotonic() + time_limit + 10
        docker("start", name, timeout=time_limit + 10)
        # 候选stdout/stderr不直达宿主；日志只写入有容量限制的/output。
        executed = subprocess.run(["docker", "exec", name, "python", "-I", "-B", "/worker.py", str(time_limit)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=max(0.1, deadline - time.monotonic()))
        # 固定镜像内的收集器用不同UID运行，候选不能写它的管道或修改其代码。
        collected = docker("exec", "--user=0", name, "python", "-I", "-B", "/collect.py",
                           timeout=max(0.1, deadline - time.monotonic()))
    finally:
        docker("rm", "--force", name)
    # 只有移除整个容器成功后，才接收其不可信结果；不保留候选创建的其他文件。
    payload = json.loads(collected.stdout)
    (folder / "solver.log").write_text(payload["log"], encoding="utf-8")
    if executed.returncode or "error" in payload:
        raise ValueError(f"沙箱求解失败（退出码{executed.returncode}）：" + payload.get("error", "") + payload["log"][-2000:])
    return payload["solution"]

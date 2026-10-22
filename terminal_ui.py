"""终端显示层：中文事件记录、阶段/轮次计时；不参与Agent决策。仅用标准库。"""
import json
import os
import shutil
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime


class TerminalUI:
    def __init__(self):
        self.active = False

    @contextmanager
    def session(self, goal, output, max_steps):
        self.stream = sys.stdout
        self.started = time.perf_counter()
        self.step, self.max_steps, self.round_started = 0, max_steps, None
        self.log = (output / "progress.jsonl").open("a", encoding="utf-8")
        self.active = True
        self.note("运行开始", f"任务：{goal.task} | 实例：{goal.instance}\n"
                  f"最多{max_steps}轮 | 每个候选求解预算{goal.time_limit}秒\n输出：{output}")
        try:
            yield
        except BaseException as error:
            self.note("用户中止" if isinstance(error, KeyboardInterrupt) else "运行异常",
                      f"{type(error).__name__}: {error}")
            raise
        finally:
            self.end_round()
            self.note("运行结束", "详细消息见trace.json；各阶段计时见progress.jsonl",
                      time.perf_counter() - self.started)
            self.active = False
            self.log.close()

    def note(self, label, detail="", seconds=None):
        if not self.active:
            return
        # 模型/工具文字只作为文字显示；不执行终端控制字符，也不显示本机模型密钥。
        detail = str(detail)
        secret = os.environ.get("DEEPSEEK_API_KEY")
        if secret:
            detail = detail.replace(secret, "[密钥已隐藏]")
        detail = "".join(c if c.isprintable() or c == "\n" else f"\\x{ord(c):02x}" for c in detail)
        record = {"time": datetime.now().astimezone().isoformat(timespec="seconds"),
                  "round": self.step, "event": label, "detail": detail,
                  "seconds": None if seconds is None else round(seconds, 6),
                  "total_seconds": round(time.perf_counter() - self.started, 6)}
        duration = "" if seconds is None else f" | 耗时 {seconds:.3f} 秒"
        if seconds is not None and seconds < 0.001:
            duration = " | 耗时 <0.001 秒"
        prefix = f"第{self.step}/{self.max_steps}轮" if self.step else "准备"
        quoted = "\n".join("  | " + line for line in detail.splitlines())
        # 显示或日志写入失败不能阻止候选容器的finally清理。
        for target, text in ((self.stream, f"[{prefix}] {label}{duration}\n{quoted}\n"),
                             (self.log, json.dumps(record, ensure_ascii=False) + "\n")):
            try:
                target.write(text)
                target.flush()
            except (OSError, ValueError):
                pass

    def begin_round(self, step):
        if self.active:
            self.step, self.round_started = step, time.perf_counter()
            self.note("本轮开始", "模型将根据当前状态和历史反馈选择动作；轮数不是完成百分比。")

    def end_round(self):
        if self.active and self.round_started is not None:
            self.note("本轮结束", seconds=time.perf_counter() - self.round_started)
            self.round_started = None

    def _refresh(self, stop, label, started):
        frame = 0
        while not stop.wait(0.1):
            now = time.perf_counter()
            round_time = 0 if self.round_started is None else now - self.round_started
            text = (f" {'|/-'[frame % 3]} 第{self.step}/{self.max_steps}轮 | {label} {now-started:.1f}s"
                    f" | 本轮 {round_time:.1f}s | 总计 {now-self.started:.1f}s")
            # 留一列避免自动换行；中文按终端两列宽度计算。
            width = max(1, shutil.get_terminal_size((100, 24)).columns - 1)
            line, used = "", 0
            for char in text:
                size = 2 if unicodedata.east_asian_width(char) in "WF" else 1
                if used + size > width:
                    break
                line, used = line + char, used + size
            try:
                self.stream.write("\r" + line + " " * (width - used))
                self.stream.flush()
            except (OSError, ValueError):
                return
            frame += 1

    @contextmanager
    def stage(self, label, detail=""):
        if not self.active:
            yield
            return
        started = time.perf_counter()
        self.note(label + "：开始", detail)
        stop = threading.Event()
        thread = None
        try:
            if self.stream.isatty():
                thread = threading.Thread(target=self._refresh, args=(stop, label, started), daemon=True)
                thread.start()
        except (OSError, ValueError, RuntimeError):
            thread = None  # 终端不可用或不能创建线程时，仍执行真实操作和清理。
        outcome = "结束"
        try:
            yield
        except BaseException:
            outcome = "中断或异常"
            raise
        finally:
            stop.set()
            if thread:
                thread.join()
                try:
                    width = max(1, shutil.get_terminal_size((100, 24)).columns - 1)
                    self.stream.write("\r" + " " * width + "\r")
                except (OSError, ValueError):
                    pass
            self.note(label + "：" + outcome, seconds=time.perf_counter() - started)


ui = TerminalUI()

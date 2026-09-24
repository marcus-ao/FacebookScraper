"""强制 UTF-8 输出，避免 Windows 管道和计划任务回落到 GBK。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

# 子进程看到这个变量就不再自己打印；只让最外层那一次 Ctrl+C 说一声。
STOP_NOTICE = "FBSCRAPER_STOP_NOTICE"
_STOP_LINE = "已停止。"


def force_utf8() -> None:
    """在入口处调用一次。失败不抛异常——它只是让输出可靠，不该成为新的失败源。"""
    for stream in (sys.stdout, sys.stderr):
        # pythonw 下 sys.stdout 可能是 None；测试里也常被换成不带 reconfigure 的对象
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 流已关闭或底层不支持重配。到这一步只能认命，但别让它变成崩溃点。
            continue


def note_stop() -> None:
    """一次 Ctrl+C 只留这一行。内层进程已经被告知由外层说，就保持安静。"""
    if os.environ.get(STOP_NOTICE):
        return
    print(_STOP_LINE, file=sys.stderr, flush=True)


def run_foreground(argv, *, cwd=None, env=None) -> int:
    """前台等待子进程完成，包括 Ctrl+C 后的协作清理。"""
    child_env = dict(os.environ if env is None else env)
    child_env[STOP_NOTICE] = "1"
    interrupted = False

    def on_interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    previous_handler = signal.signal(signal.SIGINT, on_interrupt)
    try:
        if interrupted:
            note_stop()
            return 130
        process = subprocess.Popen(list(argv), cwd=cwd, env=child_env)
        while True:
            try:
                code = process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
        if interrupted:
            note_stop()
        return code
    finally:
        signal.signal(signal.SIGINT, previous_handler)

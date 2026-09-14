"""强制 UTF-8 输出，避免 Windows 管道和计划任务回落到 GBK。"""
from __future__ import annotations

import sys


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

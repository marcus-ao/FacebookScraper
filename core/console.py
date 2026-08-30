"""把标准输出/错误强制成 UTF-8。

**这个模块存在的唯一理由是：本机的 ANSI 代码页是 936(GBK)。**

Python 在 Windows 上只有当 stdout 连着**真实控制台**时才走 Unicode API
(WriteConsoleW)；一旦被重定向到管道或文件，就回落到 locale 编码。
实测 `⚠` `❗` `✅` `❌` `ß` 在 cp936 下**一个都编码不出来**，
print 直接抛 UnicodeEncodeError，把整个进程带走。

所以这个故障有个很坏的性质：**双击 .bat 时永远看不到它**（那是控制台），
只在真正要它可靠的时候才发作。具体到本项目：

  * E1 的任务描述本身就要求把增量输出**追加到 state/delta.log** —— 那就是重定向；
  * E3 的计划任务在无人登录时触发，连控制台都没有；
  * translate.py 打的 `❗金额被改动` 是全项目最重要的一条安全信号，
    而它恰好就是会崩的那一行 —— 崩在网络请求已发出、归档尚未写入之际。

`errors="replace"` 而不是默认的 strict：一个符号打不出来，
不该让整批抓取失败。宁可显示成 `?` 也要让主流程走完。

.bat 那一侧另设了 `PYTHONIOENCODING=utf-8`（纯 ASCII，不违反 .bat 约定）。
两处都做是有意的：.bat 管双击路径，本模块管 `python -m routes.delta` 这种直接调用。
"""
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

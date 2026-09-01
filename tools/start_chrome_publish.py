r"""启动发布账号专用 Chrome（G0）。

发布实例固定读取 ``[publish]`` 的 profile/port；抓取侧仍由
``tools/start_chrome.py`` 读取 ``[chrome]``。两者必须同时可运行，且绝不能
互换 profile，因为抓取小号被封是本项目唯一不可恢复的失败模式。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import (PORT_WAIT_SECONDS, _cdp_profile_matches,  # noqa: E402
                         cdp_ready, launch, listening_pid, port_open)
from core.config import cfg                                             # noqa: E402
from core.console import force_utf8                                     # noqa: E402


def _report_profile_mismatch(port: int, profile) -> int:
    """端口上是 Chrome、但归属核对没过时，说清到底是哪一种。

    ⚠️ **"确实是别的 profile" 与 "核对不了" 是两件事**（CR-63）。
    原来两种情况印同一段话，于是 2026-09-01 用户调 G1 时，
    一个**完全正确**的环境被报成"你开错了浏览器"，还被建议去关掉它——
    真实原因是归属核对的子进程超时（5 秒不够，实测要 7–9 秒）。
    **让人去关一个本来就对的 Chrome，比不报错更坏。**
    """
    verdict = _cdp_profile_matches(port, profile)
    if verdict is False:
        print("[!] 发布端口 %d 上是 Chrome，但它用的**不是**发布 profile。" % port)
        print("    目标发布 profile：%s" % profile)
        print("    为防把 DE 内容带进错误会话，已停止。")
        print("    请关掉占着这个端口的那个 Chrome，再重跑本脚本。")
        print("    ⚠️ 只关那一个，不要误关仍在 9222 上工作的抓取 Chrome。")
        return 1

    pid = listening_pid(port)
    print("[!] 发布端口 %d 上是 Chrome，但**无法确认**它属于哪份 profile。" % port)
    print("    注意：这不是说它错了，是说这台机器上读不到监听进程的命令行。")
    print("    目标发布 profile：%s" % profile)
    print("    为安全起见仍然停下。自己核一眼（两条都是只读的）：")
    print("        netstat -ano | findstr :%d" % port)
    if pid is not None:
        print("        powershell -NoProfile -Command \"(Get-CimInstance Win32_Process "
              "-Filter 'ProcessId = %d').CommandLine\"" % pid)
    else:
        print("        powershell -NoProfile -Command \"(Get-CimInstance Win32_Process "
              "-Filter 'ProcessId = <上面那个PID>').CommandLine\"")
    print("    命令行里的 --user-data-dir 就是它真正在用的 profile；")
    print("    如果它和上面那行一致，说明环境是对的，是核对手段读不出来。")
    return 1


def main() -> int:
    force_utf8()

    c = cfg()
    c.assert_publish_chrome_isolated()
    exe = c.chrome_exe
    profile = c.publish_profile_dir
    port = c.publish_debug_port

    print("使用 Chrome:      %s" % exe)
    print("发布专用 profile: %s" % profile)
    print("发布调试端口:     %d" % port)
    print()

    if cdp_ready(port, profile=profile):
        print("[i] 发布端口 %d 已就绪，发布专用 Chrome 已经在运行。" % port)
        print("    不会启动第二个实例；可直接运行 G1 探查工具。")
        return 0
    if cdp_ready(port):
        return _report_profile_mismatch(port, profile)
    if port_open(port):
        print("[!] 发布端口 %d 已被其它程序占用，但它不是 Chrome 调试端口。" % port)
        print("    请关闭占用程序，或只修改 config.toml 的 [publish].debug_port。")
        return 1

    print("等待发布调试端口就绪...", end="", flush=True)
    if launch(PORT_WAIT_SECONDS, on_tick=lambda: print(".", end="", flush=True),
              port=port, profile=profile):
        print()
        print("[ok] 发布调试端口 %d 已就绪。" % port)
        print()
        print("若这是第一次运行，请只在这个窗口里人工登录有 DE Page 发布权的账号。")
        print("不要登录抓取小号，也不要把这个窗口用于日常浏览。")
        print("登录完成后运行：")
        print(r"    .venv\Scripts\python.exe tools\probe_publish.py")
        return 0

    print()
    # ⚠️ `launch()` 有两种失败：端口一直没起来、以及端口起来了但归属核对没过。
    # 原来这里只印前一种（还写死"等了 15 秒"），于是归属核对失败时
    # 用户看到的是一份**全是错的**排查清单 —— 2026-09-01 真踩了（CR-63）。
    # 所以这里重新分诊一次，再决定说什么。
    if cdp_ready(port):
        return _report_profile_mismatch(port, profile)

    print("[!] 等了 %d 秒，发布 Chrome 的调试端点 %d 仍未就绪。常见原因："
          % (PORT_WAIT_SECONDS, port))
    print("    1. 发布 profile 已被另一个 Chrome 占用；Chrome 会静默复用旧实例，")
    print("       并忽略 --remote-debugging-port。只检查使用该发布 profile 的进程，")
    print("       不要误关仍在 9222 上工作的抓取 Chrome。")
    print("    2. 端口被其它程序占用；只改 [publish].debug_port，别动 [chrome]。")
    print("    3. Chrome 路径不对；核对上面打印的路径。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

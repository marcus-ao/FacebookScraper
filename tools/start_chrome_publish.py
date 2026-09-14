"""按 publish 配置启动独立发布 Chrome，不与抓取会话混用。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import (PORT_WAIT_SECONDS, _cdp_profile_matches,  # noqa: E402
                         cdp_ready, launch, listening_pid, port_open)
from core.config import cfg                                             # noqa: E402
from core.console import force_utf8                                     # noqa: E402


def _report_profile_mismatch(port: int, profile) -> int:
    """区分 profile 错配与无法核验，给出对应提示。"""
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
    # 区分端口未就绪与 profile 核验失败。
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

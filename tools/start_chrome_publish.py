r"""启动发布账号专用 Chrome（G0）。

发布实例固定读取 ``[publish]`` 的 profile/port；抓取侧仍由
``tools/start_chrome.py`` 读取 ``[chrome]``。两者必须同时可运行，且绝不能
互换 profile，因为抓取小号被封是本项目唯一不可恢复的失败模式。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import PORT_WAIT_SECONDS, cdp_ready, launch, port_open  # noqa: E402
from core.config import cfg                                             # noqa: E402
from core.console import force_utf8                                     # noqa: E402


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
        print("[!] 发布端口 %d 上是 Chrome，但它不属于发布 profile，" % port)
        print("    或 Windows 无法确认进程归属。为防附着到抓取小号，已停止。")
        print("    目标发布 profile：%s" % profile)
        return 1
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

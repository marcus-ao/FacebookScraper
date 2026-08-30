r"""起专用 Chrome 并开放调试端口。对应实施计划的 A3。

由 scripts\start_chrome.bat 调用（用 venv 里的解释器）。

**与原 start_chrome.bat 的关键差别**：Chrome 路径、profile 目录、调试端口
全部从 config.toml 读，不再在 .bat 顶部写第二份。原来那份是重复来源——
计划里专门警告过"改了 config.toml 的 debug_port 还要同步改 bat 顶部的 PORT"，
现在没有第二处可改了。

小号在这个 profile 里人工登录一次即可，与日常 Chrome 完全隔离。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import cdp_ready, port_open  # noqa: E402
from core.config import cfg                # noqa: E402

# Chrome 冷启动到端口可连有几秒延迟，立刻去连 CDP 会失败，看起来像"脚本坏了"
PORT_WAIT_SECONDS = 15


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    c = cfg()
    exe = c.chrome_exe            # 找不到会 SystemExit 并给出可照做的提示
    profile = c.profile_dir
    port = c.debug_port

    print("使用 Chrome: %s" % exe)
    print("专用 profile: %s" % profile)
    print("调试端口:     %d" % port)
    print()

    # 端口已在监听 = 专用实例已经在跑。再启一次只会聚焦已有窗口，
    # 且新传的 --remote-debugging-port 会被静默忽略，反而制造误判。
    if cdp_ready(port):
        print("[i] 端口 %d 已在监听，说明专用 Chrome 已经在运行。" % port)
        print("    无需重复启动，直接去跑抓取脚本即可。")
        return 0
    if port_open(port):
        print("[!] 端口 %d 已被其它程序占用，但它不是 Chrome 调试端口。" % port)
        print("    请关闭占用程序，或修改 config.toml 的 [chrome].debug_port。")
        return 1

    profile.mkdir(parents=True, exist_ok=True)
    argv = [
        str(exe),
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile,
        "--no-first-run",
        "--no-default-browser-check",
    ]
    # 脱离本进程存活：脚本退出后 Chrome 窗口必须还开着
    flags = 0
    if sys.platform.startswith("win"):
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(argv, creationflags=flags, close_fds=True)

    print("等待调试端口就绪...", end="", flush=True)
    deadline = time.monotonic() + PORT_WAIT_SECONDS
    while time.monotonic() < deadline:
        if cdp_ready(port):
            print()
            print("[ok] 调试端口 %d 已就绪。" % port)
            print()
            print("若这是第一次运行，请在打开的窗口里登录抓取用的小号（含二次验证）。")
            print("保持这个窗口开着，然后运行：")
            print("    scripts\\run_backfill.bat facebook")
            return 0
        time.sleep(1)
        print(".", end="", flush=True)

    print()
    print("[!] 等了 %d 秒，Chrome 调试端点 %d 仍未就绪。常见原因："
          % (PORT_WAIT_SECONDS, port))
    print("    1. 该 user-data-dir 已被另一个 Chrome 实例占用 —— Chrome 会静默复用")
    print("       已有实例并忽略 --remote-debugging-port，没有任何报错。")
    print("       先完全退出所有 Chrome（任务管理器里确认无 chrome.exe），再重试。")
    print("    2. 端口被其它程序占用 —— 改 config.toml 的 [chrome].debug_port。")
    print("    3. Chrome 路径不对 —— 核对上面打印的路径。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

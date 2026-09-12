"""启动探测号专用 Chrome；登录由人完成，程序只复用该会话。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chrome import cdp_ready, launch, port_open  # noqa: E402
from core.config import cfg  # noqa: E402
from core.console import force_utf8  # noqa: E402


def main() -> int:
    force_utf8()
    config = cfg()
    config.assert_chrome_profiles_isolated()
    port, profile = config.detect_debug_port, config.detect_profile_dir
    print(f"探测专用 Chrome：CDP {port}；profile {profile}")
    if cdp_ready(port, profile=profile):
        print("探测实例已就绪，可直接运行监测。")
        return 0
    if port_open(port):
        print("[!] 端口已占用，尚不能确认属于探测 profile；请核对后再启动。")
        return 1
    if not launch(port=port, profile=profile):
        print("[!] 探测实例未就绪；请核对 [detect] 配置及该 profile 的进程。")
        return 1
    print("首次使用请在这个窗口人工登录探测账号；登录完成后会话会保留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

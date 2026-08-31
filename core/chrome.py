r"""CDP 附着到专用 Chrome profile。

为什么是附着而不是 Playwright 自己启动浏览器：
    附着到真实 Chrome 进程，指纹就是这台机器上真实 Chrome 的指纹 ——
    没有 Playwright 注入的自动化标记，没有 CDP 启动参数留下的痕迹。
    代价是需要先用 scripts\start_chrome.bat 把浏览器起起来。

隔离：默认用 config.toml 的 [chrome]（抓取小号）；发布侧必须显式传入
     [publish] 的 port/profile，使用另一份浏览器指纹。两个实例可以同时存在。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from core.config import cfg

# Chrome 冷启动到端口可连有几秒延迟，立刻去连 CDP 会失败，看起来像"脚本坏了"
PORT_WAIT_SECONDS = 15


def _resolved_port(port: int | None) -> int:
    """显式端口优先；不给时保持原调用方的 [chrome] 行为。"""
    value = cfg().debug_port if port is None else int(port)
    if not 1 <= value <= 65535:
        raise ValueError("Chrome 调试端口必须在 1–65535 之间：%r" % value)
    return value


def _resolved_profile(profile: Path | str | None) -> Path:
    """显式 profile 优先；不给时保持原调用方的 [chrome] 行为。"""
    if profile is None:
        return cfg().profile_dir
    return Path(os.path.expandvars(str(profile))).expanduser()


def _cdp_profile_matches(port: int, profile: Path) -> bool | None:
    """Windows 上确认监听端口的 Chrome 命令行确实使用目标 profile。

    ``/json/version`` 只能证明“这是 Chrome”，不能证明“这是哪份 profile”。
    发布侧若只验端口，另一个 Chrome 恰好占了 9223 时仍会把 DE 内容带进
    错误会话。Windows 是部署目标，因此用只读的进程信息补上这条归属证据。
    返回 ``None`` 表示系统不支持/无法读取；调用方在 Windows 上应失败闭合。
    """
    if not sys.platform.startswith("win"):
        return None
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$c=Get-NetTCPConnection -LocalPort %d -State Listen | Select-Object -First 1;"
        "if($null -eq $c){exit 3};"
        "$p=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$c.OwningProcess);"
        "$p.CommandLine" % port)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    match = re.search(
        r'--user-data-dir=(?:"([^"]+)"|(\S+))', result.stdout,
        flags=re.IGNORECASE)
    if not match:
        return None
    actual = Path(match.group(1) or match.group(2)).resolve(strict=False)
    expected = profile.resolve(strict=False)
    return os.path.normcase(str(actual)) == os.path.normcase(str(expected))


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def cdp_ready(port: int | None = None, host: str = "127.0.0.1",
              timeout: float = 1.0, *,
              profile: Path | str | None = None) -> bool:
    """确认端口是 CDP；可在 Windows 上同时核对其实际 profile。

    port 不给仍读 [chrome]。profile 不给只验证 CDP（兼容所有现有调用）；
    显式给 profile 时，Windows 上若进程归属不可确认也返回 False。
    """
    port = _resolved_port(port)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/json/version")
        response = conn.getresponse()
        if response.status != 200:
            return False
        payload = json.loads(response.read(65536))
        ready = (isinstance(payload, dict)
                 and str(payload.get("webSocketDebuggerUrl", "")).startswith(
                     ("ws://", "wss://")))
        if not ready or profile is None:
            return ready
        if not sys.platform.startswith("win"):
            return True
        return _cdp_profile_matches(port, _resolved_profile(profile)) is True
    except (OSError, ValueError):
        return False
    finally:
        conn.close()


def launch(wait_seconds: float = PORT_WAIT_SECONDS,
           on_tick: Callable[[], None] | None = None, *,
           port: int | None = None,
           profile: Path | str | None = None) -> bool:
    """起专用 Chrome 并等调试端口就绪。已在跑就直接返回 True。

    ⚠️ **这不是"自动登录"。** 它只是把那个人工登录过一次的 profile 目录
    重新用起来；会话是人留下的，程序从不代替人登录（全项目红线 1）。

    ⚠️ 若该 ``user-data-dir`` 已被另一个 Chrome 实例占用，Chrome 会**静默复用
    已有实例并忽略 --remote-debugging-port**，没有任何报错。所以这里只能靠
    轮询端口来判断成败，不能看子进程的退出码。

    `on_tick` 每等一秒回调一次，供命令行打进度点；不给就安静地等。
    """
    port = _resolved_port(port)
    profile = _resolved_profile(profile)
    if cdp_ready(port, profile=profile):
        return True
    if port_open(port):
        return False        # 端口被别的程序占了，起了也连不上，交给调用方报错

    c = cfg()
    profile.mkdir(parents=True, exist_ok=True)
    argv = [
        str(c.chrome_exe),
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

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if cdp_ready(port, profile=profile):
            return True
        time.sleep(1)
        if on_tick is not None:
            on_tick()
    return cdp_ready(port, profile=profile)


async def attach(port: int | None = None,
                 profile: Path | str | None = None, *,
                 start_script: str | None = None,
                 login_hint: str | None = None):
    """返回 (playwright, browser, context)。调用方负责 close。

    ``port`` / ``profile`` 不给时退回 [chrome]，所以 backfill / delta 的现有
    ``attach()`` 调用行为不变。发布侧显式传 [publish] 的两个值，避免把持有
    DE 发布权的账号附着到抓取小号的浏览器指纹。

    找不到调试端口时给出可直接照做的提示，而不是抛一个 CDP 连接错误。
    """
    from playwright.async_api import async_playwright

    using_default_target = port is None and profile is None
    port = _resolved_port(port)
    profile = _resolved_profile(profile)
    if start_script is None:
        start_script = (r"scripts\start_chrome.bat" if using_default_target
                        else "对应的 Chrome 启动脚本")
    if login_hint is None:
        login_hint = "抓取小号" if using_default_target else "对应账号"

    if not cdp_ready(port, profile=profile):
        if cdp_ready(port):
            detail = (f"端口 {port} 是 Chrome 调试端口，但不属于目标 profile，"
                      f"或 Windows 无法读取其进程归属。\n"
                      f"目标 profile：{profile}\n"
                      f"不要继续附着；先关闭占错端口的 Chrome，再运行 {start_script}。")
        elif port_open(port):
            detail = (f"端口 {port} 已被其它程序占用，但它不是 Chrome 调试端口。\n"
                      f"目标 profile：{profile}\n"
                      f"请修改对应配置的 debug_port，或关闭占用程序。")
        else:
            detail = (f"调试端口 {port} 没开。\n"
                      f"目标 profile：{profile}\n"
                      f"先运行 {start_script}（Windows）把专用 Chrome 起起来，"
                      f"并确认{login_hint}已在那个窗口里登录。")
        raise SystemExit(
            detail
        )

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except BaseException:
        await pw.stop()
        raise
    if not browser.contexts:
        try:
            await browser.close()
        finally:
            await pw.stop()
        raise SystemExit("Chrome 已连上但没有可用上下文，请在该窗口里打开任意标签页后重试。")
    return pw, browser, browser.contexts[0]

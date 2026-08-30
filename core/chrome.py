r"""CDP 附着到专用 Chrome profile。

为什么是附着而不是 Playwright 自己启动浏览器：
    附着到真实 Chrome 进程，指纹就是这台机器上真实 Chrome 的指纹 ——
    没有 Playwright 注入的自动化标记，没有 CDP 启动参数留下的痕迹。
    代价是需要先用 scripts\start_chrome.bat 把浏览器起起来。

隔离：用的是 config.toml 里的专用 profile_dir，不是日常 Chrome 的 profile。
     该目录里存着已登录的小号会话，已在 .gitignore 中排除。
"""
from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import time
from typing import Callable

from core.config import cfg

# Chrome 冷启动到端口可连有几秒延迟，立刻去连 CDP 会失败，看起来像"脚本坏了"
PORT_WAIT_SECONDS = 15


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def cdp_ready(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """端口上确实是可用的 Chrome DevTools Protocol，而不只是任意监听器。"""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/json/version")
        response = conn.getresponse()
        if response.status != 200:
            return False
        payload = json.loads(response.read(65536))
        return (isinstance(payload, dict)
                and str(payload.get("webSocketDebuggerUrl", "")).startswith(
                    ("ws://", "wss://")))
    except (OSError, ValueError):
        return False
    finally:
        conn.close()


def launch(wait_seconds: float = PORT_WAIT_SECONDS,
           on_tick: Callable[[], None] | None = None) -> bool:
    """起专用 Chrome 并等调试端口就绪。已在跑就直接返回 True。

    ⚠️ **这不是"自动登录"。** 它只是把那个人工登录过一次的 profile 目录
    重新用起来；会话是人留下的，程序从不代替人登录（全项目红线 1）。

    ⚠️ 若该 ``user-data-dir`` 已被另一个 Chrome 实例占用，Chrome 会**静默复用
    已有实例并忽略 --remote-debugging-port**，没有任何报错。所以这里只能靠
    轮询端口来判断成败，不能看子进程的退出码。

    `on_tick` 每等一秒回调一次，供命令行打进度点；不给就安静地等。
    """
    port = cfg().debug_port
    if cdp_ready(port):
        return True
    if port_open(port):
        return False        # 端口被别的程序占了，起了也连不上，交给调用方报错

    c = cfg()
    profile = c.profile_dir
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
        if cdp_ready(port):
            return True
        time.sleep(1)
        if on_tick is not None:
            on_tick()
    return cdp_ready(port)


async def attach():
    """返回 (playwright, browser, context)。调用方负责 close。

    找不到调试端口时给出可直接照做的提示，而不是抛一个 CDP 连接错误。
    """
    from playwright.async_api import async_playwright

    port = cfg().debug_port
    if not cdp_ready(port):
        if port_open(port):
            detail = (f"端口 {port} 已被其它程序占用，但它不是 Chrome 调试端口。\n"
                      f"请修改 config.toml 的 [chrome].debug_port，或关闭占用程序。")
        else:
            detail = (f"调试端口 {port} 没开。\n"
                      f"先运行 scripts\\start_chrome.bat（Windows）把专用 Chrome 起起来，"
                      f"并确认小号已在那个窗口里登录。")
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

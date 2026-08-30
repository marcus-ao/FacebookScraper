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

from core.config import cfg


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

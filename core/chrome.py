"""通过 CDP 附着人工登录的 Chrome；回填、监测和发布使用独立 profile。"""
from __future__ import annotations

import asyncio
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

# 为 Windows 进程命令行查询预留超时余量。
PROFILE_PROBE_TIMEOUT = 20.0


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


def _profile_from_command_line(command_line: str) -> str | None:
    """取 Chrome ``--user-data-dir``，兼容 Windows 对整段参数加引号。"""
    match = re.search(
        r'(?:(?:"--user-data-dir=([^\"]+)")|'
        r'(?:--user-data-dir(?:=|\s+)(?:"([^\"]+)"|(\S+))))',
        command_line,
        flags=re.IGNORECASE)
    if not match:
        return None
    return next((value for value in match.groups() if value is not None), None)


def listening_pid(port: int) -> int | None:
    """通过 netstat 获取监听 PID，避免加载 PowerShell 网络模块；失败返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=PROFILE_PROBE_TIMEOUT, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        fields = line.split()
        # 协议 本地地址 外部地址 状态 PID —— IPv6 是 [::1]:9223，rpartition 同样成立
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        if fields[3].upper() != "LISTENING":
            continue
        _host, sep, actual = fields[1].rpartition(":")
        if not sep or actual != str(port) or not fields[4].isdigit():
            continue
        return int(fields[4])
    return None


def _command_line_of(pid: int) -> str | None:
    """通过 CIM 读取进程命令行以核验 profile；失败返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "(Get-CimInstance Win32_Process -Filter 'ProcessId = %d').CommandLine" % pid)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=PROFILE_PROBE_TIMEOUT, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout


def _cdp_profile_matches(port: int, profile: Path) -> bool | None:
    """核验实际 profile：True 匹配，False 错配，None 无法核验；后两者均禁止附着。"""
    if not sys.platform.startswith("win"):
        return None
    pid = listening_pid(port)
    if pid is None:
        return None
    command_line = _command_line_of(pid)
    if command_line is None:
        return None
    raw_profile = _profile_from_command_line(command_line)
    if raw_profile is None:
        return None
    actual = Path(raw_profile).resolve(strict=False)
    expected = profile.resolve(strict=False)
    return os.path.normcase(str(actual)) == os.path.normcase(str(expected))


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def cdp_ready(port: int | None = None, host: str = "127.0.0.1",
              timeout: float = 1.0, *,
              profile: Path | str | None = None) -> bool:
    """检查 CDP 就绪；指定 profile 时还须确认进程归属。"""
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
    """复用人工登录的 profile 启动 Chrome；轮询端口并按秒调用 on_tick。"""
    verify_profile = profile is not None
    port = _resolved_port(port)
    profile = _resolved_profile(profile)
    if cdp_ready(port, profile=profile if verify_profile else None):
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

    # 轮询只查 CDP，端口就绪后再执行较慢的 profile 核验。
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if cdp_ready(port):
            return cdp_ready(port, profile=profile if verify_profile else None)
        time.sleep(1)
        if on_tick is not None:
            on_tick()
    return cdp_ready(port, profile=profile if verify_profile else None)


async def attach(port: int | None = None,
                 profile: Path | str | None = None, *,
                 start_script: str | None = None,
                 login_hint: str | None = None):
    """返回 (playwright, browser, context)；调用方用 pw.stop() 断开，保留人工 Chrome。"""
    from playwright.async_api import async_playwright

    using_default_target = port is None and profile is None
    verify_profile = profile is not None
    port = _resolved_port(port)
    profile = _resolved_profile(profile)
    if start_script is None:
        start_script = (r"scripts\start_chrome.bat" if using_default_target
                        else "对应的 Chrome 启动脚本")
    if login_hint is None:
        login_hint = "抓取小号" if using_default_target else "对应账号"

    if not cdp_ready(port, profile=profile if verify_profile else None):
        if cdp_ready(port):
            # 区分 profile 错配与核验失败，避免误导用户关闭正确会话。
            verdict = _cdp_profile_matches(port, profile)
            if verdict is False:
                detail = (f"端口 {port} 是 Chrome 调试端口，但它用的**不是**目标 profile。\n"
                          f"目标 profile：{profile}\n"
                          f"不要继续附着；先关闭占着这个端口的那个 Chrome，"
                          f"再运行 {start_script}。")
            else:
                detail = (f"端口 {port} 是 Chrome 调试端口，但**无法确认**它属于哪份 profile"
                          f"（读不到监听进程的命令行，不是说它错了）。\n"
                          f"目标 profile：{profile}\n"
                          f"为安全起见仍然停下。自己查一眼归属：\n"
                          f"    netstat -ano | findstr :{port}\n"
                          f"    powershell -NoProfile -Command "
                          f"\"(Get-CimInstance Win32_Process -Filter 'ProcessId = <上面那个PID>')"
                          f".CommandLine\"\n"
                          f"命令行里的 --user-data-dir 就是它真正在用的 profile。")
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
        await pw.stop()
        raise SystemExit("Chrome 已连上但没有可用上下文，请在该窗口里打开任意标签页后重试。")
    return pw, browser, browser.contexts[0]


async def close_owned_page(page) -> str | None:
    """Release only a caller-created page without closing Chrome's last tab.

    Closing the last tab can exit a manually launched Chrome. Keep an empty tab
    in that case so a later attachment does not report a missing debug port.
    Never use this on a human tab or an unresolved submission's composer.
    """
    try:
        if getattr(getattr(page, 'context', None), 'pages', ()) == [page]:
            await asyncio.wait_for(page.goto('about:blank', wait_until='commit', timeout=5000), 6)
        else:
            await asyncio.wait_for(page.close(), 6)
    except Exception as exc:
        # Cleanup must not replace a durable scheduling result or the original error.
        print('发布临时页面未能释放：' + type(exc).__name__)
        return type(exc).__name__
    return None

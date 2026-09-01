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

# profile 归属核对的子进程超时。**这个数是实测标定的，不要凭感觉调小**（CR-63）。
#
# 2026-09-01 本机（Windows 11 / Chrome 152）逐段实测：
#     powershell.exe 空跑（纯启动开销）      2.17s
#     + Get-NetTCPConnection                7.84s   ← 光加载 NetTCPIP 模块就 ~5.7s
#     + Get-CimInstance Win32_Process       3.84s
#     netstat -ano（纯 exe，同一个 PID）      0.49s
#
# 原实现是 `Get-NetTCPConnection` + `Get-CimInstance` 一次跑完，实测
# **7.4–9.4 秒**，而超时写的是 5 秒 —— 于是**每一次都超时**，
# 被 `except SubprocessError` 吃掉、返回 None，一个完全正确的环境被判成
# "核对不了"并失败闭合。用户看到的是"发布 Chrome 没起来"，其实它好好地开着。
#
# 现在端口→PID 改用 netstat（0.12s），只剩 PID→命令行 走 PowerShell（~3.5s）。
# 20 秒是在实测 3.5s 上留了 5 倍余量：这条路径一轮只跑一两次，
# 宁可慢也不能**误判**——误判的代价是把对的环境说成错的。
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
    """监听 ``port`` 的进程 PID；读不出来返回 ``None``。

    ⚠️ **用 `netstat -ano` 而不是 `Get-NetTCPConnection`**（CR-63）。
    两者给出同一个 PID，但本机实测 `Get-NetTCPConnection` 光加载 NetTCPIP
    模块就要约 5.7 秒，而 `netstat` 只要 **0.12 秒**——差 40 倍。
    原实现把它和 `Get-CimInstance` 串在一条 PowerShell 里，
    总耗时 7.4–9.4 秒却只给了 5 秒超时，于是**每次都超时**。
    """
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
    """进程的完整命令行；读不出来返回 ``None``。

    这一步只能走 WMI/CIM —— Windows 上没有别的办法从 PID 拿到命令行
    （`Get-Process` 只给可执行文件路径，那证明不了 ``--user-data-dir``）。
    本机实测约 3.5 秒，是这条链路上剩下的主要开销。
    """
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
    """Windows 上确认监听端口的 Chrome 命令行确实使用目标 profile。

    ``/json/version`` 只能证明“这是 Chrome”，不能证明“这是哪份 profile”。
    发布侧若只验端口，另一个 Chrome 恰好占了 9223 时仍会把 DE 内容带进
    错误会话。Windows 是部署目标，因此用只读的进程信息补上这条归属证据。

    **三态返回，调用方必须区分**（CR-63）：

    - ``True``  归属确认，可以附着；
    - ``False`` **确实是别的 profile** —— 危险，必须停下并让用户去关那个 Chrome；
    - ``None``  **核对不了**（非 Windows、netstat/WMI 读不出来）。
      仍然失败闭合，但**原因完全不同**：多半是工具链问题，不是环境错了。
      把这两种混成同一句话，会让一个正确的环境被报成"你开错了浏览器"——
      2026-09-01 用户调 G1 时就被这么误导过一次。

    ⚠️ **这个调用不便宜**（实测约 3.5 秒，见 :data:`PROFILE_PROBE_TIMEOUT` 的标定）。
    所以 :func:`launch` 的轮询循环**只用不带 profile 的轻量 CDP 探测**，
    等端口真的起来了再核对一次归属（CR-59）。
    **不要为了"更快"把归属核对整个去掉**：`/json/version` 证明不了是哪份
    profile，而这条证据正是 G0 存在的全部理由。
    """
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

    # 轮询只做**轻量**的 CDP 探测：归属核对要 fork PowerShell，每秒一次太贵
    # （CR-59）。端口真的起来之后再核对一次，判据完全不变。
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
    """返回 (playwright, browser, context)。调用方负责 close。

    ``port`` / ``profile`` 不给时退回 [chrome]，所以 backfill / delta 的现有
    ``attach()`` 调用行为不变。发布侧显式传 [publish] 的两个值，避免把持有
    DE 发布权的账号附着到抓取小号的浏览器指纹。

    找不到调试端口时给出可直接照做的提示，而不是抛一个 CDP 连接错误。
    """
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
            # ⚠️ **"是别的 profile" 与 "核对不了" 必须分开说**（CR-63）。
            # 原来两句合成一句，于是 2026-09-01 用户调 G1 时，
            # 一个**完全正确**的环境被报成"你开错了浏览器，去关掉它"——
            # 真实原因是归属核对子进程超时（5 秒不够，实测要 7–9 秒）。
            # 让人去关一个本来就对的 Chrome，是比不报错更坏的结果。
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
        try:
            await browser.close()
        finally:
            await pw.stop()
        raise SystemExit("Chrome 已连上但没有可用上下文，请在该窗口里打开任意标签页后重试。")
    return pw, browser, browser.contexts[0]

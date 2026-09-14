"""Chrome/CDP 启动探测自测：不能把任意占用端口误认成专用 Chrome。"""
import asyncio
import inspect
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

import core.chrome as chrome                         # noqa: E402
from core.config import Config                       # noqa: E402
from core.chrome import cdp_ready, port_open          # noqa: E402
import tools.start_chrome as start_chrome             # noqa: E402


fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Handler(BaseHTTPRequestHandler):
    body = b"{}"
    seen_path = ""

    def do_GET(self):
        type(self).seen_path = self.path
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
port = server.server_address[1]

try:
    print("[1] 端口监听与 CDP 就绪是两件不同的事")
    check(port_open(port), "本地测试服务端口确实在监听")
    Handler.body = b"{}"
    check(not cdp_ready(port), "普通 HTTP 服务不会被误认成 Chrome")

    Handler.body = json.dumps({
        "Browser": "Chrome/140.0",
        "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/test",
    }).encode("utf-8")
    check(cdp_ready(port), "含 webSocketDebuggerUrl 的 /json/version 被认作 CDP")
    check(Handler.seen_path == "/json/version", "探测的是 Chrome 标准版本端点")

    original_cfg = chrome.cfg
    try:
        class DefaultConfig:
            debug_port = port

        chrome.cfg = lambda: DefaultConfig()
        check(cdp_ready(), "cdp_ready() 不给端口时仍退回 [chrome].debug_port")
    finally:
        chrome.cfg = original_cfg

    original_match = chrome._cdp_profile_matches
    try:
        chrome._cdp_profile_matches = lambda _port, _profile: True
        check(cdp_ready(port, profile=Path("publish-profile")),
              "显式 profile 与监听进程匹配时 CDP 才就绪")
        chrome._cdp_profile_matches = lambda _port, _profile: False
        check(not cdp_ready(port, profile=Path("publish-profile")),
              "端口虽是 CDP 但属于另一 profile 时失败闭合")
    finally:
        chrome._cdp_profile_matches = original_match

    spaced = r"C:\Users\Test User\.fbscraper-publish"
    check(chrome._profile_from_command_line(
        '"chrome.exe" "--user-data-dir=%s"' % spaced) == spaced,
        "Windows 把整段 --user-data-dir 参数加引号时仍能识别含空格 profile")
    check(chrome._profile_from_command_line(
        'chrome.exe --user-data-dir="%s"' % spaced) == spaced,
        "只给 --user-data-dir 的值加引号时同样能识别")

    Handler.body = b"not-json"
    check(not cdp_ready(port), "端口返回脏响应时安全地判定为未就绪")
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


print("\n[2] start_chrome 遇到非 CDP 占用时不会继续启动 Chrome")
with tempfile.TemporaryDirectory() as d:
    class FakeConfig:
        chrome_exe = "unused-chrome.exe"
        profile_dir = Path(d)
        debug_port = 43210

    original = (start_chrome.cfg, start_chrome.cdp_ready,
                start_chrome.port_open, chrome.subprocess.Popen)
    launched = []
    try:
        start_chrome.cfg = lambda: FakeConfig()
        start_chrome.cdp_ready = lambda _port, **_kwargs: False
        start_chrome.port_open = lambda _port: True
        chrome.subprocess.Popen = lambda *_a, **_k: launched.append(True)
        rc = start_chrome.main()
    finally:
        (start_chrome.cfg, start_chrome.cdp_ready,
         start_chrome.port_open, chrome.subprocess.Popen) = original
    check(rc == 1, "非 CDP 端口占用返回失败，而不是假报 Chrome 已运行")
    check(not launched, "没有在已占用端口上启动第二个进程")


print("\n[3] launch 的显式 port/profile 不会串回抓取小号配置")
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    publish_profile = root / "publish-profile"

    class FakeConfig:
        chrome_exe = "fake-chrome.exe"
        profile_dir = root / "scrape-profile"
        debug_port = 9222

    original = (chrome.cfg, chrome.cdp_ready, chrome.port_open,
                chrome.subprocess.Popen)
    popen_calls = []
    try:
        chrome.cfg = lambda: FakeConfig()
        chrome.cdp_ready = lambda _port, **_kwargs: False
        chrome.port_open = lambda _port: False
        chrome.subprocess.Popen = lambda argv, **kwargs: popen_calls.append(
            (argv, kwargs))
        ready = chrome.launch(
            wait_seconds=0, port=9223, profile=publish_profile)
    finally:
        (chrome.cfg, chrome.cdp_ready, chrome.port_open,
         chrome.subprocess.Popen) = original

    check(not ready, "零等待且模拟端口未就绪时如实返回 False")
    check(len(popen_calls) == 1, "显式目标只启动一个 Chrome 进程")
    argv = popen_calls[0][0] if popen_calls else []
    check("--remote-debugging-port=9223" in argv,
          "发布端口 9223 进入 Chrome 参数")
    check("--user-data-dir=%s" % publish_profile in argv,
          "发布 profile 进入 Chrome 参数")
    check(not any("scrape-profile" in arg for arg in argv),
          "没有悄悄退回抓取 profile")
    check(publish_profile.is_dir(), "显式发布 profile 会在启动前创建")

    original = (chrome.cfg, chrome.cdp_ready, chrome.port_open,
                chrome.subprocess.Popen)
    default_profile_checks = []
    try:
        chrome.cfg = lambda: FakeConfig()

        def default_ready(_port, **kwargs):
            default_profile_checks.append(kwargs.get("profile"))
            return False

        chrome.cdp_ready = default_ready
        chrome.port_open = lambda _port: False
        chrome.subprocess.Popen = lambda *_a, **_k: None
        chrome.launch(wait_seconds=0)
    finally:
        (chrome.cfg, chrome.cdp_ready, chrome.port_open,
         chrome.subprocess.Popen) = original
    check(default_profile_checks and all(value is None
                                         for value in default_profile_checks),
          "无参 launch 保持旧 CDP-only 行为，不调用 Windows profile 归属检查")


print("\n[4] attach 参数化保留旧调用，并给发布入口正确提示")
params = inspect.signature(chrome.attach).parameters
check("port" in params and "profile" in params,
      "attach 同时接受显式 port 与 profile")
original = (chrome.cdp_ready, chrome.port_open)
try:
    chrome.cdp_ready = lambda _port, **_kwargs: False
    chrome.port_open = lambda _port: False
    try:
        asyncio.run(chrome.attach(
            port=9223,
            profile=Path("publish-profile"),
            start_script=r"scripts\start_chrome_publish.bat",
            login_hint="DE 发布账号"))
    except SystemExit as exc:
        detail = str(exc)
    else:
        detail = ""
finally:
    chrome.cdp_ready, chrome.port_open = original
check(r"scripts\start_chrome_publish.bat" in detail,
      "发布端口没开时提示发布专用启动脚本，不误导去开抓取 Chrome")
check("DE 发布账号" in detail and "publish-profile" in detail,
      "提示同时点名发布账号与目标 profile")

original = (chrome.cdp_ready, chrome.port_open)
default_attach_checks = []
try:
    def default_attach_ready(_port, **kwargs):
        default_attach_checks.append(kwargs.get("profile"))
        return False

    chrome.cdp_ready = default_attach_ready
    chrome.port_open = lambda _port: False
    try:
        asyncio.run(chrome.attach())
    except SystemExit:
        pass
finally:
    chrome.cdp_ready, chrome.port_open = original
check(default_attach_checks and all(value is None
                                    for value in default_attach_checks),
      "无参 attach 保持旧 CDP-only 行为，PowerShell 归属不可用也不阻断抓取")


print("\n[5] [publish] 的派生配置与 [chrome] 彼此独立")
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    config_path = root / "config.toml"
    config_path.write_text(
        "[chrome]\nprofile_dir = 'scrape-profile'\ndebug_port = 9222\n"
        "[publish]\nprofile_dir = 'publish-profile'\ndebug_port = 9223\n",
        encoding="utf-8")
    c = Config(config_path)
    check(c.debug_port == 9222 and c.publish_debug_port == 9223,
          "两个 CDP 端口分别读取各自配置")
    check(c.profile_dir.name == "scrape-profile"
          and c.publish_profile_dir.name == "publish-profile",
          "两个 profile 分别读取各自配置")
    check(c.profile_dir != c.publish_profile_dir,
          "发布与抓取的派生 profile 不会指向同一路径")
    try:
        c.assert_publish_chrome_isolated()
    except SystemExit:
        isolated = False
    else:
        isolated = True
    check(isolated, "端口/profile 都不同时隔离配置通过硬校验")

    same_port = root / "same-port.toml"
    same_port.write_text(
        "[chrome]\nprofile_dir = 'scrape'\ndebug_port = 9222\n"
        "[publish]\nprofile_dir = 'publish'\ndebug_port = 9222\n",
        encoding="utf-8")
    try:
        Config(same_port).assert_publish_chrome_isolated()
    except SystemExit as exc:
        same_port_error = str(exc)
    else:
        same_port_error = ""
    check("两个独立端口" in same_port_error,
          "发布端口误配成抓取端口时启动前失败闭合")

    same_profile = root / "same-profile.toml"
    same_profile.write_text(
        "[chrome]\nprofile_dir = 'shared'\ndebug_port = 9222\n"
        "[publish]\nprofile_dir = 'shared'\ndebug_port = 9223\n",
        encoding="utf-8")
    try:
        Config(same_profile).assert_publish_chrome_isolated()
    except SystemExit as exc:
        same_profile_error = str(exc)
    else:
        same_profile_error = ""
    check("共用浏览器 profile" in same_profile_error,
          "发布 profile 误配成抓取 profile 时启动前失败闭合")


print("\n[] launch 轮询期间不做昂贵的 profile 归属核对")
# 轮询只做轻量 CDP 检查，端口就绪后核验一次 profile。
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    calls = {"plain": 0, "with_profile": 0}
    state = {"ticks": 0}

    class FakeCfg:
        chrome_exe = "chrome.exe"
        debug_port = 9222
        profile_dir = root / "scrape"

    def fake_cdp_ready(_port=None, *_a, profile=None, **_k):
        # 端口未就绪时，带或不带 profile 的检查均失败。
        if profile is None:
            calls["plain"] += 1
        else:
            calls["with_profile"] += 1
        return state["ticks"] >= 3            # 第 4 次轮询时端口才起来

    original = (chrome.cfg, chrome.cdp_ready, chrome.port_open,
                chrome.subprocess.Popen, chrome.time.sleep)
    try:
        chrome.cfg = lambda: FakeCfg()
        chrome.cdp_ready = fake_cdp_ready
        chrome.port_open = lambda _port, *_a, **_k: False
        chrome.subprocess.Popen = lambda *_a, **_k: None
        chrome.time.sleep = lambda _s: state.__setitem__("ticks",
                                                         state["ticks"] + 1)
        ready = chrome.launch(port=9223, profile=root / "publish")
    finally:
        (chrome.cfg, chrome.cdp_ready, chrome.port_open,
         chrome.subprocess.Popen, chrome.time.sleep) = original

    check(ready, "端口起来之后 launch 返回 True")
    check(calls["with_profile"] == 2,
          "整个启动过程只核对 2 次 profile 归属（开头一次 + 就绪后一次），"
          "不是每秒一次 —— 每次都要 fork 一个 powershell.exe")
    check(calls["plain"] >= 3,
          "轮询用的是不带 profile 的轻量 CDP 探测")
    check(calls["plain"] > calls["with_profile"],
          "轻量探测次数必须多于昂贵核对次数；反过来说明有人把归属核对搬回轮询里了")


print("\n[归属核对的两个真实教训（2026-09-01 用户调 G1 时踩的）]")

check(chrome.PROFILE_PROBE_TIMEOUT >= 15,
      "归属核对超时 >= 15 秒：实测这条链路要 ~3.5 秒，5 秒那版**每次都超时**，"
      "于是一个完全正确的发布 Chrome 被报成'核对不了'并失败闭合")

netstat_sample = "\n".join([
    "活动连接", "",
    "  协议  本地地址          外部地址        状态           PID",
    "  TCP    127.0.0.1:9222         0.0.0.0:0              LISTENING       111",
    "  TCP    127.0.0.1:9223         0.0.0.0:0              LISTENING       7044",
    "  TCP    127.0.0.1:92230        0.0.0.0:0              LISTENING       222",
    "  TCP    127.0.0.1:9224         127.0.0.1:5555         ESTABLISHED     333",
    "  TCP    [::1]:9225             [::]:0                 LISTENING       444",
])


class _FakeRun:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


original_run = chrome.subprocess.run
original_platform = chrome.sys.platform
try:
    chrome.sys.platform = "win32"
    chrome.subprocess.run = lambda *_a, **_k: _FakeRun(netstat_sample)
    check(chrome.listening_pid(9223) == 7044, "从 netstat 输出里取出监听端口的 PID")
    check(chrome.listening_pid(9222) == 111, "不同端口取到不同 PID")
    check(chrome.listening_pid(92230) == 222,
          "**端口按整段匹配，不是子串**：9223 不能命中 92230，反之亦然")
    check(chrome.listening_pid(9224) is None,
          "ESTABLISHED 不算：只认 LISTENING，否则会拿到连过去的那一头")
    check(chrome.listening_pid(9225) == 444, "IPv6 的 [::1]:9225 也能解析")
    check(chrome.listening_pid(9999) is None, "没人监听时返回 None")

    chrome.subprocess.run = lambda *_a, **_k: _FakeRun("", returncode=1)
    check(chrome.listening_pid(9223) is None, "netstat 失败时返回 None，不抛异常")

    # 无法核验返回 None，不得混为 profile 错配的 False。
    chrome.subprocess.run = original_run
    original_pid, original_cmd = chrome.listening_pid, chrome._command_line_of
    try:
        chrome.listening_pid = lambda _port: None
        check(chrome._cdp_profile_matches(9223, Path("C:/x")) is None,
              "拿不到 PID → None（核对不了），**不是 False**")
        chrome.listening_pid = lambda _port: 7044
        chrome._command_line_of = lambda _pid: None
        check(chrome._cdp_profile_matches(9223, Path("C:/x")) is None,
              "拿不到命令行 → None，不是 False")
        chrome._command_line_of = lambda _pid: (
            '"chrome.exe" --remote-debugging-port=9223 --user-data-dir=C:\\x')
        check(chrome._cdp_profile_matches(9223, Path("C:/x")) is True,
              "命令行里的 --user-data-dir 与目标一致 → True")
        chrome._command_line_of = lambda _pid: (
            '"chrome.exe" --remote-debugging-port=9223 --user-data-dir=C:\\other')
        check(chrome._cdp_profile_matches(9223, Path("C:/x")) is False,
          "确实是别的 profile → False（这一种才该让用户去关浏览器）")
    finally:
        chrome.listening_pid, chrome._command_line_of = original_pid, original_cmd
finally:
    chrome.subprocess.run = original_run
    chrome.sys.platform = original_platform

source = inspect.getsource(chrome.attach)
check("verdict is False" in source,
      "attach 按三态分诊：'是别的 profile' 与 '核对不了' 必须给不同的话")
check(source.count("无法确认") >= 1 and "netstat -ano" in source,
      "'核对不了'那一支给出可自查的只读命令，而不是让用户去关浏览器")
# 只检查可执行函数，排除注释中的反例。
executed = inspect.getsource(chrome._command_line_of) + inspect.getsource(
    chrome.listening_pid).split('"""')[-1]
check("Get-NetTCPConnection" not in executed,
      "真正执行的命令里不再有 Get-NetTCPConnection —— "
      "它光加载 NetTCPIP 模块就 ~5.7 秒，是原来那次超时的主因")
check("netstat" in inspect.getsource(chrome.listening_pid),
      "端口→PID 走 netstat（0.12s），不是 PowerShell cmdlet")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

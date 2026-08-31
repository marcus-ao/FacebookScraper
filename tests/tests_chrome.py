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

    # 拉起 Chrome 的实现 2026-08-30 搬进了 core.chrome.launch（增量在 Chrome
    # 没开时要做同一件事），所以 Popen 现在挂在那个模块上，patch 点跟着走。
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


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

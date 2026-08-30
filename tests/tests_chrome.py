"""Chrome/CDP 启动探测自测：不能把任意占用端口误认成专用 Chrome。"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

import core.chrome as chrome
from core.chrome import cdp_ready, port_open
import tools.start_chrome as start_chrome


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
        start_chrome.cdp_ready = lambda _port: False
        start_chrome.port_open = lambda _port: True
        chrome.subprocess.Popen = lambda *_a, **_k: launched.append(True)
        rc = start_chrome.main()
    finally:
        (start_chrome.cfg, start_chrome.cdp_ready,
         start_chrome.port_open, chrome.subprocess.Popen) = original
    check(rc == 1, "非 CDP 端口占用返回失败，而不是假报 Chrome 已运行")
    check(not launched, "没有在已占用端口上启动第二个进程")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

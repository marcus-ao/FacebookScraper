"""切换演练：让真的 FastAPI 去伺服真的 dist，看部署形状到底成不成立。

为什么单独写一个：`tests/browser_fixture.py` 的 UIFixture 是在 Playwright 那一侧
拦路由的，找不到文件就自己回落 index.html —— 也就是说它**自带 SPA 回落**。
浏览器回归 12/12 全绿，证明的是「前端逻辑对」，不是「FastAPI 这样挂得起来」。
新 UI 用的是真实路径（/review、/history、/review/<account>/<id>），旧 UI 只用
`/?task=`、`/?view=`，这中间差的正好是服务端要不要管前端路由。

用法（两次是分开的进程，因为 web.api.app 在 import 时就把 DIST 定死了 ——
这正是生产上「改 config + 重启」的语义）：

    scripts\\run_python.bat docs/ui-refactor/tools/cutover_rehearsal.py --dist web/ui-next/dist
    scripts\\run_python.bat docs/ui-refactor/tools/cutover_rehearsal.py --dist web/ui/dist

⛔ 真实 config.toml 不碰：演练用的是临时目录里的一份副本，退出时校验原文件未变。
⛔ 真实 archive / state 不碰：临时归档，只放两篇夹具。
⛔ 非 GET 一律 503：这一轮不做任何真实写入。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402
from core.store import Archive, Post  # noqa: E402

REACT_PATHS = ["/", "/review", "/history", "/calendar", "/settings", "/runtime"]
LEGACY = ["/?view=history", "/?view=calendar", "/?view=settings", "/?view=runtime"]


def isolated_config(root: Path, dist_rel: str):
    """把 [paths].web_dist 写进夹具自己的那份 config.toml 副本。"""
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    entry = f'web_dist = "{dist_rel}"'
    if re.search(r"(?m)^\s*\[paths\]", text):
        text = re.sub(r"(?m)^(\s*\[paths\][^\r\n]*\r?\n)", r"\1" + entry + "\n", text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n[paths]\n" + entry + "\n"
    path = root / "config.toml"
    path.write_text(text, encoding="utf-8")
    runtime = root / "runtime.toml"
    runtime.write_text("[paths]\narchive = " + json.dumps(str(root / "archive"))
                       + "\nstate = " + json.dumps(str(root / "state")) + "\n", encoding="utf-8")
    return config.Config(path, runtime_path=runtime)


def seed(root: Path):
    archive = Archive(root / "archive", "neakasaofficial")
    archive.append(Post("1234567890", "facebook", "neakasaofficial",
                        "Rehearsal fixture post", "2026-09-01T12:00:00Z", tags=["Rehearsal"]))
    return "fa_neakasaofficial/1234567890"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", required=True, help="web/ui-next/dist 或 web/ui/dist")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    engine = "react" if "ui-next" in args.dist else "vue"
    original = (ROOT / "config.toml").read_bytes()
    report: dict = {"engine": engine, "dist": args.dist, "http": {}, "legacy": {}, "external_writes": []}

    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="cutover-rehearsal-")))
        stack.enter_context(patch.object(config, "_cfg", isolated_config(root, args.dist)))
        task_id = seed(root)

        listen = socket.socket()
        stack.callback(listen.close)
        listen.bind(("127.0.0.1", 0))
        listen.listen(128)
        port = listen.getsockname()[1]
        connect = socket.socket.connect

        def local_only(sock, address):
            if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
                raise AssertionError("演练禁止对外连接：" + str(address))
            return connect(sock, address)

        stack.enter_context(patch.object(socket.socket, "connect", local_only))
        from web.api.app import DIST  # noqa: E402  —— import 时就定死，见模块注释
        from web.api.app import app   # noqa: E402
        report["resolved_dist"] = str(DIST)
        assert DIST == (ROOT / args.dist).resolve(), f"web_dist 没有生效：{DIST}"

        denied = report["external_writes"]

        async def read_only(scope, receive, send):
            if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD"}:
                denied.append(scope["method"] + " " + scope["path"])
                from starlette.responses import JSONResponse
                await JSONResponse({"detail": "rehearsal is read-only"}, status_code=503)(scope, receive, send)
                return
            await app(scope, receive, send)

        server = uvicorn.Server(uvicorn.Config(read_only, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listen]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("演练宿主没起来")
            time.sleep(0.02)
        base = f"http://127.0.0.1:{port}"
        try:
            client = httpx.Client(base_url=base, timeout=20, follow_redirects=False,
                                  headers={"Accept": "text/html,application/xhtml+xml"})
            paths = REACT_PATHS + [f"/review/{task_id}", f"/history/{task_id}"] if engine == "react" else ["/"]
            for path in paths + LEGACY + [f"/?task={task_id}"]:
                response = client.get(path)
                report["http"][path] = response.status_code
            for path in ["/api/tasks", "/api/runtime", "/api/settings", "/api/calendar"]:
                report["http"][path] = client.get(path).status_code
            index = client.get("/").text
            asset = re.search(r'(?:src|href)="(/assets/[^"]+)"', index)
            assert asset, "index.html 里找不到 /assets/ 引用"
            report["asset"] = asset[1]
            report["http"][asset[1]] = client.get(asset[1]).status_code
            report["missing_asset_status"] = client.get("/assets/does-not-exist.js").status_code
            report["missing_api_status"] = client.get("/api/does-not-exist").status_code
            assert report["missing_asset_status"] == 404, "漏掉的产物被 SPA 回落藏起来了"
            assert report["missing_api_status"] == 404, "接口的 404 变成了一页 HTML"

            from playwright.sync_api import sync_playwright
            with sync_playwright() as play:
                browser = play.chromium.launch()
                page = browser.new_page(viewport={"width": 1366, "height": 768})
                errors: list[str] = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                for path in [f"/?task={task_id}", "/?view=history", "/?view=calendar"]:
                    page.goto(base + path, wait_until="networkidle")
                    page.wait_for_timeout(400)
                    report["legacy"][path] = page.url[len(base):]
                if engine == "react":
                    for label, action in [("deep_link", lambda: page.goto(base + "/calendar", wait_until="domcontentloaded")),
                                          ("deep_link_after_reload", lambda: page.reload(wait_until="domcontentloaded"))]:
                        action()
                        page.wait_for_timeout(600)
                        report[label + "_url"] = page.url[len(base):]
                        report[label + "_heading"] = (page.locator("h1").first.inner_text()
                                                      if page.locator("h1").count() else
                                                      "(没有 h1) " + page.locator("body").inner_text()[:120])
                report["page_errors"] = errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=15)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert (ROOT / "config.toml").read_bytes() == original, "真实 config.toml 被改了"
    assert not report["external_writes"], report["external_writes"]
    report["all_http_ok"] = sorted({code for code in report["http"].values()}) == [200]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["all_http_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

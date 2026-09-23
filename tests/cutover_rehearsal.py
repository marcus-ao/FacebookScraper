"""用实际 FastAPI 和静态构建核验深链接及 404；临时配置与数据，非 GET 拒绝。"""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402
from core.store import Archive, Post  # noqa: E402
from tests.http_fixture import run_http_server  # noqa: E402

REACT_PATHS = ["/", "/review", "/history", "/calendar", "/runtime"]
LEGACY = ["/?view=history", "/?view=calendar", "/?view=runtime"]


def isolated_config(root: Path, dist_rel: str):
    """把 [paths].web_dist 写进夹具自己的那份 config.toml 副本。"""
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    entry = f'web_dist = "{dist_rel}"'
    text, replaced = re.subn(r"(?m)^\s*web_dist\s*=.*$", entry, text)
    if replaced > 1:
        raise AssertionError("config.toml 里出现多个 [paths].web_dist")
    if not replaced and re.search(r"(?m)^\s*\[paths\]", text):
        text = re.sub(r"(?m)^(\s*\[paths\][^\r\n]*\r?\n)", r"\1" + entry + "\n", text, count=1)
    elif not replaced:
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
    parser.add_argument("--dist", default="web/ui/dist", help="React 构建目录，默认 web/ui/dist")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    original = (ROOT / "config.toml").read_bytes()
    report: dict = {"ui": "react", "dist": args.dist, "http": {}, "legacy": {}, "external_writes": []}

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
        from web.api.app import DIST  # noqa: E402  导入前须设定隔离配置。
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

        server = uvicorn.Server(uvicorn.Config(
            read_only, log_level="error", lifespan="off"))
        thread = threading.Thread(target=run_http_server, args=(server, [listen]), daemon=True)
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
            paths = REACT_PATHS + [f"/review/{task_id}", f"/history/{task_id}"]
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

"""Isolated data and local HTTP host for the offline Vue browser regression.

Nothing here opens an authenticated browser or calls a model. The temporary TOML
is the settings writer's actual path, including after Config reloads it.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import tempfile
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from core import config, translated
from core.store import Archive, Post, post_dirname

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_NOTE = "离线夹具：这些默认时刻只影响本次浏览器测试"


class BrowserFixture:
    """Own all resources; close them even when browser startup or assertions fail."""

    def __init__(self):
        self.stack = ExitStack()
        self.server = None
        self.thread = None
        self.sources = {}
        self.denied_backend_requests = []
        self.config_original = (ROOT / "config.toml").read_bytes()
        self.chrome_exe = config.Config().chrome_exe

    def __enter__(self):
        try:
            self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="offline-vue-")))
            self.config_path = self.root / "config.toml"
            text = self.config_original.decode("utf-8")
            text, changed = re.subn(r"(?m)^(times\s*=)", "# " + SETTINGS_NOTE + "\n" + r"\1", text, count=1)
            if changed != 1:
                raise AssertionError("The browser fixture requires publish.schedule_rule.times")
            self.config_path.write_text(text, encoding="utf-8")
            runtime = self.root / "runtime.toml"
            runtime.write_text('[paths]\narchive = ' + json.dumps(str(self.root / "archive"))
                               + '\nstate = ' + json.dumps(str(self.root / "state")) + '\n', encoding="utf-8")
            self.config = config.Config(self.config_path, runtime_path=runtime)
            self.stack.enter_context(patch.object(config, "_cfg", self.config))
            self.fb_id = self.add_post("fa_neakasaofficial", "1234567890", "facebook")
            self.ig_id = self.add_post("in_neakasa.global", "2234567890", "instagram")
            frozen = Archive(self.root / "archive", "in_neakasa.tech")
            for index in range(31):
                frozen.append(Post(str(9000000000 + index), "instagram", "neakasa.tech",
                                   "Offline frozen fixture " + str(index),
                                   f"2021-01-{index + 1:02d}T12:00:00Z", tags=["OfflineFixture"]))

            # Also guard accidental server-side network calls. Only this HTTP
            # loopback is permitted (the ASGI host and Python's event-loop pipes).
            listen = socket.socket()
            self.stack.callback(listen.close)
            listen.bind(("127.0.0.1", 0))
            listen.listen(128)
            self.port = listen.getsockname()[1]
            connect = socket.socket.connect

            def local_connect(sock, address):
                if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
                    self.denied_backend_requests.append(str(address))
                    raise AssertionError("Offline browser test blocked backend network: " + str(address))
                return connect(sock, address)

            self.stack.enter_context(patch.object(socket.socket, "connect", local_connect))
            from web.api.app import app

            # ASGI wrapper keeps mutation safety independent of browser routing.
            async def isolated_app(scope, receive, send):
                if scope["type"] == "http":
                    path, method = scope["path"], scope["method"]
                    safe_write = (method == "PUT" and (path == "/api/settings" or path.endswith("/localization")))
                    safe_write |= method == "POST" and path.endswith("/check")
                    if method not in {"GET", "HEAD"} and not safe_write:
                        self.denied_backend_requests.append(method + " " + path)
                        from starlette.responses import JSONResponse
                        await JSONResponse({"detail": "Offline test blocks external actions"}, status_code=503)(scope, receive, send)
                        return
                await app(scope, receive, send)

            self.client = self.stack.enter_context(TestClient(isolated_app))
            self.server = uvicorn.Server(uvicorn.Config(isolated_app, log_level="error", lifespan="off"))
            self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [listen]}, daemon=True)
            self.thread.start()
            deadline = time.monotonic() + 15
            while not self.server.started:
                if not self.thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("The isolated HTTP host did not start")
                time.sleep(0.02)
            self.base_url = f"http://127.0.0.1:{self.port}"
            return self
        except BaseException:
            self.close()
            raise

    def add_post(self, account, post_id, platform):
        account_dir = self.root / "archive" / account
        created = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        folder = "posts/" + post_dirname(post_id, created)
        directory = account_dir / folder
        directory.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1080), "#1e40af").save(directory / "01.jpg")
        owner = "neakasaofficial" if platform == "facebook" else "neakasa.global"
        source = {"post_id": post_id, "platform": platform, "account": owner, "owner": owner,
                  "coauthors": [], "created_at": created, "permalink": "https://example.invalid/post/" + post_id,
                  "text": "Offline fixture: a clean home. https://us.example.invalid/product #Neakasa #CatLover",
                  "media_complete": True, "tags": ["OfflineFixture"],
                  "media": [{"kind": "image", "local_path": folder + "/01.jpg"}]}
        task_id = account + "/" + post_id
        self.sources[task_id] = (source, directory, account_dir)
        self.write_source(task_id)
        (account_dir / "manifest.jsonl").write_text(json.dumps(source) + "\n", encoding="utf-8")
        self.write_machine(task_id, "Offline-Test: ein sauberes Zuhause. #Neakasa #CatLover")
        return task_id

    def write_source(self, task_id):
        source, directory, _ = self.sources[task_id]
        (directory / "post.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

    def write_machine(self, task_id, text):
        source, _, account = self.sources[task_id]
        entry = {"post_id": source["post_id"], "text_de": text, "model": "offline-fixture-no-api",
                 "translated_at": datetime.now(timezone.utc).isoformat(),
                 "prompt_version": translated.PROMPT_VERSION,
                 "source_text_sha256": translated.source_text_sha256(source["text"])}
        with (account / "translated.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def detail(self, task_id):
        response = self.client.get("/api/tasks/" + task_id)
        response.raise_for_status()
        return response.json()

    def close(self):
        try:
            if self.server:
                self.server.should_exit = True
            if self.thread:
                self.thread.join(timeout=10)
                if self.thread.is_alive():
                    raise RuntimeError("Offline browser HTTP host did not close")
        finally:
            self.stack.close()
            if (ROOT / "config.toml").read_bytes() != self.config_original:
                raise AssertionError("Real config.toml changed during offline browser regression")

    def __exit__(self, *_):
        self.close()


def runtime_payload():
    """UI-only states; this is not evidence of backend recovery or message delivery."""
    now = "2026-09-12T10:00:00Z"
    processing = {"status": "interrupted", "batch_id": "offline-batch", "state_revision": "offline-revision",
                  "started_at": now, "finished_at": None, "paid_request_ids": ["offline-paid-request"],
                  "cost_usd": 0.1234, "recovery_reason": "离线夹具：模型请求后进程中断，费用需要核对。"}
    message = {"delivery_id": "offline-delivery", "version": "offline-message-revision", "status": "uncertain",
               "created_at": now, "error": "离线夹具：远端结果未知，禁止自动重发。", "task_ids": ["offline/post"]}
    return {"observed_at": now, "process": {"alive": True, "last_tick_at": now},
            "business": {"last_successful_run": None, "processing": processing},
            "heartbeat": {"status": "disabled"}, "stages": [
                {"number": 1, "name": "监测与调度", "status": "active"},
                {"number": 2, "name": "源内容与归档", "status": "available"},
                {"number": 3, "name": "德语本地化", "status": "interrupted"},
                {"number": 4, "name": "飞书提醒", "status": "needs_attention", "outbox": {
                    "enabled": False, "counts": {"uncertain": 1}, "deliveries": [message]}},
                {"number": 5, "name": "审校与单渠道排期", "status": "blocked"}]}


def calendar_payload():
    """Explicit remote-observation substitutes for frontend label assertions only."""
    return {"month_ui": "2026-09", "business_timezone": "Europe/Berlin", "ui_timezone": "America/Los_Angeles",
            "display_start": "2026-09-01", "display_end_exclusive": "2026-10-01",
            "cached_at": "2026-09-12T10:00:00Z", "stale": False, "status": "ready", "error": None,
            "coverage": {"matches_current_month": True}, "refresh_available": False,
            "refresh_unavailable_reason": "离线替身：不连接发布后台", "cards": [
                {"at": "2026-09-13T08:00:00Z", "at_business": "2026-09-13T10:00:00+02:00",
                 "channels": ["facebook"], "card_sha256": "offline-scheduled", "delivery": "scheduled",
                 "rendered": "Offline scheduled fixture"},
                {"at": "2026-09-12T08:00:00Z", "at_business": "2026-09-12T10:00:00+02:00",
                 "channels": ["instagram"], "card_sha256": "offline-published", "delivery": "published",
                 "rendered": "Offline published observation fixture"}]}

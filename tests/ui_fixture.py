"""Serve the built React UI over BrowserFixture; all business data stays temporary."""
from __future__ import annotations

import copy
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

#: 回归产物落在 state/（已 gitignore），避免每次运行改动受版本控制的文件。
EVIDENCE = ROOT / "state" / "ui-regression"
EVIDENCE.mkdir(parents=True, exist_ok=True)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.browser_fixture import BrowserFixture
from audit_fixture_host import AuditHost, SAMPLES


class UIFixture:
    def __init__(self, fx: BrowserFixture):
        self.fx = fx
        self.dist = ROOT / "web" / "ui" / "dist"
        self.requests = []
        self.errors = []
        self.overrides = {}
        self.host = AuditHost(fx)
        for index, sample in enumerate(SAMPLES):
            self.host.add(*sample, age_days=index + 2)
        for index in range(12):
            self.host.add(f"extra-{index}", "facebook", f"Alltag mit Katzen {index}", "Riko", "219.99", "30", 1, "clean", age_days=index+2)
        self.host.act("snoozed", "snoozed", reason="等落地页确认", snooze_days=3)
        self.host.act("skipped", "skipped", reason="德国站不发")
        self.host.act("handed", "handed_off", handoff_url="https://example.invalid/manual")
        self.list_data = fx.client.get("/api/tasks").json()

    def attach(self, page):
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.route("**/*", self.route)

    def route(self, route):
        request = route.request
        url = urlparse(request.url)
        if url.hostname != "127.0.0.1" or url.port != self.fx.port:
            route.abort()
            self.errors.append("Blocked external browser request: " + request.url)
            return
        if url.path.startswith("/api/"):
            body = request.post_data_json if request.post_data else None
            self.requests.append({"method":request.method,"path":url.path,"query":url.query,"body":body})
            override = self.overrides.get((request.method, url.path))
            if override:
                status, payload = override(body) if callable(override) else override
                route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))
            else:
                route.continue_()
            return
        path = self.dist / url.path.lstrip("/")
        if not path.is_file():
            path = self.dist / "index.html"
        mime = "text/javascript" if path.suffix == ".js" else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        route.fulfill(status=200, content_type=mime, body=path.read_bytes())

    def synthetic_buckets(self):
        data = copy.deepcopy(self.list_data)
        template = data["tasks"][0]
        statuses = ["pending_review", "edited", "not_ready", "snoozed", "approved", "scheduled", "skipped", "handed_off"]
        data["tasks"] = []
        for index, status in enumerate(statuses):
            row = copy.deepcopy(template)
            row.update(id=f"fa_neakasaofficial/{7000000000 + index}", status=status,
                       text_de_excerpt=f"Queue fixture {status}", month="2026-09", platform="facebook", tags=["Riko"])
            row["review"]["status"] = status
            row["hard_alerts"] = [{"code":"unknown_collaborator", "label":"第三方作者，需授权初翻"}] if status == "not_ready" else []
            if status == "not_ready":
                row["text_de_excerpt"] = ""
            data["tasks"].append(row)
        # 两个计数要一起改：审校台按平台分了入口，角标读的是 by_platform_status。
        # 只改 by_status 会让页签停在旧数字上，而行数已经换成新的了。
        counts = {status: 1 for status in statuses}
        data["summary"].update(total=8, with_hard_alerts=1, by_status=counts, tags=["Riko"],
                               by_platform_hard_alerts={"facebook": 1, "instagram": 0},
                               by_platform_status={"facebook": counts,
                                                   "instagram": {status: 0 for status in statuses}})
        self.overrides[("GET", "/api/tasks")] = (200, data)
        return data

    def count_list_gets(self):
        return sum(row["method"] == "GET" and row["path"] == "/api/tasks" for row in self.requests)

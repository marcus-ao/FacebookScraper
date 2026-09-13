"""Repeatable offline regression against the *built* Vue app and local FastAPI.

Run from the repository (uses installed Python Playwright and Config.chrome_exe):
    npm.cmd --prefix web/ui run build
    <venv-python> tests/tests_browser_workflow.py -v

Human saves, source conflicts, archive paging and settings CAS exercise real
backend code with temporary files. Runtime and remote publication responses are
explicit browser routes: those assertions cover UI behavior ONLY. All external
requests and unapproved backend mutations fail the test. There are no paid calls,
live account sessions, Feishu messages or real publish/scheduling submissions.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from playwright.sync_api import expect, sync_playwright  # noqa: E402
from browser_fixture import BrowserFixture, SETTINGS_NOTE, calendar_payload, runtime_payload  # noqa: E402


class BrowserWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        if not (ROOT / "web/ui/dist/index.html").is_file():
            raise AssertionError("Build the actual Vue app first: npm.cmd --prefix web/ui run build")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cls.artifacts = Path(os.environ.get("OFFLINE_BROWSER_ARTIFACT_DIR", ROOT / "state" / f"offline-browser-{stamp}-{os.getpid()}"))
        cls.artifacts.mkdir(parents=True, exist_ok=True)
        cls.fixtures = cls.resources.enter_context(BrowserFixture())
        cls.playwright = cls.resources.enter_context(sync_playwright())
        cls.context = cls.playwright.chromium.launch_persistent_context(
            str(cls.fixtures.root / "isolated-chrome-profile"),
            executable_path=cls.fixtures.chrome_exe, headless=True,
            viewport={"width": 1600, "height": 1100}, timezone_id="Asia/Shanghai",
            service_workers="block", args=["--disable-background-networking", "--disable-component-update",
                                           "--no-first-run", "--no-default-browser-check"])
        cls.resources.callback(cls.context.close)
        cls.context.set_default_timeout(10000)
        cls.observations = []
        cls.resources.callback(cls.write_report)

    @classmethod
    def write_report(cls):
        result = {"suite": "offline-browser-workflow", "artifact_directory": str(cls.artifacts),
                  "tests": cls.observations, "server_network_or_mutation_denials": cls.fixtures.denied_backend_requests,
                  "vue_build": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (ROOT / "web/ui/dist/assets").glob("*.js")},
                  "backend_scope": "temporary archive, human/localization ledgers, source CAS, history, temporary TOML CAS",
                  "ui_only_scope": "runtime interruption/uncertain-message states and scheduled/published remote observations",
                  "real_external_actions": False}
        (cls.artifacts / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nOffline browser artifacts: " + str(cls.artifacts))

    def setUp(self):
        self.page = self.context.new_page()
        self.addCleanup(self.page.close)
        self.errors, self.denied, self.writes, self.responses = [], [], [], {}
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("**/*", self.route)
        self.addCleanup(self.capture)

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        key = (request.method, parsed.path)
        if key in self.responses:
            response = self.responses[key]
            if callable(response):
                response = response(request)
            route.fulfill(status=response.get("status_code", 200), json=response["body"])
            return
        if parsed.scheme in {"data", "blob"}:
            route.continue_()
            return
        if parsed.netloc != urlsplit(self.fixtures.base_url).netloc:
            self.denied.append(request.url)
            route.abort()
            return
        if request.method not in {"GET", "HEAD"}:
            self.writes.append(key)
            allowed = request.method == "PUT" and (parsed.path == "/api/settings" or parsed.path.endswith("/localization"))
            allowed |= request.method == "POST" and parsed.path.endswith("/check")
            if not allowed:
                self.denied.append(request.method + " " + parsed.path)
                route.abort()
                return
        route.continue_()

    def capture(self):
        screenshot = self.artifacts / (self._testMethodName + ".png")
        result = self._outcome.result
        failed = any(test is self for test, _ in result.errors + result.failures)
        failed |= bool(self.errors or self.denied or self.fixtures.denied_backend_requests)
        try:
            self.page.screenshot(path=str(screenshot), full_page=True, animations="disabled")
        except BaseException:
            failed = True
            raise
        finally:
            self.observations.append({"test": self._testMethodName, "status": "failed" if failed else "passed",
                                      "screenshot": screenshot.name,
                                      "page_errors": self.errors, "denied_requests": self.denied,
                                      "local_writes": self.writes})
        self.assertEqual(self.errors, [], "Vue emitted an uncaught browser error")
        self.assertEqual(self.denied, [], "The UI attempted an unapproved network action")
        self.assertEqual(self.fixtures.denied_backend_requests, [], "The backend attempted an external action")

    def open_task(self, task_id):
        self.page.goto(self.fixtures.base_url + "/?task=" + quote(task_id, safe=""))
        expect(self.page.get_by_role("button", name="返回列表")).to_be_visible()
        self.assertEqual(urlsplit(self.page.url).query, "task=" + quote(task_id, safe=""))

    def save_draft(self):
        with self.page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/localization")) as result:
            self.page.get_by_role("button", name="保存", exact=True).click()
        self.assertEqual(result.value.status, 200, result.value.text())
        expect(self.page.get_by_role("status", name="", exact=True).filter(has_text="本篇文案与本地化选择已保存")).to_be_visible()

    def test_01_facebook_save_refresh_and_source_conflict_keep_the_draft(self):
        """Real local backend: three sections save atomically; new source gives 409."""
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_role("button", name="编辑德语").click()
        body = self.page.get_by_role("textbox", name="德语译文")
        expect(body).not_to_have_value(re.compile("https?://"))
        body.fill("Offline-Test: von Hand überarbeitet. 🐈")
        self.page.get_by_label("本篇使用的语义标签（用空格分隔，可增删）").fill("#Katzenliebe")
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_label("本篇德语落地页").fill("https://de.example.invalid/produkt")
        self.page.get_by_label("我已确认落地页适用于德国站").check()
        self.save_draft()
        saved = self.fixtures.detail(self.fixtures.fb_id)
        self.assertEqual(saved["localization"]["tags"], ["#Neakasa", "#Katzenliebe"])
        self.assertEqual(saved["localization"]["body_de"], "Offline-Test: von Hand überarbeitet. 🐈")
        self.assertIn("https://de.example.invalid/produkt", saved["text"]["de_human"])
        self.fixtures.write_machine(self.fixtures.fb_id, "Offline-Neuübersetzung. #Neakasa #CatLover")
        self.page.reload()
        expect(self.page.get_by_text("Offline-Test: von Hand überarbeitet. 🐈", exact=True)).to_be_visible()
        expect(self.page.get_by_text("本篇链接已确认", exact=True)).to_be_visible()

        self.page.get_by_role("button", name="编辑德语").click()
        body.fill("Offline-Konflikt: diesen Entwurf behalten.")
        self.fixtures.sources[self.fixtures.fb_id][0]["text"] = "Updated offline source. https://us.example.invalid/new #Neakasa #CatLover"
        self.fixtures.write_source(self.fixtures.fb_id)
        with self.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/localization")) as conflict:
            self.page.get_by_role("button", name="保存", exact=True).click()
        self.assertEqual(conflict.value.status, 409)
        expect(self.page.get_by_role("alert").filter(has_text="源帖已更新")).to_be_visible()
        expect(body).to_have_value("Offline-Konflikt: diesen Entwurf behalten.")
        self.page.get_by_role("button", name="载入最新内容并保留草稿").click()
        expect(self.page.get_by_role("link", name="https://us.example.invalid/new", exact=True)).to_be_visible()
        expect(body).to_have_value("Offline-Konflikt: diesen Entwurf behalten.")
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_label("本篇德语落地页").fill("https://de.example.invalid/neu")
        self.page.get_by_label("我已确认落地页适用于德国站").check()
        self.save_draft()
        self.assertFalse(self.fixtures.detail(self.fixtures.fb_id)["text"]["stale"])

    def test_02_instagram_bio_counter_and_unsaved_navigation(self):
        """Real save: URL excluded, CTA retained, emoji count and >30 tags stay intact."""
        self.open_task(self.fixtures.ig_id)
        expect(self.page.locator(".bio")).to_contain_text("当前 bio（只读）：未配置")
        self.page.get_by_role("button", name="编辑德语").click()
        body = "Offline-Test 🐈 für Instagram."
        cta = "Weitere Infos im Link in unserer Bio."
        tags = ["#Neakasa"] + ["#Offline" + str(index) for index in range(30)]
        self.page.get_by_role("textbox", name="德语译文").fill(body)
        self.page.get_by_label("本篇使用的语义标签（用空格分隔，可增删）").fill(" ".join(tags[1:]))
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_role("combobox", name=re.compile("^引导话术")).select_option("custom")
        self.page.get_by_label("自定义 bio 引导").fill(cta)
        caption = "\n\n".join([body, cta, " ".join(tags)])
        expect(self.page.locator(".caption-counter")).to_contain_text(f"发布文案 {len(caption)} / 2,200 字符")
        expect(self.page.locator(".counter.near")).to_have_text("31 / 30 个标签")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.get_by_role("button", name="发布月历", exact=True).click()
        expect(self.page.get_by_role("textbox", name="德语译文")).to_have_value(body)
        self.save_draft()
        human = self.fixtures.detail(self.fixtures.ig_id)["text"]["de_human"]
        self.assertEqual(human, caption)
        self.assertNotIn("https://", human)
        self.page.reload()
        expect(self.page.get_by_text(cta, exact=True)).to_be_visible()

    def test_03_history_pages_and_frozen_account_are_read_only(self):
        """Real index/archive query: page two and a frozen account detail are read-only."""
        self.page.goto(self.fixtures.base_url + "/?view=history")
        expect(self.page.get_by_role("heading", name="历史归档", exact=True)).to_be_visible()
        self.page.get_by_role("combobox", name=re.compile("^平台")).select_option("instagram")
        expect(self.page.get_by_text("共 32 篇", exact=True)).to_be_visible()
        expect(self.page.locator(".history-rows > li")).to_have_count(30)
        self.page.get_by_role("button", name="下一页", exact=True).click()
        expect(self.page.get_by_text("第 2 / 2 页", exact=True)).to_be_visible()
        expect(self.page.locator(".history-rows > li")).to_have_count(2)
        expect(self.page.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
        self.page.get_by_role("button", name="查看归档").first.click()
        expect(self.page.get_by_text("这是冻结账号的历史归档，可查阅来源和处理记录。", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name="编辑德语")).to_have_count(0)
        expect(self.page.get_by_role("button", name="通过并创建排期")).to_have_count(0)
        expect(self.page.get_by_role("button", name="翻译这篇", exact=True)).to_have_count(0)
        self.assertIn("view=history", self.page.url)
        self.assertFalse((self.fixtures.root / "archive/in_neakasa.tech/translated_human.jsonl").exists())
        self.assertEqual(self.writes, [])

    def test_04_settings_preserve_comments_and_reject_stale_version(self):
        """Real temporary TOML only: comments displayed; stale save keeps form values."""
        self.page.goto(self.fixtures.base_url + "/?view=settings")
        expect(self.page.get_by_text(SETTINGS_NOTE, exact=False)).to_be_visible()
        time_input = self.page.get_by_label(re.compile("^默认排期时间（柏林）"))
        time_input.fill("11:00, 18:00")
        self.page.get_by_label(re.compile("^默认挂起期限")).fill("4")
        self.page.get_by_role("button", name="保存设置", exact=True).click()
        expect(self.page.get_by_text("设置已保存，下次选期或挂起时生效。", exact=True)).to_be_visible()
        self.assertIn(SETTINGS_NOTE, self.fixtures.config_path.read_text("utf-8"))
        current = self.fixtures.client.get("/api/settings").json()
        self.assertEqual(current["editable"], {"default_times": ["11:00", "18:00"], "snooze_default_days": 4})
        field = next(field for fields in current["controlled_fields"].values() for field in fields if field["help"])
        section = self.page.locator(".controlled details").filter(has_text=field["key"])
        section.locator("summary").click()
        expect(section.locator(".help").filter(has_text=field["help"])).to_be_visible()
        time_input.fill("12:00, 19:00")
        other = self.fixtures.client.put("/api/settings", json={"version": current["version"], "values": {"default_times": ["09:00"], "snooze_default_days": 5}})
        self.assertEqual(other.status_code, 200, other.text)
        with self.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/settings")) as conflict:
            self.page.get_by_role("button", name="保存设置", exact=True).click()
        self.assertEqual(conflict.value.status, 409)
        expect(self.page.get_by_role("alert")).to_contain_text("配置已有更新")
        expect(time_input).to_have_value("12:00, 19:00")
        self.page.get_by_role("button", name="重新读取", exact=True).click()
        expect(time_input).to_have_value("09:00")
        expect(self.page.get_by_label(re.compile("^默认挂起期限"))).to_have_value("5")
        self.assertEqual((ROOT / "config.toml").read_bytes(), self.fixtures.config_original)

    def test_05_runtime_distinguishes_process_business_and_uncertain_delivery(self):
        """UI-only fixture: interrupted model job and uncertain message never show success."""
        self.responses[("GET", "/api/runtime")] = {"body": runtime_payload()}
        self.page.goto(self.fixtures.base_url + "/?view=runtime")
        expect(self.page.get_by_role("region", name="运行状态")).to_be_visible()
        expect(self.page.get_by_text("处理已中断", exact=True)).to_be_visible()
        expect(self.page.get_by_text("本批 1 次模型请求，已记录费用 US$ 0.1234", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name="核对后关闭中断批次")).to_be_enabled()
        expect(self.page.locator(".facts")).to_contain_text("尚无记录")
        self.page.get_by_text("查看最近消息状态", exact=True).click()
        expect(self.page.get_by_text("离线夹具：远端结果未知，禁止自动重发。", exact=False)).to_be_visible()
        expect(self.page.get_by_role("button", name="登记已送达", exact=True)).to_be_enabled()
        expect(self.page.get_by_role("button", name="核对未送达后恢复", exact=True)).to_be_enabled()
        # Dismissing either confirmation must not send a recovery request.
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.get_by_role("button", name="核对后关闭中断批次").click()
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.get_by_role("button", name="核对未送达后恢复").click()
        self.assertEqual(self.writes, [])

    def test_06_schedule_receipt_is_not_a_publication_observation(self):
        """UI-only simulated approval receipt; no production approve endpoint is reached."""
        detail = self.fixtures.detail(self.fixtures.fb_id)
        detail["status"] = "edited"
        endpoint = "/api/tasks/" + self.fixtures.fb_id
        posted = []
        self.responses[("GET", endpoint)] = {"body": detail}
        self.responses[("GET", endpoint + "/approval-options")] = {"body": {
            "available": True, "reason": "Offline UI fixture only", "fingerprint": "offline-fingerprint",
            "earliest": "2026-09-13T08:00:00Z", "latest": "2026-10-01T08:00:00Z",
            "default_times": ["10:00"], "business_timezone": "Europe/Berlin", "ui_timezone": "America/Los_Angeles"}}

        def approve(request):
            posted.append(request.post_data_json)
            detail["status"] = "scheduled"
            detail["publication"] = {"attempt_id": "offline-attempt", "status": "scheduled"}
            detail["delivery"] = {"status": "not_observed", "message": "尚无远端公开发布观测"}
            return {"body": {"ok": True, "status": "scheduled", "publication": detail["publication"],
                             "message": "离线浏览器替身回执，仅验证页面。"}}

        self.responses[("POST", endpoint + "/approve")] = approve
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_label("发布时间（柏林当地时间）").fill("2026-09-13T10:00")
        self.page.get_by_role("button", name="通过并创建排期", exact=True).click()
        expect(self.page.get_by_text("排期已确认。离线浏览器替身回执，仅验证页面。", exact=True)).to_be_visible()
        expect(self.page.get_by_text("已排期", exact=True)).to_be_visible()
        expect(self.page.get_by_text("尚无远端公开发布观测", exact=True)).to_be_visible()
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["scheduled_at"], "2026-09-13T10:00")
        self.assertEqual(posted[0]["content_fingerprint"], "offline-fingerprint")
        self.responses[("GET", "/api/calendar")] = {"body": calendar_payload()}
        self.page.get_by_role("button", name="发布月历", exact=True).click()
        scheduled = self.page.locator(".calendar-entry").filter(has_text="Offline scheduled fixture")
        published = self.page.locator(".calendar-entry").filter(has_text="Offline published observation fixture")
        expect(scheduled).to_contain_text("已创建定时任务")
        expect(scheduled).not_to_contain_text("已观测到公开发布")
        expect(published).to_contain_text("已观测到公开发布")
        self.assertEqual(self.writes, [])


    def test_07_facebook_inline_link_insertion_uses_backend_counter_and_survives_reload(self):
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_role('button', name='编辑德语').click()
        body = self.page.get_by_role('textbox', name='德语译文')
        body.fill('Details:  bitte lesen.')
        body.evaluate('el => el.setSelectionRange(9, 9)')
        self.page.get_by_role('button', name='插入正文', exact=True).click()
        expect(body).to_have_value('Details: {{link1}} bitte lesen.')
        link = 'https://de.example.invalid/inline'
        self.page.get_by_label('本篇德语落地页').fill(link)
        self.page.get_by_label('我已确认落地页适用于德国站').check()
        draft = self.fixtures.detail(self.fixtures.fb_id)['localization']
        expected = 'Details: ' + link + ' bitte lesen.\n\n' + ' '.join(draft['tags'])
        expect(self.page.locator('.caption-counter')).to_contain_text('发布文案 ' + str(len(expected)) + ' 字符')
        self.save_draft()
        detail = self.fixtures.detail(self.fixtures.fb_id)
        self.assertEqual(detail['text']['de_human'], expected)
        self.assertEqual(detail['text']['de_human'].count(link), 1)
        self.page.reload()
        expect(self.page.get_by_text('Details: {{link1}} bitte lesen.', exact=True)).to_be_visible()


if __name__ == "__main__":
    unittest.main()

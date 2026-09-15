"""Built React + temporary FastAPI regression; saves use real local writes, external results use browser overrides."""
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
            raise AssertionError("Build the React app first: npm.cmd --prefix web/ui run build")
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
                  "react_build": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (ROOT / "web/ui/dist/assets").glob("*.js")},
                  "backend_scope": "temporary archive, human/localization ledgers, source CAS, history, temporary TOML CAS",
                  "ui_only_scope": "runtime interruption/uncertain messages, single-post capture recovery controls, scheduled/published observations",
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
        self.assertEqual(self.errors, [], "React emitted an uncaught browser error")
        self.assertEqual(self.denied, [], "The UI attempted an unapproved network action")
        self.assertEqual(self.fixtures.denied_backend_requests, [], "The backend attempted an external action")

    def open_task(self, task_id):
        expected = "/review/" + quote(task_id, safe="/")
        self.page.goto(self.fixtures.base_url + expected)
        expect(self.page.get_by_role("link", name="返回列表", exact=True)).to_be_visible()
        self.assertEqual(urlsplit(self.page.url).path, expected)

    def save_draft(self):
        with self.page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/localization")) as result:
            self.page.get_by_role("button", name="保存", exact=True).click()
        self.assertEqual(result.value.status, 200, result.value.text())
        expect(self.page.get_by_role("button", name="编辑德语", exact=True)).to_be_visible()

    def test_01_facebook_save_refresh_and_source_conflict_keep_the_draft(self):
        """Real local backend: three sections save atomically; new source gives 409."""
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_role("button", name="编辑德语").click()
        body = self.page.get_by_role("textbox", name="德语正文")
        expect(body).not_to_have_value(re.compile("https?://"))
        body.fill("Offline-Test: von Hand überarbeitet. 🐈")
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        self.page.get_by_role("textbox", name="本篇语义标签").fill("#Katzenliebe")
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_role("textbox", name="链接 1 德语落地页").fill("https://de.example.invalid/produkt")
        self.page.get_by_label("我已确认落地页适用于德国站").check()
        self.save_draft()
        saved = self.fixtures.detail(self.fixtures.fb_id)
        self.assertEqual(saved["localization"]["tags"], ["#Neakasa", "#Katzenliebe"])
        self.assertEqual(saved["localization"]["body_de"], "Offline-Test: von Hand überarbeitet. 🐈")
        self.assertIn("https://de.example.invalid/produkt", saved["text"]["de_human"])
        self.fixtures.write_machine(self.fixtures.fb_id, "Offline-Neuübersetzung. #Neakasa #CatLover")
        self.page.reload()
        self.page.get_by_role("tab", name="正文对照", exact=True).click()
        expect(self.page.get_by_text("Offline-Test: von Hand überarbeitet. 🐈", exact=True)).to_be_visible()
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        expect(self.page.get_by_text("本篇链接已确认", exact=True)).to_be_visible()

        self.page.get_by_role("button", name="编辑德语").click()
        self.page.get_by_role("tab", name="正文对照", exact=True).click()
        body.fill("Offline-Konflikt: diesen Entwurf behalten.")
        self.fixtures.sources[self.fixtures.fb_id][0]["text"] = "Updated offline source. https://us.example.invalid/new #Neakasa #CatLover"
        self.fixtures.write_source(self.fixtures.fb_id)
        with self.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/localization")) as conflict:
            self.page.get_by_role("button", name="保存", exact=True).click()
        self.assertEqual(conflict.value.status, 409)
        expect(self.page.get_by_role("alert").filter(has_text="源帖已更新")).to_be_visible()
        expect(body).to_have_value("Offline-Konflikt: diesen Entwurf behalten.")
        self.page.get_by_role("button", name="载入最新内容并保留我的修改").click()
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        expect(self.page.get_by_role("link", name="https://us.example.invalid/new", exact=True)).to_be_visible()
        self.page.get_by_role("tab", name="正文对照", exact=True).click()
        expect(body).to_have_value("Offline-Konflikt: diesen Entwurf behalten.")
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_role("textbox", name="链接 1 德语落地页").fill("https://de.example.invalid/neu")
        self.page.get_by_label("我已确认落地页适用于德国站").check()
        self.save_draft()
        self.assertFalse(self.fixtures.detail(self.fixtures.fb_id)["text"]["stale"])

    def test_02_instagram_bio_counter_and_unsaved_navigation(self):
        """Real save: URL excluded, CTA retained, emoji count and >30 tags stay intact."""
        self.open_task(self.fixtures.ig_id)
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        expect(self.page.get_by_text("当前 bio（只读）：未配置", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="编辑德语").click()
        body = "Offline-Test 🐈 für Instagram."
        cta = "Weitere Infos im Link in unserer Bio."
        tags = ["#Neakasa"] + ["#Offline" + str(index) for index in range(30)]
        self.page.get_by_role("tab", name="正文对照", exact=True).click()
        self.page.get_by_role("textbox", name="德语正文").fill(body)
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        self.page.get_by_role("textbox", name="本篇语义标签").fill(" ".join(tags[1:]))
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_role("combobox", name="常用引导话术").click()
        self.page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter(
            has_text="自定义（下方输入）").click()
        self.page.get_by_label("自定义 bio 引导").fill(cta)
        caption = "\n\n".join([body, cta, " ".join(tags)])
        expect(self.page.get_by_text(re.compile(rf"^发布文案 {len(caption)} / 2,200 字符"))).to_be_visible()
        expect(self.page.get_by_role("region", name="话题标签选择")).to_contain_text("31 / 30 个")
        self.page.get_by_role("link", name="发布月历", exact=True).click()
        expect(self.page.get_by_role("dialog")).to_be_visible()
        self.page.get_by_role("button", name="留在本页", exact=True).click()
        self.page.get_by_role("tab", name="正文对照", exact=True).click()
        expect(self.page.get_by_role("textbox", name="德语正文")).to_have_value(body)
        self.save_draft()
        human = self.fixtures.detail(self.fixtures.ig_id)["text"]["de_human"]
        self.assertEqual(human, caption)
        self.assertNotIn("https://", human)
        self.page.reload()
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        expect(self.page.get_by_text(cta, exact=True)).to_be_visible()

    def test_03_history_pages_and_frozen_account_are_read_only(self):
        """Real index/archive query: page two and a frozen account detail are read-only."""
        self.page.goto(self.fixtures.base_url + "/history")
        expect(self.page.get_by_role("heading", name="历史归档", exact=True)).to_be_visible()
        self.page.get_by_role("combobox", name="筛选平台").click()
        self.page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter(has_text="Instagram").click()
        expect(self.page.get_by_text("共 32 篇", exact=True)).to_be_visible()
        self.page.locator(".ant-pagination-options-size-changer").click()
        self.page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter(
            has_text=re.compile(r"^20")).click()
        expect(self.page.locator("tr[data-task-id]")).to_have_count(20)
        self.page.locator(".ant-pagination-item-2").click()
        expect(self.page.locator("tr[data-task-id]")).to_have_count(12)
        expect(self.page.locator(".ant-pagination-next")).to_have_class(re.compile("disabled"))
        self.page.locator("tr[data-task-id] a").first.click()
        expect(self.page.get_by_text("冻结账号的历史归档 · 仅供查阅", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name="编辑德语")).to_have_count(0)
        expect(self.page.get_by_role("button", name="通过并创建排期")).to_have_count(0)
        expect(self.page.get_by_role("button", name="翻译这篇", exact=True)).to_have_count(0)
        self.assertIn("/history/", self.page.url)
        self.assertFalse((self.fixtures.root / "archive/in_neakasa.tech/translated_human.jsonl").exists())
        self.assertEqual(self.writes, [])

    def test_04_settings_preserve_comments_and_reject_stale_version(self):
        """Real temporary TOML only: comments survive; stale save keeps form values."""
        self.page.goto(self.fixtures.base_url + "/settings")
        time_input = self.page.get_by_label(re.compile("^默认排期时间（柏林）"))
        time_input.fill("11:00, 18:00")
        self.page.get_by_label(re.compile("^默认挂起期限")).fill("4")
        self.page.get_by_role("button", name="保存设置", exact=True).click()
        expect(self.page.get_by_text("设置已保存，下次选期或挂起时生效。", exact=True)).to_be_visible()
        self.assertIn(SETTINGS_NOTE, self.fixtures.config_path.read_text("utf-8"))
        current = self.fixtures.client.get("/api/settings").json()
        self.assertEqual(current["editable"], {"default_times": ["11:00", "18:00"], "snooze_default_days": 4})
        controlled_key, field = next((key, field) for key, fields in current["controlled_fields"].items()
                                     for field in fields if field["help"])
        controlled_names = {"targets": "监测来源账号", "publish_identity": "发布账号核验名",
                            "price_map": "价格映射", "trusted_owners": "信任名单",
                            "pipeline": "处理方式与预算", "delta": "抓取控制"}
        self.page.get_by_role("button", name=controlled_names[controlled_key], exact=True).click()
        expect(self.page.get_by_text(field["help"], exact=True)).to_be_visible()
        time_input.fill("12:00, 19:00")
        other = self.fixtures.client.put("/api/settings", json={"version": current["version"], "values": {"default_times": ["09:00"], "snooze_default_days": 5}})
        self.assertEqual(other.status_code, 200, other.text)
        with self.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/settings")) as conflict:
            self.page.get_by_role("button", name="保存设置", exact=True).click()
        self.assertEqual(conflict.value.status, 409)
        expect(self.page.get_by_role("alert")).to_contain_text("设置在别处被改过了")
        expect(time_input).to_have_value("12:00, 19:00")
        self.page.get_by_role("button", name="载入最新设置并保留我的修改", exact=True).click()
        expect(time_input).to_have_value("12:00, 19:00")
        expect(self.page.get_by_label(re.compile("^默认挂起期限"))).to_have_value("4")
        with self.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/settings")) as saved:
            self.page.get_by_role("button", name="保存设置", exact=True).click()
        self.assertEqual(saved.value.status, 200, saved.value.text())
        final = self.fixtures.client.get("/api/settings").json()
        self.assertEqual(final["editable"], {"default_times": ["12:00", "19:00"], "snooze_default_days": 4})
        self.assertIn(SETTINGS_NOTE, self.fixtures.config_path.read_text("utf-8"))
        self.assertEqual((ROOT / "config.toml").read_bytes(), self.fixtures.config_original)

    def test_05_runtime_distinguishes_process_business_and_uncertain_delivery(self):
        """UI-only fixture: interrupted model job and uncertain message never show success."""
        self.responses[("GET", "/api/runtime")] = {"body": runtime_payload()}
        self.page.goto(self.fixtures.base_url + "/runtime")
        expect(self.page.get_by_role("heading", name="运行状态", exact=True)).to_be_visible()
        expect(self.page.get_by_text("调度进程：活跃", exact=True)).to_be_visible()
        expect(self.page.get_by_text("最近完整业务处理成功：尚无记录", exact=True)).to_be_visible()
        expect(self.page.get_by_role("region", name="需要你处理")).to_contain_text("一批内容处理已中断。")
        expect(self.page.get_by_role("button", name="核对后关闭中断批次")).to_be_enabled()
        expect(self.page.get_by_text("提醒 1 · 送达结果待核对", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name="登记已送达", exact=True)).to_be_enabled()
        expect(self.page.get_by_role("button", name="核对未送达后恢复", exact=True)).to_be_enabled()
        self.page.get_by_role("button", name="核对后关闭中断批次").click()
        expect(self.page.get_by_role("dialog")).to_contain_text("1 次已记录请求 · US$0.1234")
        self.page.get_by_role("button", name="继续核对", exact=True).click()
        self.page.get_by_role("button", name="核对未送达后恢复").click()
        expect(self.page.get_by_role("dialog")).to_contain_text("会重新发送飞书消息")
        self.page.get_by_role("button", name="取消", exact=True).click()
        self.assertEqual(self.writes, [])

    def test_06_schedule_receipt_is_not_a_publication_observation(self):
        """UI-only simulated approval receipt; no real approve endpoint is reached."""
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
        self.responses[("GET", "/api/calendar")] = {"body": calendar_payload()}
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_label("发布时间（柏林当地时间）").fill("2026-09-13T10:00")
        self.page.get_by_role("button", name="通过并创建排期", exact=True).click()
        self.page.get_by_role("button", name="确认通过并创建排期", exact=True).click()
        expect(self.page.get_by_role("status")).to_contain_text("排期已确认。")
        expect(self.page.get_by_text("已排期", exact=True)).to_be_visible()
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["scheduled_at"], "2026-09-13T10:00")
        self.assertEqual(posted[0]["content_fingerprint"], "offline-fingerprint")
        self.page.get_by_role("link", name="发布月历", exact=True).click()
        scheduled = self.page.get_by_role("button").filter(has_text="已创建定时任务")
        published = self.page.get_by_role("button").filter(has_text="已观测到公开发布")
        expect(scheduled).to_contain_text("已创建定时任务")
        expect(scheduled).not_to_contain_text("已观测到公开发布")
        expect(published).to_contain_text("已观测到公开发布")
        scheduled.click()
        expect(self.page.get_by_text("Offline scheduled fixture", exact=True)).to_be_visible()
        published.click()
        expect(self.page.get_by_text("Offline published observation fixture", exact=True)).to_be_visible()
        self.assertEqual(self.writes, [])


    def test_07_facebook_inline_link_insertion_uses_backend_counter_and_survives_reload(self):
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_role('button', name='编辑德语').click()
        body = self.page.get_by_role('textbox', name='德语正文')
        body.fill('Details:  bitte lesen.')
        body.evaluate('el => el.setSelectionRange(9, 9)')
        self.page.get_by_role('tab', name='话题标签与链接', exact=True).click()
        self.page.get_by_role('button', name='将链接 1 插入正文光标处', exact=True).click()
        expect(body).to_have_value('Details: 〔链接 1〕 bitte lesen.')
        link = 'https://de.example.invalid/inline'
        self.page.get_by_role('tab', name='话题标签与链接', exact=True).click()
        self.page.get_by_role('textbox', name='链接 1 德语落地页').fill(link)
        self.page.get_by_label('我已确认落地页适用于德国站').check()
        draft = self.fixtures.detail(self.fixtures.fb_id)['localization']
        expected = 'Details: ' + link + ' bitte lesen.\n\n' + ' '.join(draft['tags'])
        expect(self.page.get_by_text(re.compile(r'^发布文案 ' + str(len(expected)) + r' 字符'))).to_be_visible()
        self.save_draft()
        detail = self.fixtures.detail(self.fixtures.fb_id)
        self.assertEqual(detail['text']['de_human'], expected)
        self.assertEqual(detail['text']['de_human'].count(link), 1)
        self.page.reload()
        self.page.get_by_role('button', name='编辑德语').click()
        self.page.get_by_role('tab', name='正文对照', exact=True).click()
        expect(self.page.get_by_role('textbox', name='德语正文')).to_have_value('Details: 〔链接 1〕 bitte lesen.')
        self.page.get_by_role('button', name='放弃修改').click()

    def test_08_schedule_date_clear_and_default_time_keep_approval_safe(self):
        """UI-only gate: empty or invalid wall dates cannot reach the approve dialog."""
        detail = self.fixtures.detail(self.fixtures.fb_id)
        detail["status"] = "edited"
        endpoint = "/api/tasks/" + self.fixtures.fb_id
        self.responses[("GET", endpoint)] = {"body": detail}
        self.responses[("GET", endpoint + "/approval-options")] = {"body": {
            "available": True, "reason": "", "fingerprint": "offline-date-gate",
            "earliest": "2026-09-13T08:00:00+02:00", "latest": "2026-10-01T20:00:00+02:00",
            "default_times": ["09:00", "17:00"], "business_timezone": "Europe/Berlin",
            "ui_timezone": "America/Los_Angeles",
        }}
        self.open_task(self.fixtures.fb_id)
        field = self.page.get_by_role("textbox", name="发布时间（柏林当地时间）", exact=True)
        approve = self.page.get_by_role("button", name="通过并创建排期", exact=True)

        field.fill("")
        expect(field).to_have_value("")
        expect(self.page.get_by_role("button", name="09:00 柏林", exact=True)).to_be_disabled()
        expect(self.page.get_by_role("button", name="17:00 柏林", exact=True)).to_be_disabled()
        expect(approve).to_be_disabled()
        approve.click(force=True)
        expect(self.page.get_by_role("dialog")).to_have_count(0)

        # datetime-local permits seconds; this UI requires minute precision.
        field.fill("2026-09-15T10:30:15")
        expect(field).to_have_value("2026-09-15T10:30:15")
        expect(self.page.get_by_text("请填写完整有效的柏林日期和时间", exact=True)).to_be_visible()
        expect(approve).to_be_disabled()
        expect(self.page.get_by_role("button", name="09:00 柏林", exact=True)).to_be_disabled()
        approve.click(force=True)
        expect(self.page.get_by_role("dialog")).to_have_count(0)

        field.fill("2026-09-15T10:30")
        self.page.get_by_role("button", name="09:00 柏林", exact=True).click()
        expect(field).to_have_value("2026-09-15T09:00")
        approve.click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog).to_contain_text("Facebook · 2026-09-15 09:00 柏林")
        dialog.get_by_role("button", name="继续核对", exact=True).click()
        expect(dialog).to_have_count(0)
        self.assertNotIn(("POST", endpoint + "/approve"), self.writes)

    def test_09_capture_link_unknown_total_and_explicit_one_attempt_recovery(self):
        """UI-only recovery receipt: exact post, current revision, and a required human reason."""
        payload = runtime_payload()
        key = "instagram:neakasa.global:offline-capture"
        item = {"key": key, "scan_id": "offline-scan", "status": "manual",
                "post_id": "offline-capture", "platform": "instagram", "account_dir": "in_neakasa.global",
                "classification": "time_unknown", "reason": "来源媒体列表未确认，等待人工处理",
                "archived": False, "saved_images": 0, "source_media_count": None,
                "source_media_complete": False, "media_complete": False,
                "first_seen_at": "2026-09-15T02:00:00Z", "finished_at": "2026-09-15T02:00:15Z",
                "permalink": "https://www.instagram.com/p/offline-capture/",
                "discovery_wait_seconds": None, "capture_seconds": 15}
        payload["monitoring"] = {"status": "ready", "revision": 8, "capture_revision": 11,
            "reason": None, "baselines": {"instagram": {"enabled_at": "2026-09-14T23:00:00Z",
                "lookback_days": 30, "recent_count": 9}},
            "platforms": {"instagram": {"paused": False, "failures": 0, "reason": None,
                "next_due_at": "2026-09-15T03:00:00Z", "homepage_used": 10, "homepage_limit": 24,
                "detail_used": 3, "detail_limit": 12}},
            "items": [item, dict(item, key="instagram:neakasa.global:other-post", post_id="other-post")]}
        self.responses[("GET", "/api/runtime")] = {"body": payload}
        posted = []

        def recover(request):
            posted.append(request.post_data_json)
            item.update(status="complete", archived=True, saved_images=2, source_media_count=2,
                        source_media_complete=True, media_complete=True, classification="recovered", reason=None)
            payload["monitoring"]["capture_revision"] = 12
            return {"body": payload}

        self.responses[("POST", "/api/runtime/capture/recover")] = recover
        self.page.goto(self.fixtures.base_url + "/runtime?capture=" + quote(key, safe=""))
        panel = self.page.get_by_role("region", name="新帖采集状态", exact=True)
        expect(panel).to_contain_text("主页 10/24 次，详情 3/12 次")
        expect(panel).to_contain_text("已校验 0 / 总数待核对 张")
        expect(panel).to_contain_text("发现等待 未知 · 本次抓取 15 秒")
        expect(panel).not_to_contain_text("other-post")
        expect(panel.get_by_role("link", name="查看已存原帖", exact=True)).to_have_count(0)
        expect(panel.get_by_role("link", name="查看源帖", exact=True)).to_have_attribute("href", item["permalink"])
        panel.get_by_role("button", name="处理后尝试一次", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        confirm = dialog.get_by_role("button", name="确认采集一次", exact=True)
        expect(confirm).to_be_disabled()
        reason = "已人工核对当前帖子；允许这次离线界面模拟尝试"
        dialog.get_by_label("处理说明").fill(reason)
        confirm.click()
        expect(dialog).to_have_count(0)
        self.assertEqual(posted, [{"key": key, "version": 11, "reason": reason}])
        expect(panel).to_contain_text("补齐完成")
        expect(panel).to_contain_text("已校验 2 / 2 张")
        expect(panel.get_by_role("link", name="查看已存原帖", exact=True)).to_have_attribute(
            "href", "/history/in_neakasa.global/offline-capture")
        expect(panel.get_by_role("button", name="处理后尝试一次", exact=True)).to_have_count(0)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()

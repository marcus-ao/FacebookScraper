"""Built React + temporary FastAPI regression; saves use real local writes, external results use browser overrides."""
from __future__ import annotations

import json
import hashlib
import io
import os
import re
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from playwright.sync_api import expect, sync_playwright  # noqa: E402
from browser_fixture import (BrowserFixture, SETTINGS_NOTE, approval_options,  # noqa: E402
                             calendar_payload, publish_operation, runtime_payload)


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
            if self.fixtures.local_image_writes and request.method == "POST":
                allowed |= parsed.path.endswith(("/image/0/upload", "/image/0/selection", "/export"))
                allowed |= parsed.path.startswith("/api/image-versions/task/")
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

    def test_12_review_lists_show_newest_sources_before_older_posts(self):
        """Real API + React: all platform queues, paging and adjacent navigation."""
        from core import review, translated
        from core.store import Archive, Media, Post

        base = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0)
        queues = [('review', 'pending_review'), ('not_ready', 'not_ready'),
                  ('snoozed', 'snoozed'), ('processed', 'skipped')]
        expected = {}
        for platform_index, (platform, account, owner) in enumerate([
                ('facebook', 'fa_neakasaofficial', 'neakasaofficial'),
                ('instagram', 'in_neakasa.global', 'neakasa.global')]):
            archive = Archive(self.fixtures.root / 'archive', account)
            for queue_index, (queue, status) in enumerate(queues):
                ids = {}
                # Neither insertion order nor IDs represent publication order.
                for age_index, suffix in [(1, '9'), (0, '8'), (2, '7')]:
                    moment = base + timedelta(hours=age_index)
                    if age_index == 2:
                        moment = moment.astimezone(timezone(timedelta(hours=-7)))
                    post_id = f'880{platform_index}{queue_index}{suffix}'
                    source_text = 'Offline ordering fixture ' + post_id
                    archive.append(Post(post_id, platform, owner, source_text,
                                        moment.isoformat(), owner=owner, tags=['OrderFixture'],
                                        media=[Media('https://example.invalid/order.jpg', 'image')]))
                    source = next(row for row in archive.rows() if row['post_id'] == post_id)
                    ids[age_index] = account + '/' + post_id
                    digest = translated.source_text_sha256(source_text)
                    if status != 'not_ready':
                        with (archive.base / 'translated.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps({'post_id': post_id, 'text_de': 'Sortierprobe ' + post_id,
                                'model': 'offline-fixture', 'prompt_version': translated.PROMPT_VERSION,
                                'translated_at': base.isoformat(), 'source_text_sha256': digest}) + '\n')
                    if status in {'snoozed', 'skipped'}:
                        review.transition(archive.base, source, status, expected_revision=None,
                                          expected_source_sha256=digest, reason='Offline ordering fixture')
                expected[(platform, queue)] = [ids[2], ids[1], ids[0]]

        for platform in ('facebook', 'instagram'):
            for queue, status in queues:
                with self.subTest(platform=platform, queue=queue):
                    ids = expected[(platform, queue)]
                    params = {'platform': platform, 'tag': 'OrderFixture', 'status': status}
                    response = self.fixtures.client.get('/api/tasks', params=params)
                    self.assertEqual(response.status_code, 200)
                    rows = response.json()['tasks']
                    self.assertEqual([row['id'] for row in rows], ids)
                    if queue == 'review':
                        # Existing oldest-first slot allocation survives the display change.
                        slots = [row['schedule']['at'] for row in reversed(rows)]
                        self.assertEqual(slots, sorted(slots))
                        self.assertEqual(len(set(slots)), 3)
                    for page, task_id in enumerate(ids, 1):
                        payload = self.fixtures.client.get('/api/tasks', params={**params, 'page': page, 'limit': 1}).json()
                        self.assertEqual(payload['pagination']['total'], 3)
                        self.assertEqual([row['id'] for row in payload['tasks']], [task_id])

                    search = urlencode({'queue': queue, 'tag': 'OrderFixture'})
                    self.page.goto(self.fixtures.base_url + '/review/' + platform + '?' + search)
                    table_rows = self.page.locator('tr[data-task-id]')
                    expect(table_rows).to_have_count(3)
                    self.assertEqual(table_rows.evaluate_all("rows => rows.map(row => row.dataset.taskId)"), ids)
                    self.page.screenshot(path=str(self.artifacts / f'order-{platform}-{queue}.png'), full_page=True)
                    table_rows.first.click()
                    expect(self.page.get_by_role('button', name='上一篇', exact=True)).to_be_disabled()
                    self.page.get_by_role('button', name='下一篇', exact=True).click()
                    expect(self.page).to_have_url(re.compile('/review/' + re.escape(ids[1]) + r'\?'))
                    self.page.get_by_role('button', name='上一篇', exact=True).click()
                    expect(self.page).to_have_url(re.compile('/review/' + re.escape(ids[0]) + r'\?'))

    def test_11_refinement_rejection_explains_cause_and_refresh_clears_only_after_success(self):
        endpoint = '/api/refinements/task/' + self.fixtures.ig_id
        reason = 'instagram:3984612646028833441 的正文/图片/轮播完整性硬闸未通过，未调用付费服务。'
        submissions = []
        def reject(request):
            submissions.append(request.post_data_json)
            return {'status_code': 409, 'body': {'detail': reason}}
        self.responses[('POST', endpoint)] = reject
        self.open_task(self.fixtures.ig_id)
        self.page.get_by_text('单篇优化（可选）', exact=True).click()
        instruction = self.page.get_by_role('textbox', name='这一次希望怎样调整')
        instruction.fill('保留型号，用更自然的德语。')
        self.page.get_by_role('button', name=re.compile('生成文案候选')).click()
        expect(self.page.get_by_text(reason, exact=True)).to_be_visible()
        expect(instruction).to_have_value('保留型号，用更自然的德语。')
        self.responses[('GET', endpoint)] = {'status_code': 503, 'body': {'detail': '任务状态暂时不可读'}}
        self.page.get_by_role('button', name='刷新任务状态', exact=True).click()
        expect(self.page.get_by_text('任务状态暂时不可读', exact=True)).to_be_visible(timeout=15000)
        del self.responses[('GET', endpoint)]
        self.page.get_by_role('button', name='刷新任务状态', exact=True).click()
        expect(self.page.get_by_text(reason, exact=True)).to_have_count(0)
        expect(self.page.get_by_text('任务状态暂时不可读', exact=True)).to_have_count(0)
        expect(self.page.get_by_text('本次处理未完成或状态暂不可读，已保留输入，请刷新状态核对', exact=True)).to_have_count(0)
        expect(instruction).to_have_value('保留型号，用更自然的德语。')
        self.assertEqual(len(submissions), 1, '刷新状态不得重新提交付费请求')

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
        self.page.get_by_label("我已确认本篇的链接与主页引导").check()
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
        self.page.get_by_label("我已确认本篇的链接与主页引导").check()
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
        time_input = self.page.get_by_label(re.compile("^默认排期时间（北京）"))
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
        detail["status"] = "content_locked"
        endpoint = "/api/tasks/" + self.fixtures.fb_id
        posted = []
        self.responses[("GET", endpoint)] = {"body": detail}
        self.responses[("GET", endpoint + "/approval-options")] = {"body": approval_options(
            reason="Offline UI fixture only", fingerprint="offline-fingerprint",
            earliest="2026-09-13T08:00:00Z", latest="2026-10-01T08:00:00Z",
            default_times=["16:00"])}

        def approve(request):
            posted.append(request.post_data_json)
            detail["status"] = "scheduled"
            detail["publication"] = {"attempt_id": "offline-attempt", "status": "scheduled"}
            detail["delivery"] = {"status": "not_observed", "message": "尚无远端公开发布观测"}
            detail["publish_operation"] = publish_operation(status="running", step_index=1, step="打开编辑器")
            return {"body": detail["publish_operation"]}

        self.responses[("POST", endpoint + "/approve")] = approve
        # 提交跑在请求之外；页面轮询到终态才算数。
        self.responses[("GET", "/api/publish-operations/offline-operation")] = lambda request: {
            "body": detail["publish_operation"]}
        self.responses[("GET", "/api/calendar")] = {"body": calendar_payload()}
        self.open_task(self.fixtures.fb_id)
        self.page.get_by_label("发布时间（北京时间）").fill("2026-09-13T16:00")
        self.page.get_by_role("button", name="确认发布时间并排期", exact=True).click()
        self.page.get_by_role("button", name="确认并创建排期", exact=True).click()
        expect(self.page.get_by_role("alert").filter(has_text="正在创建排期")).to_contain_text("打开编辑器")
        self.page.reload()
        expect(self.page.get_by_role("alert").filter(has_text="正在创建排期")).to_contain_text("打开编辑器")
        detail["publish_operation"] = publish_operation(status="succeeded", step_index=7,
            step="提交并回读月历", message="自动提交并回读为 scheduled")
        expect(self.page.get_by_text("排期已确认；排期详情图片未核验", exact=True)).to_be_visible(timeout=15000)
        expect(self.page.get_by_role("status")).to_contain_text("排期已确认。")
        expect(self.page.get_by_text("已排期", exact=True)).to_be_visible()
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["scheduled_at"], "2026-09-13T16:00")
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
        self.page.get_by_label('我已确认本篇的链接与主页引导').check()
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
        detail["status"] = "content_locked"
        endpoint = "/api/tasks/" + self.fixtures.fb_id
        self.responses[("GET", endpoint)] = {"body": detail}
        self.responses[("GET", endpoint + "/approval-options")] = {"body": approval_options(
            fingerprint="offline-date-gate", earliest="2026-09-13T08:00:00+08:00",
            latest="2026-10-01T20:00:00+08:00", default_times=["09:00", "17:00"])}
        self.open_task(self.fixtures.fb_id)
        field = self.page.get_by_role("textbox", name="发布时间（北京时间）", exact=True)
        approve = self.page.get_by_role("button", name="确认发布时间并排期", exact=True)

        field.fill("")
        expect(field).to_have_value("")
        expect(self.page.get_by_role("button", name="09:00 北京", exact=True)).to_be_disabled()
        expect(self.page.get_by_role("button", name="17:00 北京", exact=True)).to_be_disabled()
        expect(approve).to_be_disabled()
        approve.click(force=True)
        expect(self.page.get_by_role("dialog")).to_have_count(0)

        # datetime-local permits seconds; this UI requires minute precision.
        field.fill("2026-09-15T10:30:15")
        expect(field).to_have_value("2026-09-15T10:30:15")
        expect(self.page.get_by_text("请填写完整有效的日期和时间", exact=True)).to_be_visible()
        expect(approve).to_be_disabled()
        expect(self.page.get_by_role("button", name="09:00 北京", exact=True)).to_be_disabled()
        approve.click(force=True)
        expect(self.page.get_by_role("dialog")).to_have_count(0)

        field.fill("2026-09-15T10:30")
        self.page.get_by_role("button", name="09:00 北京", exact=True).click()
        expect(field).to_have_value("2026-09-15T09:00")
        # 北京 09:00 是柏林凌晨 3 点：提示要出现，但不能挡住她。
        expect(self.page.get_by_text("这个时刻德国还在凌晨，粉丝多半看不到")).to_be_visible()
        approve.click()
        dialog = self.page.get_by_role("dialog")
        expect(dialog).to_contain_text("Facebook · Neakasa Deutschland · 2026-09-15 09:00 北京")
        expect(dialog).to_contain_text("德国 9/15 03:00 柏林")
        dialog.get_by_role("button", name="继续核对", exact=True).click()
        expect(dialog).to_have_count(0)
        self.assertNotIn(("POST", endpoint + "/approve"), self.writes)

    def test_09_freezing_content_is_its_own_step_and_can_be_released(self):
        """「编辑确认无误」把内容锁住；时刻选择框在冻结之后才出现。"""
        detail = self.fixtures.detail(self.fixtures.fb_id)
        detail["status"] = "edited"
        endpoint = "/api/tasks/" + self.fixtures.fb_id
        self.responses[("GET", endpoint)] = {"body": detail}
        self.responses[("GET", endpoint + "/approval-options")] = {"body": approval_options(
            available=False, reason="[publish].ui_constraints_verified 仍为 false")}

        def lock(_request):
            detail["status"] = "content_locked"
            return {"body": detail}

        def unlock(_request):
            detail["status"] = "edited"
            return {"body": detail}

        self.responses[("POST", endpoint + "/content-lock")] = lock
        self.responses[("DELETE", endpoint + "/content-lock")] = unlock
        self.open_task(self.fixtures.fb_id)
        # 录证缺失让排期不可用，但不该挡住人确认文案和图片。
        field = self.page.get_by_role("textbox", name="发布时间（北京时间）", exact=True)
        expect(field).to_have_count(0)
        self.page.get_by_role("button", name="编辑确认无误", exact=True).click()
        expect(self.page.get_by_text("正文与图片已按当前版本锁定")).to_be_visible()
        expect(field).to_have_count(1)
        self.page.get_by_role("button", name="解除冻结", exact=True).click()
        expect(self.page.get_by_text("正文与图片已按当前版本锁定")).to_have_count(0)
        expect(field).to_have_count(0)

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


    def test_10_image_preview_download_upload_stays_in_system(self):
        """Real local API and browser flow; seed paid-image outputs without a model."""
        from core import translated
        from localize import images
        task_id = self.fixtures.add_post("fa_neakasaofficial", "3234567890", "facebook")
        source, directory, account = self.fixtures.sources[task_id]
        text = translated.load_translated(account / "translated.jsonl")[source["post_id"]]["text_de"]
        media_de = directory / "media_de"
        media_de.mkdir()
        for number, colour in enumerate(("green", "purple")):
            path = media_de / ("01.jpg" if number == 0 else "01_v" + "a" * 32 + ".jpg")
            Image.new("RGB", (1080, 1080), colour).save(path)
            row = {
                "post_id": source["post_id"], "media_index": 0,
                "source_sha256": images.sha256_file(directory / "01.jpg"),
                "text_de_sha256": images.text_de_sha256(text),
                "prompt_version": images.IMAGE_PROMPT_VERSION, "model": "gpt-image-2",
                "out_path": path.relative_to(account).as_posix(), "output_sha256": images.sha256_file(path),
                "size_requested": "1080x1080", "size_returned": "1080x1080", "quality": "high",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if number:
                row["refine_id"] = "a" * 32
            images.append_image_jsonl(account / "images_de.jsonl", row)
        self.fixtures.local_image_writes = True
        self.addCleanup(setattr, self.fixtures, "local_image_writes", False)
        self.open_task(task_id)
        self.page.get_by_role("tab", name="图片 1", exact=True).click()
        self.page.get_by_role("button", name="预览第 1 版", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="历史版本预览（尚未采用）")
        preview = dialog.get_by_role("img", name="待比较的历史版本")
        expect(preview).to_be_visible()
        self.page.wait_for_function("() => [...document.images].filter(i => i.alt === '待比较的历史版本').every(i => i.complete && i.naturalWidth > 0)")
        self.assertIsNone(self.fixtures.detail(task_id)["review"]["revision"])
        self.page.keyboard.press("Escape")
        self.page.get_by_role("button", name="采用这一版", exact=True).click()
        self.page.wait_for_function("() => !document.querySelector('.ant-btn-loading')")
        before = self.fixtures.detail(task_id)
        self.assertEqual(before["status"], "edited")
        with self.page.expect_download():
            self.page.get_by_role("button", name="下载本篇素材", exact=True).click()
        self.assertEqual(self.fixtures.detail(task_id)["review"], before["review"])
        for colour in ("orange", "red"):
            data = io.BytesIO()
            Image.new("RGB", (1080, 700), colour).save(data, "PNG")
            with self.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/image/0/upload")) as response:
                # Direct set_input_files bypasses the disabled upload control while
                # download/upload refreshes are still updating the review revision.
                with self.page.expect_file_chooser() as chooser:
                    self.page.get_by_role("button", name=re.compile("上传图片替换第")).click()
                chooser.value.set_files({
                    "name": "manual.png", "mimeType": "image/png", "buffer": data.getvalue()})
            self.assertEqual(response.value.status, 200, response.value.text())
            current = self.fixtures.detail(task_id)
            expect(self.page.get_by_role("img", name="最终图片 1", exact=True)).to_have_attribute("src", current["images"][0]["de_url"])
            expect(self.page.get_by_text("人工图片", exact=True)).to_be_visible()
            expect(self.page.get_by_role("alert").filter(has_text="宽高比")).to_be_visible()
            self.assertEqual(current["status"], "edited")
        self.page.get_by_text("单篇优化（可选）", exact=True).click()
        self.page.get_by_role("combobox", name="优化内容").click()
        self.page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter(has_text=re.compile("^图片$")).click()
        self.page.get_by_role("textbox", name="这一次希望怎样调整").fill("请把 CTA 改短")
        expect(self.page.get_by_role("button", name=re.compile("生成图片"))).to_be_disabled()
        self.page.get_by_role("note", name=re.compile("生成图片.*人工图片")).hover()
        expect(self.page.get_by_text("这一张已换成人工图片，模型优化不会被采用", exact=True)).to_be_visible()
        self.assertFalse((self.fixtures.root / "state/paid_requests.jsonl").exists())

    def test_14_original_confirmation_uses_real_selection_api_and_can_be_revoked(self):
        task_id = self.fixtures.add_post('fa_neakasaofficial', '4234567890', 'facebook')
        self.fixtures.local_image_writes = True
        self.addCleanup(setattr, self.fixtures, 'local_image_writes', False)
        self.open_task(task_id)
        self.page.get_by_role('tab', name=re.compile('图片')).click()
        for action, selection in (('确认使用原图', 'original_confirmed'), ('撤销原图确认', 'original')):
            with self.page.expect_response(lambda response: response.request.method == 'POST'
                    and response.url.endswith('/image/0/selection')) as response:
                self.page.get_by_role('button', name=action, exact=True).click()
            self.assertEqual(response.value.status, 200, response.value.text())
            self.assertEqual(self.fixtures.detail(task_id)['images'][0]['selection'], selection)
            if selection == 'original_confirmed':
                expect(self.page.get_by_text('已确认使用原图', exact=True)).to_be_visible()
                expect(self.page.get_by_text('缺德语图，显示的是原图', exact=True)).to_have_count(0)
            else:
                expect(self.page.get_by_text('缺德语图，显示的是原图', exact=True)).to_be_visible()
        self.assertEqual(self.fixtures.detail(task_id)['review']['status'], 'edited')
        self.assertFalse((self.fixtures.root / 'state/paid_requests.jsonl').exists())

    def test_13_capture_table_page_size_follows_the_size_changer(self):
        """Client-side capture table: the size changer must change the rows actually shown."""
        payload = runtime_payload()
        items = []
        for index in range(78):
            number = f"{index:03d}"
            items.append({
                "key": f"facebook:neakasaofficial:{number}", "scan_id": "scan-keep" if index < 12 else "scan-other",
                "status": "complete", "post_id": f"post-{number}", "platform": "facebook",
                "account_dir": "fa_neakasaofficial", "classification": "new", "reason": None,
                "archived": True, "saved_images": 1, "source_media_count": 1,
                "source_media_complete": True, "media_complete": True,
                "first_seen_at": "2026-09-20T00:00:00Z", "finished_at": "2026-09-20T00:00:01Z",
                "permalink": None, "discovery_wait_seconds": 60, "capture_seconds": 1})
        payload["monitoring"] = {
            "status": "ready", "revision": 3, "capture_revision": 4, "reason": None,
            "baselines": {"facebook": {"enabled_at": "2026-09-01T00:00:00Z", "lookback_days": 30, "recent_count": 78}},
            "platforms": {"facebook": {"paused": False, "failures": 0, "reason": None,
                "next_due_at": "2026-09-20T01:00:00Z", "homepage_used": 1, "homepage_limit": 24,
                "detail_used": 1, "detail_limit": 12}},
            "items": items}
        self.responses[("GET", "/api/runtime")] = {"body": payload}
        self.page.goto(self.fixtures.base_url + "/runtime")
        panel = self.page.get_by_role("region", name="新帖采集状态", exact=True)
        rows = panel.locator("tbody tr.ant-table-row")

        def choose(size):
            panel.locator(".ant-pagination-options-size-changer").click()
            self.page.locator(".ant-select-dropdown:visible .ant-select-item-option-content").filter(
                has_text=re.compile(rf"^{size}\b")).click()

        def expect_window(size, count, last_page, first_post):
            expect(panel.locator(".ant-pagination-options-size-changer")).to_contain_text(f"{size} 条/页")
            expect(rows).to_have_count(count)
            expect(rows.first).to_contain_text(first_post)
            expect(panel.locator(f".ant-pagination-item-{last_page}")).to_have_count(1)
            expect(panel.locator(f".ant-pagination-item-{last_page + 1}")).to_have_count(0)

        expect_window(10, 10, 8, "post-077")
        choose(20)
        expect_window(20, 20, 4, "post-077")
        choose(50)
        expect_window(50, 50, 2, "post-077")
        choose(100)
        expect_window(100, 78, 1, "post-077")
        expect(rows.last).to_contain_text("post-000")
        choose(10)
        panel.locator(".ant-pagination-item-5").click()
        expect(rows.first).to_contain_text("post-037")
        choose(100)
        expect_window(100, 78, 1, "post-077")
        expect(rows.last).to_contain_text("post-000")
        with self.page.expect_response(lambda response: response.request.method == "GET" and response.url.endswith("/api/runtime")) as refreshed:
            self.page.get_by_role("button", name="刷新状态", exact=True).click()
        self.assertEqual(refreshed.value.status, 200, refreshed.value.text())
        expect_window(100, 78, 1, "post-077")

        self.page.goto(self.fixtures.base_url + "/runtime?scan=scan-keep")
        expect(panel).not_to_contain_text("post-012")
        expect_window(10, 10, 2, "post-011")
        panel.locator(".ant-pagination-item-2").click()
        expect(rows).to_have_count(2)
        expect(rows.first).to_contain_text("post-001")
        expect(rows.last).to_contain_text("post-000")
        choose(20)
        expect_window(20, 12, 1, "post-011")
        expect(rows.last).to_contain_text("post-000")

        key = "facebook:neakasaofficial:005"
        self.page.goto(self.fixtures.base_url + "/runtime?capture=" + quote(key, safe=""))
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("post-005")
        expect(panel).not_to_contain_text("post-004")
        expect(panel.locator(".ant-pagination-item-2")).to_have_count(0)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()

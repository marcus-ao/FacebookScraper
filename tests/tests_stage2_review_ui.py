"""Stage-two browser integration with real local editing and isolated model/clipboard replies."""
from __future__ import annotations

import json
import re
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import expect

from tests_browser_workflow import BrowserWorkflowTests, ROOT
from browser_fixture import approval_options
from localize import suggest
from pipeline import refinement


class Stage2ReviewTests(BrowserWorkflowTests):
    @classmethod
    def write_report(cls):
        super().write_report()
        path = cls.artifacts / "report.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report.update(suite="stage2-review-ui",
                      backend_scope="temporary sources, actual detail/check/localization save APIs and suggestion reader",
                      ui_only_scope="model job completion, suggestion artifacts and clipboard outcomes",
                      paid_and_published_ledgers="byte-for-byte unchanged")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="")

    def setUp(self):
        super().setUp()
        self.expected_puts = 0
        self.held_checks = []
        self.hold_checks = False
        self.held_saves = []
        self.hold_saves = False
        self.model_requests = []
        self.ledger_before = {
            directory / filename: self.bytes_or_none(directory / filename)
            for directory in (self.fixtures.config.state_dir, ROOT / "state")
            for filename in ("paid_requests.jsonl", "published.jsonl")}
        self.addCleanup(self.assert_mutations)
        self.addCleanup(self.release_checks)
        self.addCleanup(self.release_saves)
        number = int(self._testMethodName.split("_")[2])
        self.task_id = self.fixtures.add_post("fa_neakasaofficial", str(3234567890 + number), "facebook")
        self.fixtures.write_machine(self.task_id,
                                    "Kostenlose Versand heute. Unsere Katzen freuen sich. #Neakasa #CatLover")

    @staticmethod
    def bytes_or_none(path):
        return path.read_bytes() if path.exists() else None

    def assert_mutations(self):
        for path, before in self.ledger_before.items():
            self.assertEqual(self.bytes_or_none(path), before, f"Unexpected paid/published write: {path}")
        self.assertEqual([entry for entry in self.writes if entry[0] == "PUT"],
                         [("PUT", f"/api/tasks/{self.task_id}/localization")] * self.expected_puts)

    def release_checks(self):
        for route, response in self.held_checks:
            route.fulfill(status=response.status_code, json=response.json())
        self.held_checks.clear()

    def release_saves(self):
        for route, response in self.held_saves:
            route.fulfill(status=response.status_code, json=response.json())
        self.held_saves.clear()

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        if self.hold_saves and request.method == 'PUT' and path.endswith('/localization'):
            self.assertEqual(urlsplit(request.url).netloc, urlsplit(self.fixtures.base_url).netloc)
            self.writes.append((request.method, path))
            response = self.fixtures.client.put(path, json=request.post_data_json)
            self.assertEqual(response.status_code, 200, response.text)
            self.held_saves.append((route, response))
            return
        if self.hold_checks and request.method == "POST" and path.endswith("/check"):
            self.assertEqual(urlsplit(request.url).netloc, urlsplit(self.fixtures.base_url).netloc)
            self.writes.append((request.method, path))
            response = self.fixtures.client.post(path, json=request.post_data_json)
            self.assertEqual(response.status_code, 200, response.text)
            self.held_checks.append((route, response))
            return
        super().route(route)

    def wait_checks(self, count):
        deadline = time.monotonic() + 5
        while len(self.held_checks) < count and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(self.held_checks), count, "The real /check request did not arrive")

    def wait_saves(self, count):
        deadline = time.monotonic() + 5
        while len(self.held_saves) < count and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(self.held_saves), count, "The real localization save did not arrive")

    def setup_model(self, *, complete=True):
        path = f"/api/refinements/task/{self.task_id}"
        capability = self.fixtures.client.get(path).json()
        state = {"jobs": [], "complete": complete, "capability_reads": 0}

        def capabilities(_request):
            state["capability_reads"] += 1
            return {"body": dict(capability, jobs=state["jobs"])}

        def submit(request):
            payload = request.post_data_json
            self.model_requests.append(payload)
            self.assertEqual(payload["kind"], "suggest")
            self.assertIsInstance(payload["body_de"], str)
            job = {"job_id": uuid4().hex, "kind": "suggest", "status": "pending",
                   "source_text_sha256": payload["source_text_sha256"],
                   "body_de": payload["body_de"], "recorded_at": datetime.now(timezone.utc).isoformat(),
                   "cost_usd": 0, "paid_request_ids": []}
            state["jobs"].append(job)

            def poll(_request):
                if state["complete"] and job["status"] == "pending":
                    item = {"quote": "Kostenlose Versand", "replacement": "Kostenloser Versand",
                            "kind": "grammar", "why": "Versand 是阳性，形容词词尾需要改为 -er。"}
                    account, post_id = self.task_id.split("/")
                    # 仅替代模型产出；详情仍经真实 reader 读取临时派生文件并核版本。
                    record = suggest.record(job_id=job["job_id"], account=account, post_id=post_id,
                                            source_text_sha256_value=job["source_text_sha256"],
                                            text_de=job["body_de"], parsed={"items": [item], "dropped": []},
                                            paid_request_id=None)
                    suggest.append_suggestions(self.fixtures.config.state_dir / refinement.SUGGESTIONS_FILE,
                                               record)
                    job.update(status="succeeded", suggestions=[item], dropped=[])
                return {"body": job}

            self.responses[("GET", f"/api/refinements/jobs/{job['job_id']}")] = poll
            return {"body": job}

        self.responses[("GET", path)] = capabilities
        self.responses[("POST", path)] = submit
        return state

    def enter_edit(self):
        self.open_task(self.task_id)
        self.step("德语正文")
        self.page.get_by_role("button", name="编辑德语", exact=True).click()
        return self.page.get_by_role("textbox", name="德语正文")

    def suggestion_panel(self):
        # 建议区默认折叠；只有带着上一轮建议进来时才自动展开。
        panel = self.page.get_by_role("region", name="德语文案优化建议")
        button = panel.locator("button[data-paid-action]")
        if not button.is_visible():
            panel.get_by_text("文案优化建议（可选）", exact=True).click()
        return panel, button

    def ask_for_suggestions(self):
        panel, button = self.suggestion_panel()
        expect(button).to_be_enabled()
        button.click()
        return panel

    def localized_link(self, *, target="https://de.example.invalid/stage2", tags="#Neakasa #Katzenliebe"):
        # 所有拟发布标签都由人填写（D21），品牌标签也要写进来才会保留。
        self.step("标签与链接")
        self.page.get_by_role("textbox", name="本篇拟发布标签").fill(tags)
        self.page.get_by_role("textbox", name="链接 1 德语落地页").fill(target)
        self.step("德语正文")

    def open_caption(self):
        """第四步按需向服务端取回完整文案；只有它回来的成品才能复制。"""
        self.step("最终确认与排期")
        self.page.get_by_role("button", name="查看完整发布文案", exact=True).click()
        return self.page.get_by_role("button", name=re.compile("复制发布文案$"))

    def complete_button(self):
        # 保存中按钮名前会多出加载图标的名称。
        return self.page.get_by_role("button", name=re.compile("完成标签与链接审核$"))

    def reviewed_status(self):
        return self.page.get_by_role("status").filter(has_text="标签与链接已审核")

    def clipboard(self):
        self.page.add_init_script("""
            window.stage2CopyMode = 'native';
            window.stage2Copies = [];
            window.stage2Fallbacks = [];
            Object.defineProperty(navigator, 'clipboard', { configurable: true, get() {
                if (['missing', 'manual'].includes(window.stage2CopyMode)) return undefined;
                return { writeText: async text => {
                    if (window.stage2CopyMode === 'rejected') throw new Error('offline clipboard rejection');
                    window.stage2Copies.push(text);
                }};
            }});
            document.execCommand = command => {
                if (command !== 'copy') throw new Error('Unexpected command: ' + command);
                window.stage2Fallbacks.push(document.activeElement.value);
                return window.stage2CopyMode !== 'manual';
            };
        """)

    def test_stage2_01_unsaved_suggestions_expire_regenerate_and_save_explicitly(self):
        self.setup_model()
        body = self.enter_edit()
        self.localized_link()
        pending = "Kostenlose Versand heute. Deine Katze freut sich."
        body.fill(pending)
        _, _, account = self.fixtures.sources[self.task_id]
        paths = [account / "translated.jsonl", account / "translated_human.jsonl"]
        original = [self.bytes_or_none(path) for path in paths]
        panel = self.ask_for_suggestions()
        adopt = panel.get_by_role("button", name="采用", exact=True)
        expect(adopt).to_be_enabled()
        self.assertEqual(self.model_requests[0]["body_de"], pending)
        stored = self.fixtures.detail(self.task_id)["text_suggestions"]
        self.assertEqual(stored["body_de"], pending)
        self.assertFalse(stored["current"], "Unsaved input differs from the saved body")

        body.fill("Kostenlose Versand heute. Die andere Aussage ist neu.")
        expect(adopt).to_be_disabled()
        expect(panel.get_by_role("alert")).to_contain_text("「采用」已停用")
        body.fill(pending)
        expect(adopt).to_be_enabled()
        panel.get_by_role("button", name="忽略", exact=True).click()
        expect(adopt).to_have_count(0)
        self.ask_for_suggestions()
        expect(adopt).to_be_enabled()
        self.assertEqual(len(self.model_requests), 2)
        self.assertNotEqual(self.fixtures.detail(self.task_id)["text_suggestions"]["job_id"], stored["job_id"])
        adopt.click()
        adopted = "Kostenloser Versand heute. Deine Katze freut sich."
        expect(body).to_have_value(adopted)
        # 采用只改编辑区，保存之前两份译文账本一个字节都不变。
        self.assertEqual([self.bytes_or_none(path) for path in paths], original)
        self.assertEqual([request for request in self.writes if request[0] == "PUT"], [])

        self.expected_puts = 1
        self.save_draft()
        detail = self.fixtures.detail(self.task_id)
        self.assertEqual(detail["localization"]["body_de"], adopted)
        self.assertEqual(detail["localization"]["tags"], ["#Neakasa", "#Katzenliebe"])
        self.assertEqual(detail["localization"]["links"][0]["target_url"], "https://de.example.invalid/stage2")

    def test_stage2_02_refresh_recovers_pending_job_without_resubmitting(self):
        model = self.setup_model(complete=False)
        self.enter_edit()
        self.ask_for_suggestions()
        _, button = self.suggestion_panel()
        expect(button).to_be_disabled()
        reads = model["capability_reads"]
        self.page.reload()
        self.step("德语正文")
        self.page.get_by_role("button", name="编辑德语", exact=True).click()
        panel, button = self.suggestion_panel()
        expect(button).to_be_disabled()
        self.assertGreater(model["capability_reads"], reads)
        model["complete"] = True
        expect(panel.get_by_role("button", name="采用", exact=True)).to_be_enabled()
        self.assertEqual(len(self.model_requests), 1)

    def test_stage2_03_copy_matches_real_server_caption_with_inline_link_and_local_tags(self):
        self.clipboard()
        body = self.enter_edit()
        self.localized_link()
        body.fill("Jetzt entdecken: 〔链接 1〕. 🐈")
        # 编辑中不给看完整文案：复制出去的必须是服务端按已保存内容渲染的成品。
        self.step("最终确认与排期")
        expect(self.page.get_by_role("button", name="查看完整发布文案", exact=True)).to_be_disabled()
        self.step("德语正文")
        self.expected_puts = 1
        self.save_draft()
        copy = self.open_caption()
        expect(copy).to_be_enabled()
        copy.click()
        self.page.wait_for_function("window.stage2Copies.length === 1")
        self.assertEqual(self.page.evaluate("window.stage2Copies[0]"),
                         "Jetzt entdecken: https://de.example.invalid/stage2. 🐈\n\n#Neakasa #Katzenliebe")

    def test_stage2_04_stale_check_response_cannot_enable_copy(self):
        self.clipboard()
        self.open_task(self.task_id)
        self.step("最终确认与排期")
        self.hold_checks = True
        self.page.get_by_role("button", name="查看完整发布文案", exact=True).click()
        self.wait_checks(1)
        self.hold_checks = False
        # 旧请求还没回来时正文被改并保存，页面的内容版本已经变了。
        self.step("德语正文")
        self.page.get_by_role("button", name="编辑德语", exact=True).click()
        self.page.get_by_role("textbox", name="德语正文").fill("Aktuelle gespeicherte Fassung. 🐈")
        self.expected_puts = 1
        self.save_draft()
        route, response = self.held_checks.pop(0)
        route.fulfill(status=200, json=response.json())
        self.step("最终确认与排期")
        view = self.page.get_by_role("button", name="查看完整发布文案", exact=True)
        expect(view).to_be_enabled()
        self.page.evaluate("async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }")
        copy = self.page.get_by_role("button", name=re.compile("复制发布文案$"))
        expect(copy).to_have_count(0)
        self.assertEqual(self.page.evaluate("window.stage2Copies"), [])
        view.click()
        expect(copy).to_be_enabled()
        copy.click()
        self.page.wait_for_function("window.stage2Copies.length === 1")
        self.assertEqual(self.page.evaluate("window.stage2Copies[0]"),
                         "Aktuelle gespeicherte Fassung. 🐈\n\n#Neakasa #CatLover")

    def test_stage2_05_clipboard_fallback_and_manual_dialog_keep_exact_caption(self):
        self.clipboard()
        body = self.enter_edit()
        self.localized_link()
        body.fill("Weitere Infos: 〔链接 1〕. 🐈")
        self.expected_puts = 1
        self.save_draft()
        expected = "Weitere Infos: https://de.example.invalid/stage2. 🐈\n\n#Neakasa #Katzenliebe"
        copy = self.open_caption()
        expect(copy).to_be_enabled()
        self.page.evaluate("window.stage2CopyMode = 'missing'")
        copy.click()
        self.page.wait_for_function("window.stage2Fallbacks.length === 1")
        self.assertEqual(self.page.evaluate("window.stage2Fallbacks[0]"), expected)
        expect(self.page.get_by_role("button", name=re.compile("已复制$"))).to_be_visible()
        self.page.evaluate("window.stage2CopyMode = 'rejected'")
        self.page.get_by_role("button", name=re.compile("已复制$")).click()
        self.page.wait_for_function("window.stage2Fallbacks.length === 2")
        self.assertEqual(self.page.evaluate("window.stage2Fallbacks[1]"), expected)
        self.page.evaluate("window.stage2CopyMode = 'manual'")
        self.page.get_by_role("button", name=re.compile("已复制$")).click()
        dialog = self.page.get_by_role("dialog", name="手动复制发布文案")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("textbox", name="完整发布文案")).to_have_value(expected)
        expect(dialog.get_by_role("textbox", name="完整发布文案")).to_have_attribute("readonly", "")
        self.assertEqual(self.page.evaluate("window.stage2Copies"), [])

    def test_stage2_06_edits_while_saving_remain_draft_and_can_be_saved_again(self):
        body = self.enter_edit()
        self.localized_link()
        body.fill('Erste Bearbeitung.')
        self.expected_puts = 2
        self.hold_saves = True
        self.page.get_by_role('button', name='保存修改', exact=True).click()
        self.wait_saves(1)
        body.fill('Neue Bearbeitung waehrend Speichern.')
        self.localized_link(target='https://de.example.invalid/new-edit', tags='#Neakasa #Katzenzuhause')
        self.release_saves()
        self.hold_saves = False
        # 保存只认点击时那一版；等待期间的新输入留在编辑区，不能被回写覆盖。
        expect(self.page.get_by_role('button', name='保存修改', exact=True)).to_be_enabled()
        self.assertEqual(self.fixtures.detail(self.task_id)['localization']['body_de'], 'Erste Bearbeitung.')
        expect(body).to_have_value('Neue Bearbeitung waehrend Speichern.')
        self.step('标签与链接')
        expect(self.page.get_by_role('textbox', name='本篇拟发布标签')).to_have_value('#Neakasa #Katzenzuhause')
        expect(self.page.get_by_role('textbox', name='链接 1 德语落地页')).to_have_value('https://de.example.invalid/new-edit')
        self.step('德语正文')
        self.save_draft()
        saved = self.fixtures.detail(self.task_id)['localization']
        self.assertEqual(saved['body_de'], 'Neue Bearbeitung waehrend Speichern.')
        self.assertEqual(saved['tags'], ['#Neakasa', '#Katzenzuhause'])
        self.assertEqual(saved['links'][0]['target_url'], 'https://de.example.invalid/new-edit')

    def test_stage2_07_confirm_sections_without_entering_editor(self):
        self.task_id = self.fixtures.add_post('in_neakasa.global', '3234567907', 'instagram')
        source = self.fixtures.sources[self.task_id][0]
        source['text'] = 'A clean home. #Neakasa #CatLover'
        self.fixtures.write_source(self.task_id)
        self.fixtures.write_machine(self.task_id, 'Ein sauberes Zuhause. #Neakasa #CatLover')
        before = self.fixtures.detail(self.task_id)['localization_validation']['caption']
        self.open_task(self.task_id)
        self.step('标签与链接')
        expect(self.page.get_by_text('原帖没有链接。', exact=True)).to_be_visible()
        # 不进编辑区也能一次完成审核；它只记下决定，不改文案。
        self.expected_puts += 1
        with self.page.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/localization')):
            self.complete_button().click()
        expect(self.reviewed_status()).to_be_visible()
        expect(self.complete_button()).to_have_count(0)
        expect(self.page.get_by_role('button', name='编辑标签与链接', exact=True)).to_be_enabled()
        expect(self.page.get_by_role('textbox', name='本篇拟发布标签')).to_have_count(0)
        self.page.reload()
        expect(self.reviewed_status()).to_be_visible()
        saved = self.fixtures.detail(self.task_id)
        self.assertTrue(saved['localization']['hashtags_confirmed'])
        self.assertTrue(saved['localization_validation']['ready'])
        self.assertEqual(saved['localization_validation']['caption'], before)
        self.assertEqual(saved['localization']['ig_cta'], '')
        self.page.get_by_role('button', name='编辑标签与链接', exact=True).click()
        self.page.get_by_label('自定义 bio 引导').fill('Mehr dazu im Profil')
        expect(self.complete_button()).to_be_visible()
        expect(self.reviewed_status()).to_have_count(0)
        self.page.get_by_role('button', name='放弃修改', exact=True).click()
        expect(self.reviewed_status()).to_be_visible()
        expect(self.complete_button()).to_have_count(0)

    def test_stage2_08_confirmation_failure_and_conflict_do_not_claim_saved(self):
        self.open_task(self.task_id)
        self.step('标签与链接')
        endpoint = ('PUT', f'/api/tasks/{self.task_id}/localization')
        self.responses[endpoint] = {'status_code': 503, 'body': {'detail': 'temporary failure'}}
        self.complete_button().click()
        failure = self.page.get_by_role('alert').filter(has_text='确认未保存')
        expect(failure).to_be_visible()
        expect(failure).not_to_contain_text('temporary')
        expect(self.reviewed_status()).to_have_count(0)
        self.assertFalse(self.fixtures.detail(self.task_id)['localization']['hashtags_confirmed'])
        del self.responses[endpoint]
        self.fixtures.sources[self.task_id][0]['text'] += ' Changed'
        self.fixtures.write_source(self.task_id)
        self.expected_puts += 1
        self.complete_button().click()
        expect(self.page.get_by_role('button', name='载入最新内容并保留我的修改')).to_be_visible()
        expect(self.reviewed_status()).to_have_count(0)
        self.page.get_by_role('button', name='载入最新内容并保留我的修改').click()
        expect(self.complete_button()).to_be_enabled()
        # 原文变了，旧德语稿不能直接确认；页面要说清下一步，而不是只让人重试。
        self.expected_puts += 1
        self.complete_button().click()
        expect(self.page.get_by_role('alert').filter(has_text='请先编辑德语')).to_be_visible()
        expect(self.reviewed_status()).to_have_count(0)
        self.step('德语正文')
        self.page.get_by_role('button', name='编辑德语', exact=True).click()
        self.page.get_by_role('textbox', name='德语正文').fill('Erneut gepruefter Text.')
        self.expected_puts += 1
        self.save_draft()
        self.step('标签与链接')
        self.expected_puts += 1
        self.complete_button().click()
        # 这篇的原文链接没有映射，确认只记下决定，同时提醒补全落地页。
        expect(self.page.get_by_role('status').filter(has_text='审核选择已保存，请补全德语落地页')).to_be_visible()

    def test_stage2_09_facebook_confirmation_pending_and_locked_states(self):
        mapping = self.fixtures.config._d['publish'].get('link_map', {})
        self.addCleanup(self.fixtures.config._d['publish'].__setitem__, 'link_map', mapping)
        self.fixtures.config._d['publish']['link_map'] = {
            'https://us.example.invalid/product': 'https://de.example.invalid/product'}
        self.responses[('GET', f'/api/tasks/{self.task_id}/approval-options')] = {'body': approval_options()}
        self.open_task(self.task_id)
        self.step('标签与链接')
        edit = self.page.get_by_role('button', name='编辑标签与链接', exact=True)
        before = self.fixtures.detail(self.task_id)['localization_validation']['caption']
        self.expected_puts = 1
        self.hold_saves = True
        self.complete_button().click()
        # 保存途中两个入口都不可点，不能开出第二份编辑或重复确认。
        expect(self.complete_button()).to_be_disabled()
        expect(edit).to_be_disabled()
        self.wait_saves(1)
        self.release_saves()
        self.hold_saves = False
        expect(self.reviewed_status()).to_be_visible()
        expect(edit).to_be_enabled()
        saved = self.fixtures.detail(self.task_id)
        self.assertTrue(saved['localization']['links'][0]['confirmed'])
        self.assertTrue(saved['localization']['links_confirmed'])
        self.assertEqual(saved['localization_validation']['caption'], before)
        for status, read_only in [('content_locked', False), ('pending_review', True), ('scheduled', False)]:
            locked = self.fixtures.detail(self.task_id)
            locked.update(status=status, read_only=read_only)
            self.responses[('GET', f'/api/tasks/{self.task_id}')] = {'body': locked}
            self.page.reload()
            expect(self.reviewed_status()).to_be_visible()
            expect(self.complete_button()).to_have_count(0)
            expect(edit).to_have_count(0)


def load_tests(_loader, _tests, _pattern):
    # 复用原回归的服务器、浏览器及证据生命周期，不重复执行父类的其它阶段用例。
    return unittest.TestSuite(Stage2ReviewTests(name) for name in sorted(dir(Stage2ReviewTests))
                              if name.startswith("test_stage2_"))


if __name__ == "__main__":
    unittest.main()

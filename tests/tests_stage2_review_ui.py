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
        self.model_requests = []
        self.ledger_before = {
            directory / filename: self.bytes_or_none(directory / filename)
            for directory in (self.fixtures.config.state_dir, ROOT / "state")
            for filename in ("paid_requests.jsonl", "published.jsonl")}
        self.addCleanup(self.assert_mutations)
        self.addCleanup(self.release_checks)
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

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
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
        self.assertEqual(len(self.held_checks), count, "The debounced real /check request did not arrive")

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
        self.page.get_by_role("button", name="编辑德语", exact=True).click()
        return self.page.get_by_role("textbox", name="德语正文")

    def ask_for_suggestions(self):
        # 两个动作使用不同标题，但始终属于这个具名区域。
        panel = self.page.get_by_role("region", name="德语文案优化建议")
        button = panel.locator("button[data-paid-action]")
        expect(button).to_be_enabled()
        button.click()
        return panel

    def localized_link(self, *, target="https://de.example.invalid/stage2"):
        self.page.get_by_role("tab", name="话题标签与链接", exact=True).click()
        self.page.get_by_role("textbox", name="本篇语义标签").fill("#Katzenliebe")
        self.page.get_by_label("我已确认本篇使用的话题标签").check()
        self.page.get_by_role("textbox", name="链接 1 德语落地页").fill(target)
        self.page.get_by_label("我已确认落地页适用于德国站").check()
        self.page.get_by_role("tab", name="正文对照", exact=True).click()

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
        panel = self.ask_for_suggestions()
        button = panel.locator("button[data-paid-action]")
        expect(button).to_be_disabled()
        reads = model["capability_reads"]
        self.page.reload()
        self.page.get_by_role("button", name="编辑德语", exact=True).click()
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
        copy = self.page.get_by_role("button", name=re.compile("复制发布文案$"))
        expect(copy).to_be_enabled()
        copy.click()
        self.page.wait_for_function("window.stage2Copies.length === 1")
        self.assertEqual(self.page.evaluate("window.stage2Copies[0]"),
                         "Jetzt entdecken: https://de.example.invalid/stage2. 🐈\n\n#Neakasa #Katzenliebe")

    def test_stage2_04_stale_check_response_cannot_enable_copy(self):
        self.clipboard()
        body = self.enter_edit()
        copy = self.page.get_by_role("button", name=re.compile("复制发布文案$"))
        expect(copy).to_be_enabled()
        self.hold_checks = True
        body.fill("Alte ungespeicherte Fassung.")
        self.wait_checks(1)
        body.fill("Aktuelle ungespeicherte Fassung. 🐈")
        self.wait_checks(2)
        expect(copy).to_be_disabled()
        route, response = self.held_checks.pop(0)
        route.fulfill(status=200, json=response.json())
        self.page.evaluate("async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }")
        expect(copy).to_be_disabled()
        self.assertEqual(self.page.evaluate("window.stage2Copies"), [])
        route, response = self.held_checks.pop(0)
        route.fulfill(status=200, json=response.json())
        expect(copy).to_be_enabled()
        copy.click()
        self.page.wait_for_function("window.stage2Copies.length === 1")
        self.assertEqual(self.page.evaluate("window.stage2Copies[0]"),
                         "Aktuelle ungespeicherte Fassung. 🐈\n\n#Neakasa #CatLover")

    def test_stage2_05_clipboard_fallback_and_manual_dialog_keep_exact_caption(self):
        self.clipboard()
        body = self.enter_edit()
        self.localized_link()
        body.fill("Weitere Infos: 〔链接 1〕. 🐈")
        expected = "Weitere Infos: https://de.example.invalid/stage2. 🐈\n\n#Neakasa #Katzenliebe"
        copy = self.page.get_by_role("button", name=re.compile("复制发布文案$"))
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


def load_tests(_loader, _tests, _pattern):
    # 复用原回归的服务器、浏览器及证据生命周期，不重复执行父类的其它阶段用例。
    return unittest.TestSuite(Stage2ReviewTests(name) for name in sorted(dir(Stage2ReviewTests))
                              if name.startswith("test_stage2_"))


if __name__ == "__main__":
    unittest.main()

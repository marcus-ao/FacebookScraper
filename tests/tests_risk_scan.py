"""英文语义风险预扫的状态、付费账本和展示契约。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, paid_requests
from pipeline import engine, risk_scan
import tests_web_review as fixtures


NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


class FakeCaller:
    def __init__(self, response: str):
        self.response = response
        self.last_usage = {"input_tokens": 20, "output_tokens": 10}
        self.s = SimpleNamespace(
            model="offline-risk-model", provider="offline-fixture",
            cost_rates={"input": 0, "cache_read": 0, "output": 0})

    def translate(self, text: str, system: str) -> str:
        return self.response


class MissingLocalKeyCaller(FakeCaller):
    @property
    def client(self):
        raise SystemExit("missing local key")


class RiskScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prompt = self.root / "risk.md"
        self.prompt.write_text("Find only the three requested risk classes.", encoding="utf-8")
        self.controller = paid_requests.RequestController(
            self.root, preflight=lambda: None, operation_id="content-job-17")

    def test_completed_empty_scan_is_distinct_from_not_scanned(self):
        before = risk_scan.current_view(
            self.root, "fa_brand/1", "A plain product caption.", prompt_path=self.prompt)
        self.assertEqual(before["status"], "not_scanned")

        completed = risk_scan.scan_source(
            self.root, task_id="fa_brand/1", source_ref="facebook:1",
            source_text="A plain product caption.", controller=self.controller,
            caller=FakeCaller('{"risks": []}'), prompt_path=self.prompt, now=NOW)

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["risks"], [])
        self.assertEqual(completed["source"]["model"], "offline-risk-model")
        events = paid_requests.load_events(self.root)
        self.assertTrue(events)
        self.assertTrue(all(row["operation_id"] == "content-job-17" for row in events))
        self.assertEqual(events[0]["stage"], "risk_scan")

    def test_exact_source_spans_and_allowed_kinds_are_persisted(self):
        source = "This vacuum really sucks. Keep it in your den."
        response = json.dumps({"risks": [
            {"kind": "pun", "start": 12, "end": 24,
             "quote": "really sucks", "label": "Slogan uses a double meaning."},
            {"kind": "us_only", "start": 42, "end": 45,
             "quote": "den", "label": "Housing term may need localization."},
        ]})
        result = risk_scan.scan_source(
            self.root, task_id="fa_brand/2", source_ref="facebook:2",
            source_text=source, controller=self.controller, caller=FakeCaller(response),
            prompt_path=self.prompt, now=NOW)
        self.assertEqual([row["kind"] for row in result["risks"]], ["pun", "us_only"])
        self.assertEqual(result["risks"][0]["en_span"], [12, 24])

    def test_contract_failure_is_failed_and_never_an_empty_success(self):
        result = risk_scan.scan_source(
            self.root, task_id="fa_brand/3", source_ref="facebook:3",
            source_text="Ordinary caption", controller=self.controller,
            caller=FakeCaller('{"risks":[{"kind":"sentiment","start":0,"end":8,"quote":"Ordinary","label":"x"}]}'),
            prompt_path=self.prompt, now=NOW)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["risks"], [])
        self.assertIn("人工", result["message"])

    def test_model_configuration_failure_is_persisted_as_failed(self):
        with patch.object(risk_scan.translate, "Settings", side_effect=SystemExit("missing model config")):
            result = risk_scan.scan_source(
                self.root, task_id="fa_brand/config", source_ref="facebook:config",
                source_text="Ordinary caption", controller=self.controller,
                prompt_path=self.prompt, now=NOW)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["risks"], [])

    def test_local_key_is_validated_before_paid_request_started(self):
        result = risk_scan.scan_source(
            self.root, task_id="fa_brand/no-key", source_ref="facebook:no-key",
            source_text="Ordinary caption", controller=self.controller,
            caller=MissingLocalKeyCaller('{"risks": []}'), prompt_path=self.prompt, now=NOW)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(paid_requests.load_events(self.root), [])

    def test_risk_scan_cost_is_part_of_monthly_text_model_cost(self):
        caller = FakeCaller('{"risks": []}')
        caller.s.cost_rates = {"input": 1_000_000, "cache_read": 0, "output": 1_000_000}
        risk_scan.scan_source(
            self.root, task_id="fa_brand/cost", source_ref="facebook:cost",
            source_text="Ordinary caption", controller=self.controller,
            caller=caller, prompt_path=self.prompt, now=NOW)
        month = paid_requests.ledger_month_snapshot(self.root, month="2026-09")
        self.assertEqual(month.translation_usd, 30.0)
        self.assertEqual(month.unknown, ())

    def test_source_or_prompt_version_change_makes_old_result_stale(self):
        risk_scan.scan_source(
            self.root, task_id="fa_brand/4", source_ref="facebook:4",
            source_text="Original", controller=self.controller,
            caller=FakeCaller('{"risks": []}'), prompt_path=self.prompt, now=NOW)
        changed_source = risk_scan.current_view(
            self.root, "fa_brand/4", "Changed", prompt_path=self.prompt)
        self.assertEqual(changed_source["status"], "stale")
        self.assertEqual(changed_source["risks"], [])
        shifted = risk_scan.current_view(self.root, 'fa_brand/4', ' Original', prompt_path=self.prompt)
        self.assertEqual(shifted['status'], 'stale')

        self.prompt.write_text("A new risk prompt version.", encoding="utf-8")
        changed_prompt = risk_scan.current_view(
            self.root, "fa_brand/4", "Original", prompt_path=self.prompt)
        self.assertEqual(changed_prompt["status"], "stale")

    def test_outer_whitespace_change_invalidates_exact_risk_spans(self):
        risk_scan.scan_source(
            self.root, task_id="fa_brand/spans", source_ref="facebook:spans",
            source_text="sucks", controller=self.controller,
            caller=FakeCaller('{"risks":[{"kind":"pun","start":0,"end":5,'
                              '"quote":"sucks","label":"double meaning"}]}'),
            prompt_path=self.prompt, now=NOW)
        changed = risk_scan.current_view(
            self.root, "fa_brand/spans", "  sucks", prompt_path=self.prompt)
        self.assertEqual(changed["status"], "stale")
        self.assertEqual(changed["risks"], [])

    def test_padded_source_scan_stays_current_and_preserves_raw_offsets(self):
        source = '  sucks\n'
        result = risk_scan.scan_source(
            self.root, task_id='fa_brand/padded', source_ref='facebook:padded',
            source_text=source, controller=self.controller,
            caller=FakeCaller('{"risks":[{"kind":"pun","start":2,"end":7,'
                              '"quote":"sucks","label":"double meaning"}]}'),
            prompt_path=self.prompt, now=NOW)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['risks'][0]['en_span'], [2, 7])
        row = json.loads((self.root / risk_scan.STATE_NAME).read_text('utf-8'))
        self.assertEqual(row['source_text_sha256'], risk_scan.translate.source_text_sha256('sucks'))
        self.assertEqual(row['scan_text_sha256'], risk_scan._digest(source))
        self.assertEqual(risk_scan.current_view(self.root, 'fa_brand/padded', 'sucks',
                         prompt_path=self.prompt)['status'], 'stale')

    def test_review_reader_exposes_scan_status_and_never_reads_demo_fixture(self):
        fixture = fixtures.WebReviewTests()
        fixture.setUp()
        try:
            detail = fixture.client.get(fixture.url).json()
            self.assertEqual(detail["risk_scan"]["status"], "not_scanned")
            self.assertEqual(detail["risks"], [])
            controller = paid_requests.RequestController(
                fixture.root / "state", preflight=lambda: None, operation_id="reader-test")
            risk_scan.scan_source(
                fixture.root / "state", task_id=fixture.task_id,
                source_ref="facebook:" + fixture.post_id,
                source_text=fixture.source["text"], controller=controller,
                caller=FakeCaller('{"risks": []}'), prompt_path=risk_scan.PROMPT_PATH, now=NOW)
            completed = fixture.client.get(fixture.url).json()
            self.assertEqual(completed["risk_scan"]["status"], "completed")
            self.assertEqual(completed["risks"], [])
        finally:
            fixture.doCleanups()

    def test_real_pipeline_runner_scans_before_invoking_translation_cli(self):
        source = engine.SourcePost(
            "facebook", self.root / "archive" / "fa_brand",
            {"post_id": "9", "platform": "facebook", "text": "Caption"},
            NOW, "facebook:9")
        test_config = config.Config()
        test_config._d["paths"] = {
            "archive": str(self.root / "archive"), "state": str(self.root / "state")}
        calls = []

        def scan(*args, **kwargs):
            calls.append(("risk_scan", kwargs["controller"].operation_id))
            return {"status": "completed", "risks": []}

        with patch.object(config, "_cfg", test_config), \
                patch.object(risk_scan, "scan_source", side_effect=scan), \
                patch.object(engine.translation, "main",
                             side_effect=lambda argv: calls.append(("translate", argv)) or 0):
            code = engine.RealStageRunner().translate(source)
        self.assertEqual(code, 0)
        self.assertEqual([item[0] for item in calls], ["risk_scan", "translate"])
        self.assertTrue(calls[0][1])


if __name__ == "__main__":
    unittest.main()

"""Google Trends public CSV export gate; every browser/download is offline-injected."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import hashtag_sampling, trends_export


NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


class TestConfig:
    def __init__(self, root):
        self.state_dir = Path(root)
        self.detect_debug_port = 9224
        self.detect_profile_dir = Path(root) / "detect"

    def assert_chrome_profiles_isolated(self):
        return None


class TrendsExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = TestConfig(self.temp.name)
        self.request = trends_export.ExportRequest.create(
            candidate_group="#Cats", tags=("#Katzen", "#Katzenspielzeug", "#Katzentoilette"),
            geo="DE", start="2026-09-01", end="2026-09-07")
        observation = {
            "method": "passive_accessibility_snapshot",
            "observed_url": self.request.url,
            "control": {"role": "button", "accessible_name": "Download CSV",
                        "exact": True, "count": 1},
        }
        self.proof = trends_export.create_control_proof(self.request, observation, recorded_at=NOW)

    def test_request_is_de_only_and_limits_one_group_to_five_candidates(self):
        self.assertIn("geo=DE", self.request.url)
        self.assertIn("date=2026-09-01%202026-09-07", self.request.url)
        with self.assertRaises(ValueError):
            trends_export.ExportRequest.create(candidate_group="#Cats",
                tags=tuple(f"#Tag{i}" for i in range(6)), geo="DE",
                start="2026-09-01", end="2026-09-07")
        with self.assertRaises(ValueError):
            trends_export.ExportRequest.create(candidate_group="#Cats", tags=("#A", "#B"),
                geo="US", start="2026-09-01", end="2026-09-07")

    def test_proof_requires_one_passively_observed_exact_accessible_control(self):
        bad = {"method": "passive_accessibility_snapshot", "observed_url": self.request.url,
               "control": {"role": "button", "accessible_name": "CSV",
                           "exact": True, "count": 2}}
        with self.assertRaises(ValueError):
            trends_export.create_control_proof(self.request, bad, recorded_at=NOW)
        changed = dict(self.proof)
        changed["control"] = dict(changed["control"], accessible_name="Other")
        with self.assertRaises(ValueError):
            trends_export.validate_control_proof(self.request, changed)

    def test_persistent_429_blocks_before_any_later_runner(self):
        state = {"schema_version": 1, "status": "blocked", "http_status": 429,
                 "reason": "HTTP 429", "blocked_at": NOW.isoformat()}
        trends_export.save_state(self.c, state)
        called = False

        def runner(**kwargs):
            nonlocal called
            called = True
            return {}

        with self.assertRaises(trends_export.TrendsExportBlocked):
            trends_export.export_public_csv(
                self.request, proof=self.proof, c=self.c, browser_runner=runner, now=NOW)
        self.assertFalse(called)

    def test_shared_detect_hard_stop_also_blocks_before_runner(self):
        (self.c.state_dir / "delta_state.json").write_text(json.dumps({
            "detect_hard_blocked": {"reason": "Instagram HTTP 429"}
        }), encoding="utf-8")
        called = False

        def runner(**kwargs):
            nonlocal called
            called = True

        with self.assertRaises(trends_export.TrendsExportBlocked):
            trends_export.export_public_csv(
                self.request, proof=self.proof, c=self.c, browser_runner=runner, now=NOW)
        self.assertFalse(called)
        self.assertEqual(trends_export.load_state(self.c)["status"], "blocked")

    def test_new_429_is_persisted_and_requires_explicit_manual_reset(self):
        def blocked(**kwargs):
            raise trends_export.TrendsExportBlocked("Google Trends HTTP 429", http_status=429)

        with self.assertRaises(trends_export.TrendsExportBlocked):
            trends_export.export_public_csv(
                self.request, proof=self.proof, c=self.c, browser_runner=blocked, now=NOW)
        state = trends_export.load_state(self.c)
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["http_status"], 429)
        recovered = trends_export.reset_block(self.c, reason="operator verified challenge cleared", now=NOW)
        self.assertEqual(recovered["status"], "recovered")

    def test_success_preserves_raw_csv_hash_and_verified_context_for_import(self):
        raw = (b"Week,Katzen,Katzenspielzeug,Katzentoilette\r\n"
               b"2026-09-01,10,20,30\r\n")

        def runner(**kwargs):
            return {"body": raw, "final_url": self.request.url,
                    "download_url": "browser-download", "suggested_filename": "multiTimeline.csv"}

        artifact = trends_export.export_public_csv(
            self.request, proof=self.proof, c=self.c, browser_runner=runner, now=NOW)
        self.assertEqual(Path(artifact["raw_path"]).read_bytes(), raw)
        self.assertEqual(artifact["sha256"], hashlib.sha256(raw).hexdigest())
        rows = hashtag_sampling.import_trends_csv(
            raw.decode(), candidate_group="#Cats", tags=self.request.tags, geo="DE",
            time_range="2026-09-01 2026-09-07", sampled_at=NOW,
            export_context=artifact["export_context"])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["source_sha256"] == artifact["sha256"] for row in rows))

    def test_playwright_uses_only_proved_exact_control_and_unknown_control_fails(self):
        class Page:
            url = self.request.url

            def __init__(page_self):
                page_self.lookups = []
                page_self.listeners = []

            def on(page_self, event, handler):
                page_self.listeners.append((event, handler))

            def remove_listener(page_self, event, handler):
                page_self.listeners.remove((event, handler))

            async def goto(page_self, url, wait_until):
                return SimpleNamespace(status=200)

            def get_by_role(page_self, role, *, name, exact):
                page_self.lookups.append((role, name, exact))
                return SimpleNamespace(count=AsyncMock(return_value=0))

            async def close(page_self):
                return None

        page = Page()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        browser = SimpleNamespace(close=AsyncMock())
        pw = SimpleNamespace(stop=AsyncMock())
        with patch("core.chrome.attach", new=AsyncMock(return_value=(pw, browser, context))):
            with self.assertRaisesRegex(trends_export.TrendsExportUnavailable, "未尝试其他控件"):
                asyncio.run(trends_export._playwright_download(self.request, self.proof, self.c))
        self.assertEqual(page.lookups, [("button", "Download CSV", True)])

    def test_playwright_observes_page_429_before_control_lookup(self):
        class Page:
            url = self.request.url

            def on(page_self, event, handler):
                page_self.handler = handler

            def remove_listener(page_self, event, handler):
                return None

            async def goto(page_self, url, wait_until):
                response = SimpleNamespace(status=429, url=self.request.url)
                page_self.handler(response)
                return response

            def get_by_role(page_self, *args, **kwargs):
                self.fail("blocked page must not inspect controls")

            async def close(page_self):
                return None

        context = SimpleNamespace(new_page=AsyncMock(return_value=Page()))
        browser = SimpleNamespace(close=AsyncMock())
        pw = SimpleNamespace(stop=AsyncMock())
        with patch("core.chrome.attach", new=AsyncMock(return_value=(pw, browser, context))):
            with self.assertRaises(trends_export.TrendsExportBlocked) as caught:
                asyncio.run(trends_export._playwright_download(self.request, self.proof, self.c))
        self.assertEqual(caught.exception.http_status, 429)


if __name__ == "__main__":
    unittest.main()

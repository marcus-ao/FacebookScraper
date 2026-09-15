"""标签公开数据导入仅产生可比较、可追溯的离线观测。"""
from __future__ import annotations

import tempfile
import sys
import json
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import hashtag_rank, hashtag_sampling
from pipeline import hashtag_suggestions
from routes import delta


NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
KEEP = {"brands": [], "models": []}


class HashtagSamplingTests(unittest.TestCase):
    def test_trends_csv_normalizes_one_candidate_group_in_one_batch(self):
        rows = hashtag_sampling.import_trends_csv(
            "Category: All categories\nWeek,#Katzen: (Germany),#Katzenliebe: (Germany),#Hauskatzen: (Germany)\n"
            "2026-08-30,20,40,10\n2026-09-06,40,80,20\n",
            candidate_group="#Cats", tags=("#Katzen", "#Katzenliebe", "#Hauskatzen"),
            geo="DE", time_range="2026-08-30 2026-09-06", sampled_at=NOW)
        self.assertEqual([row["trend_score"] for row in rows], [50.0, 100.0, 25.0])
        self.assertEqual(len({row["sample_batch"] for row in rows}), 1)
        self.assertTrue(all(row["comparison_group"] == "#Cats" for row in rows))

    def test_trends_rejects_missing_candidate_samples_and_mislabeled_dates(self):
        with self.assertRaisesRegex(ValueError, "完整"):
            hashtag_sampling.import_trends_csv(
                "Week,#A: (Germany),#B: (Germany)\n2026-09-01,100,\n2026-09-08,,10\n",
                candidate_group="#X", tags=("#A", "#B"), geo="DE",
                time_range="2026-09-01 2026-09-08", sampled_at=NOW)

    def test_trends_accepts_germany_columns_and_week_month_coverage(self):
        weekly = hashtag_sampling.import_trends_csv(
            "Week,#A: (Germany),#B: (Germany)\n"
            "2026-08-31,20,40\n2026-09-07,40,80\n",
            candidate_group="#X", tags=("#A", "#B"), geo="DE",
            time_range="2026-09-01 2026-09-07", sampled_at=NOW)
        self.assertEqual([row["trend_score"] for row in weekly], [50.0, 100.0])
        monthly = hashtag_sampling.import_trends_csv(
            "Month,#A: (Germany),#B: (Germany)\n2026-08,10,20\n2026-09,20,40\n",
            candidate_group="#X", tags=("#A", "#B"), geo="DE",
            time_range="2026-08-15 2026-09-12", sampled_at=NOW)
        self.assertEqual(len(monthly), 2)
        with self.assertRaisesRegex(ValueError, "地域"):
            hashtag_sampling.import_trends_csv(
                "Day,#A: (United States),#B: (United States)\n2026-09-01,10,20\n",
                candidate_group="#X", tags=("#A", "#B"), geo="DE",
                time_range="2026-09-01 2026-09-01", sampled_at=NOW)
        with self.assertRaisesRegex(ValueError, "时间范围"):
            hashtag_sampling.import_trends_csv(
                "Week,#A: (Germany),#B: (Germany)\n1999-01-01,100,10\n",
                candidate_group="#X", tags=("#A", "#B"), geo="DE",
                time_range="2026-09-01 2026-09-08", sampled_at=NOW)

    def test_trends_from_different_groups_or_batches_are_not_compared(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.jsonl"
            hashtag_rank.append_samples(path, [
                {"tag": "#Katzen", "trend_score": 100, "geo": "DE",
                 "comparison_group": "#Cats", "sample_batch": "batch-a",
                 "time_range": "today 30-d", "sampled_at": NOW.isoformat(),
                 "source": "Google Trends CSV export"},
                {"tag": "#Katzenliebe", "trend_score": 1, "geo": "DE",
                 "comparison_group": "#Pets", "sample_batch": "batch-b",
                 "time_range": "today 30-d", "sampled_at": NOW.isoformat(),
                 "source": "Google Trends CSV export"},
            ])
            result = hashtag_rank.recommend(
                "#Cats", {"#Cats": ["#Katzenliebe", "#Katzen", "#Hauskatzen"]},
                keep_verbatim=KEEP, signals=hashtag_rank.load_signals(path), now=NOW,
                peer_accounts=("peer",))
        self.assertEqual(result["selected"], ["#Katzenliebe"])
        self.assertIn("不可跨批", result["notice"])

    def test_instagram_counts_are_labeled_global_cumulative(self):
        sampled = hashtag_sampling.instagram_global_counts(
            {"#Katzen": {"count": 123456, "url": "https://www.instagram.com/explore/tags/katzen/"}},
            sampled_at=NOW)
        row = sampled["rows"][0]
        self.assertEqual(row["media_count"], 123456)
        self.assertEqual(row["geo"], "global")
        self.assertEqual(row["metric_scope"], "global_cumulative_posts")
        self.assertNotIn("DE", row["source"])

    def test_verified_payload_count_requires_matching_hashtag_identity(self):
        payloads = [{"data": {"hashtag": {"name": "katzen",
                    "edge_hashtag_to_media": {"count": 123456}}}}]
        self.assertEqual(hashtag_sampling.verified_instagram_count(payloads, "#Katzen"), 123456)
        self.assertIsNone(hashtag_sampling.verified_instagram_count(payloads, "#Hunde"))
        self.assertIsNone(hashtag_sampling.verified_instagram_count(
            [{"edge_hashtag_to_media": {"count": 999}}], "#Katzen"))
        self.assertIsNone(hashtag_sampling.verified_instagram_count(
            [{"name": "katzen", "count": 42, "type": "unrelated_object"}], "#Katzen"))
        self.assertIsNone(hashtag_sampling.verified_instagram_count(
            [{"name": "katzen", "media_count": 42}], "#Katzen"))
        self.assertIsNone(hashtag_sampling.verified_instagram_count(
            [{"name": "katzen", "edge_hashtag_to_media": {"count": 42},
              "type": "unrelated_object"}], "#Katzen"))

    def _production_config(self, directory, *, failure_budget=3):
        hashtag_values = {"enabled": True, "peer_accounts": [], "refresh_days": 7,
                          "max_candidate_tags": 12, "max_peer_accounts": 5}

        class TestConfig:
            state_dir = Path(directory)
            detect_debug_port = 9224
            detect_profile_dir = Path(directory) / "detect"

            def get(self, section, key, default=None):
                if section == "hashtags":
                    return hashtag_values.get(key, default)
                if section == "delta" and key == "failure_budget":
                    return failure_budget
                return default

            def __getitem__(self, section):
                if section == "targets":
                    return {"facebook": "neakasaofficial", "instagram": "neakasa.global"}
                raise KeyError(section)

            def assert_chrome_profiles_isolated(self):
                return None

        access = delta.AccessController(Path(directory))
        if not access.path.exists():
            access.initialize("isolated sampler fixture")
        return TestConfig()

    def test_persistent_hard_stop_and_failure_budget_block_before_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self._production_config(directory)
            state_path = Path(directory) / "delta_state.json"
            entry = delta.blank_entry()
            entry.update(account="neakasa.global", consecutive_failures=3)
            state = {"instagram": entry, "detect_hard_blocked": {
                "reason": "HTTP 429", "platform": "instagram", "recorded_at": NOW.isoformat()}}
            delta.save_state(state_path, state)
            access = delta.AccessController(Path(directory))
            access.outcome("instagram", success=False, hard=True, reason="HTTP 429")
            with patch("core.chrome.attach", new_callable=AsyncMock) as attach:
                with self.assertRaises(hashtag_sampling.SamplingUnavailable):
                    hashtag_sampling.collect_browser_observations(tags=("#Katzen",), c=c)
                attach.assert_not_awaited()

            state.pop("detect_hard_blocked")
            delta.save_state(state_path, state)
            access.recover(access.status()["revision"], "fixture profile recovered")
            for _ in range(3):
                access.outcome("instagram", success=False, reason="ordinary fixture")
            with patch("core.chrome.attach", new_callable=AsyncMock) as attach:
                with self.assertRaises(hashtag_sampling.SamplingUnavailable):
                    hashtag_sampling.collect_browser_observations(tags=("#Katzen",), c=c)
                attach.assert_not_awaited()

    def test_429_persists_shared_hard_stop_and_failure_before_next_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self._production_config(directory)
            collector = SimpleNamespace(
                payloads=[], blocked_status=lambda: (429, "https://www.instagram.com/api/graphql"))
            pw = SimpleNamespace(stop=AsyncMock())
            browser = SimpleNamespace(close=AsyncMock())
            with patch("core.chrome.attach", new=AsyncMock(return_value=(pw, browser, object()))) as attach, \
                    patch.object(delta, "scan_page", new=AsyncMock(
                        return_value=(collector, "https://www.instagram.com/explore/tags/katzen/"))):
                result = hashtag_sampling.collect_browser_observations(tags=("#Katzen",), c=c)
                self.assertTrue(result["errors"])
                state = delta.AccessController(Path(directory)).status()
                self.assertEqual(state["platforms"]["instagram"]["failures"], 1)
                self.assertEqual(state["hard_stop"]["platform"], "instagram")
                with self.assertRaises(hashtag_sampling.SamplingUnavailable):
                    hashtag_sampling.collect_browser_observations(tags=("#Katzen",), c=c)
                self.assertEqual(attach.await_count, 1)

    def test_unknown_payload_failures_accumulate_across_sampler_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self._production_config(directory, failure_budget=3)
            collector = SimpleNamespace(
                payloads=[{"data": {"name": "unrelated", "count": 12}}],
                blocked_status=lambda: None)
            pw = SimpleNamespace(stop=AsyncMock())
            browser = SimpleNamespace(close=AsyncMock())
            attach = AsyncMock(return_value=(pw, browser, object()))
            scan = AsyncMock(return_value=(
                collector, "https://www.instagram.com/explore/tags/katzen/"))
            clock = [NOW]
            access = delta.AccessController(Path(directory), clock=lambda: clock[0])
            access.plan_homepage("instagram", NOW)
            with patch("core.chrome.attach", new=attach), patch.object(delta, "scan_page", new=scan), \
                    patch.object(hashtag_sampling, "AccessController", return_value=access):
                for _ in range(3):
                    result = hashtag_sampling.collect_browser_observations(tags=("#Katzen",), c=c)
                    self.assertEqual(result["instagram"], {})
                    clock[0] = datetime.fromisoformat(access.status()["platforms"]["instagram"]["next_due_at"])
                state = access.status()
                self.assertEqual(state["platforms"]["instagram"]["failures"], 3)
                with self.assertRaises(hashtag_sampling.SamplingUnavailable):
                    hashtag_sampling.collect_browser_observations(tags=("#Katzen",), c=c)
                self.assertEqual(attach.await_count, 3)

    def test_sampling_missing_or_exhausted_access_cannot_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self._production_config(directory)
            access = delta.AccessController(Path(directory))
            access.path.unlink()
            with patch('core.chrome.attach', new_callable=AsyncMock) as attach:
                with self.assertRaises(hashtag_sampling.SamplingUnavailable):
                    hashtag_sampling.collect_browser_observations(peers=('peer',), c=c)
                attach.assert_not_awaited()
            status = access.initialize('isolated quota fixture')
            now = delta.utcnow()
            status['platforms']['instagram']['intents'] = [
                dict(kind='homepage', run_kind='sampling', scan_id=str(i), post_id='', at=now.isoformat())
                for i in range(24)]
            status['platforms']['instagram']['next_due_at'] = now.isoformat()
            access.path.write_text(json.dumps(status), encoding='utf-8')
            with patch('core.chrome.attach', new_callable=AsyncMock) as attach:
                with self.assertRaises(hashtag_sampling.SamplingUnavailable):
                    hashtag_sampling.collect_browser_observations(tags=('#Katzen',), c=c)
                attach.assert_not_awaited()

    def test_sampler_debits_shared_homepage_before_scan_and_cannot_run_second_page(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self._production_config(directory)
            access = delta.AccessController(Path(directory))
            calls = []
            async def scan(context, url, config):
                calls.append(url)
                intents = access.status()['platforms']['instagram']['intents']
                self.assertEqual(intents[-1]['scan_id'], config.scan_id)
                self.assertEqual(intents[-1]['run_kind'], 'sampling')
                self.assertIsNotNone(config.access)
                return SimpleNamespace(payloads=[], blocked_status=lambda: None), url
            pw = SimpleNamespace(stop=AsyncMock())
            browser = SimpleNamespace(close=AsyncMock())
            with patch('core.chrome.attach', new=AsyncMock(return_value=(pw, browser, object()))), \
                    patch.object(delta, 'scan_page', new=scan), patch.object(delta, '_pause', new=AsyncMock()), \
                    patch.object(hashtag_sampling, 'verified_instagram_count', return_value=10):
                result = hashtag_sampling.collect_browser_observations(tags=('#Katzen', '#Spielzeug'), c=c)
            self.assertEqual(len(calls), 1)
            self.assertTrue(result['errors'])
            self.assertEqual(len(access.status()['platforms']['instagram']['intents']), 1)

    def test_empty_peer_list_skips_without_calling_fetcher(self):
        called = False

        def fetcher(account):
            nonlocal called
            called = True
            return []

        result = hashtag_sampling.collect_peer_usage([], fetcher=fetcher, sampled_at=NOW)
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(called)

    def test_peer_collection_counts_recent_posts_and_degrades_on_failure(self):
        def fetcher(account):
            if account == "broken":
                raise OSError("browser unavailable")
            return [
                {"created_at": (NOW - timedelta(days=2)).isoformat(),
                 "tags": ["#Katzen", "#Tierpflege"], "url": "https://instagram.com/p/1"},
                {"created_at": (NOW - timedelta(days=20)).isoformat(),
                 "tags": ["#Katzen"], "url": "https://instagram.com/p/old"},
            ]

        sampled = hashtag_sampling.collect_peer_usage(["peer_de"], fetcher=fetcher, sampled_at=NOW)
        self.assertEqual(sampled["status"], "sampled")
        counts = {row["tag"]: row["peer_uses_14d"] for row in sampled["rows"]}
        self.assertEqual(counts, {"#Katzen": 1, "#Tierpflege": 1})
        self.assertTrue(all(row["geo"] == "DE" for row in sampled["rows"]))

        failed = hashtag_sampling.collect_peer_usage(["broken"], fetcher=fetcher, sampled_at=NOW)
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(failed["rows"], [])

    def test_sampling_config_validates_peers_and_production_results_degrade(self):
        c = SimpleNamespace(get=lambda section, key, default=None: {
            "enabled": True, "peer_accounts": ["katzen_de", "tier.pflege"],
            "refresh_days": 7, "max_candidate_tags": 12, "max_peer_accounts": 5,
        }.get(key, default))
        settings = hashtag_sampling.SamplingConfig.load(c)
        self.assertEqual(settings.peer_accounts, ("katzen_de", "tier.pflege"))

        sampled = hashtag_sampling.sample_instagram_tags(
            ("#Katzen",), sampled_at=NOW, c=c,
            browser_runner=lambda **kwargs: {"instagram": {
                "#Katzen": {"count": 42,
                    "url": "https://www.instagram.com/explore/tags/katzen/"}},
                "peer_posts": {}, "errors": []})
        self.assertEqual(sampled[0]["media_count"], 42)
        with self.assertRaises(hashtag_sampling.SamplingUnavailable):
            hashtag_sampling.sample_instagram_tags(
                ("#Katzen",), sampled_at=NOW, c=c,
                browser_runner=lambda **kwargs: {
                    "instagram": {}, "peer_posts": {}, "errors": ["unknown DOM"]})

    def test_weekly_peer_hook_is_due_once_and_writes_real_collector_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            values = {
                "enabled": True, "peer_accounts": ["katzen_de"],
                "refresh_days": 7, "max_candidate_tags": 12, "max_peer_accounts": 5,
            }
            c = SimpleNamespace(
                state_dir=Path(directory),
                get=lambda section, key, default=None: values.get(key, default))
            calls = []

            def runner(**kwargs):
                calls.append(kwargs["peers"])
                return {"instagram": {}, "peer_posts": {"katzen_de": [{
                    "created_at": (NOW - timedelta(days=1)).isoformat(),
                    "tags": ["#Katzen"], "url": "https://instagram.com/p/1",
                }]}, "errors": []}

            self.assertTrue(hashtag_suggestions.weekly_refresh_due(now=NOW, c=c))
            result = hashtag_suggestions.weekly_refresh(now=NOW, c=c, browser_sampler=runner)
            self.assertEqual(result["status"], "sampled")
            self.assertEqual(result["count"], 1)
            self.assertEqual(calls, [("katzen_de",)])
            self.assertFalse(hashtag_suggestions.weekly_refresh_due(now=NOW, c=c))
            self.assertEqual(
                hashtag_suggestions.weekly_refresh(now=NOW, c=c, browser_sampler=runner)["status"],
                "not_due")

    def test_weekly_peer_hook_skips_empty_list_without_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            values = {"enabled": True, "peer_accounts": [], "refresh_days": 7,
                      "max_candidate_tags": 12, "max_peer_accounts": 5}
            c = SimpleNamespace(state_dir=Path(directory),
                get=lambda section, key, default=None: values.get(key, default))
            result = hashtag_suggestions.weekly_refresh(
                now=NOW, c=c, browser_sampler=lambda **kwargs: self.fail("browser called"))
            self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()

"""标签公开数据导入仅产生可比较、可追溯的离线观测。"""
from __future__ import annotations

import tempfile
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import hashtag_rank, hashtag_sampling


NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
KEEP = {"brands": [], "models": []}


class HashtagSamplingTests(unittest.TestCase):
    def test_trends_csv_normalizes_one_candidate_group_in_one_batch(self):
        rows = hashtag_sampling.import_trends_csv(
            "Category: All categories\nWeek,#Katzen,#Katzenliebe,#Hauskatzen\n"
            "2026-08-30,20,40,10\n2026-09-06,40,80,20\n",
            candidate_group="#Cats", tags=("#Katzen", "#Katzenliebe", "#Hauskatzen"),
            geo="DE", time_range="2026-08-30 2026-09-12", sampled_at=NOW)
        self.assertEqual([row["trend_score"] for row in rows], [50.0, 100.0, 25.0])
        self.assertEqual(len({row["sample_batch"] for row in rows}), 1)
        self.assertTrue(all(row["comparison_group"] == "#Cats" for row in rows))

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


if __name__ == "__main__":
    unittest.main()

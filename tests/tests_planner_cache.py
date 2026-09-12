"""Planner cache tests have no browser; readers below return fixed observations."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish.business_suite import RemotePlannerCard, RemoteSlotInventory  # noqa: E402
from publish.journal import PublishOperationLock  # noqa: E402
from publish.planner_cache import read_cache, refresh_cache, inventory_from_cache  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
SLOT = NOW + timedelta(hours=4)


def inventory():
    return RemoteSlotInventory(
        (SLOT,), "America/Los_Angeles", date(2026, 9, 1), date(2026, 9, 30),
        (RemotePlannerCard(SLOT, ("instagram",), (("instagram", "123456"),),
                           "Manual phone post September 12, 2026, 7:00 AM", "fixture-sha"),), True)


class PlannerCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.path = self.state / "planner_cache.json"

    def refresh(self, reader, now=NOW):
        return asyncio.run(refresh_cache(self.path, reader, state_dir=self.state, now=now))

    def test_read_missing_cache_is_read_only_and_does_not_claim_an_empty_calendar(self):
        result = read_cache(self.path, now=NOW)
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(inventory_from_cache(result))
        self.assertFalse(self.state.exists())

    def test_success_retains_all_remote_cards_and_original_observation_time(self):
        reader = AsyncMock(return_value=inventory())
        result = self.refresh(reader)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["advisory_only"])
        self.assertEqual(result["observed_at"], NOW.isoformat())
        self.assertEqual(result["refresh_status"], "refreshed")
        rows = inventory_from_cache(result)
        self.assertEqual(rows.occupied_for_channel("instagram"), (SLOT,))
        self.assertEqual(rows.occupied_for_channel("facebook"), ())
        self.assertIn("Manual phone post", rows.cards[0].rendered)
        self.assertEqual(rows.cards[0].remote_ids, (("instagram", "123456"),))
        later = read_cache(self.path, now=NOW + timedelta(minutes=30))
        self.assertEqual(later["age_seconds"], 1800)
        self.assertEqual(later["observed_at"], result["observed_at"])

    def test_publishing_busy_skips_reader_without_reentrant_background_access(self):
        self.refresh(AsyncMock(return_value=inventory()))
        before = self.path.read_bytes()
        reader = AsyncMock(side_effect=AssertionError("browser should not be used"))
        with PublishOperationLock(self.state / "publish.lock"):
            result = self.refresh(reader, NOW + timedelta(hours=1))
        reader.assert_not_called()
        self.assertEqual(result["refresh_status"], "busy")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failure_preserves_success_time_and_data_but_exposes_refresh_error(self):
        self.refresh(AsyncMock(return_value=inventory()))
        result = self.refresh(AsyncMock(side_effect=TimeoutError("fixture failure")),
                              NOW + timedelta(hours=2))
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["observed_at"], NOW.isoformat())
        self.assertEqual(result["age_seconds"], 7200)
        self.assertEqual(result["last_attempt_at"], (NOW + timedelta(hours=2)).isoformat())
        self.assertEqual(result["refresh_status"], "failed")
        self.assertEqual(result["refresh_error"], "timeout")
        self.assertEqual(inventory_from_cache(result).cards, inventory().cards)
        self.assertNotIn("fixture failure", self.path.read_text())

    def test_first_failure_has_no_calendar_not_an_empty_success(self):
        result = self.refresh(AsyncMock(side_effect=RuntimeError("fixture")))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["observed_at"])
        self.assertIsNone(inventory_from_cache(result))

    def test_success_timestamp_is_read_completion_not_attempt_start(self):
        completed = NOW + timedelta(minutes=2)
        clock = iter((NOW, completed, completed))
        result = asyncio.run(refresh_cache(self.path, AsyncMock(return_value=inventory()),
                                          state_dir=self.state, clock=lambda: next(clock)))
        self.assertEqual(result["last_attempt_at"], NOW.isoformat())
        self.assertEqual(result["observed_at"], completed.isoformat())
        self.assertEqual(result["age_seconds"], 0)

    def test_reader_runs_under_the_shared_publish_lock_and_lock_releases_on_failure(self):
        async def reader():
            with self.assertRaises(RuntimeError):
                with PublishOperationLock(self.state / "publish.lock"):
                    self.fail("background did not hold publishing lock")
            raise RuntimeError("failed read")
        self.refresh(reader)
        with PublishOperationLock(self.state / "publish.lock"):
            pass

    def test_unknown_channels_are_displayable_but_not_marked_complete(self):
        rows = RemoteSlotInventory((SLOT,), "America/Los_Angeles", date(2026, 9, 1),
                                   date(2026, 9, 30), (RemotePlannerCard(SLOT),), True)
        result = self.refresh(AsyncMock(return_value=rows))
        self.assertEqual(result["status"], "partial")
        self.assertFalse(inventory_from_cache(result).channels_complete)

    def test_unproven_month_does_not_replace_last_full_calendar(self):
        self.refresh(AsyncMock(return_value=inventory()))
        incomplete = RemoteSlotInventory((), "America/Los_Angeles", cards_loaded=True)
        result = self.refresh(AsyncMock(return_value=incomplete), NOW + timedelta(minutes=1))
        self.assertEqual(result["refresh_status"], "failed")
        self.assertEqual(result["refresh_error"], "coverage_unavailable")
        self.assertEqual(result["observed_at"], NOW.isoformat())
        self.assertEqual(len(inventory_from_cache(result).cards), 1)

    def test_corrupt_cache_is_preserved_until_a_successful_fresh_read(self):
        self.state.mkdir()
        self.path.write_text("not-json", encoding="utf-8")
        self.assertEqual(read_cache(self.path, now=NOW)["status"], "unavailable")
        self.refresh(AsyncMock(side_effect=TimeoutError()))
        self.assertEqual(self.path.read_text(), "not-json")
        self.assertEqual(self.refresh(AsyncMock(return_value=inventory()))["status"], "ready")

    def test_clock_skew_never_claims_fresh_data_and_hardlink_is_not_read_or_overwritten(self):
        self.refresh(AsyncMock(return_value=inventory()))
        self.assertEqual(read_cache(self.path, now=NOW - timedelta(minutes=1))["status"], "clock_skew")
        external = Path(self.temp.name) / "outside.json"
        self.path.unlink()
        external.write_text(json.dumps({"secret": "must remain unchanged"}))
        os.link(external, self.path)
        reader = AsyncMock(return_value=inventory())
        result = self.refresh(reader)
        self.assertEqual(result["refresh_status"], "failed")
        reader.assert_not_called()
        self.assertEqual(json.loads(external.read_text()), {"secret": "must remain unchanged"})


if __name__ == "__main__":
    unittest.main()

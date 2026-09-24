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
from publish.month_inventory import PlannerItemError  # noqa: E402
from publish.planner_cache import read_cache, refresh_cache, inventory_from_cache  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
SLOT = NOW + timedelta(hours=4)


def inventory():
    return RemoteSlotInventory(
        (SLOT,), "America/Los_Angeles", date(2026, 9, 1), date(2026, 9, 30),
        (RemotePlannerCard(SLOT, ("instagram",), (("instagram", "123456"),),
                           "Manual phone post September 12, 2026, 7:00 AM", "fixture-sha",
                           placement='feed', time_verified=True),), True)


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

    def test_a_month_whose_details_are_unreadable_is_still_the_stored_sync(self):
        """整月网格读全了，就是一次真实同步——逐条明细读不出来不作废这个月。

        旧行为把它降级成第二层 partial_inventory，观测时间永不前进，页面于是一直
        说「数据可能已过期 / 不可判断空档」，而那个月的占用其实是完整的。
        """
        self.refresh(AsyncMock(return_value=inventory()))
        unread = RemoteSlotInventory((SLOT,), 'America/Los_Angeles', date(2026,9,1), date(2026,9,30),
            (RemotePlannerCard(SLOT, placement='story', read_status='incomplete',
                               time_verified=False, diagnostic_index=0),), True,
            ({'code':'identity_unverified','date':'2026-09-12','time':'7:00 AM','stage':'published_detail'},))
        result = self.refresh(AsyncMock(return_value=unread), NOW + timedelta(minutes=1))
        self.assertEqual(result['refresh_status'], 'refreshed')
        self.assertIsNone(result['refresh_error'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['observed_at'], (NOW + timedelta(minutes=1)).isoformat())
        stored = inventory_from_cache(result)
        self.assertEqual(stored.cards[0].read_status, 'incomplete')
        self.assertFalse(stored.cards[0].time_verified)
        # 那一刻仍然被占着，只是没人能说它属于哪个渠道，也不能据此确认空档。
        self.assertEqual(stored.unverified_moments(), (SLOT,))
        self.assertEqual(stored.occupied_for_channel('instagram'), ())
        self.assertFalse(stored.decision_complete)
        self.assertEqual(result['refresh_diagnostic']['code'], 'identity_unverified')
        self.assertNotIn('partial_inventory', result)

    def test_a_legacy_partial_layer_on_disk_is_dropped_instead_of_shown(self):
        """服务机现有缓存里还留着上一版的第二层，它不能再盖住这次的读取。"""
        self.refresh(AsyncMock(return_value=inventory()))
        data = json.loads(self.path.read_text('utf-8'))
        data['partial_inventory'] = data['inventory']
        data['partial_observed_at'] = (NOW - timedelta(days=3)).isoformat()
        self.path.write_text(json.dumps(data), encoding='utf-8')
        snapshot = read_cache(self.path, now=NOW)
        self.assertEqual(snapshot['status'], 'ready')
        self.assertNotIn('partial_inventory', snapshot)
        self.assertNotIn('partial_observed_at', snapshot)
        self.refresh(AsyncMock(return_value=inventory()), NOW + timedelta(minutes=1))
        self.assertNotIn('partial_inventory', json.loads(self.path.read_text('utf-8')))

    def test_first_read_with_an_unreadable_item_still_becomes_a_calendar(self):
        rows = RemoteSlotInventory((SLOT,), 'America/Los_Angeles', date(2026,9,1),date(2026,9,30),
            (RemotePlannerCard(SLOT, placement='unknown', read_status='unsupported'),),True)
        result = self.refresh(AsyncMock(return_value=rows))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['observed_at'], NOW.isoformat())
        stored = inventory_from_cache(result)
        self.assertTrue(stored.cards_loaded)
        self.assertEqual(stored.unverified_moments(), (SLOT,))

    def test_item_failure_retains_safe_location_and_clears_it_after_success(self):
        self.refresh(AsyncMock(return_value=inventory()))
        failure = PlannerItemError({'date': date(2026, 9, 30)},
            {'index': 0, 'time': '5:30 PM', 'text': 'private body',
             'href': 'https://example.test/?token=secret', 'labels': ['private caption']}, 'item_ready')
        result = self.refresh(AsyncMock(side_effect=failure), NOW + timedelta(minutes=1))
        self.assertEqual(result['refresh_diagnostic']['date'], '2026-09-30')
        self.assertEqual(result['refresh_diagnostic']['time'], '5:30 PM')
        self.assertEqual(result['refresh_diagnostic']['stage'], 'item_ready')
        self.assertEqual(result['observed_at'], NOW.isoformat())
        self.assertEqual(inventory_from_cache(result).cards, inventory().cards)
        for private in ('private body', 'private caption', 'token=secret'):
            self.assertNotIn(private, self.path.read_text())
        result = self.refresh(AsyncMock(return_value=inventory()), NOW + timedelta(minutes=2))
        self.assertIsNone(result['refresh_diagnostic'])

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
        self.assertEqual(result["status"], "ready")
        stored = inventory_from_cache(result)
        self.assertTrue(stored.occupancy_complete)
        self.assertFalse(stored.channels_complete)
        self.assertFalse(stored.decision_complete)

    def test_legacy_cache_is_displayable_but_does_not_prove_classified_inventory(self):
        self.refresh(AsyncMock(return_value=inventory()))
        data = json.loads(self.path.read_text('utf-8'))
        for card in data['inventory']['cards']:
            for field in ('placement','media_kind','caption_status','read_status','time_verified'):
                card.pop(field)
        self.path.write_text(json.dumps(data),encoding='utf-8')
        snapshot = read_cache(self.path,now=NOW)
        # status 只讲新鲜度；「分类过没有」是卡片自己的事，两者不能再混。
        self.assertEqual(snapshot['status'],'ready')
        loaded = inventory_from_cache(snapshot)
        self.assertEqual(loaded.cards[0].rendered,inventory().cards[0].rendered)
        self.assertFalse(loaded.cards[0].time_verified)
        self.assertFalse(loaded.decision_complete)

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


    def test_public_permalink_roundtrips_and_a_bad_host_or_missing_field_stays_safe(self):
        link = 'https://www.instagram.com/p/AbCdEf'
        card = RemotePlannerCard(SLOT, ('instagram',), (('instagram', '123456'),), 'caption', 'sha',
                                 placement='feed', time_verified=True, permalinks=(('instagram', link),))
        stored = RemoteSlotInventory((SLOT,), 'America/Los_Angeles', date(2026, 9, 1), date(2026, 9, 30),
                                     (card,), True)
        result = self.refresh(AsyncMock(return_value=stored))
        self.assertEqual(inventory_from_cache(result).cards[0].permalinks, (('instagram', link),))
        data = json.loads(self.path.read_text(encoding='utf-8'))
        del data['inventory']['cards'][0]['permalinks']
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(inventory_from_cache(read_cache(self.path, now=NOW)).cards[0].permalinks, ())
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['inventory']['cards'][0]['permalinks'] = {'instagram': 'https://evil.example/p/1'}
        with self.assertRaises(ValueError):
            inventory_from_cache(data)
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(read_cache(self.path, now=NOW)['status'], 'unavailable')


if __name__ == "__main__":
    unittest.main()

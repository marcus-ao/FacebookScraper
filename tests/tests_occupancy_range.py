"""Incomplete details may be excluded only with independent time evidence."""
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish import business_suite as bs, month_inventory, month_readback, planner_cache, planning, workflow
from publish.compose import ScheduleWindow

WHEN = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
WINDOW = ScheduleWindow('', timedelta(0), None, 'UTC')


def inventory(*cards, diagnostics=()):
    return bs.RemoteSlotInventory(tuple(c.at for c in cards), 'UTC', date(2026, 8, 30),
                                  date(2026, 10, 3), tuple(cards), True, diagnostics)


def card(at=WHEN, channels=('facebook',), **kw):
    return bs.RemotePlannerCard(at, channels, time_verified=True, **kw)


class OccupancyRangeTests(unittest.IsolatedAsyncioTestCase):
    def decision(self, rows, at=WHEN):
        return planning.evaluate_slot(at, 'facebook', rows, now=WHEN-timedelta(days=1), window=WINDOW)

    def test_partial_outside_range_does_not_block_but_relevant_unknown_does(self):
        partial = card(WHEN+timedelta(days=1), channels=(), read_status='incomplete', diagnostic_index=0)
        rows = inventory(partial, diagnostics=({'code': 'missing_fields'},))
        self.assertFalse(rows.decision_complete)
        self.assertTrue(self.decision(rows).allowed)
        self.assertFalse(self.decision(inventory(replace(partial, at=WHEN), diagnostics=rows.diagnostics)).allowed)
        self.assertFalse(self.decision(inventory(replace(partial, time_verified=False), diagnostics=rows.diagnostics)).allowed)
        self.assertFalse(self.decision(inventory(diagnostics=rows.diagnostics)).allowed)

    def test_missing_caption_or_placement_still_occupies_at_59_but_not_60_seconds(self):
        partial = card(read_status='incomplete', caption_status='unknown')
        for seconds, allowed in ((59, False), (60, True)):
            self.assertEqual(self.decision(inventory(partial), WHEN+timedelta(seconds=seconds)).allowed, allowed)
        self.assertTrue(planning.evaluate_slot(WHEN, 'instagram', inventory(partial),
            now=WHEN-timedelta(days=1), window=WINDOW).allowed)
        self.assertTrue(self.decision(inventory(replace(partial, channels=())), WHEN+timedelta(minutes=1)).allowed)

    def test_suggestions_apply_same_range_rule_at_each_candidate(self):
        unknown = card(WHEN+timedelta(minutes=2), (), read_status='incomplete')
        rows = inventory(card(), unknown)
        decision = self.decision(rows)
        self.assertEqual(decision.reason, 'conflict')
        self.assertEqual(len(decision.suggestions), 3)
        for at in decision.suggestions:
            self.assertTrue(self.decision(rows, at).allowed)
            self.assertGreaterEqual(abs((at-unknown.at).total_seconds()), 60)

    def test_cache_roundtrip_preserves_proof_and_legacy_cache_does_not_invent_it(self):
        rows = inventory(card(WHEN+timedelta(days=1), read_status='incomplete', diagnostic_index=0),
                         diagnostics=({'code':'missing_fields'},))
        raw = planner_cache._serialize(rows)
        self.assertTrue(self.decision(planner_cache.inventory_from_cache({'inventory':raw})).allowed)
        del raw['cards'][0]['time_verified']
        self.assertFalse(self.decision(planner_cache.inventory_from_cache({'inventory':raw})).allowed)

    def test_readback_does_not_turn_incomplete_target_into_zero_baseline(self):
        partial = card(read_status='incomplete')
        with self.assertRaises(bs.PublishStepError):
            month_readback.baseline_from_inventory(inventory(partial), WHEN, 'caption', ('facebook',))
        outside = replace(partial, at=WHEN+timedelta(days=1))
        self.assertEqual(month_readback.baseline_from_inventory(inventory(outside), WHEN,
                         'caption', ('facebook',)).match_count, 0)

    async def test_partial_aggregate_never_borrows_outer_time_for_unknown_channel(self):
        outer = WHEN+timedelta(days=1)
        row = {'date':outer.date(), 'items':[{'time':'12:00 PM','index':0}]}
        error = month_inventory.PlannerItemError(row, row['items'][0], 'published_detail')
        error.variants = ({'ui_at':outer, 'channels':('facebook',), 'remote_ids':{'facebook':'123456'},
                           'text':'known', 'placement':'feed','delivery':'published'},)
        with patch.object(month_inventory, 'prepare', AsyncMock()), \
                patch.object(month_inventory, 'read_grid', AsyncMock(return_value=[row])), \
                patch.object(month_inventory, 'read_item', AsyncMock(side_effect=error)):
            rows = await month_inventory.read(None, ui_timezone='UTC', business_timezone='UTC')
        rows = replace(rows, visible_start=date(2026,9,1), visible_end=date(2026,9,30))
        self.assertTrue(rows.cards[0].time_verified)
        self.assertFalse(rows.cards[1].time_verified)
        self.assertFalse(self.decision(rows).allowed)

    async def test_live_check_sees_new_occupancy_after_empty_opening_inventory(self):
        from types import SimpleNamespace
        self.assertTrue(self.decision(inventory()).allowed)
        with patch.object(month_inventory, 'read', AsyncMock(return_value=inventory(card()))) as read, \
                patch.object(planning, 'config_window', return_value=WINDOW):
            with self.assertRaisesRegex(bs.PublishStepError, '冲突'):
                await workflow.check_live_slot(None, SimpleNamespace(platform='facebook'), WHEN,
                    ui_timezone='UTC', timeout=1, run=object(), now=WHEN-timedelta(days=1))
        self.assertEqual(read.await_count, 1)


if __name__ == '__main__':
    unittest.main()

"""Scheduling policy is offline; all Planner observations below are fixtures."""
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish.business_suite import RemotePlannerCard, RemoteSlotInventory, ProbeRequired  # noqa: E402
from publish.compose import ComposeError, ScheduleWindow  # noqa: E402
from publish.planning import calendar_bounds, evaluate_slot, configured_window  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
UI = "America/Los_Angeles"
NOW = datetime(2026, 9, 12, 10, tzinfo=BERLIN)
WINDOW = ScheduleWindow("offline-fixture", timedelta(minutes=20), timedelta(days=30), UI)


def inventory(*entries, start=date(2026, 9, 1), end=date(2026, 9, 30)):
    cards = tuple(RemotePlannerCard(at=at, channels=channels, rendered="fixture", placement="feed", time_verified=True)
                  for at, channels in entries)
    return RemoteSlotInventory(tuple(card.at for card in cards), UI, start, end,
                               cards=cards, cards_loaded=True)


class PlanningTests(unittest.TestCase):
    def test_unmeasured_platform_window_uses_beijing_month_without_inventing_limits(self):
        now = datetime.fromisoformat('2026-09-01T10:00:00+08:00')
        window = ScheduleWindow('accepted-probe', timedelta(0), None, 'Asia/Shanghai')
        rows = RemoteSlotInventory((), 'Asia/Shanghai', date(2026, 9, 1), date(2026, 9, 30),
                                   cards=(), cards_loaded=True)
        bounds = calendar_bounds(now, window=window)
        self.assertEqual(bounds.earliest, now)
        self.assertEqual(bounds.end_exclusive, datetime.fromisoformat('2026-10-01T00:00:00+08:00'))
        for target in (now + timedelta(minutes=1), now + timedelta(days=29, hours=5)):
            self.assertTrue(evaluate_slot(target, 'facebook', rows, now=now, window=window).allowed)
        self.assertFalse(evaluate_slot(now, 'facebook', rows, now=now, window=window).allowed)

        target = now + timedelta(minutes=90)
        occupied = RemotePlannerCard(at=target, channels=('facebook',), rendered='fixture', placement='feed', time_verified=True)
        rows = RemoteSlotInventory((target,), 'Asia/Shanghai', date(2026, 9, 1), date(2026, 9, 30),
                                   cards=(occupied,), cards_loaded=True)
        result = evaluate_slot(target, 'facebook', rows, now=now, window=window)
        self.assertEqual(len(result.suggestions), 3)
        for suggestion in result.suggestions:
            self.assertTrue(evaluate_slot(suggestion, 'facebook', rows, now=now, window=window).allowed)

    def test_configured_window_reuses_verified_local_probe_and_preserves_missing_evidence(self):
        with patch("publish.planning.verified_constraints_from_config", return_value=(None, WINDOW)) as load:
            self.assertEqual(configured_window("facebook"), WINDOW)
            load.assert_called_once_with("facebook")
        with patch("publish.planning.verified_constraints_from_config", side_effect=ComposeError("missing fixture probe")):
            with self.assertRaisesRegex(ComposeError, "missing fixture probe"):
                configured_window("instagram")

    def test_another_caption_conflicts_only_with_its_proven_channel(self):
        target = NOW + timedelta(hours=4)
        rows = inventory((target - timedelta(minutes=5), ("facebook",)))
        fb = evaluate_slot(target, "facebook", rows, now=NOW, window=WINDOW)
        self.assertFalse(fb.allowed)
        self.assertEqual(fb.reason, "conflict")
        self.assertEqual(fb.conflicts, (target - timedelta(minutes=5),))
        self.assertTrue(evaluate_slot(target, "instagram", rows, now=NOW, window=WINDOW).allowed)

    def test_exact_gap_boundary_is_allowed_and_59_seconds_less_is_not(self):
        occupied = NOW + timedelta(hours=4)
        rows = inventory((occupied, ("facebook",)))
        self.assertTrue(evaluate_slot(occupied + timedelta(minutes=90), "facebook", rows,
                                     now=NOW, window=WINDOW).allowed)
        self.assertFalse(evaluate_slot(occupied + timedelta(minutes=89, seconds=1), "facebook", rows,
                                      now=NOW, window=WINDOW).allowed)

    def test_manual_conflict_keeps_requested_time_and_offers_three_distinct_options(self):
        target = NOW + timedelta(hours=4)
        rows = inventory((target, ("facebook",)))
        result = evaluate_slot(target, "facebook", rows, now=NOW, window=WINDOW)
        self.assertEqual(target, NOW + timedelta(hours=4))
        self.assertEqual(len(result.suggestions), 3)
        self.assertEqual(result.suggestions[0], target + timedelta(minutes=90))
        for suggestion in result.suggestions:
            self.assertTrue(evaluate_slot(suggestion, "facebook", rows, now=NOW, window=WINDOW).allowed)
        self.assertTrue(all(abs((a-b).total_seconds()) >= 5400
                            for i, a in enumerate(result.suggestions) for b in result.suggestions[i+1:]))

    def test_missing_channel_evidence_never_means_empty(self):
        target = NOW + timedelta(hours=4)
        rows = inventory((target, ()))
        with self.assertRaises(ProbeRequired):
            rows.occupied_for_channel("facebook")
        result = evaluate_slot(target, "facebook", rows, now=NOW, window=WINDOW)
        self.assertEqual(result.reason, "channels_unavailable")
        self.assertFalse(result.allowed)
        self.assertEqual(result.suggestions, ())

    def test_visible_month_must_cover_the_whole_conflict_window(self):
        end = datetime(2026, 10, 1, 8, tzinfo=BERLIN)  # LA September 30 23:00
        result = evaluate_slot(end, "facebook", inventory(), now=NOW, window=WINDOW)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "calendar_incomplete")

    def test_berlin_next_month_can_still_be_current_ui_month(self):
        valid = datetime(2026, 10, 1, 6, tzinfo=BERLIN)
        invalid = datetime(2026, 10, 1, 10, tzinfo=BERLIN)
        self.assertTrue(evaluate_slot(valid, "facebook", inventory(), now=NOW, window=WINDOW).allowed)
        self.assertEqual(evaluate_slot(invalid, "facebook", inventory(), now=NOW,
                                      window=WINDOW).reason, "outside_ui_month")
        bounds = calendar_bounds(NOW, window=WINDOW)
        self.assertEqual(bounds.end_exclusive.astimezone(BERLIN), datetime(2026, 10, 1, 9, tzinfo=BERLIN))

    def test_dst_differences_use_zoneinfo_in_both_directions(self):
        for berlin, ui in (("2026-03-15T10:00:00+01:00", "2026-03-15T02:00:00-07:00"),
                           ("2026-10-28T10:00:00+01:00", "2026-10-28T02:00:00-07:00")):
            left, right = datetime.fromisoformat(berlin), datetime.fromisoformat(ui)
            self.assertEqual(left.astimezone(ZoneInfo(UI)), right)
            self.assertEqual(right.astimezone(BERLIN), left)
        november = datetime(2026, 11, 1, 8, tzinfo=BERLIN)
        bounds = calendar_bounds(november, window=WINDOW)
        self.assertEqual(bounds.end_exclusive.astimezone(BERLIN), datetime(2026, 12, 1, 9, tzinfo=BERLIN))

    def test_ambiguous_ui_hour_and_nonexistent_local_time_are_rejected(self):
        now = datetime(2026, 11, 1, 8, tzinfo=BERLIN)
        rows = inventory(start=date(2026, 11, 1), end=date(2026, 11, 30))
        repeated = datetime(2026, 11, 1, 10, tzinfo=BERLIN)
        self.assertEqual(evaluate_slot(repeated, "facebook", rows, now=now,
                                      window=WINDOW).reason, "ambiguous_ui_time")
        with self.assertRaises(ValueError):
            evaluate_slot(datetime(2026, 3, 29, 2, 30, tzinfo=BERLIN), "facebook", rows,
                          now=NOW, window=WINDOW)
        with self.assertRaises(ValueError):
            evaluate_slot(datetime(2026, 9, 12, 14), "facebook", rows, now=NOW, window=WINDOW)

    def test_window_and_configured_gap_are_consumed(self):
        target = NOW + timedelta(hours=4)
        rows = inventory((target - timedelta(minutes=60), ("facebook",)))
        with patch("publish.planning.cfg") as configuration:
            configuration.return_value.get.return_value = 45
            self.assertTrue(evaluate_slot(target, "facebook", rows, now=NOW, window=WINDOW).allowed)
            configuration.return_value.get.assert_called_with("publish", "min_channel_gap_min", 90)
        self.assertEqual(evaluate_slot(NOW + timedelta(minutes=5), "facebook", inventory(),
                                      now=NOW, window=WINDOW).reason, "outside_window")
        for invalid in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                evaluate_slot(target, "facebook", rows, now=NOW, window=WINDOW, gap_minutes=invalid)

    def test_gap_uses_absolute_time_across_berlin_fallback(self):
        early = datetime(2026, 10, 25, 2, 15, tzinfo=BERLIN, fold=0)
        later = datetime(2026, 10, 25, 2, 45, tzinfo=BERLIN, fold=1)
        now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        rows = inventory((early, ("facebook",)), start=date(2026, 10, 1), end=date(2026, 10, 31))
        self.assertTrue(evaluate_slot(later, "facebook", rows, now=now, window=WINDOW).allowed)


if __name__ == "__main__":
    unittest.main()

"""Calendar routes operate only on temporary caches and injected browser readers."""
import asyncio
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config  # noqa: E402
from publish.business_suite import ProbeRequired, RemotePlannerCard, RemoteSlotInventory  # noqa: E402
from publish.compose import ComposeError, ScheduleWindow  # noqa: E402
from publish.journal import PublishOperationLock  # noqa: E402
from publish import planner_cache  # noqa: E402
from web.api import calendar  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
WINDOW = ScheduleWindow("fixture", timedelta(minutes=20), timedelta(days=30), "America/Los_Angeles")
SLOT = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)  # Berlin 06:00, LA Sep 30 21:00
ROWS = RemoteSlotInventory((SLOT,), WINDOW.ui_timezone, date(2026, 9, 1), date(2026, 9, 30),
                          (RemotePlannerCard(SLOT, ("facebook",), (("facebook", "123456"),), "Manual post"),), True)


class CalendarApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.path = self.state / "planner_cache.json"
        config = Config()
        config._d["paths"]["state"] = str(self.state)
        for name, value in (("web.api.calendar.cfg", config),
                            ("web.api.calendar.current_time", NOW),
                            ("web.api.calendar.configured_window", WINDOW),
                            ("web.api.calendar.bs.require_readback_evidence", object())):
            mocked = patch(name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        app = FastAPI()
        app.include_router(calendar.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def populate(self):
        asyncio.run(planner_cache.refresh_cache(self.path, AsyncMock(return_value=ROWS),
                                               state_dir=self.state, now=NOW))

    def test_missing_is_explicit_and_get_does_not_create_state_or_open_browser(self):
        with patch("web.api.calendar.read_live_inventory", AsyncMock(side_effect=AssertionError("browser"))):
            result = self.client.get("/api/calendar")
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data["status"], "missing")
        self.assertIsNone(data["cached_at"])
        self.assertEqual(data["cards"], [])
        self.assertTrue(data["bounds"]["facebook"]["available"])
        self.assertEqual(data["bounds"]["facebook"]["end_exclusive"], "2026-10-01T07:00:00+00:00")
        self.assertFalse(self.state.exists())

    def test_calendar_keeps_berlin_next_month_card_inside_la_current_month(self):
        self.populate()
        data = self.client.get("/api/calendar").json()
        self.assertEqual(data["month_ui"], "2026-09")
        self.assertEqual(data["cards"][0]["at_business"], "2026-10-01T06:00:00+02:00")
        self.assertEqual(data["cards"][0]["channels"], ["facebook"])
        self.assertEqual(data["cached_at"], NOW.isoformat())
        self.assertEqual(data["coverage"]["visible_start"], "2026-09-01")
        self.assertTrue(data["coverage"]["matches_current_month"])
        self.assertTrue(data["advisory_only"])

    def test_missing_probe_disables_picker_and_refresh_without_changing_old_cache(self):
        self.populate()
        before = self.path.read_bytes()
        with patch("web.api.calendar.configured_window", side_effect=ComposeError("missing local probe")), \
                patch("web.api.calendar.bs.require_readback_evidence", side_effect=ProbeRequired("missing readback")), \
                patch("web.api.calendar.read_live_inventory", AsyncMock()) as reader:
            data = self.client.get("/api/calendar").json()
            result = self.client.post("/api/calendar/refresh")
        self.assertFalse(data["bounds"]["facebook"]["available"])
        self.assertFalse(data["refresh_available"])
        self.assertEqual(result.status_code, 409)
        self.assertIn("missing readback", result.json()["detail"])
        self.assertEqual(self.path.read_bytes(), before)
        reader.assert_not_called()

    def test_busy_refresh_returns_old_data_without_touching_browser(self):
        self.populate()
        with PublishOperationLock(self.state / "publish.lock"), \
                patch("web.api.calendar.read_live_inventory", AsyncMock()) as reader:
            result = self.client.post("/api/calendar/refresh")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["cached_at"], NOW.isoformat())
        reader.assert_not_called()

    def test_failed_refresh_is_visible_and_does_not_refresh_old_data_timestamp(self):
        self.populate()
        later = NOW + timedelta(hours=2)
        with patch("web.api.calendar.current_time", return_value=later), \
                patch("web.api.calendar.read_live_inventory", AsyncMock(side_effect=TimeoutError("private failure"))):
            response = self.client.post("/api/calendar/refresh")
            reread = self.client.get("/api/calendar").json()
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.json()["stale"])
        self.assertEqual(response.json()["cached_at"], NOW.isoformat())
        self.assertTrue(reread["error"])
        self.assertNotIn("private failure", response.text)

    def test_live_reader_requires_evidence_before_attaching_and_closes_own_page(self):
        with patch("publish.planner_cache.bs.require_readback_evidence", side_effect=ProbeRequired("missing")), \
                patch("publish.planner_cache.attach", AsyncMock()) as attach:
            with self.assertRaises(ProbeRequired):
                asyncio.run(planner_cache.read_live_inventory())
            attach.assert_not_called()
        page, context, pw = AsyncMock(), AsyncMock(), AsyncMock()
        context.new_page.return_value = page
        with patch("publish.planner_cache.bs.require_readback_evidence", return_value=object()), \
                patch("publish.planner_cache.attach", AsyncMock(return_value=(pw, object(), context))), \
                patch("publish.planner_cache.bs.read_remote_slot_inventory", AsyncMock(return_value=ROWS)) as read:
            self.assertEqual(asyncio.run(planner_cache.read_live_inventory()), ROWS)
        self.assertTrue(read.call_args.kwargs["include_cards"])
        page.close.assert_awaited_once()
        pw.stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

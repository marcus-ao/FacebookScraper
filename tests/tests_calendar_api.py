"""Calendar routes operate only on temporary caches and injected browser readers."""
import asyncio
import sqlite3
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
from publish.journal import PublishAttempt, PublishOperationLock, append as append_attempt  # noqa: E402
from publish import planner_cache  # noqa: E402
from publish.month_inventory import PlannerItemError  # noqa: E402
from publish.planner_content import DetailReadError  # noqa: E402
from web.api import calendar  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
WINDOW = ScheduleWindow("fixture", timedelta(minutes=20), timedelta(days=30), "America/Los_Angeles")
SLOT = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)  # Berlin 06:00, LA Sep 30 21:00
ROWS = RemoteSlotInventory((SLOT,), WINDOW.ui_timezone, date(2026, 9, 1), date(2026, 9, 30),
                          (RemotePlannerCard(SLOT, ("facebook",), (("facebook", "123456"),), "Manual post",
                                             placement="feed", time_verified=True),), True)


class CalendarApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.path = self.state / "planner_cache.json"
        config = Config()
        self.config = config
        config._d["paths"]["state"] = str(self.state)
        config._d["publish"]["ui_timezone"] = WINDOW.ui_timezone
        for name, value in (("web.api.calendar.cfg", config),
                            ("web.api.calendar.current_time", NOW),
                            ("web.api.calendar.configured_window", WINDOW),
                            ("web.api.calendar.bs.require_readback_evidence", object())):
            mocked = patch(name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        app = FastAPI()
        app.include_router(calendar.router)
        self.client = TestClient(app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 41000))
        self.addCleanup(self.client.close)

    def populate(self):
        asyncio.run(planner_cache.refresh_cache(self.path, AsyncMock(return_value=ROWS),
                                               state_dir=self.state, now=NOW))

    def test_local_layer_is_drawn_beside_the_remote_one_but_never_judges_slots(self):
        """⛔ 本地记录只画给人看；拿它证明自己没冲突，等于不检查。"""
        self.populate()
        frozen = {"kind": "content_locked", "task_id": "fa_x/1", "platform": "facebook",
                  "review_status": "content_locked", "at": None, "snapshot_id": "a" * 32,
                  "remote_id": ""}
        submitting = {**frozen, "kind": "submitting", "task_id": "fa_x/2",
                      "review_status": "approved", "at": SLOT.isoformat()}
        with patch.object(calendar.local_schedule, "entries",
                          return_value=[frozen, submitting]):
            data = self.client.get("/api/calendar").json()
        self.assertEqual([item["kind"] for item in data["local"]],
                         ["content_locked", "submitting"])
        self.assertIsNone(data["local"][0]["at_business"])
        self.assertEqual(data["local"][1]["at_business"], "2026-10-01T12:00:00+08:00")
        self.assertIsNone(data["local_error"])
        # 远端层不受影响：占用仍然只由读回来的卡片决定。
        self.assertEqual(len(data["cards"]), 1)
        self.assertEqual(data["cards"][0]["remote_ids"], {"facebook": "123456"})

    def test_review_permalink_is_attached_by_the_publish_remote_id(self):
        self.populate()
        self.state.mkdir(exist_ok=True)
        database = self.state / "index.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE posts (platform TEXT, post_id TEXT, permalink TEXT)")
            connection.execute("INSERT INTO posts VALUES ('facebook', 'post-1', ?)",
                               ("https://www.facebook.com/neakasaofficial/posts/1",))
            connection.commit()
        finally:
            connection.close()
        append_attempt(self.state, PublishAttempt(
            post_id="post-1", platform="facebook", status="scheduled",
            scheduled_at=SLOT.isoformat(), recorded_at=NOW.isoformat(),
            text_de_sha256="abc", remote_id="facebook=123456"))
        data = self.client.get("/api/calendar").json()
        self.assertEqual(data["cards"][0]["permalinks"],
                         {"facebook": "https://www.facebook.com/neakasaofficial/posts/1"})
        database.unlink()

    def test_a_broken_review_ledger_is_reported_not_shown_as_an_empty_local_layer(self):
        self.populate()
        with patch.object(calendar.local_schedule, "entries",
                          side_effect=ValueError("审校记录第 3 行损坏")):
            data = self.client.get("/api/calendar").json()
        self.assertEqual(data["local"], [])
        self.assertIn("损坏", data["local_error"])

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

    def test_an_unreadable_item_updates_the_month_and_reports_itself_without_leaking(self):
        """明细读不出来的条目照常进这个月，页面不再被整体标成过期。

        它自己仍然可数：unresolved_count 加一，诊断保留结构而不带正文或 URL。
        """
        self.populate()
        failure = PlannerItemError({'date':date(2026,9,4)},
            {'index':0,'time':'6:39 PM','text':'private caption','href':'https://example.test/?token=secret'},
            'published_detail', DetailReadError(
                'missing_fields', placement='story', missing_fields=('owner',)))
        unread = RemoteSlotInventory((SLOT,), WINDOW.ui_timezone, ROWS.visible_start, ROWS.visible_end,
            (RemotePlannerCard(SLOT, placement='story', read_status='incomplete'),), True, (failure.diagnostic,))
        later = NOW + timedelta(minutes=1)
        with patch('web.api.calendar.current_time', return_value=later), \
                patch('web.api.calendar.read_live_inventory', AsyncMock(return_value=unread)):
            result = self.client.post('/api/calendar/refresh')
            data = result.json()
        self.assertEqual(result.status_code,200)
        self.assertEqual(data['status'],'ready')
        self.assertEqual(data['cached_at'],later.isoformat())
        self.assertEqual(data['cards'][0]['placement'],'story')
        self.assertEqual(data['cards'][0]['read_status'],'incomplete')
        self.assertFalse(data['stale'])
        self.assertIsNone(data['error'])
        self.assertTrue(data['coverage']['grid_complete'])
        self.assertFalse(data['coverage']['decision_complete'])
        self.assertTrue(data['coverage']['occupancy_complete'])
        self.assertEqual(data['coverage']['unresolved_count'],1)
        self.assertEqual(data['refresh_diagnostic']['missing_fields'],['owner'])
        self.assertNotIn('partial_cards',data)
        for private in ('private caption','token=secret'):
            self.assertNotIn(private,result.text)

    def test_calendar_keeps_next_business_month_card_inside_la_current_month(self):
        """北京比美西快 15–16 小时，所以美西 9 月的尾巴伸进北京 10 月一整个白天。"""
        self.populate()
        data = self.client.get("/api/calendar").json()
        self.assertEqual(data["month_ui"], "2026-09")
        self.assertEqual(data["cards"][0]["at_business"], "2026-10-01T12:00:00+08:00")
        self.assertEqual(data["cards"][0]["audience"],
                         {"timezone": "Europe/Berlin", "at": "2026-10-01T06:00:00+02:00",
                          "quiet_hours": False})
        self.assertEqual(data["business_timezone"], "Asia/Shanghai")
        self.assertEqual(data["cards"][0]["channels"], ["facebook"])
        self.assertEqual(data["cached_at"], NOW.isoformat())
        self.assertEqual(data["coverage"]["visible_start"], "2026-09-01")
        self.assertTrue(data["coverage"]["matches_current_month"])
        self.assertTrue(data["advisory_only"])

    def test_beijing_ui_uses_the_confirmed_month_without_a_measured_platform_limit(self):
        self.config._d['publish']['ui_timezone'] = 'Asia/Shanghai'
        window = ScheduleWindow('accepted-probe', timedelta(0), None, 'Asia/Shanghai')
        with patch('web.api.calendar.configured_window', return_value=window):
            data = self.client.get('/api/calendar').json()
        self.assertEqual(data['ui_timezone'], 'Asia/Shanghai')
        self.assertEqual(data['display_start'], '2026-09-01T00:00:00+08:00')
        self.assertEqual(data['display_end_exclusive'], '2026-10-01T00:00:00+08:00')
        self.assertTrue(data['bounds']['facebook']['available'])
        self.assertEqual(data['bounds']['facebook']['earliest'], NOW.isoformat())
        self.assertEqual(data['bounds']['instagram']['end_exclusive'], '2026-09-30T16:00:00+00:00')

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
                patch("publish.planner_cache.month_inventory.read", AsyncMock(return_value=ROWS)) as read:
            self.assertEqual(asyncio.run(planner_cache.read_live_inventory()), ROWS)
        read.assert_awaited_once()
        page.close.assert_awaited_once()
        pw.stop.assert_awaited_once()

    def test_failed_item_exposes_its_location_without_exception_or_caption_text(self):
        self.populate()
        failure = PlannerItemError({'date': date(2026, 9, 30)},
            {'index': 0, 'time': '5:30 PM', 'text': 'private caption'}, 'item_ready')
        with patch('web.api.calendar.read_live_inventory', AsyncMock(side_effect=failure)):
            response = self.client.post('/api/calendar/refresh')
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertEqual(data['refresh_diagnostic']['stage'], 'item_ready')
        self.assertIn('2026-09-30', data['error'])
        self.assertIn('5:30 PM', data['error'])
        self.assertNotIn('private caption', response.text)
        self.assertEqual(data['cached_at'], NOW.isoformat())


if __name__ == "__main__":
    unittest.main()

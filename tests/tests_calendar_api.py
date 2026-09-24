"""Calendar routes operate only on temporary caches and injected browser readers."""
import asyncio
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config  # noqa: E402
from publish.business_suite import ProbeRequired, RemotePlannerCard, RemoteSlotInventory  # noqa: E402
from publish.compose import ComposeError, ScheduleWindow  # noqa: E402
from publish.journal import PublishOperationLock  # noqa: E402
from publish import planner_cache  # noqa: E402
from publish.month_inventory import PlannerItemError  # noqa: E402
from publish.planner_content import DetailReadError  # noqa: E402
from web.api import calendar, reader  # noqa: E402

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
WINDOW = ScheduleWindow("fixture", timedelta(minutes=20), timedelta(days=30), "America/Los_Angeles")
SLOT = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)  # Berlin 06:00, LA Sep 30 21:00
ROWS = RemoteSlotInventory((SLOT,), WINDOW.ui_timezone, date(2026, 9, 1), date(2026, 9, 30),
                          (RemotePlannerCard(SLOT, ("facebook",), (("facebook", "123456"),), "Manual post",
                                             placement="feed", time_verified=True),), True)
SOURCE_URL = 'https://www.facebook.com/neakasaofficial/posts/1'
ENTRY = {'kind': 'scheduled', 'task_id': 'fa_neakasaofficial/post-1', 'platform': 'facebook',
         'channels': ['instagram'], 'review_status': 'scheduled', 'at': SLOT.isoformat(),
         'snapshot_id': 'a' * 32, 'remote_id': 'instagram=4378984725697354'}
PUBLISHED = RemotePlannerCard(SLOT + timedelta(minutes=1), ('instagram',),
                             (('instagram', '18084155825688886'),), 'Published caption',
                             delivery='published', placement='feed', time_verified=True)


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

    def populate(self, card=None):
        rows = replace(ROWS, cards=(card,), occupied=(card.at,)) if card else ROWS
        asyncio.run(planner_cache.refresh_cache(self.path, AsyncMock(return_value=rows),
                                               state_dir=self.state, now=NOW))

    def linked_payload(self, card=PUBLISHED, entries=None, source_url=SOURCE_URL, source_error=None):
        self.populate(card)
        source = SimpleNamespace(row={'permalink': source_url}) if source_url is not None else None
        with patch.object(calendar.local_schedule, 'entries', return_value=[ENTRY] if entries is None else entries), \
                patch.object(reader, 'source_post', return_value=source, side_effect=source_error):
            response = self.client.get('/api/calendar')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_local_layer_is_drawn_beside_the_remote_one_but_never_judges_slots(self):
        """⛔ 本地记录只画给人看；拿它证明自己没冲突，等于不检查。"""
        self.populate()
        frozen = {"kind": "content_locked", "task_id": "fa_x/1", "platform": "facebook",
                  "review_status": "content_locked", "at": None, "snapshot_id": "a" * 32,
                  "remote_id": "", "channels": []}
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

    def test_published_card_links_to_source_when_its_id_changed_after_scheduling(self):
        card = self.linked_payload()['cards'][0]
        self.assertEqual(card.get('source_task_id'), 'fa_neakasaofficial/post-1')
        self.assertEqual(card['source_platform'], 'facebook')
        self.assertEqual(card['source_permalink'], SOURCE_URL)

    def test_scheduled_remote_id_match_takes_priority_over_the_time_window(self):
        scheduled = replace(PUBLISHED, delivery='scheduled', at=SLOT + timedelta(hours=2),
                            remote_ids=(('instagram', '4378984725697354'),))
        nearby_other = dict(ENTRY, task_id='fa_neakasaofficial/other', at=scheduled.at.isoformat(),
                            remote_id='instagram=999999')
        card = self.linked_payload(scheduled, [ENTRY, nearby_other])['cards'][0]
        self.assertEqual(card.get('source_task_id'), ENTRY['task_id'])

    def test_source_matching_requires_unique_scheduled_entries_for_every_channel(self):
        cases = [
            ('outside window', replace(PUBLISHED, at=SLOT + timedelta(minutes=5, seconds=1)), [ENTRY]),
            ('different channel', PUBLISHED, [dict(ENTRY, channels=['facebook'])]),
            ('two tasks', PUBLISHED, [ENTRY, dict(ENTRY, task_id='fa_neakasaofficial/other')]),
            ('unknown delivery', replace(PUBLISHED, delivery='unknown'), [ENTRY]),
            ('failed delivery', replace(PUBLISHED, delivery='failed'), [ENTRY]),
            ('submitting only', PUBLISHED, [dict(ENTRY, kind='submitting')]),
            ('unknown channel', replace(PUBLISHED, channels=(), remote_ids=()), [ENTRY]),
            ('unmatched channel', replace(PUBLISHED, channels=('facebook', 'instagram')), [ENTRY]),
            ('different tasks per channel', replace(PUBLISHED, channels=('facebook', 'instagram')),
             [ENTRY, dict(ENTRY, channels=['facebook'], task_id='fa_neakasaofficial/other')]),
            ('manual card without local schedule', PUBLISHED, []),
        ]
        for name, remote, entries in cases:
            with self.subTest(name=name):
                card = self.linked_payload(remote, entries)['cards'][0]
                for field in ('source_task_id', 'source_platform', 'source_permalink'):
                    self.assertIn(field, card)
                    self.assertIsNone(card[field])

    def test_five_minute_boundary_and_duplicate_rows_for_one_task_are_allowed(self):
        for minutes in (-5, 5):
            with self.subTest(minutes=minutes):
                card = self.linked_payload(replace(PUBLISHED, at=SLOT + timedelta(minutes=minutes)),
                                           [ENTRY, dict(ENTRY)])['cards'][0]
                self.assertEqual(card.get('source_task_id'), ENTRY['task_id'])

    def test_both_channels_must_resolve_to_the_same_task(self):
        both = replace(PUBLISHED, channels=('facebook', 'instagram'))
        entry = dict(ENTRY, channels=['facebook', 'instagram'],
                     remote_id='facebook=123456;instagram=4378984725697354')
        card = self.linked_payload(both, [entry])['cards'][0]
        self.assertEqual(card.get('source_task_id'), ENTRY['task_id'])

    def test_competing_remote_cards_do_not_share_one_source_on_the_same_channel(self):
        exact = replace(PUBLISHED, at=SLOT, delivery='scheduled',
                        remote_ids=(('instagram', '4378984725697354'),))
        nearby = replace(PUBLISHED, at=SLOT + timedelta(minutes=2),
                         remote_ids=(('instagram', '999999'),))
        facebook = replace(PUBLISHED, channels=('facebook',), remote_ids=(('facebook', '888888'),))
        entry = dict(ENTRY, channels=['facebook', 'instagram'])
        cases = [
            ('exact wins', (exact, nearby), [ENTRY['task_id'], None]),
            ('two time matches', (PUBLISHED, nearby), [None, None]),
            ('independent channels', (PUBLISHED, facebook), [ENTRY['task_id'], ENTRY['task_id']]),
        ]
        for name, cards, expected in cases:
            with self.subTest(name=name):
                inventory = replace(ROWS, cards=cards, occupied=tuple(card.at for card in cards))
                asyncio.run(planner_cache.refresh_cache(self.path, AsyncMock(return_value=inventory),
                                                       state_dir=self.state, now=NOW))
                with patch.object(calendar.local_schedule, 'entries', return_value=[entry]), \
                        patch.object(reader, 'source_post', return_value=None):
                    data = self.client.get('/api/calendar').json()
                self.assertEqual([card['source_task_id'] for card in data['cards']], expected)

    def test_source_link_validation_keeps_the_review_link(self):
        for url in (None, '', 'http://www.facebook.com/posts/1', 'https://example.test/post',
                    'https://www.facebook.com.evil.test/post', 'https://user@www.facebook.com/posts/1'):
            with self.subTest(url=url):
                card = self.linked_payload(source_url=url)['cards'][0]
                self.assertEqual(card.get('source_task_id'), ENTRY['task_id'])
                self.assertIsNone(card['source_permalink'])
        for error in (OSError('fixture unreadable'), ValueError('fixture malformed')):
            with self.subTest(error=error):
                card = self.linked_payload(source_error=error)['cards'][0]
                self.assertEqual(card.get('source_task_id'), ENTRY['task_id'])
                self.assertIsNone(card['source_permalink'])

    def test_published_urls_are_not_replaced_by_the_source_url(self):
        url = 'https://www.instagram.com/p/Published/'
        remote = replace(PUBLISHED, permalinks=(('instagram', url),))
        card = self.linked_payload(remote)['cards'][0]
        self.assertEqual(card['permalinks'], {'instagram': url})
        self.assertEqual(card.get('source_permalink'), SOURCE_URL)

    def test_source_matching_uses_entries_before_the_month_filter(self):
        at = datetime(2026, 9, 1, 7, tzinfo=timezone.utc)
        entry = dict(ENTRY, at=(at - timedelta(minutes=1)).isoformat())
        data = self.linked_payload(replace(PUBLISHED, at=at), [entry])
        self.assertEqual(data['local'], [])
        self.assertEqual(data['cards'][0].get('source_task_id'), ENTRY['task_id'])

    def test_source_permalink_comes_from_post_truth_without_an_index(self):
        from core import config, store
        self.config._d['paths']['archive'] = str(Path(self.temp.name) / 'archive')
        archive = store.Archive(self.config.archive_dir, 'fa_neakasaofficial')
        archive.append(store.Post('post-1', 'facebook', 'neakasaofficial', 'Source caption',
                                  NOW.isoformat(), permalink=SOURCE_URL))
        self.populate(PUBLISHED)
        with patch.object(config, '_cfg', self.config), \
                patch.object(calendar.local_schedule, 'entries', return_value=[ENTRY]):
            data = self.client.get('/api/calendar').json()
        self.assertEqual(data['cards'][0].get('source_permalink'), SOURCE_URL)
        self.assertFalse((self.state / 'index.sqlite').exists())

    def test_a_broken_review_ledger_is_reported_not_shown_as_an_empty_local_layer(self):
        self.populate()
        with patch.object(calendar.local_schedule, "entries",
                          side_effect=ValueError("审校记录第 3 行损坏")):
            response = self.client.get("/api/calendar")
            data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["local"], [])
        self.assertIn("损坏", data["local_error"])
        self.assertIn('source_task_id', data['cards'][0])
        self.assertIsNone(data['cards'][0]['source_task_id'])

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

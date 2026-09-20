"""Real archive/state writes with isolated fake transport; no social or Feishu access."""
import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_fixtures import image_bytes
from core.capture import Collector
from core import config
from core.capture_state import CaptureState, CaptureStateError, post_key, verified_images
from core.monitor_access import AccessController
from core.monitoring import MonitoringJournal
from core.store import Archive, Media, Post
from routes import delta


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 9, 15, 2, tzinfo=timezone.utc)
        self.arc = Archive(self.root / 'archive', 'in_target')
        self.state = CaptureState(self.root / 'state')
        self.state.initialize(self.root / 'archive', {'facebook': 'target', 'instagram': 'target'},
                              'fixture baseline verified', now=self.now)
        self.access = AccessController(self.root / 'state', clock=lambda: self.now)
        self.access.initialize('fixture')
        self.dcfg = delta.DeltaConfig(access=self.access, scan_id='scan-one', first_screen_seconds=0,
                                       min_own_posts=0, max_scrolls=0)
        self.ctx = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(side_effect=self.response)))
        self.col = Collector()
        self.col.payloads = [{}]

    def tearDown(self):
        self.temp.cleanup()

    async def response(self, url, **kwargs):
        return SimpleNamespace(status=200, ok=True, headers={'content-type': 'image/jpeg'},
                               body=AsyncMock(return_value=image_bytes()))

    def post(self, identifier='1', **kwargs):
        args = dict(post_id=identifier, platform='instagram', account='target', owner='target',
                    text='English text', created_at=(self.now + timedelta(seconds=1)).isoformat(),
                    media=[Media('https://cdn.invalid/' + identifier, 'image')],
                    permalink='https://www.instagram.com/p/' + identifier + '/',
                    source_media_complete=True, source_media_count=1)
        args.update(kwargs)
        return Post(**args)

    async def scan(self, posts, detail_posts=None):
        with patch.object(delta, 'scan_page', AsyncMock(return_value=(self.col, 'https://www.instagram.com/target/'))) as scan:
            with patch.object(delta, 'extract', side_effect=[posts, detail_posts] if detail_posts is not None else [posts]):
                result = await delta.delta_once(self.ctx, 'instagram', 'target', self.arc, self.dcfg,
                    facts=MonitoringJournal(self.root / 'state', now=self.now, inspect_running=False))
            return result, scan

    async def test_all_candidates_survive_first_post_cancellation(self):
        self.ctx.request.get.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.scan([self.post('1'), self.post('2')])
        state = self.state.status()
        self.assertEqual(len(state['items']), 2)
        self.assertEqual({i['status'] for i in state['items'].values()}, {'manual', 'deferred'})
        self.assertEqual(len(state['events']), 1)
        self.assertEqual(self.ctx.request.get.await_count, 1)

    async def test_failed_images_save_text_zero_verified_and_no_automatic_retry(self):
        self.ctx.request.get.return_value = SimpleNamespace(status=403, ok=False)
        self.ctx.request.get.side_effect = None
        await self.scan([self.post()])
        item = next(iter(self.state.status()['items'].values()))
        self.assertEqual((item['status'], item['saved_images'], item['archived']), ('manual', 0, True))
        self.assertEqual(self.arc.rows()[0]['text'], 'English text')
        self.now += timedelta(hours=4)
        self.dcfg.scan_id = 'scan-two'
        await self.scan([self.post()])
        self.assertEqual(self.ctx.request.get.await_count, 1)

    async def test_cdn_429_stops_following_files_and_posts(self):
        self.ctx.request.get.return_value = SimpleNamespace(status=429, ok=False)
        self.ctx.request.get.side_effect = None
        with self.assertRaises(delta.DeltaBlocked):
            await self.scan([self.post('1'), self.post('2')])
        self.assertIsNotNone(self.access.status()['hard_stop'])
        self.assertEqual(self.ctx.request.get.await_count, 1)

    async def test_detail_only_accepts_candidate_and_keeps_known_total(self):
        original = self.post(source_media_complete=False, source_media_count=2, media_complete=False)
        complete = self.post(media=[Media('https://cdn.invalid/A', 'image'), Media('https://cdn.invalid/B', 'image')],
                             source_media_count=2)
        _, scan = await self.scan([original], [self.post('recommended'), complete])
        self.assertEqual(scan.await_count, 2)
        self.assertEqual([r['post_id'] for r in self.arc.rows()], ['1'])
        item = next(iter(self.state.status()['items'].values()))
        self.assertEqual((item['status'], item['saved_images']), ('complete', 2))

    async def test_matching_detail_preserves_homepage_facts_and_fuller_partial_media(self):
        for complete in (True, False):
            identifier = str(complete)
            original = self.post(identifier, coauthors=['known-partner'], source_media_complete=False,
                                 source_media_count=2 if complete else 3, media_complete=False)
            detail = self.post(identifier, text='', created_at='', coauthors=['detail-partner'],
                media=[Media('https://cdn.invalid/A', 'image'), Media('https://cdn.invalid/B', 'image')],
                source_media_complete=complete, source_media_count=2 if complete else None,
                media_complete=complete)
            self.now += timedelta(hours=4)
            self.dcfg.scan_id = 'scan-' + identifier
            await self.scan([original], [detail])
            item = self.state.status()['items'][post_key('instagram', 'target', identifier)]
            self.assertEqual(item['source']['text'], 'English text')
            self.assertEqual(item['source']['created_at'], original.created_at)
            self.assertEqual(set(item['source']['coauthors']), {'known-partner', 'detail-partner'})
            self.assertEqual(item['saved_images'], 2)
            self.assertEqual(item['status'], 'complete' if complete else 'manual')
            self.assertEqual(item['source']['source_media_complete'], complete)
            self.assertEqual(item['source']['source_media_count'], 2 if complete else 3)
            event = next(e for e in self.state.status()['events'].values() if e['source']['post_id'] == identifier)
            self.assertTrue(event['eligible'])

    async def test_three_detail_failure_rounds_pause_once_per_round(self):
        c = config.Config()
        c._d['paths'].update(archive=str(self.root / 'archive'), state=str(self.root / 'state'))
        c._d['targets']['instagram'] = 'target'
        browser = SimpleNamespace(close=AsyncMock())
        pw = SimpleNamespace(stop=AsyncMock())
        legacy = {}
        for round_number in range(3):
            self.now += timedelta(hours=4)
            posts = [self.post(str(round_number) + str(i), source_media_complete=False) for i in range(2)]
            with patch.object(config, '_cfg', c), patch.object(delta, 'attach', AsyncMock(return_value=(pw, browser, self.ctx))), \
                 patch.object(delta, 'scan_page', AsyncMock(side_effect=[(self.col, 'https://www.instagram.com/target/'), TimeoutError(), TimeoutError()])), \
                 patch.object(delta, 'extract', return_value=posts), patch.object(delta, 'notify'):
                rc = await delta._run_due(['instagram'], self.dcfg, legacy, self.root / 'state' / 'delta_state.json', False)
            self.assertEqual(rc, 1)
            entry = self.access.status()['platforms']['instagram']
            self.assertEqual(entry['failures'], round_number + 1)
        self.assertTrue(entry['paused'])
        self.assertEqual(len(self.state.status()['items']), 6)

    def test_explicit_manual_detail_failure_records_outcome(self):
        post = self.post(source_media_complete=False)
        self.state.begin('original', [post], {}, self.now)
        self.state.started(post, self.now)
        self.state.interrupt('original', self.now, 'fixture manual')
        c = config.Config()
        c._d['paths'].update(archive=str(self.root / 'archive'), state=str(self.root / 'state'))
        with patch.object(config, '_cfg', c), patch.object(delta, 'attach', AsyncMock(return_value=(SimpleNamespace(stop=AsyncMock()), SimpleNamespace(close=AsyncMock()), self.ctx))), \
             patch.object(delta, 'scan_page', AsyncMock(side_effect=TimeoutError())):
            result = delta.recover_post(post_key('instagram', 'target', '1'), self.state.status()['revision'], 'checked')
        self.assertEqual(self.access.status()['platforms']['instagram']['failures'], 1)
        self.assertEqual(next(iter(result['items'].values()))['status'], 'manual')
        with patch.object(config, '_cfg', c), patch.object(delta, 'attach', AsyncMock(return_value=(SimpleNamespace(stop=AsyncMock()), SimpleNamespace(close=AsyncMock()), self.ctx))), \
             patch.object(delta, 'scan_page', AsyncMock(return_value=(self.col, 'https://www.instagram.com/p/1/'))), \
             patch.object(delta, 'extract', return_value=[self.post()]):
            result = delta.recover_post(post_key('instagram', 'target', '1'), result['revision'], 'source fixed')
        self.assertEqual(self.access.status()['platforms']['instagram']['failures'], 0)
        self.assertEqual(next(iter(result['items'].values()))['status'], 'complete')

    def test_manual_quota_refusal_does_not_change_platform_failure_history(self):
        post = self.post(source_media_complete=False)
        self.state.begin('original', [post], {}, self.now)
        self.state.started(post, self.now)
        self.state.interrupt('original', self.now, 'fixture manual')
        self.access.outcome('instagram', success=False, reason='previous source failure')
        c = config.Config()
        c._d['paths'].update(archive=str(self.root / 'archive'), state=str(self.root / 'state'))
        with patch.object(config, '_cfg', c), patch.object(delta, 'attach', AsyncMock(return_value=(SimpleNamespace(stop=AsyncMock()), SimpleNamespace(close=AsyncMock()), self.ctx))), \
             patch.object(AccessController, 'reserve_detail', side_effect=delta.AccessDenied('quota')), \
             patch.object(delta, 'scan_page', AsyncMock()) as scan:
            delta.recover_post(post_key('instagram', 'target', '1'), self.state.status()['revision'], 'checked')
        scan.assert_not_awaited()
        self.assertEqual(self.access.status()['platforms']['instagram']['failures'], 1)

    async def test_media_failures_and_quota_are_not_source_request_failures(self):
        for failure in (SimpleNamespace(status=403, ok=False),
                        SimpleNamespace(status=200, ok=True, headers={'content-type': 'image/jpeg'}, body=AsyncMock(return_value=b'bad'))):
            self.ctx.request.get.side_effect = None
            self.ctx.request.get.return_value = failure
            self.now += timedelta(hours=4)
            self.dcfg.scan_id = 'media-' + str(self.now.hour)
            await self.scan([self.post(str(self.now.hour))])
            self.assertEqual(self.dcfg.source_failures, [])
        self.ctx.request.get.side_effect = OSError('file/transport fixture')
        self.now += timedelta(hours=4)
        self.dcfg.scan_id = 'media-file'
        await self.scan([self.post('file')])
        self.assertEqual(self.dcfg.source_failures, [])

    async def test_signed_url_change_with_equal_bytes_does_not_add_event_or_revision_history(self):
        await self.scan([self.post()])
        before = len(self.state.status()['events'])
        self.now += timedelta(hours=4)
        self.dcfg.scan_id = 'scan-two'
        post = self.post(media=[Media('https://cdn.invalid/1?sig=rotated', 'image')])
        post.created_at = self.arc.rows()[0]['created_at']
        await self.scan([post])
        self.assertEqual(len(self.state.status()['events']), before)
        self.assertEqual(list(self.arc.base.rglob('source_history.jsonl')), [])
        self.assertEqual(self.arc.rows()[0]['media'][0]['url'], post.media[0].url)

    async def test_verified_count_rejects_file_changed_after_capture(self):
        await self.scan([self.post()])
        row = self.arc.rows()[0]
        self.assertEqual(verified_images(self.arc.base, row), 1)
        (self.arc.base / row['media'][0]['local_path']).write_bytes(b'not an image')
        self.assertEqual(verified_images(self.arc.base, row), 0)

    def test_baseline_is_independent_and_cannot_be_overwritten(self):
        self.assertFalse((self.root / 'state' / 'pipeline_state.json').exists())
        with self.assertRaises(CaptureStateError):
            self.state.initialize(self.root / 'archive', {}, 'reset')
        before = self.state.path.read_bytes()
        with self.assertRaises(CaptureStateError):
            self.state.recover('missing', 0, 'fix')
        self.assertEqual(self.state.path.read_bytes(), before)

    def test_classifies_history_and_unknown_dates_without_guessing(self):
        self.state.begin('s', [self.post('old', created_at='2025-01-01T00:00:00Z'),
                              self.post('unknown', created_at='not-a-date')], {}, self.now)
        items = self.state.status()['items']
        self.assertEqual(items[post_key('instagram', 'target', 'old')]['classification'], 'historical')
        self.assertEqual(items[post_key('instagram', 'target', 'unknown')]['classification'], 'time_unknown')

    async def test_explicit_recovery_gets_one_new_attempt_and_finishes(self):
        self.ctx.request.get.side_effect = OSError('fixture failure')
        await self.scan([self.post()])
        key = post_key('instagram', 'target', '1')
        before = self.access.status()['platforms']['instagram']['intents']
        item = self.state.recover(key, self.state.status()['revision'], 'fixture fixed')
        self.dcfg.scan_id, self.dcfg.run_kind = item['scan_id'], 'recovery'
        self.ctx.request.get.side_effect = self.response
        await delta.capture_post(self.ctx, 'instagram', 'target', self.arc, self.dcfg, self.post(), lifecycle=self.state)
        result = self.state.status()['items'][key]
        self.assertEqual((result['status'], result['classification']), ('complete', 'recovered'))
        self.assertEqual(self.access.status()['platforms']['instagram']['intents'], before)
        with self.assertRaises(CaptureStateError):
            self.state.recover(key, self.state.status()['revision'], 'repeat')

    async def test_429_during_page_load_prevents_any_scrolling(self):
        handlers = {}
        page = SimpleNamespace(url='about:blank', mouse=SimpleNamespace(wheel=AsyncMock(), move=AsyncMock()),
            evaluate=AsyncMock(return_value=[1280, 800]), close=AsyncMock(),
            on=lambda event, fn: handlers.update({event: fn}), remove_listener=lambda *args: None)
        async def goto(url, **kwargs):
            page.url = url
            handlers['response'](SimpleNamespace(status=429, url='https://www.instagram.com/api/v1/feed/target/'))
        page.goto = goto
        self.dcfg.max_scrolls = 3
        with self.assertRaises(delta.DeltaBlocked):
            await delta.scan_page(SimpleNamespace(new_page=AsyncMock(return_value=page)),
                                  'https://www.instagram.com/target/', self.dcfg)
        page.mouse.wheel.assert_not_awaited()
        self.assertIsNotNone(self.access.status()['hard_stop'])
        page.close.assert_awaited_once()

    async def test_per_round_detail_quota_exhaustion_is_manual_without_extra_navigation(self):
        posts = [self.post(str(i), source_media_complete=False, source_media_count=None, media_complete=False) for i in range(4)]
        with patch.object(delta, 'scan_page', AsyncMock(return_value=(self.col, 'https://www.instagram.com/target/'))) as scan, \
             patch.object(delta, 'extract', side_effect=[posts, [], [], []]):
            await delta.delta_once(self.ctx, 'instagram', 'target', self.arc, self.dcfg)
        self.assertEqual(scan.await_count, 4)  # one homepage + three details
        self.assertEqual(len(self.dcfg.source_failures), 3)  # quota refusal adds no source failure
        items = self.state.status()['items'].values()
        self.assertTrue(all(item['status'] == 'manual' for item in items))
        self.assertTrue(any('quota' in item['reason'] for item in items))

    async def test_recovery_api_rejects_stale_or_blocked_attempts_before_browser_attach(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.api.runtime import router

        self.ctx.request.get.side_effect = OSError('fixture failure')
        await self.scan([self.post()])
        key = post_key('instagram', 'target', '1')
        revision = self.state.status()['revision']
        c = config.Config()
        c._d['paths'].update(archive=str(self.root / 'archive'), state=str(self.root / 'state'))
        c._d['runtime'] = {'env_file': str(self.root / 'empty.env')}
        (self.root / 'empty.env').write_bytes(b'')
        app = FastAPI()
        app.include_router(router)
        attach = AsyncMock(side_effect=AssertionError('must not access the browser'))
        with patch.object(config, '_cfg', c), patch.object(delta, 'attach', attach), TestClient(app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 41000)) as client:
            before = self.state.path.read_bytes()
            stale = client.post('/api/runtime/capture/recover', json={'key': key, 'version': revision - 1, 'reason': 'checked'})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(self.state.path.read_bytes(), before)
            self.access.profile_stop('fixture content 429', source='instagram')
            stopped = client.post('/api/runtime/capture/recover', json={'key': key, 'version': revision, 'reason': 'checked'})
            self.assertEqual(stopped.status_code, 409)
            self.assertEqual(self.state.path.read_bytes(), before)
            snapshot = self.access.status()
            bad_version = client.post('/api/runtime/monitor/recover', json={'version': snapshot['revision'] - 1, 'reason': 'checked'})
            self.assertEqual(bad_version.status_code, 409)
            recovered = client.post('/api/runtime/monitor/recover', json={'version': snapshot['revision'], 'reason': 'manually checked profile'})
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertIsNone(self.access.status()['hard_stop'])
            self.assertEqual(self.access.status()['platforms']['instagram']['intents'], snapshot['platforms']['instagram']['intents'])
            self.assertEqual(self.state.path.read_bytes(), before)
            self.access.path.write_text('{broken', encoding='utf-8', newline='')
            corrupt = client.post('/api/runtime/capture/recover', json={'key': key, 'version': revision, 'reason': 'checked'})
            self.assertEqual(corrupt.status_code, 409)
            self.assertEqual(self.state.path.read_bytes(), before)
            attach.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

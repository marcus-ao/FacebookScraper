"""Monitoring regressions with isolated archives and passive fake browser responses."""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_fixtures import image_bytes
from core.capture import Collector
from core.capture_state import CaptureState, post_key
from core.feishu import FeishuSettings, Outbox
from core.media import image_facts
from core.monitor_access import AccessController
from core.monitoring import MonitoringJournal
from core.parse import extract
from core.store import Archive, Media, Post
from routes import delta
from pipeline.service import Runtime


def timeline():
    return {'data': {'node': {'timeline_list_feed_units': {'edges': [{'node': {
        'post_id': '123', 'message': {'text': 'Source caption'},
        'creation_time': 1789850000, 'actors': [{'id': '42', 'url': 'https://www.facebook.com/target/'}],
        'attachments': [], 'url': 'https://www.facebook.com/target/posts/123/'}}]}}}}


class Page:
    def __init__(self, *, embedded=False, delayed=False):
        self.url = 'about:blank'
        self.handlers = []
        self.embedded, self.delayed = embedded, delayed
        self.task = None
        self.mouse = SimpleNamespace(move=AsyncMock(), wheel=AsyncMock())
        self.visits = 0

    def on(self, event, handler):
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)

    async def goto(self, url, **kwargs):
        self.url = url
        self.visits += 1
        response = SimpleNamespace(url='https://www.facebook.com/api/graphql/', status=200,
                                   text=AsyncMock(return_value=json.dumps({'data': {'viewer': {}}})))
        for handler in self.handlers:
            handler(response)
        if self.delayed:
            async def deliver():
                await asyncio.sleep(.03)
                response.text = AsyncMock(return_value=json.dumps(timeline()))
                for handler in self.handlers:
                    handler(response)
            self.task = asyncio.create_task(deliver())

    async def evaluate(self, expression):
        if 'script' in expression:
            return [json.dumps({'require': [['RelayPrefetchedStreamCache', 'next', [],
                     ['key', {'__bbox': {'result': timeline()}}]]]}),
                    json.dumps({'secret': 'must-not-be-persisted'})] if self.embedded else []
        return 0

    async def close(self):
        if self.task:
            await self.task


class PassiveCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_deadline_also_bounds_unfinished_response_reads(self):
        page = Page()
        original = page.goto
        async def slow_text():
            await asyncio.sleep(.5)
            return '{}'
        async def goto(url, **kwargs):
            await original(url, **kwargs)
            for handler in page.handlers:
                handler(SimpleNamespace(url='https://www.facebook.com/api/graphql/', status=200, text=slow_text))
        page.goto = goto
        started = time.monotonic()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(delta.scan_page(SimpleNamespace(new_page=AsyncMock(return_value=page)),
                'https://www.facebook.com/target/', delta.DeltaConfig(first_screen_seconds=.1,
                    max_session_seconds=.03)), .03)
        self.assertLess(time.monotonic() - started, .3)

    async def test_detail_url_does_not_wait_for_a_nonexistent_account_slug(self):
        page = Page(embedded=True)
        config = delta.DeltaConfig(first_screen_seconds=.01, max_session_seconds=.2, run_kind='detail')
        col, _ = await asyncio.wait_for(delta.scan_page(SimpleNamespace(new_page=AsyncMock(return_value=page)),
            'https://www.facebook.com/permalink.php?story_fbid=123&id=42', config), .1)
        self.assertEqual(len(extract(col.payloads, 'facebook', 'target', 'delta')), 1)

    async def test_browser_embedded_and_delayed_payloads_use_passive_page_data(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                for embedded in (True, False):
                    with self.subTest(embedded=embedded):
                        context = await browser.new_context()
                        requests = []
                        payload = json.dumps({'require': [['RelayPrefetchedStreamCache', 'next', [],
                            ['key', {'__bbox': {'result': timeline()}}]]]})
                        body = ('<script type="application/json">' + payload + '</script>' if embedded else
                                '<script>setTimeout(() => fetch("/api/graphql/"), 250)</script>')
                        async def respond(route):
                            requests.append(route.request.url)
                            if route.request.url == 'https://www.facebook.com/target/':
                                await route.fulfill(content_type='text/html', body=body)
                            elif route.request.url == 'https://www.facebook.com/api/graphql/':
                                await route.fulfill(content_type='application/json', body=json.dumps(timeline()))
                            else:
                                await route.abort()
                        await context.route('**/*', respond)
                        col, _ = await delta.scan_page(context, 'https://www.facebook.com/target/',
                            delta.DeltaConfig(first_screen_seconds=.1, max_session_seconds=2))
                        self.assertEqual([p.post_id for p in extract(col.payloads, 'facebook', 'target', 'delta')], ['123'])
                        self.assertEqual(requests, ['https://www.facebook.com/target/'] +
                                         ([] if embedded else ['https://www.facebook.com/api/graphql/']))
                        await context.close()
            finally:
                await browser.close()

    def test_embedded_payloads_keep_identity_binding_without_bootstrap_secrets(self):
        identity = json.loads((Path(__file__).parent / 'fixtures/facebook_profile_identity.json').read_text('utf-8'))
        story = timeline()
        story['data']['node']['timeline_list_feed_units']['edges'][0]['node']['actors'] = [{'id': '61591381265280'}]
        col = Collector()
        def relay(result):
            return {'require': [['RelayPrefetchedStreamCache', 'next', [], ['key', {'__bbox': {'result': result}}]]]}
        col.add_document_json([json.dumps({'data': {'bootstrap': {'DTSGInitialData': {'token': 'fixture-secret'}},
                                                  'result': relay(story)}}), json.dumps(relay(identity))])
        posts = extract(col.payloads, 'facebook', 'neakasaofficial', 'delta')
        self.assertEqual([p.owner for p in posts], ['neakasaofficial'])
        self.assertNotIn('fixture-secret', json.dumps(col.payloads))

    async def test_first_screen_reads_embedded_timeline_without_navigation_or_scroll(self):
        page = Page(embedded=True)
        col, _ = await delta.scan_page(SimpleNamespace(new_page=AsyncMock(return_value=page)),
            'https://www.facebook.com/target/', delta.DeltaConfig(first_screen_seconds=0))
        self.assertEqual([p.post_id for p in extract(col.payloads, 'facebook', 'target', 'delta')], ['123'])
        self.assertNotIn('must-not-be-persisted', json.dumps(col.payloads))
        self.assertEqual(page.visits, 1)
        page.mouse.wheel.assert_not_awaited()

    async def test_waits_for_late_timeline_within_existing_session_budget(self):
        page = Page(delayed=True)
        col, _ = await delta.scan_page(SimpleNamespace(new_page=AsyncMock(return_value=page)),
            'https://www.facebook.com/target/', delta.DeltaConfig(first_screen_seconds=.01,
                max_session_seconds=.2))
        self.assertEqual(len(extract(col.payloads, 'facebook', 'target', 'delta')), 1)
        self.assertEqual(page.visits, 1)
        page.mouse.wheel.assert_not_awaited()


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 9, 19, 22, 39, tzinfo=timezone.utc)
        self.arc = Archive(self.root / 'archive', 'in_target')
        self.state = CaptureState(self.root / 'state')
        self.state.initialize(self.root / 'archive', {'facebook': 'target', 'instagram': 'target'},
                              'confirmed baseline', now=self.now)
        self.access = AccessController(self.root / 'state', clock=lambda: self.now)
        self.access.initialize('fixture')
        self.cfg = delta.DeltaConfig(access=self.access, scan_id='scan', first_screen_seconds=0,
                                     min_own_posts=0)
        self.col = Collector()
        self.col.payloads = [{'data': {'viewer': {}}}]
        self.requests = []
        async def get(url, **kwargs):
            self.requests.append(url)
            return SimpleNamespace(status=200, ok=True, headers={'content-type': 'image/jpeg'},
                                   body=AsyncMock(return_value=image_bytes()))
        self.ctx = SimpleNamespace(request=SimpleNamespace(get=get))

    def post(self, pid='1', **kwargs):
        values = dict(post_id=pid, platform='instagram', account='target', owner='target',
                      text='Source caption', created_at=self.now.isoformat(),
                      permalink='https://www.instagram.com/p/' + pid + '/',
                      media=[Media('https://cdn.invalid/' + pid + '?oh=old&oe=123', 'image',
                                   source_media_id=pid)], source_media_complete=True, source_media_count=1)
        values.update(kwargs)
        return Post(**values)

    def save(self, post):
        data = image_bytes()
        self.arc.save_media(post, 0, data, image_facts(data, 'image/jpeg'))
        self.arc.append(post)

    async def scan(self, posts):
        with patch.object(delta, 'scan_page', AsyncMock(return_value=(self.col, 'https://www.instagram.com/target/'))), \
             patch.object(delta, 'extract', return_value=posts), patch.object(delta, 'utcnow', return_value=self.now):
            return await delta.delta_once(self.ctx, 'instagram', 'target', self.arc, self.cfg,
                facts=MonitoringJournal(self.root / 'state', now=self.now, inspect_running=False))

    async def test_auxiliary_payload_failure_identifies_missing_timeline(self):
        with self.assertRaisesRegex(delta.DeltaBlocked, '未取得.*帖子.*数据'):
            await self.scan([])

    async def test_signature_only_change_reuses_verified_original_without_processing(self):
        old = self.post()
        self.save(old)
        fresh = self.post(media=[Media('https://cdn.invalid/1?oh=new&oe=456', 'image', source_media_id='1')])
        result = await self.scan([fresh])
        self.assertEqual(self.requests, [])
        self.assertEqual(result.upgraded, 0)
        self.assertEqual(self.state.status()['items'], {})

    async def test_changed_media_path_still_downloads_even_with_same_media_id(self):
        self.save(self.post())
        await self.scan([self.post(media=[Media('https://cdn.invalid/replacement', 'image', source_media_id='1')])])
        self.assertEqual(self.requests, ['https://cdn.invalid/replacement'])

    async def test_baseline_excludes_unarchived_old_posts_but_keeps_known_updates(self):
        old_time = (self.now - timedelta(days=80)).isoformat()
        known = self.post('known', created_at=old_time)
        self.save(known)
        await self.scan([self.post('old', created_at=old_time), self.post('recent'),
                         self.post('known', created_at=old_time, text='Updated caption')])
        self.assertEqual({r['post_id'] for r in self.arc.rows()}, {'known', 'recent'})
        self.assertNotIn(post_key('instagram', 'target', 'old'), self.state.status()['items'])
        facts = (self.root / 'state' / 'monitoring_facts.jsonl').read_text('utf-8')
        self.assertIn('outside_baseline', facts)

    async def test_cancellation_distinguishes_started_from_unstarted_candidates(self):
        async def cancel(*args, **kwargs):
            raise asyncio.CancelledError()
        self.ctx.request.get = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.scan([self.post('1'), self.post('2')])
        items = self.state.status()['items']
        self.assertEqual(items[post_key('instagram', 'target', '1')]['status'], 'manual')
        self.assertEqual(items[post_key('instagram', 'target', '2')]['status'], 'deferred')
        self.assertEqual(len(self.state.status()['events']), 1)

    async def test_budget_does_not_start_another_post_without_time_to_finish(self):
        self.cfg.max_session_seconds = .05
        await self.scan([self.post('1'), self.post('2')])
        self.assertEqual(self.requests, [])
        self.assertEqual({i['status'] for i in self.state.status()['items'].values()}, {'deferred'})

    async def test_manual_item_accepts_complete_local_evidence_and_preserves_human_files(self):
        old = self.post(created_at='', source_media_complete=False, source_media_count=None, media_complete=False)
        self.state.begin('old', [old], {}, self.now)
        self.save(old)
        self.state.finish(old, self.arc, self.now, archived=True, reason='来源列表不完整')
        directory = self.arc.post_dir(old)
        human = directory / 'translated_human.jsonl'
        human.write_bytes(b'{"text":"Human choice"}\n')
        await self.scan([self.post()])
        item = self.state.status()['items'][post_key('instagram', 'target', '1')]
        self.assertEqual(item['status'], 'complete')
        row = self.arc.rows()[0]
        self.assertEqual(row['created_at'], self.now.isoformat())
        self.assertTrue(row['source_media_complete'])
        self.assertEqual(self.requests, [])
        self.assertEqual(human.read_bytes(), b'{"text":"Human choice"}\n')

    async def test_manual_item_with_changed_text_or_bad_local_image_stays_manual(self):
        old = self.post(source_media_complete=False, media_complete=False)
        self.state.begin('old', [old], {}, self.now)
        self.save(old)
        self.state.finish(old, self.arc, self.now, archived=True, reason='来源列表不完整')
        await self.scan([self.post(text='Different source caption')])
        self.assertEqual(self.state.status()['items'][post_key('instagram', 'target', '1')]['status'], 'manual')
        self.assertEqual(self.requests, [])

    async def test_manual_recovery_interrupted_before_attach_never_becomes_automatic_retry(self):
        post = self.post()
        self.state.begin('old', [post], {}, self.now)
        self.state.started(post, self.now)
        self.state.interrupt('old', self.now, 'attempt failed')
        item = self.state.recover(post_key('instagram', 'target', '1'), self.state.status()['revision'], 'checked')
        self.state.interrupt(item['scan_id'], self.now, 'attach failed')
        await self.scan([self.post()])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.state.status()['items'][item['key']]['status'], 'manual')

    async def test_image_transform_or_media_identity_change_cannot_reuse_original(self):
        self.save(self.post())
        for media in [Media('https://cdn.invalid/1?oh=new&oe=456&stp=crop', 'image', source_media_id='1'),
                      Media('https://cdn.invalid/1?oh=new&oe=456', 'image', source_media_id='replacement')]:
            post = self.post(media=[media])
            self.assertIsNone(self.arc.reusable_media(post, media.url))

    async def test_corrupt_local_image_cannot_resolve_manual_item(self):
        old = self.post(source_media_complete=False, media_complete=False)
        self.state.begin('old', [old], {}, self.now)
        self.save(old)
        self.state.finish(old, self.arc, self.now, archived=True, reason='incomplete source')
        (self.arc.base / old.media[0].local_path).write_bytes(b'broken image')
        await self.scan([self.post()])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.state.status()['items'][post_key('instagram', 'target', '1')]['status'], 'manual')

    async def test_deferred_candidate_requires_fresh_observation_before_processing(self):
        self.cfg.max_session_seconds = .01
        await self.scan([self.post('1'), self.post('2')])
        self.cfg.max_session_seconds = 300
        self.now += timedelta(hours=4)
        self.cfg.scan_id = 'next-scan'
        await self.scan([self.post('2')])
        self.assertEqual([r['post_id'] for r in self.arc.rows()], ['2'])
        self.assertEqual(self.state.status()['items'][post_key('instagram', 'target', '1')]['status'], 'deferred')

    async def test_local_resolution_enqueues_only_current_result(self):
        old = self.post(source_media_complete=False, media_complete=False)
        self.state.begin('old', [old], {}, self.now)
        self.save(old)
        self.state.finish(old, self.arc, self.now, archived=True, reason='incomplete source')
        await self.scan([self.post()])
        settings = FeishuSettings(True, 'http://review.internal')
        outbox = Outbox(self.root / 'state' / 'feishu_outbox.json', settings)
        runtime = SimpleNamespace(c=SimpleNamespace(state_dir=self.root / 'state'), settings=settings,
                                  scan_reports=True, outbox=outbox)
        Runtime.enqueue_capture_results(runtime, self.now)
        events = json.loads(outbox.path.read_text('utf-8'))['events'].values()
        saved = [e for e in events if e['kind'] == 'monitor_saved']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['payload']['capture_status'], '完整')
        self.assertFalse(any(e['kind'] == 'system' for e in events))

    async def test_unchanged_result_remains_deliverable_after_a_new_scan_id(self):
        post = self.post()
        await self.scan([post])
        self.now += timedelta(hours=4)
        self.cfg.scan_id = 'second'
        await self.scan([self.post(created_at=post.created_at,
            media=[Media('https://cdn.invalid/replacement', 'image', source_media_id='1')])])
        self.assertEqual(len(self.state.status()['events']), 1)
        settings = FeishuSettings(True, 'http://review.internal')
        outbox = Outbox(self.root / 'state' / 'feishu_outbox.json', settings)
        runtime = SimpleNamespace(c=SimpleNamespace(state_dir=self.root / 'state'), settings=settings,
                                  scan_reports=True, outbox=outbox)
        Runtime.enqueue_capture_results(runtime, self.now)
        self.assertTrue(outbox.path.exists(), 'Unchanged current result must still reach the outbox')
        events = json.loads(outbox.path.read_text('utf-8'))['events'].values()
        self.assertEqual(sum(e['kind'] == 'monitor_saved' for e in events), 1)

    async def test_notification_tick_during_new_attempt_preserves_unreported_prior_result(self):
        post = self.post()
        await self.scan([post])
        settings = FeishuSettings(True, 'http://review.internal')
        outbox = Outbox(self.root / 'state' / 'feishu_outbox.json', settings)
        runtime = SimpleNamespace(c=SimpleNamespace(state_dir=self.root / 'state'), settings=settings,
                                  scan_reports=True, outbox=outbox)
        self.state.begin('second', [self.post()], {r['post_id']: r for r in self.arc.rows()}, self.now)
        Runtime.enqueue_capture_results(runtime, self.now)
        self.assertFalse(next(iter(self.state.status()['events'].values()))['acknowledged'])
        self.state.finish(post, self.arc, self.now, archived=True)
        Runtime.enqueue_capture_results(runtime, self.now)
        self.assertTrue(outbox.path.exists())


if __name__ == '__main__':
    unittest.main()

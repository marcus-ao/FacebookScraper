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

    async def test_rotating_video_address_is_not_re_captured_every_scan(self):
        # 2026-09-22 服务机那轮：视线内 3 篇视频帖全被算进"处理已有帖"，8 篇图片帖一篇没有。
        # 图片有 sha256 可比，视频没有，于是判据落到地址上——而地址每次响应都换一批。
        posted_at = (self.now + timedelta(seconds=1)).isoformat()

        def video(url):  # 发布时刻固定，两轮之间只有地址变了。
            return self.post('v1', created_at=posted_at,
                             media=[Media(url, 'video', source_media_id='17900000000000001')])

        first, _ = await self.scan([video('https://instagram.fbom33-1.fna.fbcdn.net/o1/v/t2/f2/m86/'
                                          'AQN1aaa.mp4?vs=17800000000000001_1&oh=00_AfAaaa&oe=6AA7C003')])
        self.assertEqual((first.new, first.upgraded), (1, 0))
        self.now += timedelta(hours=4)
        self.dcfg.scan_id = 'scan-two'
        second, _ = await self.scan([video('https://instagram.flhe5-2.fna.fbcdn.net/o1/v/t2/f2/m86/'
                                           'AQN9zzz.mp4?vs=17800000000000001_2&oh=00_AfAzzz&oe=6AB0FF77')])
        self.assertEqual((second.new, second.upgraded), (0, 0),
                         '同一条视频不该每轮都算一次"处理已有帖" —— 那会让这个数字永远读不出健康')
        self.assertEqual(second.summary().count('处理已有帖 0 篇'), 1)

    async def test_deferred_mixed_post_closes_locally_when_the_video_address_rotated(self):
        # 退役回 deferred 的那批里有图文+视频的混合帖。本地证据早就齐了，只有视频地址在变；
        # 拿地址比视频会让 reconcile_local 拒绝收尾，把一篇白白推去再采一次。
        posted_at = (self.now + timedelta(seconds=1)).isoformat()
        address = ('https://instagram.f%s.fna.fbcdn.net/o1/v/t2/f2/m86/AQN%s.mp4'
                   '?vs=17800000000000001_%d&oh=00_AfA%s&oe=6AA7C00%d')

        def mixed(host, token, serial):
            return self.post('m1', created_at=posted_at, source_media_count=2,
                             media=[Media('https://cdn.invalid/m1', 'image', source_media_id='17900000000000001'),
                                    Media(address % (host, token, serial, token, serial), 'video',
                                          source_media_id='17900000000000002')])

        await self.scan([mixed('bom33-1', 'aaa', 1)])
        row = self.arc.rows()[0]
        self.assertTrue(row['media_complete'])
        key = post_key('instagram', 'target', 'm1')
        self.now += timedelta(hours=4)
        # 后一轮把它重新收进候选后被预算拦下 —— 那 33 条退役项就是这个形状。
        self.state.begin('scan-two', [mixed('lhe5-2', 'bbb', 2)], {row['post_id']: row}, self.now)
        self.state.interrupt('scan-two', self.now, '本轮预算耗尽')
        self.assertEqual(self.state.status()['items'][key]['status'], 'deferred')

        fresh = mixed('mrs2-1', 'ccc', 3)  # 再下一轮，地址又换了一批
        for media in fresh.media:
            if media.kind == 'image':
                delta.reuse_image(self.arc, fresh, media)
        self.assertTrue(self.state.reconcile_local(fresh, self.arc, self.now),
                        '视频地址轮换不该挡住本地证据收尾 —— 否则这一篇还要再采一次')
        self.assertEqual(self.state.status()['items'][key]['status'], 'complete')
        self.assertEqual(self.ctx.request.get.await_count, 1, '收尾全靠本地证据，没有新的平台请求')

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

    async def test_expired_signed_url_is_refreshed_only_on_explicit_recovery(self):
        # oe=60000000 是 2020 年；归档里这种地址再请求只会拿到 403，重下多少次都一样。
        expired = 'https://scontent-a1.cdninstagram.com/v/t51/99_n.jpg?stp=dst-jpg&oe=60000000&_nc_ht=a1'
        fresh = 'https://scontent-b2.cdninstagram.com/v/t99/99_n.jpg?stp=dst-jpg&oe=7FFFFFFF&_nc_ht=b2'
        stale = self.post(media=[Media(expired, 'image')])
        self.ctx.request.get.side_effect = OSError('fixture expired url')
        await self.scan([stale])
        key = post_key('instagram', 'target', '1')
        self.assertEqual(self.state.status()['items'][key]['status'], 'manual')

        # 自动轮次不变：同一篇再扫一次仍然不开详情，靠的是 begin() 跳过人工项。
        detail = AsyncMock(return_value=(self.col, 'https://www.instagram.com/p/1/'))
        item = self.state.recover(key, self.state.status()['revision'], 'fixture refresh')
        self.dcfg.scan_id, self.dcfg.run_kind = item['scan_id'], 'recovery'
        self.ctx.request.get.side_effect = self.response
        with patch.object(delta, 'scan_page', detail):
            with patch.object(delta, 'extract', side_effect=[[self.post(media=[Media(fresh, 'image')])]]):
                await delta.capture_post(self.ctx, 'instagram', 'target', self.arc, self.dcfg,
                                         self.post(media=[Media(expired, 'image')]), lifecycle=self.state)
        self.assertEqual(detail.await_count, 1)
        self.assertEqual(self.ctx.request.get.await_args.args[0], fresh)
        result = self.state.status()['items'][key]
        self.assertEqual((result['status'], result['saved_images']), ('complete', 1))
        self.assertTrue(self.arc.rows()[0]['media_complete'])

    async def test_recovery_keeps_replaying_a_url_whose_expiry_is_unknown(self):
        """没有 `oe` 就不知道失效时刻，不能凭猜测去开详情——那是额外的平台访问。"""
        self.ctx.request.get.side_effect = OSError('fixture failure')
        await self.scan([self.post()])
        key = post_key('instagram', 'target', '1')
        item = self.state.recover(key, self.state.status()['revision'], 'fixture retry')
        self.dcfg.scan_id, self.dcfg.run_kind = item['scan_id'], 'recovery'
        self.ctx.request.get.side_effect = self.response
        with patch.object(delta, 'scan_page', AsyncMock(side_effect=AssertionError('no detail visit'))):
            await delta.capture_post(self.ctx, 'instagram', 'target', self.arc, self.dcfg,
                                     self.post(), lifecycle=self.state)
        self.assertEqual(self.state.status()['items'][key]['status'], 'complete')

    async def test_retiring_stale_manual_keeps_real_failures_and_needs_cas(self):
        """复现服务机现场：旧 interrupt() 把未开始的候选也打成 manual，之后永远不再排程。"""
        self.ctx.request.get.side_effect = OSError('fixture failure')
        await self.scan([self.post('started')])
        never = post_key('instagram', 'target', 'never')
        data = self.state.status()
        # 按 23c719d 之前的写法伪造一条：只有 manual，没有 attempt_started_at。
        data['items'][never] = dict(data['items'][post_key('instagram', 'target', 'started')],
                                    key=never, status='manual', attempt_started_at=None, recovery=False,
                                    reason='本轮会话中断或预算耗尽，未处理候选已转人工',
                                    source=dict(self.post('never').to_row()))
        self.state._save(data)
        revision = self.state.status()['revision']

        with self.assertRaises(CaptureStateError):
            self.state.retire_stale_manual(revision - 1, 'stale revision')
        with self.assertRaises(CaptureStateError):
            self.state.retire_stale_manual(revision, '   ')

        result = self.state.retire_stale_manual(revision, '23c719d 之前的漏判')
        self.assertEqual([row['key'] for row in result['retired']], [never])
        self.assertEqual([row['key'] for row in result['kept_manual']],
                         [post_key('instagram', 'target', 'started')])
        items = self.state.status()['items']
        self.assertEqual(items[never]['status'], 'deferred')
        self.assertIn('23c719d 之前的漏判', items[never]['reason'])
        # 真实失败过的那条原样保留：退役不能把它藏起来。
        self.assertEqual(items[post_key('instagram', 'target', 'started')]['status'], 'manual')
        # 投递账本不因重新分类而改写。
        self.assertEqual(len(self.state.status()['events']), len(data['events']))

    async def test_retired_item_becomes_a_candidate_again_without_new_requests(self):
        self.ctx.request.get.side_effect = OSError('fixture failure')
        await self.scan([self.post('1')])
        key = post_key('instagram', 'target', '1')
        data = self.state.status()
        data['items'][key].update(attempt_started_at=None)
        self.state._save(data)
        self.state.retire_stale_manual(self.state.status()['revision'], 'fixture retire')
        self.assertEqual(self.state.status()['items'][key]['status'], 'deferred')
        # deferred 不再被 begin() 跳过，所以这一篇能重新进候选并被正常处理。
        self.ctx.request.get.side_effect = self.response
        self.now += timedelta(hours=2)  # 主页配额到点；退役本身不改配额。
        self.dcfg.scan_id = 'scan-two'
        await self.scan([self.post('1')])
        self.assertEqual(self.state.status()['items'][key]['status'], 'complete')

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

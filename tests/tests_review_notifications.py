"""通知使用当前有效稿，每帖计一次；已尝试投递的内容保持冻结。"""
import json
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from capture_fixtures import record_capture_rows
from core import paid_consent
from core.store import post_dirname
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
from core.feishu import notification_card
from core.capture_state import CaptureState
from pipeline import engine, notifications
from pipeline.service import Runtime
from publish import journal


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WebReviewTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.now = datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc)
        self.runtime = Runtime(detector=Mock())
        self.addCleanup(self.runtime.close)
        self.runtime.settings = FeishuSettings(True, 'http://review.internal')
        self.runtime.outbox = Outbox(cfg().state_dir / 'feishu_outbox.json', self.runtime.settings)
        self.runtime.client = Mock()
        self.runtime.client.send.return_value = 'bot-accepted:fixture'

    def event(self, identifier, kind='ready_to_publish'):
        engine.append_human_item(cfg().state_dir, engine.HumanItem(identifier, kind,
            ('facebook:' + self.f.post_id,), '请核对人工稿',
            {'source_text_sha256': journal.text_sha256(self.f.source['text']),
             'text_de_preview': 'obsolete preview'}), self.now)

    def events(self):
        return json.loads(self.runtime.outbox.path.read_text('utf-8'))['events']

    def test_monitor_only_runtime_delivers_confirmed_receipts_without_content_processing(self):
        night = self.now.replace(hour=15)
        self.assertFalse(self.runtime.process)
        self.runtime.outbox.enqueue('scheduled:original', 'scheduled', {'text': 'confirmed'}, night)
        self.runtime.outbox.enqueue('ready:other', 'ready', {'text': 'not approved'}, night)
        self.runtime._deliver(night)
        self.runtime.client.send.assert_called_once()
        self.assertEqual(self.runtime.client.send.call_args.args[0], 'publish')
        delivered = self.runtime.outbox._load()['deliveries']
        self.assertEqual([d['kind'] for d in delivered.values()], ['scheduled'])

    def test_one_post_with_several_issues_is_one_ready_and_one_backlog(self):
        self.event('one')
        self.event('two', 'human_translation_stale')
        self.runtime.collect([self.f.account], self.now)
        ready = [e for e in self.events().values() if e['kind'] == 'ready']
        self.assertEqual(len(ready), 1)
        backlog = [e for e in self.events().values() if e['kind'] == 'backlog']
        self.assertIn('1 篇待审', backlog[0]['payload']['text'])

    def test_backlog_preserves_each_platform_count_age_and_review_entry(self):
        self.event('facebook-ready')
        account = self.f.account.parent / 'in_neakasa.global'
        source = dict(self.f.source, post_id='instagram-probe', platform='instagram',
                      account='neakasa.global', owner='neakasa.global')
        folder = 'posts/' + post_dirname(source['post_id'], source['created_at'])
        source['media'] = [{'kind': 'image', 'local_path': folder + '/01.jpg'}]
        post = account / folder
        post.mkdir(parents=True)
        (post / '01.jpg').write_bytes((self.f.post_dir / '01.jpg').read_bytes())
        (post / 'post.json').write_text(json.dumps(source), encoding='utf-8', newline='')
        (account / 'manifest.jsonl').write_text(json.dumps(source) + '\n', encoding='utf-8', newline='')
        engine.append_human_item(cfg().state_dir, engine.HumanItem('instagram-ready',
            'ready_to_publish', ('instagram:' + source['post_id'],), 'ready',
            {'source_text_sha256': journal.text_sha256(source['text'])}),
            self.now - timedelta(hours=5))
        for _ in range(2):
            self.runtime.collect([self.f.account, account], self.now)
        backlog = [event for event in self.events().values() if event['kind'] == 'backlog']
        self.assertEqual(len(backlog), 2)
        by_platform = {event['payload']['platform']: event['payload'] for event in backlog}
        self.assertIn('1 篇待审，最早一篇已等待 0 小时', by_platform['facebook']['text'])
        self.assertIn('1 篇待审，最早一篇已等待 5 小时', by_platform['instagram']['text'])
        for platform, payload in by_platform.items():
            card = notification_card('backlog', [payload], self.runtime.settings)
            self.assertIn(platform.capitalize(), card['elements'][0]['fields'][0]['text']['content'])
            buttons = [button for part in card['elements'] if part['tag'] == 'action'
                       for button in part['actions']]
            self.assertEqual(buttons[0]['url'], 'http://review.internal/review/' + platform)

    def test_effective_caption_and_german_preview_replace_old_event_snapshot(self):
        self.event('one')
        self.f.save('Aktueller deutscher Text. #Neakasa')
        self.f.write_generated_image('Ein sauberes Zuhause. #Neakasa')
        self.runtime.collect([self.f.account], self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.assertIn('Aktueller deutscher Text', payload['text'])
        self.assertNotIn('obsolete', payload['text'])
        self.runtime.prepare_preview(payload)
        # 图不再随卡片走，但"当前有效首图是德语图"这个判断依据要留在卡片上。
        self.assertEqual(payload['image_variant'], 'de')
        self.assertIn('德语首图', payload['image_note'])
        self.assertIn('未随卡片投递', payload['image_note'])

    def test_original_fallback_is_explicit(self):
        self.event('one')
        self.runtime.collect([self.f.account], self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.runtime.prepare_preview(payload)
        self.assertEqual(payload['image_variant'], 'original')
        self.assertIn('原图', payload['image_note'])

    def test_ready_and_waiting_timestamps_come_from_matching_business_events(self):
        self.event('waiting', 'unmapped_price')
        self.runtime.collect([self.f.account], self.now + timedelta(hours=1))
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.assertIsNone(payload.get('ready_at'))
        self.assertEqual(datetime.fromisoformat(payload['occurred_at']), self.now)
        self.assertEqual(payload.get('risk_status'), 'not_scanned')
        self.now += timedelta(minutes=15)
        self.event('ready')
        self.runtime.collect([self.f.account], self.now + timedelta(hours=2))
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.assertEqual(datetime.fromisoformat(payload['ready_at']), self.now)

    def test_batch_uses_latest_source_and_actual_discovery_instead_of_delivery_time(self):
        rows = [dict(event_id='old', scan_id='scan', key='old', classification='historical',
                     eligible=False, first_seen_at='2026-09-22T01:01:00Z',
                     source={'post_id': 'old', 'created_at': '2026-09-01T00:00:00Z', 'text': 'Old'}),
                dict(event_id='new', scan_id='scan', key='new', classification='new',
                     eligible=False, first_seen_at='2026-09-22T02:01:00Z',
                     source={'post_id': 'new', 'created_at': '2026-09-22T02:00:00Z', 'text': 'Newest'})]
        found, saved = notifications.scan_cards('delta', 'facebook', 'example', rows, {}, self.now)
        self.assertEqual(found['published_at'], '2026-09-22T02:00:00Z')
        self.assertEqual(found['discovered_at'], '2026-09-22T02:01:00Z')
        self.assertEqual(found['counts'], {'historical': 1, 'new': 1})
        self.assertEqual(found['text'], 'Newest')
        self.assertEqual(saved, [])

    def test_long_morning_report_preserves_counts_and_paid_recovery_advice(self):
        now = self.now.replace(hour=0)
        reason = '原处理进程已退出；可能已经产生付费请求，未自动重放'
        activity = {'discovered': 120, 'reconcile_discovered': 15, 'reconcile_skipped': 2,
                    'reconcile_skipped_platforms': ['facebook', 'instagram'],
                    'skipped': {'video': 12, 'mixed_media': 12, 'no_media': 12, 'no_text': 12}}
        processing = {'status': 'interrupted', 'last_success_duration_minutes': 123.4,
                      'recovery_reason': reason}
        with patch.object(self.runtime, 'activity_summary', return_value=activity), \
                patch.object(self.runtime, 'processing_status', return_value=processing):
            self.runtime.collect([self.f.account], now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'morning')
        card = notification_card('morning', [payload], self.runtime.settings)
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn(reason, rendered)
        self.assertIn('123.4', rendered)
        self.assertIn('120', rendered)
        self.assertIn('15', rendered)
        self.assertIn('未执行兜底 2 次', rendered)
        self.assertEqual(card['header']['template'], 'orange')

    def test_unreadable_lead_image_degrades_its_card_instead_of_the_round(self):
        # 首图读不出只降级这一张图；德语正文已经完成，丢掉整张卡等于白等一轮审校。
        self.event('one')
        (self.f.post_dir / '01.jpg').unlink()
        self.runtime.collect([self.f.account], self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.assertEqual(payload['image_variant'], 'unreadable')
        self.assertIn('审校台', payload['image_note'])
        self.assertIn('Ein sauberes Zuhause', payload['text'])
        self.runtime.prepare_preview(payload)
        self.assertNotIn('image_key', payload)
        alerts = [e for e in self.events().values() if e['kind'] == 'system']
        self.assertTrue(any('facebook:' + self.f.post_id in e['payload']['text'] for e in alerts))
        self.assertTrue(any(e['kind'] == 'backlog' for e in self.events().values()))

    def test_unreadable_review_material_costs_only_its_own_card(self):
        # material() 还要读译文、人工稿和风险扫描；任何一处读不出都不能掀掉本轮其余提醒。
        self.event('one')
        with patch.object(notifications, 'material', side_effect=ValueError('读不出')):
            self.runtime.collect([self.f.account], self.now)
        self.assertEqual([e for e in self.events().values() if e['kind'] == 'ready'], [])
        alerts = [e for e in self.events().values() if e['kind'] == 'system']
        self.assertTrue(any('facebook:' + self.f.post_id in e['payload']['text'] for e in alerts))
        self.assertTrue(any(e['kind'] == 'backlog' for e in self.events().values()))

    def test_changed_source_image_alert_preserves_remote_schedule(self):
        source = self.f.source
        fingerprint = paid_consent.fingerprint(source, self.f.account)
        attempt = journal.PublishAttempt(post_id=source['post_id'], platform='facebook',
            status=journal.STATUS_SCHEDULED, scheduled_at=self.now.isoformat(),
            recorded_at=self.now.isoformat(), text_de_sha256='fixture', source_fingerprint=fingerprint)
        journal.append(cfg().state_dir, attempt)
        before = (cfg().state_dir / 'published.jsonl').read_bytes()
        (self.f.post_dir / '01.jpg').write_bytes(b'changed image')
        self.runtime.collect([self.f.account], self.now)
        changes = [e for key, e in self.events().items() if key.startswith('source-changed:')]
        self.assertEqual(len(changes), 1)
        self.assertIn('图片', changes[0]['payload']['text'])
        self.assertEqual((cfg().state_dir / 'published.jsonl').read_bytes(), before)

    def test_scheduled_source_checks_use_each_records_fingerprint_version(self):
        self.f.source['media'][0]['url'] = 'https://fixture.fbcdn.net/image.jpg?oh=old&oe=123&stp=keep'
        self.f.write_source()
        attempts = {}
        for version in (1, 2):
            attempt = journal.PublishAttempt(post_id=self.f.post_id, platform='facebook',
                status=journal.STATUS_SCHEDULED, scheduled_at=self.now.isoformat(),
                recorded_at=self.now.isoformat(), text_de_sha256='fixture',
                source_fingerprint=paid_consent.fingerprint(self.f.source,self.f.account,version=version),
                source_fingerprint_version=version)
            journal.append(cfg().state_dir,attempt)
            attempts[version] = attempt
        before = (cfg().state_dir/'published.jsonl').read_bytes()
        self.runtime.collect([self.f.account],self.now)
        self.assertFalse(any(key.startswith('source-changed:') for key in self.events()))
        self.f.source['media'][0]['url'] = 'https://fixture.fbcdn.net/image.jpg?oh=new&oe=456&stp=keep'
        self.f.write_source()
        self.runtime.collect([self.f.account],self.now)
        self.assertFalse(any(key.startswith('source-changed:'+attempts[2].attempt_id) for key in self.events()))
        self.assertEqual((cfg().state_dir/'published.jsonl').read_bytes(), before)

    def scanned(self, rows):
        record_capture_rows(self.runtime, rows, self.now, 'facebook')
        self.runtime.scan('delta', 'facebook')
        if not self.runtime.outbox.path.exists():
            return []
        return [e for e in self.events().values() if e['kind'] in {'monitor_found', 'monitor_saved'}]

    def test_the_found_card_shows_the_english_source_even_with_a_german_draft_ready(self):
        # 发现卡的正文来自抓取时记下的英文原文，不读 translated.jsonl——否则在发现阶段
        # 只会显示"德语稿尚未就绪"，而那句话对判断"要不要现在开"毫无帮助。
        self.f.save('Ein sauberes Zuhause. #Neakasa')
        cards = self.scanned([('post_discovered', {
            'post_id': self.f.post_id, 'created_at': self.f.source['created_at'],
            'head': 'A clean home starts here', 'images': 1, 'known': False})])
        found = next(card for card in cards if card['kind'] == 'monitor_found')
        self.assertIn('A clean home', found['payload']['text'])
        self.assertNotIn('sauberes Zuhause', found['payload']['text'])

    def test_a_collaboration_post_is_labelled_instead_of_passing_as_our_own(self):
        cards = self.scanned([('post_discovered', {
            'post_id': self.f.post_id, 'created_at': self.f.source['created_at'],
            'head': 'Gemeinsam', 'images': 2, 'known': False,
            'account': 'neakasaofficial', 'owner': 'partner_us', 'coauthors': ['neakasaofficial']})])
        text = next(c['payload']['origin_note'] for c in cards if c['kind'] == 'monitor_found')
        self.assertIn('合作帖，原作者 partner_us', text)
        self.assertIn('合作方 neakasaofficial', text)

    def test_the_saved_card_states_success_or_failure_for_every_post(self):
        cards = self.scanned([
            ('post_discovered', {'post_id': 'p1', 'created_at': self.f.source['created_at'],
                                 'head': 'erste', 'images': 2, 'known': False}),
            ('post_discovered', {'post_id': 'p2', 'created_at': self.f.source['created_at'],
                                 'head': 'zweite', 'images': 1, 'known': False}),
            ('post_captured', {'post_id': 'p1', 'images': 2, 'videos': 0,
                               'folder': 'posts/2026-09/Riko/2026-09-10_1200_p1'}),
            ('post_capture_incomplete', {'post_id': 'p2'})])
        saved = [c['payload'] for c in cards if c['kind'] == 'monitor_saved']
        self.assertEqual(len(saved), 2)
        self.assertEqual({p['post_id'] for p in saved}, {'p1', 'p2'})
        success = next(p for p in saved if p['post_id'] == 'p1')
        failure = next(p for p in saved if p['post_id'] == 'p2')
        self.assertIn('已保存并校验 2/2 张', json.dumps(success, ensure_ascii=False))
        self.assertIn('待人工', failure['capture_status'])
        self.assertFalse(failure['archived'])

    def test_a_fully_successful_round_says_so_instead_of_staying_silent(self):
        cards = self.scanned([
            ('post_discovered', {'post_id': 'p1', 'created_at': self.f.source['created_at'],
                                 'head': 'alles gut', 'images': 1, 'known': False}),
            ('post_captured', {'post_id': 'p1', 'images': 1, 'videos': 0, 'folder': 'posts/2026-09/未分类/p1'})])
        saved = next(c['payload'] for c in cards if c['kind'] == 'monitor_saved')
        self.assertEqual(saved['capture_status'], '完整')
        self.assertTrue(saved['archived'])

    def test_monitor_cards_go_to_separate_stage_bots_even_off_duty(self):
        self.scanned([
            ('post_discovered', {'post_id': 'p1', 'created_at': self.f.source['created_at'],
                                 'head': 'nachts', 'images': 1, 'known': False}),
            ('post_captured', {'post_id': 'p1', 'images': 1, 'videos': 0, 'folder': 'f'})])
        self.runtime.outbox.enqueue('alert', 'system', {'text': '请检查探测会话'}, self.now)
        sent = []
        night = datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)   # 上海 23:00，离岗
        self.runtime.outbox.dispatch(night, lambda *args: sent.append(args) or 'bot-accepted')
        by_group = {}
        for recipient, card, _delivery in sent:
            by_group.setdefault(recipient, []).append(card['header']['title']['content'])
        self.assertEqual(by_group['detect'], ['[Neakasa 德国] 监测到新发帖'])
        self.assertEqual(by_group['capture'], ['[Neakasa 德国] 原帖抓取完成'])
        self.assertEqual(by_group['alert'], ['[Neakasa 德国] 任务异常告警'])

    def test_publication_projection_preserves_requested_time_and_remote_uncertainty(self):
        from publish.records import queue_notification
        attempt = {'status': journal.STATUS_SUBMIT_AMBIGUOUS, 'attempt_id': 'uncertain-example',
                   'scheduled_at': '2026-09-23T04:30:00Z', 'recorded_at': '2026-09-22T01:02:03Z'}
        with patch('publish.records.FeishuSettings.load', return_value=self.runtime.settings):
            queue_notification(self.f.account, self.f.source, attempt, self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'schedule_failed')
        card = notification_card('schedule_failed', [payload], self.runtime.settings)
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn('2026-09-23 12:30:00', rendered)
        self.assertIn('2026-09-22 09:02:03', rendered)
        self.assertIn('避免重复提交', rendered)
        self.assertNotIn('远端已确认排期', rendered)

    def test_a_broken_card_costs_the_broadcast_not_the_scan_exit_code(self):
        # 播报是旁路。它抛异常不能改抓取的退出码，否则会被当成"抓取失败"去查浏览器。
        self.runtime.detector = lambda _kind, _platform: 0
        with patch.object(notifications, 'scan_cards', side_effect=ValueError('读不出')):
            code = self.runtime.scan('delta', 'facebook')
        self.assertEqual(code, 0)

    def test_posts_already_in_the_archive_are_not_announced_again(self):
        # 卡片来自抓取事实，不来自遍历归档——这正是与"逐篇扫归档"方案的分水岭。
        # 归档里躺着夹具那一篇，但本轮没有任何抓取事实，所以一张卡都不该有。
        # 回填因此结构上就进不来：它不走 delta_once，一条事实都不会留。
        self.assertTrue((self.f.post_dir / 'post.json').exists())
        self.assertEqual(self.scanned([]), [])

    def test_a_single_cycle_finishes_its_delivery_before_shutting_down(self):
        # --once 提交投递后立刻 close()，cancel_futures 会把还没启动的那个取消掉：
        # 一条消息都发不出去，而且不报错——正好会被误判成"飞书配置有问题"。
        self.runtime.outbox.enqueue('alert', 'system', {'text': '请检查探测会话'}, self.now)
        with patch.object(self.runtime, 'refresh_hashtags'), patch.object(self.runtime, 'refresh_calendar'):
            self.runtime.maintenance(self.now, [])
        self.runtime.await_delivery()
        self.runtime.close()
        self.assertGreater(self.runtime.client.send.call_count, 0)

    def test_event_updates_stop_after_any_delivery_attempt(self):
        box = self.runtime.outbox
        box.enqueue('post', 'ready', {'text': 'old'}, self.now)
        box.enqueue('post', 'ready', {'text': 'current'}, self.now)
        calls = []
        def send(*args):
            calls.append(args)
            raise RuntimeError('unknown')
        box.dispatch(self.now, send)
        box.enqueue('post', 'ready', {'text': 'later edit'}, self.now)
        box.enqueue('new-post', 'ready', {'text': 'another post'}, self.now)
        state = json.loads(box.path.read_text('utf-8'))
        self.assertEqual(state['events']['post']['payload']['text'], 'current')
        self.assertIn('current', json.dumps(calls[0][1]))
        self.assertNotIn('later edit', json.dumps(state['deliveries']))

    def test_skipped_reconcile_is_recorded_and_reaches_technical_and_morning_cards(self):
        now = self.now.replace(hour=0, minute=1)
        result = {'platform': 'instagram', 'kind': 'reconcile', 'exit_code': None,
                  'skipped': '已错过早班处理截止线', 'deadline_at': now.isoformat(), 'business_date': str(now.date())}
        with patch.object(self.runtime, 'refresh_hashtags'), patch.object(self.runtime.delivery_executor, 'submit'):
            self.runtime.maintenance(now, [result])
        self.runtime.collect([self.f.account], now)
        rows = list(self.events().values())
        self.assertTrue(any(row['kind'] == 'system' and 'instagram' in row['payload']['text'] for row in rows))
        self.assertTrue(any(row['kind'] == 'morning' and '未执行兜底 1 次' in row['payload']['text'] for row in rows))

    def test_long_excerpt_preserves_counts_and_per_post_buttons(self):
        cards = self.scanned([
            ('post_discovered', {'post_id': 'one', 'created_at': self.f.source['created_at'],
                                 'head': 'A' * 4000, 'images': 1, 'known': False}),
            ('post_captured', {'post_id': 'one', 'images': 1}),
            ('post_discovered', {'post_id': 'two', 'created_at': self.f.source['created_at'],
                                 'head': 'Second', 'images': 1, 'known': False}),
            ('post_captured', {'post_id': 'two', 'images': 1})])
        saved = [item['payload'] for item in cards if item['kind'] == 'monitor_saved']
        self.assertEqual(len(saved), 2)
        for payload in saved:
            card = notification_card('monitor_saved', [payload], self.runtime.settings)
            rendered = json.dumps(card, ensure_ascii=False)
            self.assertIn('已保存并校验 1/1 张', rendered)
            buttons = [button for part in card['elements'] if part['tag'] == 'action' for button in part['actions']]
            self.assertEqual(buttons[0]['url'], 'http://review.internal/history/fa_neakasaofficial/' + payload['post_id'])
            self.assertIn(payload['post_id'], buttons[1]['url'])
            self.assertFalse(any(part['tag'] == 'img' for part in card['elements']))
            if payload['post_id'] == 'one':
                self.assertIn('A' * 150 + '...', rendered)
                self.assertNotIn('A' * 151, rendered)

    def test_interleaved_delivery_waits_for_final_finish(self):
        self._assert_interleaved_summary(interrupted=False)

    def test_interleaved_delivery_waits_for_restart_recovery(self):
        self._assert_interleaved_summary(interrupted=True)

    def _assert_interleaved_summary(self, *, interrupted):
        from core.store import Archive, Post, Media
        ledger = CaptureState(cfg().state_dir)
        ledger.initialize(cfg().archive_dir, cfg()['targets'], 'fixture', now=self.now - timedelta(days=1))
        account = cfg()['targets']['facebook']
        arc = Archive(cfg().archive_dir, 'fa_' + account)
        posts = [Post(identifier, 'facebook', account, 'English text', self.now.isoformat(),
                      media=[Media('https://cdn.invalid/' + identifier, 'image')],
                      source_media_complete=False) for identifier in ('first', 'second')]
        ledger.begin('interleaved', posts, {}, self.now)
        ledger.finish(posts[0], arc, self.now, reason='fixture missing image')
        self.runtime.enqueue_capture_results(self.now)
        self.runtime.outbox.dispatch(self.now, lambda *args: 'fake accepted')
        self.assertEqual([e['kind'] for e in self.events().values()], ['monitor_saved'])
        self.assertFalse(next(iter(ledger.status()['events'].values()))['acknowledged'])
        # Restart recovery closes the final pending candidate without losing its summary fact.
        if interrupted:
            ledger.started(posts[1], self.now)
            ledger.recover_interrupted(self.now)
        else:
            ledger.finish(posts[1], arc, self.now, reason='fixture missing image')
        self.runtime.enqueue_capture_results(self.now)
        self.runtime.outbox.dispatch(self.now, lambda *args: 'fake accepted')
        self.runtime.enqueue_capture_results(self.now)
        events = list(self.events().values())
        self.assertEqual(sum(e['kind'] == 'monitor_saved' for e in events), 2)
        found = [e for e in events if e['kind'] == 'monitor_found']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['payload']['counts'], {'new': 2})
        manual = [e for e in events if e['kind'] == 'system']
        self.assertEqual(len(manual), 1)
        self.assertIn('2 篇需要人工', manual[0]['payload']['text'])
        self.assertTrue(all(e['acknowledged'] for e in ledger.status()['events'].values()))

    def test_missing_enqueue_replays_from_capture_truth_once(self):
        rows = [('post_discovered', {'post_id': 'replay', 'created_at': self.f.source['created_at'],
                                    'head': 'Recover queue', 'images': 1, 'known': False}),
                ('post_captured', {'post_id': 'replay', 'images': 1})]
        record_capture_rows(self.runtime, rows, self.now, 'facebook')
        self.runtime.detector('delta', 'facebook')
        with patch.object(self.runtime.outbox, 'enqueue', side_effect=RuntimeError('fixture queue busy')):
            self.runtime.enqueue_capture_results(self.now)
        self.assertTrue(any(not row['acknowledged'] for row in CaptureState(cfg().state_dir).status()['events'].values()))
        self.runtime.enqueue_capture_results(self.now)
        self.runtime.enqueue_capture_results(self.now)
        self.assertEqual(len(self.events()), 2)

    def test_unknown_total_is_explicit_and_failure_links_to_issue(self):
        self.scanned([('post_discovered', {'post_id': 'issue', 'created_at': self.f.source['created_at'],
                                         'head': 'Unknown list', 'images': 1, 'known': False})])
        event = next(iter(CaptureState(cfg().state_dir).status()['events'].values()))
        event['source']['source_media_count'] = None
        payload = notifications.capture_card(event)
        card = notification_card('monitor_saved', [payload], self.runtime.settings)
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn('已保存并校验 0 张，原帖总数待确认', rendered)
        self.assertIn('/runtime?capture=facebook%3Aneakasaofficial%3Aissue', rendered)
        self.assertNotIn('/history/', rendered)

    def test_stage_one_maintenance_does_not_start_later_stages_or_send_their_old_cards(self):
        self.runtime.outbox.enqueue('old-ready', 'ready', {'text': 'later stage'}, self.now)
        with patch.object(self.runtime, 'refresh_hashtags') as tags, \
             patch.object(self.runtime, 'refresh_calendar') as calendar, \
             patch.object(self.runtime, 'mirror_sources') as mirror, \
             patch.object(self.runtime, 'collect') as collect:
            self.runtime.maintenance(self.now)
            self.runtime.await_delivery()
            tags.assert_not_called()
            calendar.assert_not_called()
            mirror.assert_not_called()
            collect.assert_not_called()
        self.runtime.client.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

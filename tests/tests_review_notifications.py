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

    def test_one_post_with_several_issues_is_one_ready_and_one_backlog(self):
        self.event('one')
        self.event('two', 'human_translation_stale')
        self.runtime.collect([self.f.account], self.now)
        ready = [e for e in self.events().values() if e['kind'] == 'ready']
        self.assertEqual(len(ready), 1)
        backlog = [e for e in self.events().values() if e['kind'] == 'backlog']
        self.assertIn('1 篇待审', backlog[0]['payload']['text'])

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
        text = next(c['payload']['text'] for c in cards if c['kind'] == 'monitor_found')
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
        self.assertEqual(by_group['detect'], ['Neakasa 德国站 · 监测到新帖 · 新帖检测推送机器人'])
        self.assertEqual(by_group['capture'], ['Neakasa 德国站 · 原帖抓取结果：完整 · 新帖爬取推送机器人'])
        self.assertEqual(by_group['alert'], ['Neakasa 德国站 · 系统需要处理 · 状态告警推送机器人'])

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
                self.assertIn('A' * 300, rendered)
                self.assertNotIn('A' * 301, rendered)
                self.assertIn('已截断', rendered)

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
        self.assertIn('新发布 2 篇', found[0]['payload']['text'])
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

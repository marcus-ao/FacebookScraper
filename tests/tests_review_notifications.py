"""通知使用当前有效稿，每帖计一次；已尝试投递的内容保持冻结。"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import paid_consent
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
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
        self.runtime.settings = FeishuSettings(True, 'http://review.internal', ('operator',), ('developer',))
        self.runtime.outbox = Outbox(cfg().state_dir / 'feishu_outbox.json', self.runtime.settings)
        self.runtime.client = Mock()
        self.runtime.client.upload_image.return_value = 'image-key'

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
        generated = self.f.write_generated_image('Ein sauberes Zuhause. #Neakasa')
        self.runtime.collect([self.f.account], self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.assertIn('Aktueller deutscher Text', payload['text'])
        self.assertNotIn('obsolete', payload['text'])
        self.runtime.prepare_preview('ready', payload)
        self.runtime.client.upload_image.assert_called_once_with(generated)
        self.assertEqual(payload['image_variant'], 'de')

    def test_original_fallback_is_explicit(self):
        self.event('one')
        self.runtime.collect([self.f.account], self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.runtime.prepare_preview('ready', payload)
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
        self.runtime.prepare_preview('ready', payload)
        self.runtime.client.upload_image.assert_not_called()
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

    def route(self, value):
        self.f.source['source_route'] = value
        self.f.write_source()

    def discovered(self):
        return [e for e in self.events().values() if e['kind'] == 'discovered']

    def test_monitored_post_is_announced_once_with_its_english_source(self):
        self.route('delta')
        self.runtime.collect([self.f.account], self.now)
        self.assertEqual(len(self.discovered()), 1)
        self.assertIn('A clean home', self.discovered()[0]['payload']['text'])

    def test_backfilled_history_is_not_announced(self):
        # 回填是人主动滚出来的历史，逐篇推送等于上线第一天刷屏。
        self.route('backfill')
        self.runtime.collect([self.f.account], self.now)
        self.assertEqual(self.discovered(), [])

    def test_repeated_scans_do_not_re_announce_the_same_post(self):
        self.route('delta')
        self.runtime.collect([self.f.account], self.now)
        self.runtime.collect([self.f.account], self.now)
        self.assertEqual(len(self.discovered()), 1)

    def test_discovered_card_carries_the_original_lead_image(self):
        self.route('delta')
        self.runtime.collect([self.f.account], self.now)
        cards = []
        self.runtime.outbox.dispatch(
            self.now, lambda recipient, card, delivery: cards.append(card) or 'message',
            prepare_payload=self.runtime.prepare_preview)
        self.assertTrue(any(element.get('tag') == 'img' for element in cards[0]['elements']))
        self.runtime.client.upload_image.assert_called_once_with((self.f.post_dir / '01.jpg').read_bytes())

    def test_unreadable_lead_image_degrades_the_discovery_card_instead_of_dropping_it(self):
        # 发现卡是监测链路唯一的存活信号。少一张图就整条不推，运营侧看起来和"今天没有新帖"一样。
        self.route('delta')
        (self.f.post_dir / '01.jpg').unlink()
        self.runtime.collect([self.f.account], self.now)
        self.assertEqual(len(self.discovered()), 1)
        payload = self.discovered()[0]['payload']
        self.assertIn('A clean home', payload['text'])
        self.assertIn('审校台', payload['image_note'])
        alerts = [e for e in self.events().values() if e['kind'] == 'system']
        self.assertTrue(any('facebook:' + self.f.post_id in e['payload']['text'] for e in alerts))

    def test_unreadable_discovery_material_costs_only_its_own_card(self):
        # 首图之外还要读 post.json 与型号表；读不出的那一篇不能连累本轮其余发现。
        self.route('delta')
        with patch.object(notifications, 'discovered_material', side_effect=ValueError('读不出')):
            self.runtime.collect([self.f.account], self.now)
        self.assertEqual(self.discovered(), [])
        alerts = [e for e in self.events().values() if e['kind'] == 'system']
        self.assertTrue(any('facebook:' + self.f.post_id in e['payload']['text'] for e in alerts))

    def test_discovered_material_shows_english_source_even_when_a_german_draft_exists(self):
        material, path = notifications.discovered_material(self.f.account, self.f.source)
        self.assertIn('A clean home', material['text'])
        self.assertNotIn('sauberes Zuhause', material['text'])
        self.assertEqual(material['image_count'], 1)
        self.assertEqual(path, self.f.post_dir / '01.jpg')

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


if __name__ == '__main__':
    unittest.main()

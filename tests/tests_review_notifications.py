"""通知使用当前有效稿，每帖计一次；已尝试投递的内容保持冻结。"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import paid_consent
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
from pipeline import engine
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
        self.runtime.prepare_preview(payload)
        self.runtime.client.upload_image.assert_called_once_with(generated)
        self.assertEqual(payload['image_variant'], 'de')

    def test_original_fallback_is_explicit(self):
        self.event('one')
        self.runtime.collect([self.f.account], self.now)
        payload = next(e['payload'] for e in self.events().values() if e['kind'] == 'ready')
        self.runtime.prepare_preview(payload)
        self.assertEqual(payload['image_variant'], 'original')
        self.assertIn('原图', payload['image_note'])

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


if __name__ == '__main__':
    unittest.main()

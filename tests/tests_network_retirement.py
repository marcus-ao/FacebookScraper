"""Old queued alerts are retired without deleting original cards or unknown receipts."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.feishu import Outbox, FeishuSettings
from core.monitoring import detection_failure_kind


class RetirementTests(unittest.TestCase):
    def test_old_alert_not_sent_but_other_alert_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = Outbox(Path(tmp) / 'outbox.json', FeishuSettings(True, 'https://review.invalid'))
            now = datetime.now(timezone.utc)
            box.enqueue('network:old', 'system', {'text': 'old'}, now)
            box.enqueue('capture:failed', 'system', {'text': 'still relevant'}, now)
            sent = []
            box.dispatch(now, lambda *args: sent.append(args) or 'receipt')
            self.assertEqual(len(sent), 1)
            self.assertIn('still relevant', json.dumps(sent))
            data = json.loads(box.path.read_text('utf-8'))
            self.assertIn('cancelled_at', data['events']['network:old'])
            self.assertEqual(data['events']['network:old']['payload'], {'text': 'old'})

    def test_pending_mixed_card_rebuilt_and_uncertain_card_preserved(self):
        for status in ('pending', 'uncertain', 'sent'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                box = Outbox(Path(tmp) / 'outbox.json', FeishuSettings(True, 'https://review.invalid'))
                now = datetime.now(timezone.utc)
                for key in ('network-change:old', 'other'):
                    box.enqueue(key, 'system', {'text': key}, now)
                data = json.loads(box.path.read_text('utf-8'))
                original = {'original': 'frozen card'}
                data['deliveries']['mixed'] = {'events': list(data['events']), 'kind': 'system',
                    'recipient': 'alert', 'card': original, 'status': status, 'attempts': 0 if status == 'pending' else 1,
                    'next_at': now.isoformat(), 'created_at': now.isoformat()}
                box.path.write_text(json.dumps(data), encoding='utf-8')
                sent = []
                box.dispatch(now, lambda *args: sent.append(args) or 'receipt')
                updated = json.loads(box.path.read_text('utf-8'))
                self.assertEqual(updated['deliveries']['mixed']['card'], original)
                self.assertEqual(updated['deliveries']['mixed']['status'], 'cancelled' if status == 'pending' else status)
                self.assertEqual(len(sent), 1 if status == 'pending' else 0)
                if sent:
                    self.assertNotIn('network-change', json.dumps(sent))

    def test_actual_platform_failures_still_classify(self):
        self.assertEqual(detection_failure_kind('HTTP 429'), 'rate_limited')
        self.assertEqual(detection_failure_kind('/checkpoint'), 'account_checkpoint')
        self.assertEqual(detection_failure_kind('connection timeout'), 'connection_or_timeout')


if __name__ == '__main__':
    unittest.main()

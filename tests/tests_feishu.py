"""飞书用假 HTTP 和注入时钟验证；不会联系真实收件人。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.feishu import FeishuClient, FeishuSettings, Outbox


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'outbox.json'
        self.settings = FeishuSettings(True, 'http://review.internal:8765',
                                       ('operator',), ('developer',))

    def test_empty_and_disabled_are_silent(self):
        outbox = Outbox(self.path, self.settings)
        self.assertEqual(outbox.dispatch(at('2026-09-12T10:00:00+08:00'),
                                        lambda *args: self.fail('unexpected send')), 0)

    def test_overnight_merge_survives_restart_and_keeps_every_task(self):
        outbox = Outbox(self.path, self.settings)
        night = at('2026-09-11T23:00:00+08:00')
        for i in range(3):
            self.assertTrue(outbox.enqueue(f'ready-{i}', 'ready',
                                          {'task_id': f'account/{i}', 'text': f'Deutsch {i}'}, night))
        calls = []
        self.assertEqual(outbox.dispatch(night, lambda *args: calls.append(args)), 0)
        restarted = Outbox(self.path, self.settings)
        self.assertEqual(restarted.dispatch(at('2026-09-12T08:00:00+08:00'),
                                           lambda *args: calls.append(args) or 'message'), 1)
        self.assertEqual(calls[0][0], 'operator')
        rendered = json.dumps(calls[0][1], ensure_ascii=False)
        self.assertTrue(all(f'Deutsch {i}' in rendered for i in range(3)))
        self.assertIn('task=account%2F0', rendered)
        self.assertEqual(restarted.dispatch(at('2026-09-12T08:01:00+08:00'),
                                           lambda *args: self.fail('duplicate')), 0)

    def test_urgent_only_goes_to_technical_group_during_quiet_hours(self):
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-11T23:00:00+08:00')
        outbox.enqueue('alert', 'system', {'text': '会话失效，请手动登录'}, now)
        outbox.enqueue('failed', 'schedule_failed', {'text': '请重新核对排期'}, now)
        calls = []
        outbox.dispatch(now, lambda *args: calls.append(args) or 'ok')
        self.assertEqual([call[0] for call in calls], ['developer'])
        outbox.dispatch(at('2026-09-12T08:00:00+08:00'), lambda *args: calls.append(args) or 'ok')
        self.assertEqual(calls[-1][0], 'operator')

    def test_daytime_ready_items_are_individual_reminders(self):
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        for number in range(2):
            outbox.enqueue(str(number), 'ready', {'text': 'Deutsch'}, now)
        self.assertEqual(outbox.dispatch(now, lambda *args: 'sent'), 2)

    def test_notification_retry_keeps_delivery_id_and_has_backoff(self):
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('a', 'ready', {'text': 'Hallo'}, now)
        ids = []
        def fails(recipient, card, delivery_id):
            ids.append(delivery_id)
            raise RuntimeError('untrusted detail must not be stored')
        outbox.dispatch(now, fails)
        outbox.dispatch(at('2026-09-12T09:01:00+08:00'), fails)
        self.assertEqual(len(ids), 1)
        outbox.dispatch(at('2026-09-12T09:16:00+08:00'), fails)
        self.assertEqual(ids[0], ids[1])
        self.assertNotIn('untrusted detail', self.path.read_text(encoding='utf-8'))

    def test_http_contract_uses_tenant_token_and_card_json_string(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.url.path.endswith('/internal'):
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'token', 'expire': 7200})
            self.assertEqual(request.headers['authorization'], 'Bearer token')
            body = json.loads(request.content)
            self.assertEqual(body['msg_type'], 'interactive')
            self.assertIsInstance(body['content'], str)
            self.assertEqual(body['uuid'], 'stable-uuid')
            self.assertEqual(request.url.params['receive_id_type'], 'open_id')
            return httpx.Response(200, json={'code': 0, 'data': {'message_id': 'message'}})
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = FeishuClient('test-id', 'test-secret', http=http)
            self.assertEqual(client.send_card('operator', {'header': {}}, 'stable-uuid'), 'message')
        self.assertEqual(len(requests), 2)

    def test_cancelled_review_is_not_retried_with_an_outdated_group(self):
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('a', 'ready', {'text': 'Old draft'}, now)
        def timeout(*args):
            raise TimeoutError()
        outbox.dispatch(now, timeout)
        outbox.retain_ready(set(), now)
        outbox.dispatch(at('2026-09-12T09:20:00+08:00'),
                        lambda *args: self.fail('stale review must not be sent again'))
        delivery = next(iter(json.loads(self.path.read_text(encoding='utf-8'))['deliveries'].values()))
        self.assertEqual(delivery['status'], 'uncertain')
        self.assertEqual(delivery['attempts'], 1)

    def test_upload_uses_message_image_multipart_and_reuses_authentication(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.url.path.endswith('/internal'):
                return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'token', 'expire': 7200})
            self.assertEqual(request.url.path, '/open-apis/im/v1/images')
            self.assertIn('multipart/form-data', request.headers['content-type'])
            self.assertIn(b'name="image_type"', request.content)
            self.assertIn(b'message', request.content)
            self.assertIn(b'image-fixture', request.content)
            return httpx.Response(200, json={'code': 0, 'data': {'image_key': 'image-key'}})
        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = FeishuClient('id', 'secret', http=http)
            self.assertEqual(client.upload_image(b'image-fixture'), 'image-key')
            client.upload_image(b'image-fixture')
        self.assertEqual(len(requests), 3)

    def test_preview_is_prepared_only_when_sending_and_frozen_across_retries(self):
        outbox = Outbox(self.path, self.settings)
        night = at('2026-09-11T23:00:00+08:00')
        outbox.enqueue('a', 'ready', {'task_id': 'account/a'}, night)
        prepared, sent = [], []
        def prepare(payload):
            prepared.append(payload['task_id'])
            payload['image_key'] = 'uploaded-key'
        def fails(recipient, card, delivery_id):
            sent.append(card)
            raise TimeoutError()
        outbox.dispatch(night, fails, prepare_payload=prepare)
        self.assertFalse(prepared)
        outbox.dispatch(at('2026-09-12T08:00:00+08:00'), fails, prepare_payload=prepare)
        outbox.dispatch(at('2026-09-12T08:16:00+08:00'), fails, prepare_payload=prepare)
        self.assertEqual(prepared, ['account/a'])
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(sent[0]['elements'][0]['img_key'], 'uploaded-key')


if __name__ == '__main__':
    unittest.main()

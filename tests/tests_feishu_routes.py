"""四机器人路由、旧发件箱与未知投递结果；全部使用隔离文件和假 HTTP。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.feishu import FeishuError, FeishuSettings, Outbox, WEBHOOK_PREFIX, WebhookBot

EXPECTED = {'monitor_found': 'detect', 'monitor_saved': 'capture', 'ready': 'publish',
            'scheduled': 'publish', 'backlog': 'alert', 'schedule_failed': 'alert',
            'morning': 'alert', 'system': 'alert'}
TARGETS = {role: WEBHOOK_PREFIX + role + '-fixture' for role in ('detect', 'capture', 'publish', 'alert')}
NOW = datetime(2026, 9, 15, 1, tzinfo=timezone.utc)


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'outbox.json'
        self.settings = FeishuSettings(True, 'http://review.internal:8765')
        self.outbox = Outbox(self.path, self.settings)

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8', newline='')

    def client(self, handle):
        client = WebhookBot(TARGETS, http=httpx.Client(transport=httpx.MockTransport(handle)))
        self.addCleanup(client.close)
        return client

    def test_all_eight_kinds_route_and_archive_after_retention(self):
        seen = []
        for kind in EXPECTED:
            self.outbox.enqueue(kind, kind, {'text': kind}, NOW)
        self.assertEqual(self.outbox.dispatch(NOW, lambda role, card, identifier: seen.append(role) or identifier), 8)
        original = self.read()
        self.assertEqual({row['kind']: row['recipient'] for row in original['deliveries'].values()}, EXPECTED)
        self.assertEqual(sorted(seen), sorted(EXPECTED.values()))
        self.outbox.dispatch(NOW + timedelta(days=31), lambda *_: self.fail('duplicate'))
        self.assertEqual(self.read()['events'], {})
        for kind in EXPECTED:
            event = self.outbox.archived_event(kind)
            self.assertEqual(next(iter(event['deliveries'].values()))['recipient'], EXPECTED[kind])
            self.assertFalse(self.outbox.enqueue(kind, kind, {'text': 'again'}, NOW + timedelta(days=32)))

    def test_quiet_hours_depend_on_kind_instead_of_the_shared_alert_bot(self):
        night = NOW.replace(hour=15)
        for kind in EXPECTED:
            self.outbox.enqueue(kind, kind, {'text': kind}, night)
        self.outbox.dispatch(night, lambda *_: 'receipt')
        sent = {item['kind']: item['recipient'] for item in self.read()['deliveries'].values()}
        self.assertEqual(sent, {'monitor_found': 'detect', 'monitor_saved': 'capture', 'system': 'alert'})

    def legacy(self, status='sent', attempts=1):
        delivery = {'events': ['a'], 'kind': 'monitor_saved', 'recipient': 'ops',
                    'card': {'elements': [{'text': 'frozen original'}]}, 'status': status,
                    'attempts': attempts, 'next_at': NOW.isoformat(), 'created_at': NOW.isoformat()}
        if status == 'sent':
            delivery.update(sent_at=NOW.isoformat(), message_id='bot-accepted:old-delivery')
        return {'schema_version': 1, 'events': {'a': {'kind': 'monitor_saved',
                'payload': {'text': 'latest'}, 'created_at': NOW.isoformat()}},
                'deliveries': {'old-delivery': delivery}, 'archived_events': {}}

    def test_legacy_sent_receipt_is_not_resent_to_the_new_bot(self):
        data = self.legacy()
        self.write(data)
        self.outbox.dispatch(NOW + timedelta(days=31), lambda *_: self.fail('legacy duplicate'))
        archived = self.outbox.archived_event('a')
        self.assertEqual(archived['deliveries'], data['deliveries'])
        self.assertEqual(self.read()['events'], {})

    def test_legacy_unattempted_delivery_can_move_to_the_new_stage(self):
        self.write(self.legacy('pending', 0))
        seen = []
        self.outbox.dispatch(NOW, lambda role, *_: seen.append(role) or 'receipt')
        self.assertEqual(seen, ['capture'])
        old = self.read()['deliveries']['old-delivery']
        self.assertEqual((old['status'], old['recipient'], old['attempts']), ('cancelled', 'ops', 0))
        self.outbox.dispatch(NOW + timedelta(days=31), lambda *_: self.fail('duplicate'))
        self.assertEqual(self.read()['events'], {})

    def test_legacy_attempt_is_held_and_manual_no_delivery_rebinds_frozen_card(self):
        data = self.legacy('retry')
        self.write(data)
        self.outbox.dispatch(NOW, lambda *_: self.fail('unconfirmed legacy replay'))
        delivery = self.outbox.status()['deliveries'][0]
        self.assertEqual(delivery['status'], 'uncertain')
        self.outbox.resolve('old-delivery', action='not_delivered', expected_version=delivery['version'], now=NOW)
        calls = []
        self.outbox.dispatch(NOW, lambda role, card, identifier: calls.append((role, card, identifier)) or 'receipt')
        self.assertEqual(calls[0][:2], ('capture', data['deliveries']['old-delivery']['card']))
        self.assertNotEqual(calls[0][2], 'old-delivery')
        old = self.read()['deliveries']['old-delivery']
        self.assertEqual((old['recipient'], old['status']), ('ops', 'cancelled'))
        self.outbox.dispatch(NOW + timedelta(days=31), lambda *_: self.fail('duplicate'))
        self.assertEqual(self.read()['events'], {})

    def test_lost_webhook_response_never_replays_without_human_resolution(self):
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout('fixture response lost')
        client = self.client(timeout)
        self.outbox.enqueue('a', 'monitor_found', {'text': 'found'}, NOW)
        self.outbox.dispatch(NOW, client.send)
        row = next(iter(self.read()['deliveries'].values()))
        self.assertEqual(row['status'], 'uncertain')
        Outbox(self.path, self.settings).dispatch(NOW + timedelta(hours=1), client.send)
        self.assertEqual(len(calls), 1)

    def test_two_legacy_receipts_rebind_to_one_current_delivery(self):
        for send_between in (False, True):
            with self.subTest(send_between=send_between):
                data = self.legacy('uncertain')
                data['deliveries']['old-delivery']['recipient'] = 'ops1'
                data['deliveries']['second-old'] = dict(data['deliveries']['old-delivery'], recipient='ops2')
                self.write(data)
                self.outbox.dispatch(NOW, lambda *_: self.fail('legacy replay'))
                calls = []
                for identifier in ('old-delivery', 'second-old'):
                    row = next(item for item in self.outbox.status()['deliveries'] if item['delivery_id'] == identifier)
                    self.outbox.resolve(identifier, action='not_delivered', expected_version=row['version'], now=NOW)
                    if send_between:
                        self.outbox.dispatch(NOW, lambda role, *_: calls.append(role) or 'receipt')
                self.outbox.dispatch(NOW, lambda role, *_: calls.append(role) or 'receipt')
                self.assertEqual(calls, ['capture'])
                self.outbox.dispatch(NOW + timedelta(days=31), lambda *_: self.fail('duplicate'))
                self.assertEqual(self.read()['events'], {})

    def test_partly_obsolete_legacy_card_routes_only_live_events_to_current_bot(self):
        data = self.legacy('uncertain')
        data['events']['a']['cancelled_at'] = NOW.isoformat()
        data['events']['b'] = {'kind': 'monitor_saved', 'payload': {'text': 'live b'}, 'created_at': NOW.isoformat()}
        data['deliveries']['old-delivery']['events'] = ['a', 'b']
        self.write(data)
        self.outbox.dispatch(NOW, lambda *_: self.fail('legacy replay'))
        row = self.outbox.status()['deliveries'][0]
        self.outbox.resolve(row['delivery_id'], action='not_delivered', expected_version=row['version'], now=NOW)
        calls = []
        self.outbox.dispatch(NOW, lambda role, card, *_: calls.append((role, card)) or 'receipt')
        self.assertEqual([role for role, _ in calls], ['capture'])
        self.assertIn('live b', json.dumps(calls[0][1]))
        old = self.read()['deliveries']['old-delivery']
        self.assertEqual(old['card'], data['deliveries']['old-delivery']['card'])
        self.assertEqual(old['status'], 'cancelled')
        self.outbox.dispatch(NOW + timedelta(days=31), lambda *_: self.fail('duplicate'))
        self.assertEqual(self.read()['events'], {})

    def test_process_exit_after_send_intent_stays_uncertain_on_restart(self):
        self.outbox.enqueue('a', 'monitor_saved', {'text': 'saved'}, NOW)
        def dies(*_):
            raise SystemExit('fixture process interruption')
        with self.assertRaises(SystemExit):
            self.outbox.dispatch(NOW, dies)
        self.assertEqual(next(iter(self.read()['deliveries'].values()))['status'], 'uncertain')
        Outbox(self.path, self.settings).dispatch(NOW + timedelta(hours=1), lambda *_: self.fail('replayed'))

    def test_definite_rate_rejection_is_retryable_after_backoff(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={'code': 11232}) if len(calls) == 1 else httpx.Response(200, json={'code': 0})
        client = self.client(handle)
        self.outbox.enqueue('a', 'monitor_found', {'text': 'found'}, NOW)
        self.outbox.dispatch(NOW, client.send)
        self.assertEqual(next(iter(self.read()['deliveries'].values()))['status'], 'retry')
        self.outbox.dispatch(NOW + timedelta(minutes=1), client.send)
        self.assertEqual(len(calls), 1)
        self.outbox.dispatch(NOW + timedelta(minutes=16), client.send)
        self.assertEqual(next(iter(self.read()['deliveries'].values()))['status'], 'sent')

    def test_duplicate_bot_address_is_rejected_before_any_request(self):
        duplicate = dict(TARGETS, capture=TARGETS['detect'])
        with self.assertRaisesRegex(FeishuError, '重复'):
            WebhookBot(duplicate)

    def test_selftest_names_all_four_bots_without_creating_business_events(self):
        from pipeline import cli
        cards = []
        fake = SimpleNamespace(send=lambda role, card, _id: cards.append((role, card)) or 'fixture', close=lambda: None)
        with patch('core.feishu.FeishuSettings.load', return_value=self.settings), \
                patch('core.feishu.WebhookBot.from_environment', return_value=fake), \
                patch('pipeline.cli.cfg', return_value=SimpleNamespace(state_dir=self.path.parent)):
            self.assertEqual(cli.main(['notifications', '--self-test']), 0)
        self.assertEqual([role for role, _ in cards], ['detect', 'capture', 'publish', 'alert'])
        titles = {card['header']['title']['content'] for _, card in cards}
        self.assertEqual(len(titles), 4)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()

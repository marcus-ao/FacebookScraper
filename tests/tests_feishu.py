"""发件箱用注入时钟验证；不会联系真实收件人。传输层另见 tests_feishu_webhook。"""
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.feishu import FeishuSettings, Outbox, notification_card


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

    def test_monitor_cards_reach_the_operator_during_quiet_hours(self):
        # 监测播报是这条链路唯一的存活信号，静默窗不能吞掉它；但它仍然进业务组。
        outbox = Outbox(self.path, self.settings)
        night = at('2026-09-11T23:00:00+08:00')
        for kind in ('monitor_found', 'monitor_saved'):
            outbox.enqueue(kind + ':instagram:42', kind, {'text': 'A clean home.'}, night)
        calls = []
        self.assertEqual(outbox.dispatch(night, lambda *args: calls.append(args) or 'message'), 2)
        self.assertEqual([call[0] for call in calls], ['operator', 'operator'])

    def test_monitor_cards_carry_counts_folders_and_the_source_link(self):
        card = notification_card('monitor_saved', [{
            'platform': 'Instagram', 'account': 'neakasa.global',
            'created_at': '普通探测 · 上海 09-10 22:23（以下时刻均为上海）',
            'text': '发现 1 篇，成功落档 1 篇，没有失败\n· 42  已落档 · 4 图 0 视频 · posts/2026-09/M1-Pro/abc',
            'permalink': 'https://www.instagram.com/p/abc/'}], self.settings)
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn('原帖抓取完成', rendered)
        self.assertIn('已落档 · 4 图 0 视频 · posts/2026-09/M1-Pro/abc', rendered)
        self.assertIn('查看原帖', rendered)
        # 没有 task_id 也要给原帖按钮：监测卡发生在有审校任务之前。
        self.assertNotIn('去审校', rendered)

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

    def test_expired_sent_cards_are_archived_and_never_enqueued_again(self):
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('a', 'ready', {'text': 'original ' * 500}, now)
        outbox.dispatch(now, lambda *_args: 'message-a')
        original = json.loads(self.path.read_text(encoding='utf-8'))
        original_size = self.path.stat().st_size
        outbox.dispatch(now + timedelta(days=31), lambda *_args: self.fail('duplicate send'))
        compact = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(compact['events'], {})
        self.assertEqual(compact['deliveries'], {})
        self.assertLess(self.path.stat().st_size, original_size // 3)
        restarted = Outbox(self.path, self.settings)
        self.assertEqual(restarted.archived_event('a'), {
            'event': original['events']['a'], 'deliveries': original['deliveries']})
        self.assertFalse(restarted.enqueue('a', 'ready', {'text': 'replacement'}, now + timedelta(days=32)))
        restarted.dispatch(now + timedelta(days=32), lambda *_args: self.fail('archived event sent again'))
        self.assertEqual(restarted.archived_event('a')['deliveries'], original['deliveries'])

    def test_retention_counts_from_delivery_completion_instead_of_event_creation(self):
        outbox = Outbox(self.path, self.settings)
        old = at('2026-09-12T23:00:00+08:00')
        outbox.enqueue('old-backlog', 'ready', {'text': 'still needed'}, old)
        sent_at = at('2026-10-20T09:00:00+08:00')
        outbox.dispatch(sent_at, lambda *_args: 'late-message')
        self.assertIn('old-backlog', json.loads(self.path.read_text(encoding='utf-8'))['events'])
        outbox.dispatch(sent_at + timedelta(days=31), lambda *_args: self.fail('duplicate send'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['events'], {})

    def test_cancelled_unassigned_events_expire_but_off_duty_backlog_survives(self):
        outbox = Outbox(self.path, self.settings)
        night = at('2026-09-12T23:00:00+08:00')
        for identifier in ('cancelled', 'waiting'):
            outbox.enqueue(identifier, 'ready', {'text': identifier}, night)
        outbox.retain_ready({'waiting'}, night)
        outbox.retain_ready({'waiting'}, night + timedelta(days=29))
        outbox.dispatch(night + timedelta(days=31), lambda *_args: self.fail('off-duty send'))
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(set(data['events']), {'waiting'})
        self.assertEqual(data['deliveries'], {})
        self.assertEqual(outbox.archived_event('cancelled')['event']['cancelled_at'], night.isoformat())
        self.assertIsNone(outbox.archived_event('waiting'))

    def test_multi_recipient_shared_groups_keep_every_member_until_all_terminal(self):
        settings = replace(self.settings, recipients=('ops1', 'ops2'))
        outbox = Outbox(self.path, settings)
        night = at('2026-09-11T23:00:00+08:00')
        for identifier in ('a', 'b'):
            outbox.enqueue(identifier, 'ready', {'text': identifier}, night)
        morning = at('2026-09-12T08:00:00+08:00')
        outbox.dispatch(morning, lambda *_args: 'receipt')
        outbox.enqueue('c', 'ready', {'text': 'c'}, night)
        source = json.loads(self.path.read_text(encoding='utf-8'))
        identifiers = list(source['deliveries'])
        source['deliveries'][identifiers[1]]['events'] = ['b', 'c']
        source['deliveries']['a-ops2'] = dict(source['deliveries'][identifiers[1]], events=['a'])
        source['deliveries']['c-ops1'] = dict(source['deliveries'][identifiers[0]], events=['c'])
        later = night + timedelta(days=32)
        for status in ('uncertain', 'retry', 'pending'):
            with self.subTest(status=status):
                data = json.loads(json.dumps(source))
                data['deliveries'][identifiers[1]]['status'] = status
                self.path.write_text(json.dumps(data), encoding='utf-8')
                outbox.dispatch(later, lambda *_args: self.fail('off-duty send'))
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                self.assertEqual(saved['events'], data['events'])
                self.assertEqual(saved['deliveries'], data['deliveries'])
        # Once the entire transitive component is terminal, archive it together.
        self.path.write_text(json.dumps(source), encoding='utf-8')
        outbox.dispatch(later, lambda *_args: self.fail('off-duty send'))
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['events'], {})
        self.assertEqual(saved['deliveries'], {})
        self.assertEqual(len(set(saved['archived_events'].values())), 1)
        self.assertEqual(outbox.archived_event('b')['deliveries'], source['deliveries'])

    def test_archive_write_failure_keeps_live_cards_and_receipts(self):
        from core import feishu
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('a', 'ready', {'text': 'Hallo'}, now)
        outbox.dispatch(now, lambda *_args: 'receipt')
        original = json.loads(self.path.read_text(encoding='utf-8'))
        write = feishu.atomic_write_json

        def fails_on_archive(path, data, **kwargs):
            if Path(path) != self.path:
                raise OSError('offline archive failure')
            write(path, data, **kwargs)

        with patch.object(feishu, 'atomic_write_json', side_effect=fails_on_archive):
            with self.assertRaisesRegex(OSError, 'archive failure'):
                outbox.dispatch(now + timedelta(days=31), lambda *_args: self.fail('duplicate'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['events'], original['events'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['deliveries'], original['deliveries'])

    def test_retry_after_archive_written_but_main_trim_failed_reuses_same_archive(self):
        from core import feishu
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('a', 'ready', {'text': 'Hallo'}, now)
        outbox.dispatch(now, lambda *_args: 'receipt')
        original = json.loads(self.path.read_text(encoding='utf-8'))
        write = feishu.atomic_write_json

        def fail_on_trim(path, data, **kwargs):
            if Path(path) == self.path and data.get('archived_events'):
                raise OSError('offline trim failure')
            write(path, data, **kwargs)

        with patch.object(feishu, 'atomic_write_json', side_effect=fail_on_trim):
            with self.assertRaisesRegex(OSError, 'trim failure'):
                outbox.dispatch(now + timedelta(days=31), lambda *_args: self.fail('duplicate'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['deliveries'], original['deliveries'])
        archived_files = list(self.path.with_name('outbox_archive').glob('*.json'))
        self.assertEqual(len(archived_files), 1)
        content = archived_files[0].read_bytes()
        outbox.dispatch(now + timedelta(days=32), lambda *_args: self.fail('duplicate'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['deliveries'], {})
        self.assertEqual(list(self.path.with_name('outbox_archive').glob('*.json')), archived_files)
        self.assertEqual(archived_files[0].read_bytes(), content)

    def test_retention_setting_is_a_positive_integer_even_when_disabled(self):
        from core.config import Config
        for invalid in (True, 0, -1, '30', 1.5):
            with self.subTest(invalid=invalid):
                config = Config()
                config._d['feishu']['keep_delivered_days'] = invalid
                with patch('core.feishu.cfg', return_value=config):
                    with self.assertRaisesRegex(ValueError, 'keep_delivered_days'):
                        FeishuSettings.load()

    def test_confirmed_cancelled_attempt_archives_original_card_and_uuid(self):
        outbox = Outbox(self.path, replace(self.settings, keep_delivered_days=1))
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('cancelled-attempt', 'ready', {'text': 'frozen card'}, now)

        def fails(*_args):
            raise TimeoutError()

        outbox.dispatch(now, fails)
        outbox.retain_ready(set(), now)
        delivery = outbox.status()['deliveries'][0]
        outbox.resolve(delivery['delivery_id'], action='not_delivered',
                       expected_version=delivery['version'], now=now)
        original = json.loads(self.path.read_text(encoding='utf-8'))['deliveries']
        self.assertEqual(original[delivery['delivery_id']]['status'], 'cancelled')
        outbox.dispatch(now + timedelta(days=2), lambda *_args: self.fail('cancelled send'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['deliveries'], {})
        self.assertEqual(outbox.archived_event('cancelled-attempt')['deliveries'], original)

    def test_cancelled_group_does_not_expire_a_still_required_recipient_reminder(self):
        settings = replace(self.settings, recipients=('ops1', 'ops2'))
        outbox = Outbox(self.path, settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('still-required', 'ready', {'text': 'pending recipient'}, now)
        outbox.dispatch(now, lambda *_args: 'receipt')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        second = list(data['deliveries'].values())[1]
        second.update(status='cancelled', cancelled_at=now.isoformat())
        self.path.write_text(json.dumps(data), encoding='utf-8')
        night = at('2026-10-20T23:00:00+08:00')
        outbox.dispatch(night, lambda *_args: self.fail('off-duty send'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['events'], data['events'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['deliveries'], data['deliveries'])


if __name__ == '__main__':
    unittest.main()

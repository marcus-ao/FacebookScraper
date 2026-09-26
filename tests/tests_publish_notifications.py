"""Confirmed receipts reach the publish bot without a processing scheduler."""
import asyncio
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_publication_recovery as fixtures
from core.config import cfg
from core.feishu import FeishuError, FeishuRejected, FeishuSettings, Outbox
from publish import journal, records, snapshots, workflow
from pipeline.service import Runtime


class PublishNotificationTests(unittest.TestCase):
    def setUp(self):
        host = fixtures.PublicationRecoveryTests()
        host.setUp()
        self.addCleanup(host.doCleanups)
        self.f = host.f
        frozen, _, self.folder = snapshots.freeze(self.f.post, self.f.source,
            scheduled_at=self.f.post.scheduled_at, expected_fingerprint=self.f.params['content_fingerprint'])
        host.approved_state(frozen.snapshot_id)
        self.frozen = frozen
        self.attempt = replace(workflow.new_attempt(frozen, frozen.scheduled_at,
            ui_timezone='America/Los_Angeles'), status=journal.STATUS_SCHEDULED,
            readback_signal='offline-fixture', remote_id='facebook=123456789')
        journal.append(cfg().state_dir, self.attempt)
        self.now = datetime.fromisoformat('2026-09-26T23:00:00+08:00')
        self.settings = FeishuSettings(True, 'http://review.internal')
        self.bot = Mock()
        self.bot.send.return_value = 'bot-accepted:fixture'
        self.factory = self.enterContext(patch('core.feishu.WebhookBot.from_environment', return_value=self.bot))
        self.enterContext(patch('core.feishu.FeishuSettings.load', return_value=self.settings))
        self.enterContext(patch.object(records, 'queue_mirror', return_value=False))
        self.box = Outbox(cfg().state_dir / 'feishu_outbox.json', self.settings)
        self.event = 'scheduled:' + self.attempt.attempt_id

    def project(self, now=None):
        return records.project(self.attempt, now=now or self.now)

    def test_confirmation_sends_without_scheduler_and_does_not_flush_other_posts(self):
        self.box.enqueue('scheduled:another', 'scheduled', {'text': 'another task'}, self.now)
        self.project()
        self.project()
        self.bot.send.assert_called_once()
        self.assertEqual(self.bot.send.call_args.args[0], 'publish')
        data = self.box._load()
        delivered = [d for d in data['deliveries'].values() if d['status'] == 'sent']
        self.assertEqual([d['events'] for d in delivered], [[self.event]])
        self.assertEqual(journal.load(cfg().state_dir)[-1]['status'], 'scheduled')

    def test_legacy_queued_bit_does_not_mean_sent_and_recovery_keeps_original_attempt(self):
        records.queue_notification(self.f.account, self.f.source, self.attempt.__dict__, self.now)
        progress = {self.attempt.attempt_id + ':scheduled': {'notification': True}}
        (self.folder / 'projection.json').write_text(json.dumps(progress), encoding='utf-8', newline='')
        original = journal.journal_path(cfg().state_dir).read_bytes()
        records.recover(self.f.account, self.f.source)
        records.recover(self.f.account, self.f.source)
        self.bot.send.assert_called_once()
        self.assertEqual(journal.journal_path(cfg().state_dir).read_bytes(), original)

    def test_uncertain_delivery_is_not_replayed_and_does_not_undo_scheduling(self):
        self.bot.send.side_effect = FeishuError('offline transport outcome unknown')
        first = self.project()
        self.project(now=self.now + timedelta(hours=1))
        self.bot.send.assert_called_once()
        self.assertEqual(first['status'], 'scheduled')
        self.assertEqual(next(iter(self.box._load()['deliveries'].values()))['status'], 'uncertain')
        self.assertEqual(first['projection']['notification_delivery']['status'], 'uncertain')

    def test_definite_rejection_obeys_backoff_and_retains_delivery_id(self):
        self.bot.send.side_effect = [FeishuRejected('offline explicit rejection'), 'bot-accepted:fixture']
        self.project()
        self.project(now=self.now + timedelta(minutes=1))
        self.assertEqual(self.bot.send.call_count, 1)
        self.project(now=self.now + timedelta(minutes=16))
        self.assertEqual(self.bot.send.call_count, 2)
        self.assertEqual(self.bot.send.call_args_list[0].args[2], self.bot.send.call_args_list[1].args[2])

    def test_configuration_failure_remains_visible_and_retryable_without_new_post(self):
        self.factory.side_effect = FeishuError('offline missing bot configuration')
        result = self.project()
        self.assertEqual(result['status'], 'scheduled')
        self.assertEqual(result['projection']['notification_delivery']['status'], 'pending')
        self.assertIn('notification_delivery', result['projection']['errors'])
        self.factory.side_effect = None
        self.project()
        self.bot.send.assert_called_once()

    def test_busy_outbox_enqueue_is_repaired_by_monitor_only_delivery(self):
        with self.box._lock():
            result = self.project()
        self.assertFalse(result['projection'].get('notification'))
        self.assertIn('notification', result['projection']['errors'])
        runtime = Runtime(detector=Mock(), process=False)
        self.addCleanup(runtime.close)
        runtime.client = self.bot
        runtime._deliver(self.now)
        self.bot.send.assert_called_once()
        self.assertEqual(self.box.event_status(self.event)['status'], 'sent')

    def test_slow_notification_does_not_block_the_publishing_event_loop(self):
        started, tick = Event(), Event()
        responsive = []
        def slow_send(*args):
            started.set()
            responsive.append(tick.wait(1))
            return 'bot-accepted:fixture'
        self.bot.send.side_effect = slow_send
        async def scenario():
            async def heartbeat():
                while not started.is_set():
                    await asyncio.sleep(.01)
                tick.set()
            task = asyncio.create_task(heartbeat())
            with patch.object(workflow, '_execute_unlocked', AsyncMock(return_value=
                    workflow.AttemptOutcome(0, self.attempt, 'offline confirmed'))), \
                    patch.object(journal, 'scheduled_record_for_refs', return_value=None), \
                    patch.object(journal, 'pending_record_for_refs', return_value=None), \
                    patch.object(workflow.channels, 'require_independent_channel_evidence'), \
                    patch.object(workflow.records, 'queue_approved'):
                await workflow.execute(self.frozen, self.frozen.scheduled_at,
                    ui_timezone='America/Los_Angeles', run=object(), timeout=1,
                    stamp='offline-notification', submit_enabled=True)
            await task
        asyncio.run(scenario())
        self.assertEqual(responsive, [True])

    def test_cancellation_keeps_publish_lock_until_receipt_delivery_finishes(self):
        started, finish = Event(), Event()
        def slow_send(*args):
            started.set()
            finish.wait(5)
            return 'bot-accepted:fixture'
        self.bot.send.side_effect = slow_send
        async def scenario():
            with patch.object(workflow, '_execute_unlocked', AsyncMock(return_value=
                    workflow.AttemptOutcome(0, self.attempt, 'offline confirmed'))), \
                    patch.object(journal, 'scheduled_record_for_refs', return_value=None), \
                    patch.object(journal, 'pending_record_for_refs', return_value=None), \
                    patch.object(workflow.channels, 'require_independent_channel_evidence'), \
                    patch.object(workflow.records, 'queue_approved'):
                task = asyncio.create_task(workflow.execute(self.frozen, self.frozen.scheduled_at,
                    ui_timezone='America/Los_Angeles', run=object(), timeout=1,
                    stamp='offline-notification', submit_enabled=True))
                try:
                    async with asyncio.timeout(3):
                        while not started.is_set():
                            await asyncio.sleep(.01)
                    task.cancel()
                    await asyncio.sleep(.03)
                    task.cancel()
                    await asyncio.sleep(.03)
                    self.assertFalse(task.done(), 'projection is still sending under the publish lock')
                    with self.assertRaisesRegex(RuntimeError, '另一个单帖/批量发布正在运行'):
                        with journal.PublishOperationLock(cfg().state_dir / 'publish.lock'):
                            pass
                finally:
                    finish.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                with journal.PublishOperationLock(cfg().state_dir / 'publish.lock'):
                    pass
        asyncio.run(scenario())
        self.assertEqual(self.box.event_status(self.event)['status'], 'sent')
        self.bot.send.assert_called_once()


if __name__ == '__main__':
    unittest.main()

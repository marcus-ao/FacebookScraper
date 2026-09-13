"""Batch, delivery and public-observation recovery; all external services are fakes."""
import json
import sys
import tempfile
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import paid_requests
from core.monitoring import MonitoringJournal
from core.feishu import Outbox, FeishuSettings, FeishuError
from publish import observations
from publish.business_suite import RemotePlannerCard, RemoteSlotInventory
import tests_pipeline_service as fixtures

NOW = datetime(2026, 9, 12, 2, tzinfo=timezone.utc)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def test_batch_recovery_is_cas_and_does_not_claim_successor_or_replay(self):
        journal = MonitoringJournal(self.state, now=NOW)
        journal.request(NOW, 'facebook', 'delta', 1, {})
        batch = journal.claim(NOW)
        journal.request(NOW, 'instagram', 'delta', 1, {})
        current = journal.finish(batch, NOW, code=None, error='stopped')
        with self.assertRaises(ValueError):
            journal.recover(batch_id=batch['batch_id'], expected_revision='old', now=NOW)
        result = journal.recover(batch_id=batch['batch_id'], expected_revision=journal.revision(current), now=NOW)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('next_batch', result)
        self.assertIsNone(journal.claim(NOW))
        journal.request(NOW, 'facebook', 'delta', 2, {})
        next_batch = journal.claim(NOW)
        self.assertEqual(set(next_batch['platforms']), {'facebook', 'instagram'})

    def test_batch_scope_links_stage_calls_and_unresolved_calls_block_recovery(self):
        journal = MonitoringJournal(self.state, now=NOW)
        journal.request(NOW, 'facebook', 'delta', 1, {})
        batch = journal.claim(NOW)
        with paid_requests.operation_scope(batch['batch_id']):
            caller = paid_requests.RequestController(self.state, preflight=lambda: None, operation_id='stage-specific')
            _, receipt = caller.run(stage='translation', job_key='fixture', source_ref='facebook:123',
                media_index=None, model='fixture', request=lambda: 'result', usage_getter=lambda: {'tokens': 2},
                usage_errors=lambda _: [], usage_cost=lambda _: .03)
        self.assertEqual(receipt.operation_id, batch['batch_id'])
        current = journal.finish(batch, NOW, code=None, error='interrupted')
        with self.assertRaisesRegex(ValueError, '付费'):
            journal.recover(batch_id=batch['batch_id'], expected_revision=journal.revision(current), now=NOW, outputs_reviewed=True)
        caller.finalize(receipt, accepted=True)
        with self.assertRaisesRegex(ValueError, '产物|文案|图片'):
            journal.recover(batch_id=batch['batch_id'], expected_revision=journal.revision(current), now=NOW)
        self.assertEqual(journal.recover(batch_id=batch['batch_id'], expected_revision=journal.revision(current),
                                         now=NOW, outputs_reviewed=True)['status'], 'failed')
        self.assertEqual(len([e for e in paid_requests.load_events(self.state) if e['event'] == 'started']), 1)

    def test_uncertain_message_keeps_card_and_uuid_after_human_resolution(self):
        outbox = Outbox(self.state / 'outbox.json', FeishuSettings(True, 'http://localhost:8765', ('ops',), ('dev',)))
        outbox.enqueue('ready-one', 'ready', {'task_id': 'fa_brand/1', 'text': 'Frozen German'}, NOW)
        sent = []
        def fail(recipient, card, uid):
            sent.append((recipient, card, uid))
            raise TimeoutError()
        outbox.dispatch(NOW, fail)
        outbox.dispatch(NOW + timedelta(hours=1), fail)
        row = outbox.status()['deliveries'][0]
        self.assertEqual(row['status'], 'uncertain')
        with self.assertRaises(FeishuError):
            outbox.resolve(row['delivery_id'], action='not_delivered', expected_version='old', now=NOW)
        outbox.resolve(row['delivery_id'], action='not_delivered', expected_version=row['version'], now=NOW + timedelta(hours=1))
        outbox.enqueue('ready-one', 'ready', {'text': 'new edit'}, NOW + timedelta(hours=1))
        outbox.dispatch(NOW + timedelta(hours=1), lambda *args: sent.append(args) or 'message-id')
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(outbox.status()['counts']['sent'], 1)

    def test_scheduled_time_passing_does_not_create_publication_fact(self):
        attempt = {'status': 'scheduled', 'scheduled_at': '2020-01-01T10:00:00Z',
                   'target_channels': ['instagram'], 'remote_id': 'instagram=12345678'}
        self.assertEqual(observations.status(self.state, attempt)['status'], 'unknown')
        card = RemotePlannerCard(NOW, ('instagram',), (('instagram', '87654321'),), 'caption', 'hash', 'published')
        inventory = RemoteSlotInventory((), 'UTC', NOW.date(), NOW.date(), (card,), True)
        observations.record(self.state, inventory, NOW)
        self.assertEqual(observations.status(self.state, attempt)['status'], 'unknown')
        attempt['remote_id'] = 'instagram=87654321'
        self.assertEqual(observations.status(self.state, attempt)['status'], 'published')

    def test_cancelled_part_of_merged_message_does_not_hide_valid_post_for_other_recipient(self):
        outbox = Outbox(self.state / 'outbox.json', FeishuSettings(True, 'http://localhost', ('ops1', 'ops2'), ('dev',)))
        night = NOW - timedelta(hours=4)
        for key in ('a', 'b'):
            outbox.enqueue(key, 'ready', {'task_id': 'fa_brand/' + key, 'text': key}, night)
        def send(recipient, *args):
            if recipient == 'ops2':
                raise TimeoutError()
            return 'sent-1'
        outbox.dispatch(NOW, send)
        original = json.loads(outbox.path.read_text('utf-8'))['deliveries']
        lost_id = next(key for key, row in original.items() if row['recipient'] == 'ops2')
        outbox.retain_ready({'b'}, NOW)
        lost = next(row for row in outbox.status()['deliveries'] if row['delivery_id'] == lost_id)
        outbox.resolve(lost_id, action='not_delivered', expected_version=lost['version'], now=NOW)
        sent = []
        outbox.dispatch(NOW, lambda *args: sent.append(args) or 'sent-2')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 'ops2')
        self.assertIn('fa_brand%2Fb', json.dumps(sent[0][1]))
        self.assertNotIn('fa_brand%2Fa', json.dumps(sent[0][1]))
        final = json.loads(outbox.path.read_text('utf-8'))['deliveries']
        self.assertEqual(final[lost_id]['card'], original[lost_id]['card'])
        self.assertEqual(final[lost_id]['status'], 'cancelled')


class RuntimeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_weekly_sampling_stays_off_monitor_thread_without_duplicate_submit(self):
        from pipeline.service import Runtime
        runtime = Runtime(detector=Mock(return_value=0))
        self.addCleanup(runtime.close)
        runtime.sampling_executor.shutdown()
        runtime.sampling_executor = Mock()
        pending = Future()
        runtime.sampling_executor.submit.return_value = pending
        with patch('pipeline.hashtag_suggestions.weekly_refresh_due', return_value=True):
            runtime.refresh_hashtags(NOW)
            runtime.refresh_hashtags(NOW)
        runtime.sampling_executor.submit.assert_called_once()
        self.assertFalse(pending.done())

    def test_unavailable_processing_executor_leaves_a_closed_failure(self):
        from pipeline.service import Runtime
        runtime = Runtime(detector=Mock(), process=True)
        self.addCleanup(runtime.close)
        runtime.processing_executor.shutdown()
        runtime.processing.request(NOW, 'facebook', 'delta', 1, {})
        runtime.start_processing(NOW)
        self.assertEqual(runtime.processing_status()['status'], 'failed')
        self.assertFalse((self.fixture.state / 'paid_requests.jsonl').exists())


if __name__ == '__main__':
    unittest.main()

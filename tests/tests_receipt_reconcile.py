"""Recover an existing submission through the HTTP entry without another submit."""
import asyncio
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_publication_recovery as fixtures
from core import config, review
from publish import business_suite as bs, journal, manual_run, month_inventory, operations, records, snapshots, workflow
from web.api import approval as api


class ReceiptReconcileTests(unittest.TestCase):
    def setUp(self):
        self.host = fixtures.PublicationRecoveryTests()
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        f = self.host.f
        self.f = f
        self.frozen, _, self.folder = snapshots.freeze(f.post, f.source,
            scheduled_at=f.post.scheduled_at, expected_fingerprint=f.params['content_fingerprint'])
        self.run = manual_run.load(f.post.platform)
        snapshots.bind_schedule(self.frozen.snapshot_id, self.frozen.scheduled_at, target=self.run.target())
        self.host.approved_state(self.frozen.snapshot_id)
        self.attempt = replace(workflow.new_attempt(self.frozen, self.frozen.scheduled_at,
            ui_timezone=self.run.ui_timezone), status=journal.STATUS_SUBMITTED_UNVERIFIED,
            success_signal='semantic:heading:Your post is scheduled', ui_readback='confirmed clock',
            origin='review_desk')
        journal.append(config.cfg().state_dir, self.attempt)
        self.task_id = f.account.name + '/' + f.source['post_id']
        self.op = operations.start(self.task_id, platform=f.post.platform,
            snapshot_id=self.frozen.snapshot_id, scheduled_at=self.frozen.scheduled_at)
        operations.finish(self.op['operation_id'], status=operations.UNCERTAIN,
            message='readback failed', result={'publication': journal.load(config.cfg().state_dir)[-1]})
        self.card = bs.RemotePlannerCard(self.frozen.scheduled_at, (f.post.platform,),
            ((f.post.platform, '123456789'),), f.post.text_de, 'hash', 'scheduled',
            placement='feed', caption_status='present', accounts=((f.post.platform, self.run.account),), time_verified=True)

    def reconcile(self, cards=None, diagnostics=()):
        cards = tuple(cards if cards is not None else [self.card])
        inventory = bs.RemoteSlotInventory(tuple(c.at for c in cards), self.run.ui_timezone,
            self.card.at.date(), self.card.at.date(), cards, True, diagnostics)
        page = SimpleNamespace(close=AsyncMock())
        self.pw = SimpleNamespace(stop=AsyncMock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        with patch.object(api, '_source', return_value=SimpleNamespace(account_dir=self.f.account, row=self.f.source)), \
                patch.object(records, 'attach', AsyncMock(return_value=(self.pw, None, context))) as attach, \
                patch.object(month_inventory, 'read', AsyncMock(return_value=inventory)) as reader, \
                patch.object(bs, '_readback_screenshot', AsyncMock(return_value='')), \
                patch.object(bs, 'submit', AsyncMock(side_effect=AssertionError('must never submit'))), \
                patch.object(bs, 'open_composer', AsyncMock(side_effect=AssertionError('must never compose'))), \
                patch.object(records, 'queue_mirror', return_value=True), \
                patch.object(records, 'queue_notification', return_value=True):
            result = asyncio.run(api.reconcile_publication(self.task_id))
            self.remote_reads = reader.call_count
            self.attach_calls = attach.call_count
            return result

    def test_existing_successful_submission_is_read_and_same_attempt_recovered_once(self):
        old = journal.journal_path(config.cfg().state_dir).read_bytes()
        frozen_bytes = (self.folder / 'text_de.txt').read_bytes()
        result = self.reconcile()
        self.assertEqual(result['status'], 'scheduled')
        self.assertEqual(self.remote_reads, 1)
        self.assertEqual(self.pw.stop.await_count, 1)
        rows = journal.load(config.cfg().state_dir)
        self.assertTrue(journal.journal_path(config.cfg().state_dir).read_bytes().startswith(old))
        self.assertEqual(rows[-1]['attempt_id'], self.attempt.attempt_id)
        self.assertEqual(rows[-1]['remote_id'], 'facebook=123456789')
        self.assertEqual((self.folder / 'text_de.txt').read_bytes(), frozen_bytes)
        self.assertEqual(review.latest(self.f.account)[self.f.source['post_id']]['status'], 'scheduled')
        self.assertEqual(operations.read(self.op['operation_id'])['status'], operations.SUCCEEDED)
        self.reconcile()
        self.assertEqual(self.attach_calls, 0)
        self.assertEqual(len(journal.load(config.cfg().state_dir)), len(rows))

    def test_incomplete_or_wrong_or_duplicate_remote_never_unlocks_resubmission(self):
        for cards in ([], [replace(self.card, rendered='wrong caption')], [self.card, self.card],
                      [replace(self.card, channels=(), read_status='incomplete')]):
            with self.subTest(cards=cards):
                result = self.reconcile(cards)
                self.assertEqual(result['status'], journal.STATUS_SUBMITTED_UNVERIFIED)
                self.assertTrue(result['message'])
                self.assertIsNotNone(journal.pending_record_for_refs(config.cfg().state_dir, self.attempt.source_refs))
                self.assertEqual(review.latest(self.f.account)[self.f.source['post_id']]['status'], 'approved')

    def test_other_scheduled_detail_failure_preserves_diagnostics_and_recovers_original_attempt(self):
        unknown = bs.RemotePlannerCard(self.card.at.replace(hour=(self.card.at.hour+5) % 24),
            delivery='scheduled', read_status='incomplete', diagnostic_index=0)
        diagnostic = {'date': unknown.at.date().isoformat(), 'time': unknown.at.strftime('%I:%M %p'),
                      'stage': 'scheduled_detail', 'code': 'read_failed'}
        before = journal.journal_path(config.cfg().state_dir).read_bytes()
        result = self.reconcile([self.card, unknown], diagnostics=(diagnostic,))
        self.assertEqual(result['status'], 'scheduled')
        row = result['publication']
        self.assertEqual(row['attempt_id'], self.attempt.attempt_id)
        self.assertEqual(row['remote_id'], 'facebook=123456789')
        self.assertEqual(row['readback_diagnostics']['inventory_diagnostics'], [diagnostic])
        self.assertTrue(journal.journal_path(config.cfg().state_dir).read_bytes().startswith(before))
        self.assertEqual(operations.read(self.op['operation_id'])['status'], operations.SUCCEEDED)

    def test_corrupt_frozen_bytes_or_changed_asset_means_zero_browser_visits(self):
        caption = self.folder / 'text_de.txt'
        original = caption.read_bytes()
        for corrupt in (True, False):
            with self.subTest(corrupt=corrupt):
                if corrupt:
                    caption.write_bytes(b'changed')
                else:
                    caption.write_bytes(original)
                    config.cfg()._d['publish']['facebook_page_name'] = 'Wrong page'
                result = self.reconcile()
                self.assertEqual(result.status_code, 409)
                self.assertEqual(json.loads(result.body)['code'], 'publication_reconcile_blocked')
                self.assertEqual(self.attach_calls, 0)

    def test_bound_remote_id_cannot_be_replaced_by_a_different_identical_post(self):
        bound = replace(self.attempt, remote_id='facebook=888888888')
        journal.append(config.cfg().state_dir, bound)
        result = self.reconcile()
        self.assertEqual(result['status'], journal.STATUS_SUBMITTED_UNVERIFIED)
        self.assertEqual(result['publication']['remote_id'], bound.remote_id)

    def test_an_active_publish_lock_refuses_recovery_without_remote_reads(self):
        # A separate context cannot inherit the lock's allowed reentrancy token.
        from contextvars import Context
        with journal.PublishOperationLock(config.cfg().state_dir / 'publish.lock'):
            result = Context().run(self.reconcile)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.attach_calls, 0)

    def test_projection_failure_does_not_erase_remote_receipt_and_retry_repairs_locally(self):
        with patch.object(records, 'project', side_effect=OSError('offline projection failure')):
            result = self.reconcile()
        self.assertEqual(result['status'], 'scheduled')
        self.assertIn('projection_error', result)
        self.assertEqual(journal.load(config.cfg().state_dir)[-1]['status'], 'scheduled')
        self.reconcile()
        self.assertEqual(self.attach_calls, 0)
        self.assertEqual(review.latest(self.f.account)[self.f.source['post_id']]['status'], 'scheduled')


if __name__ == '__main__':
    unittest.main()

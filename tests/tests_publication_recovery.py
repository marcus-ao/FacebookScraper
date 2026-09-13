"""Recover frozen publication artifacts without browser writes or current-draft substitution."""
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_approval as fixtures
from core import config, review
from publish import journal, workflow


class PublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture

    def test_orphan_approved_recovers_only_after_worker_exit(self):
        from publish import records, snapshots
        frozen, _, directory = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        approved = review.transition(self.f.account, self.f.source, 'approved',
            expected_revision=None, expected_source_sha256=self.f.params['source_text_sha256'],
            snapshot_id=directory.name)
        with self.assertRaises(review.ReviewConflict):
            records.recover(self.f.account, self.f.source)
        with patch('publish.records.worker_alive', return_value=False):
            result = records.recover(self.f.account, self.f.source)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(review.latest(self.f.account)[self.f.source['post_id']]['previous_revision'], approved['revision'])
        self.assertFalse(journal.load(config.cfg().state_dir))

    def test_receipt_and_side_effects_repaired_from_frozen_version_once(self):
        from publish import records, snapshots
        frozen, files, directory = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        review.transition(self.f.account, self.f.source, 'approved', expected_revision=None,
            expected_source_sha256=self.f.params['source_text_sha256'], snapshot_id=directory.name)
        attempt = replace(workflow.new_attempt(frozen, frozen.scheduled_at, ui_timezone='America/Los_Angeles'),
                          status=journal.STATUS_SCHEDULED, readback_signal='offline-fixture')
        journal.append(config.cfg().state_dir, attempt)
        self.f.post.image_paths[0].write_bytes(b'later edit')
        queued = []
        with patch('publish.records.queue_mirror', side_effect=lambda source, stage, payload, evidence, now: queued.append((stage, payload))), patch('publish.records.queue_notification'):
            records.project(attempt, now=fixtures.NOW)
            records.project(attempt, now=fixtures.NOW)
        self.assertEqual([stage for stage, _ in queued], ['approved', 'scheduled'])
        self.assertEqual(queued[1][1][frozen.image_paths[0].name], files[frozen.image_paths[0].name])
        self.assertEqual(json.loads((directory / 'receipt.json').read_text())['attempt_id'], attempt.attempt_id)
        self.assertEqual(review.latest(self.f.account)[self.f.source['post_id']]['status'], 'scheduled')

    def test_pending_remote_result_remains_blocked(self):
        from publish import records, snapshots
        frozen, _, directory = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        review.transition(self.f.account, self.f.source, 'approved', expected_revision=None,
            expected_source_sha256=self.f.params['source_text_sha256'], snapshot_id=directory.name)
        attempt = replace(workflow.new_attempt(frozen, frozen.scheduled_at, ui_timezone='America/Los_Angeles'),
                          status=journal.STATUS_SUBMIT_AMBIGUOUS)
        journal.append(config.cfg().state_dir, attempt)
        with patch('publish.records.worker_alive', return_value=False):
            with self.assertRaises(review.ReviewConflict):
                records.recover(self.f.account, self.f.source)

    def test_source_fingerprint_includes_image_bytes_order_and_count(self):
        from publish import snapshots
        first, _, _ = snapshots.freeze(self.f.post, self.f.source, expected_fingerprint=self.f.params['content_fingerprint'])
        original = self.f.fixture.post_dir / '01.jpg'
        original.write_bytes(b'changed source image')
        second, _, _ = snapshots.freeze(self.f.post, self.f.source, expected_fingerprint=self.f.params['content_fingerprint'])
        self.assertNotEqual(first.source_fingerprint, second.source_fingerprint)

    def test_existing_snapshot_cannot_change_identity_or_schedule(self):
        from publish import snapshots
        frozen, _, _ = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        from datetime import timedelta
        for changes in ({'post_id': 'different'}, {'platform': 'instagram'},
                        {'scheduled_at': frozen.scheduled_at + timedelta(days=1)},
                        {'source_fingerprint': 'different'}):
            with self.subTest(changes=changes), self.assertRaises(review.ReviewConflict):
                snapshots.ensure(replace(frozen, **changes))

    def test_snapshot_image_order_is_bound_to_approved_fingerprint(self):
        from publish import snapshots
        other = self.f.post.image_paths[0].with_name('02.jpg')
        other.write_bytes(b'second image')
        post = replace(self.f.post, image_paths=(*self.f.post.image_paths, other))
        expected = journal.text_sha256(post.text_de) + ':' + ','.join(journal.file_sha256(p) for p in post.image_paths)
        frozen, _, directory = snapshots.freeze(post, self.f.source, expected_fingerprint=expected)
        path = directory / 'snapshot.json'
        metadata = json.loads(path.read_text('utf-8'))
        metadata['images'].reverse()
        path.write_text(json.dumps(metadata), encoding='utf-8')
        with self.assertRaises(review.ReviewConflict):
            snapshots.ensure(frozen)

    def test_projection_rejects_schedule_or_source_or_image_mismatch(self):
        from publish import records, snapshots
        frozen, _, _ = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        original = workflow.new_attempt(frozen, frozen.scheduled_at, ui_timezone='America/Los_Angeles')
        for changes in ({'source_fingerprint': 'wrong'}, {'image_sha256': ('wrong',)},
                        {'scheduled_at': '2026-09-20T10:00:00+02:00'}):
            with self.subTest(changes=changes):
                attempt = replace(original, **changes)
                journal.append(config.cfg().state_dir, attempt)
                with self.assertRaises(review.ReviewConflict):
                    records.project(attempt)

    def test_prepared_is_not_notified_as_failed(self):
        from publish import records, snapshots
        frozen, _, _ = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        attempt = replace(workflow.new_attempt(frozen, frozen.scheduled_at, ui_timezone='America/Los_Angeles'),
                          status=journal.STATUS_PREPARED)
        journal.append(config.cfg().state_dir, attempt)
        with patch('publish.records.queue_mirror'), patch('publish.records.queue_notification') as send:
            records.project(attempt, now=fixtures.NOW)
        send.assert_not_called()

    def test_new_acceptance_requires_frozen_single_channel_readback(self):
        from publish import capabilities, snapshots
        frozen, _, _ = snapshots.freeze(self.f.post, self.f.source,
            expected_fingerprint=self.f.params['content_fingerprint'])
        attempt = replace(workflow.new_attempt(frozen, frozen.scheduled_at, ui_timezone='America/Los_Angeles'),
                          status=journal.STATUS_SCHEDULED, remote_id='facebook=12345678',
                          ui_readback='observed time', target_channels=('facebook',))
        journal.append(config.cfg().state_dir, attempt)
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        verified = replace(attempt, readback_signal='planner_scheduled_card', channels_verified=('facebook',))
        journal.append(config.cfg().state_dir, verified)
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        # Synthetic complete evidence belongs only to this isolated fixture.
        verified = replace(verified, readback_diagnostics={
            'full_caption_equal': True, 'remote_images_verified': True,
            'remote_media': {'image_count': len(frozen.image_paths), 'order_verified': True,
                            'images': [{'source_sha256': value} for value in verified.image_sha256]}})
        journal.append(config.cfg().state_dir, verified)
        self.assertTrue(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        for update in ({'image_count': True}, {'image_count': 1.0}, {'images': [None]}):
            with self.subTest(update=update):
                malformed = replace(verified, readback_diagnostics=dict(verified.readback_diagnostics,
                    remote_media=dict(verified.readback_diagnostics['remote_media'], **update)))
                journal.append(config.cfg().state_dir, malformed)
                self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        for diagnostics in (['invalid'], dict(verified.readback_diagnostics, remote_media=['invalid'])):
            with self.subTest(diagnostics=diagnostics):
                journal.append(config.cfg().state_dir, replace(verified, readback_diagnostics=diagnostics))
                self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        journal.append(config.cfg().state_dir, verified)
        frozen.image_paths[0].write_bytes(b'corrupt')
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])


if __name__ == '__main__':
    unittest.main()

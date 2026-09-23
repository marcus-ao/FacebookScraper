"""Read-only enrichment of existing scheduled attempts, using isolated truth stores."""
import json
import sys
import unittest
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from contextlib import redirect_stdout
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_scheduled_readback as fixtures
from core import config, review
from publish import business_suite as bs, capabilities, journal, manual_run, month_inventory
from publish import records, scheduled_media, snapshots, workflow


class RecheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixtures.ReadbackMediaTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.host = self.f.host
        snapshots.bind_schedule(self.f.frozen.snapshot_id, self.f.frozen.scheduled_at,
                                target=manual_run.load('facebook').target())
        locked = review.transition(self.host.account, self.host.source, 'content_locked',
            expected_revision=None, expected_source_sha256=self.host.params['source_text_sha256'],
            snapshot_id=self.f.frozen.snapshot_id)
        review.transition(self.host.account, self.host.source, 'approved',
            expected_revision=locked['revision'], expected_source_sha256=self.host.params['source_text_sha256'])
        self.attempt = replace(self.f.attempt, status=journal.STATUS_SCHEDULED,
            channels_verified=('facebook',), readback_signal='planner_target_range_and_scheduled_detail',
            readback_diagnostics={'full_caption_equal': True, 'remote_images_verified': False})
        journal.append(config.cfg().state_dir, self.attempt)

    async def recheck(self, capture=None, *, card=None, cleanup_failure=False):
        actual = card or self.f.card
        inventory = bs.RemoteSlotInventory((actual.at,), 'UTC', actual.at.date(),
            actual.at.date(), (actual,), True)
        page = SimpleNamespace(close=AsyncMock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        pw = SimpleNamespace(stop=AsyncMock())
        if cleanup_failure:
            page.close.side_effect = RuntimeError('connection closed')
            pw.stop.side_effect = RuntimeError('connection closed')
        async def target(page, card, *, observe_detail, **kwargs):
            return await observe_detail(object())
        with ExitStack() as stack:
            stack.enter_context(patch('publish.records.attach', AsyncMock(return_value=(pw, None, context))))
            stack.enter_context(patch.object(month_inventory, 'read', AsyncMock(return_value=inventory)))
            stack.enter_context(patch.object(month_inventory, 'read_scheduled_target', target))
            stack.enter_context(patch.object(scheduled_media, 'collect', AsyncMock(return_value=capture or self.f.capture)))
            stack.enter_context(patch.object(bs, '_readback_screenshot', AsyncMock(return_value='')))
            for name in ('open_composer', 'upload_images', 'fill_caption', 'set_schedule', 'submit'):
                stack.enter_context(patch.object(bs, name, AsyncMock(side_effect=AssertionError('remote mutation: ' + name))))
            stack.enter_context(patch.object(workflow, 'execute', AsyncMock(side_effect=AssertionError('no resubmit'))))
            return await records.reverify_media(self.attempt.attempt_id, timeout=1)

    async def test_same_attempt_append_preserves_old_rows_and_does_not_notify_twice(self):
        old = journal.journal_path(config.cfg().state_dir).read_bytes()
        with patch.object(records, 'queue_notification', return_value=True) as notify, \
                patch.object(records, 'queue_mirror', return_value=True):
            records.project(self.attempt)
            first = await self.recheck()
            second = await self.recheck()
        self.assertTrue(first['remote_images_verified'])
        self.assertTrue(second['remote_images_verified'])
        self.assertEqual(notify.call_count, 1)
        rows = journal.load(config.cfg().state_dir)
        self.assertEqual(len(rows), 3)
        self.assertTrue(journal.journal_path(config.cfg().state_dir).read_bytes().startswith(old))
        for row in rows:
            for field in ('attempt_id', 'snapshot_id', 'remote_id', 'scheduled_at', 'source_fingerprint',
                          'source_fingerprint_version'):
                self.assertEqual(row[field], asdict(self.attempt)[field])
            self.assertEqual(row['status'], 'scheduled')
        self.assertTrue(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])

    async def test_image_failure_and_remote_disappearance_keep_scheduled_and_dedupe(self):
        for capture, card in ((replace(self.f.capture, complete=False, error='layout_unverified'), None),
                              (None, replace(self.f.card, remote_ids=(('facebook', '999999999'),)))):
            with self.subTest(remote=card), patch.object(records, 'queue_notification'), patch.object(records, 'queue_mirror'):
                result = await self.recheck(capture, card=card)
                self.assertEqual(result['status'], 'scheduled')
                self.assertFalse(result['remote_images_verified'])
                latest = journal.load(config.cfg().state_dir)[-1]
                self.assertEqual(latest['remote_id'], self.attempt.remote_id)
                self.assertIsNotNone(journal.scheduled_record_for_refs(config.cfg().state_dir, self.attempt.source_refs))
                self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])

    async def test_cancelled_ambiguous_bad_id_or_missing_binding_refuses_before_browser(self):
        variants = ({'status': journal.STATUS_CANCELLED_REMOTE}, {'status': journal.STATUS_SUBMIT_AMBIGUOUS},
                    {'remote_id': ''}, {'remote_id': 'instagram=123456789'}, {'snapshot_id': ''})
        with patch('publish.records.attach', AsyncMock(side_effect=AssertionError('browser must not be opened'))):
            for changed in variants:
                with self.subTest(changed=changed):
                    journal.append(config.cfg().state_dir, replace(self.attempt, **changed))
                    with self.assertRaises((review.ReviewConflict, bs.PublishStepError)):
                        await records.reverify_media(self.attempt.attempt_id)
            journal.append(config.cfg().state_dir, self.attempt)
            metadata = self.f.directory / 'snapshot.json'
            payload = json.loads(metadata.read_text('utf-8'))
            payload.pop('publish_target')
            metadata.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises((review.ReviewConflict, bs.PublishStepError)):
                await records.reverify_media(self.attempt.attempt_id)

    async def test_corrupt_frozen_bytes_refuse_before_browser(self):
        self.f.frozen.image_paths[0].write_bytes(b'corrupt')
        with patch('publish.records.attach', AsyncMock(side_effect=AssertionError('browser must not be opened'))):
            with self.assertRaises(review.ReviewConflict):
                await records.reverify_media(self.attempt.attempt_id)

    async def test_browser_cleanup_failure_keeps_durable_readback(self):
        with patch.object(records, 'queue_notification'), patch.object(records, 'queue_mirror'):
            result = await self.recheck(cleanup_failure=True)
        self.assertTrue(result['remote_images_verified'])
        latest = journal.load(config.cfg().state_dir)[-1]
        self.assertEqual(latest['readback_diagnostics']['browser_cleanup_errors'],
                         {'page': 'RuntimeError', 'connection': 'RuntimeError'})
        self.assertEqual(latest['attempt_id'], self.attempt.attempt_id)

    async def test_crash_before_journal_append_can_reenter_without_remote_writes(self):
        before = journal.journal_path(config.cfg().state_dir).read_bytes()
        with patch.object(journal, 'append', side_effect=OSError('interrupted append')):
            with self.assertRaises(OSError):
                await self.recheck()
        self.assertEqual(journal.journal_path(config.cfg().state_dir).read_bytes(), before)
        self.assertTrue(list((config.cfg().state_dir / 'publish_attempts' / 'remote_media').glob('*/media.json')))
        with patch.object(records, 'queue_notification'), patch.object(records, 'queue_mirror'):
            result = await self.recheck()
        self.assertTrue(result['remote_images_verified'])
        self.assertEqual(len(journal.load(config.cfg().state_dir)), 2)

    async def test_legacy_fingerprint_version_is_preserved(self):
        from core import paid_consent
        metadata_path = self.f.directory / 'snapshot.json'
        metadata = json.loads(metadata_path.read_text('utf-8'))
        metadata.pop('source_fingerprint_version')
        metadata['source_fingerprint'] = paid_consent.fingerprint(self.host.source, self.host.account, version=1)
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        self.attempt = replace(self.attempt, source_fingerprint_version=1,
                               source_fingerprint=metadata['source_fingerprint'])
        journal.append(config.cfg().state_dir, self.attempt)
        with patch.object(records, 'queue_notification'), patch.object(records, 'queue_mirror'):
            result = await self.recheck()
        self.assertTrue(result['remote_images_verified'])
        latest = journal.load(config.cfg().state_dir)[-1]
        self.assertEqual(latest['source_fingerprint_version'], 1)
        self.assertEqual(latest['source_fingerprint'], self.attempt.source_fingerprint)


class RecheckCommandTests(unittest.TestCase):
    def test_refused_attempt_and_busy_lock_return_actionable_nonzero_result(self):
        from tools import reverify_scheduled_media
        for error in (review.ReviewConflict('missing attempt'), RuntimeError('publish lock busy')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(records, 'reverify_media', AsyncMock(side_effect=error)), \
                    patch.object(reverify_scheduled_media, 'force_utf8'), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(reverify_scheduled_media.main(['--attempt-id', 'missing']), 2)
                self.assertEqual(json.loads(output.getvalue())['status'], 'refused')


if __name__ == '__main__':
    unittest.main()

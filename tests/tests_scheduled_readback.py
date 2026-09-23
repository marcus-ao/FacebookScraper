"""Frozen-byte readback and journal integration; external reads are isolated doubles."""
import io
import json
import sys
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from contextlib import ExitStack
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_approval as fixtures
from core import config, review
from publish import business_suite as bs, capabilities, journal, month_inventory, month_readback
from publish import manual_run, records, scheduled_media, snapshots, workflow


class ReadbackMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = fixtures.ApprovalTests()
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        paths, bodies = [], []
        for index in range(2):
            path = self.host.fixture.root / f'final-{index}.png'
            picture = Image.new('RGB', (240, 240), 'white')
            ImageDraw.Draw(picture).rectangle((index * 120, 0, index * 120 + 90, 239), fill='black')
            picture.save(path)
            encoded = io.BytesIO()
            picture.save(encoded, format='JPEG', quality=85)
            paths.append(path)
            bodies.append(encoded.getvalue())
        post = replace(self.host.post, image_paths=tuple(paths), image_sources=('media_de',) * 2)
        fingerprint = journal.text_sha256(post.text_de) + ':' + ','.join(journal.file_sha256(p) for p in paths)
        self.frozen, _, self.directory = snapshots.freeze(post, self.host.source,
            expected_fingerprint=fingerprint, scheduled_at=post.scheduled_at)
        self.attempt = replace(workflow.new_attempt(self.frozen, self.frozen.scheduled_at,
            ui_timezone='America/Los_Angeles'), status=journal.STATUS_SUBMITTED_UNVERIFIED,
            remote_id='facebook=123456789', ui_readback='observed-time')
        self.card = bs.RemotePlannerCard(self.frozen.scheduled_at, ('facebook',), (('facebook', '123456789'),),
            self.frozen.text_de, 'hash', 'scheduled', placement='feed', caption_status='present',
            time_verified=True, accounts=(('facebook', month_readback.accounts()['facebook']),))
        self.capture = scheduled_media.MediaCapture(tuple(bodies), {'fixture': 'complete-byte-contract'},
            screenshot=paths[0].read_bytes(), complete=True, image_count=2,
            order_basis='isolated_complete_entity', error='')

    async def verify(self, capture=None, target_error=None, operation=None, **kwargs):
        inventory = bs.RemoteSlotInventory((self.card.at,), 'UTC', self.card.at.date(),
            self.card.at.date(), (self.card,), True)
        async def target(page, card, *, observe_detail, **options):
            self.assertEqual(card.remote_ids, (('facebook', '123456789'),))
            if target_error:
                raise target_error
            return await observe_detail(object())
        with patch.object(month_inventory, 'read', AsyncMock(return_value=inventory)), \
                patch.object(month_inventory, 'read_scheduled_target', target), \
                patch.object(scheduled_media, 'collect', AsyncMock(return_value=capture or self.capture)), \
                patch.object(bs, '_readback_screenshot', AsyncMock(return_value='')):
            if operation is not None:
                return await operation()
            return await month_readback.verify(None, self.frozen.scheduled_at, self.frozen.text_de,
                ui_timezone='America/Los_Angeles', target_channels=('facebook',),
                expected_image_count=2, frozen_attempt=asdict(self.attempt), **kwargs)

    def append_result(self, result):
        attempt = replace(self.attempt, status=journal.STATUS_SCHEDULED,
            channels_verified=result.channels, readback_signal=result.success_signal,
            readback_diagnostics=result.diagnostics)
        journal.append(config.cfg().state_dir, attempt)
        return attempt

    async def test_actual_comparison_reaches_acceptance_and_lost_evidence_revokes_it(self):
        result = await self.verify()
        self.assertTrue(result.found)
        self.assertEqual(result.image_count, 2)
        self.assertTrue(result.diagnostics['remote_images_verified'])
        manifest = json.loads(Path(result.diagnostics['remote_media']['evidence_file']).read_text('utf-8'))
        target = manifest['structure']['target']
        self.assertEqual(target['accounts'], dict(self.card.accounts))
        self.assertEqual(target['remote_ids'], dict(self.card.remote_ids))
        self.assertEqual(target['method'], 'reacquired_scheduled_dialog')
        attempt = self.append_result(result)
        self.assertTrue(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['instagram']['verified'])
        Path(result.diagnostics['remote_media']['evidence_file']).unlink()
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        self.assertEqual(journal.scheduled_record_for_refs(config.cfg().state_dir, attempt.source_refs)['attempt_id'],
                         attempt.attempt_id)

    async def test_image_mismatch_or_unknown_layout_keeps_scheduled_fact_and_dedupe(self):
        for capture in (replace(self.capture, bodies=self.capture.bodies[::-1]),
                        replace(self.capture, complete=False, image_count=None, error='layout_unverified'),
                        replace(self.capture, bodies=(b'corrupt',) * 2)):
            with self.subTest(error=capture.error):
                result = await self.verify(capture)
                self.assertTrue(result.found)
                self.assertFalse(result.diagnostics['remote_images_verified'])
                self.append_result(result)
                self.assertIsNotNone(journal.scheduled_record_for_refs(config.cfg().state_dir, self.attempt.source_refs))
                self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])

    async def test_changed_target_never_becomes_scheduled_from_this_readback(self):
        result = await self.verify(target_error=bs.PublishStepError('target changed'))
        self.assertFalse(result.found)
        self.assertFalse(result.diagnostics['remote_images_verified'])

    async def test_bad_frozen_binding_keeps_fact_but_cannot_certify_images(self):
        self.attempt = replace(self.attempt, image_sha256=('wrong', 'wrong'))
        result = await self.verify()
        self.assertTrue(result.found)
        self.assertFalse(result.diagnostics['remote_images_verified'])

    async def run_workflow(self, capture=None, target_error=None):
        page = object()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        pw = SimpleNamespace(stop=AsyncMock())
        clicks = []
        async def submit(*args, **kwargs):
            self.assertEqual(journal.load(config.cfg().state_dir)[-1]['status'], journal.STATUS_SUBMIT_AMBIGUOUS)
            clicks.append(True)
            return bs.SubmitResult(clicked=True, confirmed=True, remote_id='facebook=123456789',
                                   success_signal='fixture-submit-signal')
        run = manual_run.load('facebook')
        empty = bs.RemoteSlotInventory((), 'UTC', self.card.at.date(), self.card.at.date(), (), True)
        async def operation():
            return await workflow.execute(self.frozen, self.frozen.scheduled_at,
                ui_timezone=run.ui_timezone, timeout=1, stamp='fixture', submit_enabled=True,
                run=run, opening_inventory=empty, report=lambda *args: None)
        with ExitStack() as stack:
            for name, replacement in (
                    ('publish.workflow.attach', AsyncMock(return_value=(pw, None, context))),
                    ('publish.workflow._screenshot', AsyncMock(return_value='')),
                    ('publish.workflow.check_live_slot', AsyncMock()),
                    ('publish.channels.select', AsyncMock(return_value={'channel': 'facebook', 'account': run.account})),
                    ('publish.channels.verify_before_submit', AsyncMock()),
                    ('publish.media.verify_upload', AsyncMock(return_value={'image_count': 2})),
                    ('publish.records.queue_mirror', lambda *args, **kwargs: True),
                    ('publish.records.queue_notification', lambda *args, **kwargs: True)):
                stack.enter_context(patch(name, replacement))
            for name, value in (('open_composer', page), ('upload_images', []), ('fill_caption', None),
                                ('set_schedule', 'fixture-ui-time'), ('verify_form', None)):
                stack.enter_context(patch.object(bs, name, AsyncMock(return_value=value)))
            stack.enter_context(patch.object(bs, 'submit', submit))
            result = await self.verify(capture, target_error=target_error, operation=operation)
            with self.assertRaises(bs.PublishStepError):
                await operation()
        self.assertEqual(clicks, [True])
        return result

    async def test_workflow_success_records_real_byte_comparison_and_prevents_second_submit(self):
        result = await self.run_workflow()
        self.assertEqual(result.attempt.status, 'scheduled')
        self.assertTrue(result.attempt.readback_diagnostics['remote_images_verified'])
        self.assertTrue(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        self.assertIn('2', result.attempt.verification)

    async def test_workflow_image_failure_stays_scheduled_and_does_not_pass_g8(self):
        result = await self.run_workflow(replace(self.capture, complete=False, error='layout_unverified'))
        self.assertEqual(result.attempt.status, 'scheduled')
        self.assertFalse(result.attempt.readback_diagnostics['remote_images_verified'])
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])
        self.assertIn('layout_unverified', result.attempt.verification)

    async def test_workflow_changed_identity_stays_unverified_after_single_click(self):
        result = await self.run_workflow(target_error=bs.PublishStepError('target changed during capture'))
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMITTED_UNVERIFIED)
        self.assertFalse(capabilities.acceptance(config.cfg().state_dir)['facebook']['verified'])


if __name__ == '__main__':
    unittest.main()

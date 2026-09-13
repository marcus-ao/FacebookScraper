"""Web批准使用真实归档/审核/发布账本；浏览器与平台回执以注入替身验证。"""
import asyncio
import sys
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activation_fixtures import activate as fixture_activate
import tests_web_review as fixtures
from publish_fixtures import verified_probe_config
from core import config, review, translated
from pipeline import approval, engine
from publish import business_suite as bs, compose, journal, workflow
from web.api.approval import berlin_time

NOW = datetime(2026, 9, 12, 8, tzinfo=timezone.utc)
TARGET = NOW + timedelta(hours=4)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.account, self.source = self.fixture.account, self.fixture.source
        self.fixture.write_generated_image('Ein sauberes Zuhause. #Neakasa')
        c = verified_probe_config(config.cfg(), self.fixture.root / 'state')
        patch.object(config, '_cfg', c).start()
        fixture_activate(engine, c.state_dir, g8_verified=True, now=NOW - timedelta(days=2))
        self.post = compose.compose_post(self.source['post_id'], TARGET, archive_root=config.cfg().archive_dir,
            account=self.account.name, now=NOW, require_verified_ui_constraints=True, warning_sink=None)
        self.params = {'scheduled_at': TARGET, 'source_text_sha256': translated.source_text_sha256(self.source['text']),
            'human_revision': None, 'review_revision': None, 'content_fingerprint': engine._publish_fingerprint(self.post), 'now': NOW}
        self.inventory = bs.RemoteSlotInventory((), 'America/Los_Angeles', date(2026, 9, 1), date(2026, 9, 30),
                                                cards=(), cards_loaded=True)

    def allow_fixture_evidence(self):
        patch('publish.capabilities.require', return_value=None).start()
        patch.object(bs, 'require_submission_evidence', return_value=None).start()
        patch.object(bs, 'require_readback_evidence', return_value=None).start()

    def test_missing_channel_evidence_means_zero_remote_reads_or_state_change(self):
        reader, execute = AsyncMock(), AsyncMock()
        with self.assertRaises(bs.ProbeRequired):
            asyncio.run(approval.approve(self.account, self.source, **self.params,
                                         inventory_reader=reader, executor=execute))
        reader.assert_not_called()
        execute.assert_not_called()
        self.assertFalse(review.latest(self.account))
        self.assertFalse(approval.options(self.account, self.source, now=NOW)['available'])

    def test_selected_conflicting_time_is_rejected_with_three_alternatives(self):
        self.allow_fixture_evidence()
        self.inventory = bs.RemoteSlotInventory((TARGET,), 'America/Los_Angeles', date(2026, 9, 1), date(2026, 9, 30),
            cards=(bs.RemotePlannerCard(TARGET, ('facebook',), rendered='another operator caption'),), cards_loaded=True)
        execute = AsyncMock()
        with self.assertRaises(approval.ApprovalConflict) as error:
            asyncio.run(approval.approve(self.account, self.source, **self.params,
                inventory_reader=AsyncMock(return_value=self.inventory), executor=execute))
        self.assertEqual(len(error.exception.suggestions), 3)
        execute.assert_not_called()
        self.assertFalse(review.latest(self.account))

    def test_success_uses_frozen_media_and_only_then_updates_review(self):
        self.allow_fixture_evidence()
        original = self.post.image_paths[0].read_bytes()
        async def execute(post, when, **kwargs):
            self.assertEqual(review.state_for(self.account, self.source)['status'], 'approved')
            self.assertEqual(when, TARGET)
            self.assertEqual(kwargs['target_channels'], ('facebook',))
            self.assertNotEqual(post.image_paths, self.post.image_paths)
            self.post.image_paths[0].write_bytes(b'operator edit during submission')
            self.assertEqual(post.image_paths[0].read_bytes(), original)
            base = workflow.new_attempt(post, when, ui_timezone=kwargs['ui_timezone'])
            result = journal.transition(base, journal.STATUS_SCHEDULED, recorded_at=NOW.isoformat())
            journal.append(config.cfg().state_dir, result)
            return workflow.AttemptOutcome(0, result, 'fixture-confirmed')
        result = asyncio.run(approval.approve(self.account, self.source, **self.params,
            inventory_reader=AsyncMock(return_value=self.inventory), executor=execute))
        self.assertTrue(result['ok'])
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'scheduled')
        self.assertEqual(len(list((config.cfg().state_dir / 'publish_snapshots').glob('*/receipt.json'))), 1)

    def test_stale_fingerprint_never_calls_submit(self):
        self.allow_fixture_evidence()
        execute = AsyncMock()
        with self.assertRaises(approval.ApprovalConflict):
            asyncio.run(approval.approve(self.account, self.source,
                **dict(self.params, content_fingerprint='old'),
                inventory_reader=AsyncMock(return_value=self.inventory), executor=execute))
        execute.assert_not_called()
        self.assertFalse(review.latest(self.account))

    def test_browser_failure_restores_pending_review(self):
        self.allow_fixture_evidence()
        with self.assertRaises(bs.PublishStepError):
            asyncio.run(approval.approve(self.account, self.source, **self.params,
                inventory_reader=AsyncMock(return_value=self.inventory),
                executor=AsyncMock(side_effect=bs.PublishStepError('fixture unavailable'))))
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'pending_review')
        self.assertFalse(journal.load(config.cfg().state_dir))

    def test_berlin_input_does_not_use_the_shanghai_browser_timezone(self):
        self.assertEqual(berlin_time('2026-09-12T14:30').astimezone(timezone.utc).hour, 12)
        self.assertEqual(berlin_time('2026-11-12T14:30').astimezone(timezone.utc).hour, 13)
        with self.assertRaises(ValueError):
            berlin_time('2026-03-29T02:30')
        with self.assertRaises(ValueError):
            berlin_time('2026-10-25T02:30')

    def test_new_conflict_after_composer_preparation_never_arms_or_clicks(self):
        self.allow_fixture_evidence()
        page = object()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), stop=AsyncMock())
        submit = AsyncMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(workflow.channels, 'select', AsyncMock(return_value={'channel': 'facebook', 'account': 'fixture'})))
            stack.enter_context(patch.object(workflow.channels, 'verify_before_submit', AsyncMock()))
            stack.enter_context(patch.object(workflow.media, 'verify_upload', AsyncMock(return_value={'image_count': 1})))
            stack.enter_context(patch.object(workflow, 'attach', AsyncMock(return_value=(context, None, context))))
            stack.enter_context(patch.object(workflow, '_screenshot', AsyncMock(return_value='')))
            stack.enter_context(patch.object(workflow, 'check_live_slot',
                AsyncMock(side_effect=bs.PublishStepError('刚有其他人排入同一时段'))))
            stack.enter_context(patch.object(bs, 'require_account_context_evidence', return_value=object()))
            stack.enter_context(patch.object(workflow.channel_evidence, 'require',
                return_value={'context_ids': {'asset_id': '123', 'business_id': '456'}}))
            stack.enter_context(patch.object(workflow.month_readback, 'baseline',
                AsyncMock(return_value=bs.ScheduledBaseline(TARGET.isoformat(), 0))))
            responses = {'open_composer': page, 'ensure_logged_in': SimpleNamespace(notes=()),
                'upload_images': (), 'fill_caption': None, 'set_schedule': 'fixture-time',
                'capture_failure': SimpleNamespace(screenshot=None, lines=lambda: [])}
            for name, result in responses.items():
                stack.enter_context(patch.object(bs, name, AsyncMock(return_value=result)))
            stack.enter_context(patch.object(bs, 'submit', submit))
            result = asyncio.run(workflow.execute(self.post, TARGET,
                ui_timezone='America/Los_Angeles', timeout=.1, stamp='fixture', submit_enabled=True))
        self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT)
        submit.assert_not_called()
        states = [row['status'] for row in journal.load(config.cfg().state_dir)]
        self.assertIn(journal.STATUS_PREPARED, states)
        self.assertNotIn(journal.STATUS_SUBMIT_AMBIGUOUS, states)


if __name__ == '__main__':
    unittest.main()

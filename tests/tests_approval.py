"""Web批准使用真实归档/审核/发布账本；浏览器与平台回执以注入替身验证。"""
import asyncio
import json
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
from core import config, paid_consent, review, translated
from pipeline import approval, engine
from publish import channel_evidence, manual_run
from publish import business_suite as bs, compose, journal, snapshots, workflow
from web.api.approval import business_time

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
        record = {'schema_version': 1, 'capture_origin': 'playwright_live', 'channels': {
            channel: {'channel': channel, 'accounts': channel_evidence.accounts(),
                      'port': c.publish_debug_port, 'profile': str(c.publish_profile_dir.resolve()),
                      'context_ids': {'asset_id': '1001', 'business_id': '2002'}}
            for channel in ('facebook', 'instagram')}}
        (c.state_dir / channel_evidence.FILENAME).write_text(json.dumps(record), encoding='utf-8')
        fixture_activate(engine, c.state_dir, g8_verified=True, now=NOW - timedelta(days=2))
        self.post = compose.compose_post(self.source['post_id'], TARGET, archive_root=config.cfg().archive_dir,
            account=self.account.name, now=NOW, require_verified_ui_constraints=True, warning_sink=None)
        self.params = {'scheduled_at': TARGET, 'source_text_sha256': translated.source_text_sha256(self.source['text']),
            'human_revision': None, 'review_revision': None, 'content_fingerprint': engine._publish_fingerprint(self.post),
            'publish_target': manual_run.load(self.source['platform']).target(), 'now': NOW}
        self.inventory = bs.RemoteSlotInventory((), 'America/Los_Angeles', date(2026, 9, 1), date(2026, 9, 30),
                                                cards=(), cards_loaded=True)

    def allow_fixture_evidence(self):
        patch('publish.capabilities.require', return_value=None).start()
        patch.object(bs, 'require_submission_evidence', return_value=None).start()
        patch.object(bs, 'require_readback_evidence', return_value=None).start()

    def lock_content(self):
        """排期前必须先「编辑确认无误」；返回冻结后的审校版本。"""
        return approval.lock(self.account, self.source, now=NOW,
                             source_text_sha256=self.params['source_text_sha256'],
                             review_revision=None,
                             content_fingerprint=self.params['content_fingerprint'])

    def approve(self, **kwargs):
        locked = self.lock_content()
        params = dict(self.params, review_revision=locked['revision'])
        params.update(kwargs.pop('params', {}))
        return asyncio.run(approval.approve(self.account, self.source, **params, **kwargs))

    def test_missing_asset_record_means_zero_remote_reads(self):
        (config.cfg().state_dir / channel_evidence.FILENAME).unlink()
        reader, execute = AsyncMock(), AsyncMock()
        with self.assertRaises(approval.ApprovalConflict) as error:
            self.approve(inventory_reader=reader, executor=execute)
        self.assertIn('发布资产记录缺失', str(error.exception))
        reader.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'content_locked')
        options = approval.options(self.account, self.source, now=NOW)
        self.assertTrue(options['lockable'])
        self.assertTrue(options['available'])
        self.assertGreater(options['earliest'], NOW.isoformat())
        self.assertEqual(options['preview']['target']['account'], self.params['publish_target']['account'])
        self.assertEqual(options['preview']['target']['asset_id'], '')

    def test_changed_asset_since_confirmation_means_zero_remote_reads(self):
        locked = self.lock_content()
        confirmed = approval.options(self.account, self.source, now=NOW)['preview']['target']
        path = config.cfg().state_dir / channel_evidence.FILENAME
        record = json.loads(path.read_text(encoding='utf-8'))
        record['channels']['facebook']['context_ids']['asset_id'] = '1002'
        path.write_text(json.dumps(record), encoding='utf-8')
        reader, execute = AsyncMock(), AsyncMock()
        params = dict(self.params, review_revision=locked['revision'], publish_target=confirmed)
        with self.assertRaisesRegex(approval.ApprovalConflict, '发布目标已变化'):
            asyncio.run(approval.approve(self.account, self.source, **params,
                                       inventory_reader=reader, executor=execute))
        reader.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'content_locked')

    def test_changed_account_cannot_reuse_the_old_asset_record(self):
        config.cfg()._d['publish']['facebook_page_name'] = 'Another page'
        reader, execute = AsyncMock(), AsyncMock()
        with self.assertRaisesRegex(approval.ApprovalConflict, '与当前发布账号不一致'):
            self.approve(inventory_reader=reader, executor=execute)
        reader.assert_not_called()
        execute.assert_not_called()

    def test_content_can_be_frozen_before_the_publish_gate_is_open(self):
        """内容合格就可以选时刻；历史录证不参与这个判断。"""
        options = approval.options(self.account, self.source, now=NOW)
        self.assertTrue(options['available'])
        self.assertTrue(options['lockable'])
        self.assertIsNone(options['preview'])
        self.assertEqual(options['fingerprint'], self.params['content_fingerprint'])
        self.assertGreater(options['earliest'], NOW.isoformat())

    def test_selected_conflicting_time_is_rejected_with_three_alternatives(self):
        self.allow_fixture_evidence()
        self.inventory = bs.RemoteSlotInventory((TARGET,), 'America/Los_Angeles', date(2026, 9, 1), date(2026, 9, 30),
            cards=(bs.RemotePlannerCard(TARGET, ('facebook',), rendered='another operator caption', placement='feed', time_verified=True),), cards_loaded=True)
        execute = AsyncMock()
        with self.assertRaises(approval.ApprovalConflict) as error:
            self.approve(inventory_reader=AsyncMock(return_value=self.inventory), executor=execute)
        self.assertEqual(len(error.exception.suggestions), 3)
        execute.assert_not_called()
        # 冲突不解冻：内容仍然是她确认过的那一份，改时刻就能再提交。
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'content_locked')

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
        result = self.approve(inventory_reader=AsyncMock(return_value=self.inventory), executor=execute)
        self.assertTrue(result['ok'])
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'scheduled')
        self.assertEqual(len(list((config.cfg().state_dir / 'publish_snapshots').glob('*/receipt.json'))), 1)

    def test_freezing_binds_the_moment_only_once(self):
        self.allow_fixture_evidence()
        locked = self.lock_content()
        metadata, _, _, _ = snapshots.load(locked['snapshot_id'])
        self.assertIsNone(metadata['scheduled_at'])
        self.assertEqual(snapshots.bind_schedule(locked['snapshot_id'], TARGET)['scheduled_at'],
                         TARGET.isoformat())
        # 幂等；换个时刻必须拒绝，否则冻结的内容会被悄悄搬到另一个时段。
        snapshots.bind_schedule(locked['snapshot_id'], TARGET)
        with self.assertRaises(review.ReviewConflict):
            snapshots.bind_schedule(locked['snapshot_id'], TARGET + timedelta(hours=1))

    def test_releasing_a_freeze_voids_the_snapshot_but_keeps_its_bytes(self):
        locked = self.lock_content()
        directory = snapshots.folder(locked['snapshot_id'])
        before = (directory / 'text_de.txt').read_bytes()
        approval.unlock(self.account, self.source, now=NOW,
                        source_text_sha256=self.params['source_text_sha256'],
                        review_revision=locked['revision'])
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'edited')
        metadata, _, _, _ = snapshots.load(locked['snapshot_id'])
        self.assertEqual(metadata['status'], 'discarded')
        self.assertEqual((directory / 'text_de.txt').read_bytes(), before)
        with self.assertRaises(review.ReviewConflict):
            snapshots.bind_schedule(locked['snapshot_id'], TARGET)

    def test_stale_fingerprint_never_calls_submit(self):
        self.allow_fixture_evidence()
        execute = AsyncMock()
        with self.assertRaises(approval.ApprovalConflict):
            self.approve(params={'content_fingerprint': 'old'},
                inventory_reader=AsyncMock(return_value=self.inventory), executor=execute)
        execute.assert_not_called()
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'content_locked')

    def test_changed_source_is_reported_before_reading_the_remote_calendar(self):
        locked = self.lock_content()
        directory = snapshots.folder(locked['snapshot_id'])
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        path = self.fixture.post_dir / 'post.json'
        source = json.loads(path.read_text('utf-8'))
        source['media'][0]['url'] = 'https://fixture.test/changed.jpg'
        path.write_text(json.dumps(source), encoding='utf-8', newline='')
        reader = AsyncMock(return_value=self.inventory)
        execute = AsyncMock()
        with self.assertRaises(approval.ApprovalConflict) as error:
            asyncio.run(approval.approve(self.account, self.source,
                **dict(self.params, review_revision=locked['revision']),
                inventory_reader=reader, executor=execute))
        self.assertIn('来源指纹', str(error.exception))
        reader.assert_not_called()
        execute.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'content_locked')
        self.assertFalse(journal.load(config.cfg().state_dir))

    def test_source_is_rechecked_after_the_remote_calendar_read(self):
        locked = self.lock_content()
        directory = snapshots.folder(locked['snapshot_id'])
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        async def read_inventory(**kwargs):
            path = self.fixture.post_dir / 'post.json'
            source = json.loads(path.read_text('utf-8'))
            source['media'][0]['url'] = 'https://fixture.test/changed.jpg'
            path.write_text(json.dumps(source), encoding='utf-8', newline='')
            return self.inventory
        execute = AsyncMock()
        with self.assertRaises(approval.ApprovalConflict) as error:
            asyncio.run(approval.approve(self.account, self.source,
                **dict(self.params, review_revision=locked['revision']),
                inventory_reader=read_inventory, executor=execute))
        self.assertIn('来源指纹', str(error.exception))
        execute.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
        self.assertFalse(journal.load(config.cfg().state_dir))

    def test_explicit_refreeze_replaces_a_legacy_signature_binding_without_reprocessing(self):
        path = self.fixture.post_dir / 'post.json'
        source = json.loads(path.read_text('utf-8'))
        source['media'][0]['url'] = 'https://fixture.fbcdn.net/photo.jpg?stp=s1080&oh=old&oe=123'
        path.write_text(json.dumps(source), encoding='utf-8', newline='')
        locked = self.lock_content()
        directory = snapshots.folder(locked['snapshot_id'])
        metadata_path = directory / 'snapshot.json'
        metadata = json.loads(metadata_path.read_text('utf-8'))
        metadata.pop('source_fingerprint_version')
        metadata['source_fingerprint'] = paid_consent.fingerprint(source, self.account, version=1)
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8', newline='')
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        source['media'][0]['url'] = 'https://fixture.fbcdn.net/photo.jpg?stp=s1080&oh=new&oe=456'
        path.write_text(json.dumps(source), encoding='utf-8', newline='')
        reader, execute = AsyncMock(return_value=self.inventory), AsyncMock()
        with self.assertRaisesRegex(approval.ApprovalConflict, '来源指纹.*旧版.*重新冻结'):
            asyncio.run(approval.approve(self.account, self.source,
                **dict(self.params, review_revision=locked['revision']),
                inventory_reader=reader, executor=execute))
        reader.assert_not_called()
        execute.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

        unlocked = approval.unlock(self.account, self.source, now=NOW,
            source_text_sha256=self.params['source_text_sha256'], review_revision=locked['revision'])
        new_lock = approval.lock(self.account, self.source, now=NOW,
            source_text_sha256=self.params['source_text_sha256'], review_revision=unlocked['revision'],
            content_fingerprint=self.params['content_fingerprint'])
        self.assertNotEqual(new_lock['snapshot_id'], locked['snapshot_id'])
        bound = approval._bind(self.post, new_lock['snapshot_id'], source, self.account)
        self.assertEqual(bound.source_fingerprint_version, 2)
        self.assertEqual(bound.text_de, self.post.text_de)
        self.assertEqual(bound.image_paths[0].read_bytes(), self.post.image_paths[0].read_bytes())
        old_metadata, _, _, _ = snapshots.load(locked['snapshot_id'])
        self.assertEqual(old_metadata['status'], 'discarded')
        self.assertEqual(paid_consent.fingerprint_version(old_metadata), 1)
        for name, data in before.items():
            if name != 'snapshot.json':
                self.assertEqual((directory / name).read_bytes(), data)
        self.assertFalse(journal.load(config.cfg().state_dir))

    def test_browser_failure_restores_pending_review(self):
        self.allow_fixture_evidence()
        with self.assertRaises(bs.PublishStepError):
            self.approve(inventory_reader=AsyncMock(return_value=self.inventory),
                executor=AsyncMock(side_effect=bs.PublishStepError('fixture unavailable')))
        self.assertEqual(review.latest(self.account)[self.source['post_id']]['status'], 'pending_review')
        self.assertFalse(journal.load(config.cfg().state_dir))

    def test_picker_input_is_read_in_the_business_timezone(self):
        """北京无夏令时，全年固定 +08:00；两个换算方向都要钉住。"""
        self.assertEqual(business_time('2026-09-12T14:30').astimezone(timezone.utc).hour, 6)
        self.assertEqual(business_time('2026-11-12T14:30').astimezone(timezone.utc).hour, 6)
        self.assertEqual(business_time('2026-09-12T14:30').utcoffset(), timedelta(hours=8))

    def test_a_business_timezone_with_dst_still_rejects_its_two_bad_moments(self):
        """上海没有夏令时不等于这个函数以后不会换到有夏令时的时区。"""
        with patch.object(bs, 'business_timezone', return_value='Europe/Berlin'):
            self.assertEqual(business_time('2026-09-12T14:30').astimezone(timezone.utc).hour, 12)
            with self.assertRaises(ValueError):
                business_time('2026-03-29T02:30')     # 不存在
            with self.assertRaises(ValueError):
                business_time('2026-10-25T02:30')     # 出现两次

    def test_the_german_audience_hour_is_reported_next_to_the_business_hour(self):
        """北京 10:00 落在柏林凌晨——这条换算必须让人看见，不能靠记时差。"""
        from publish import planning
        default = planning.audience_local(business_time('2026-09-12T16:00'))
        self.assertEqual(default['at'], '2026-09-12T10:00:00+02:00')
        self.assertFalse(default['quiet_hours'])
        # 看起来最正常的那个时刻，恰恰是德国受众睡着的时候。
        night = planning.audience_local(business_time('2026-09-12T10:00'))
        self.assertEqual(night['at'], '2026-09-12T04:00:00+02:00')
        self.assertTrue(night['quiet_hours'])

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

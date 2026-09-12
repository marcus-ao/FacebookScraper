"""常驻组装的离线集成：扫描不重复、审校暂停拦付费、回执与积压去重。"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import review
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
from pipeline import engine
from pipeline.service import Runtime
from publish import journal


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.now = datetime.now(timezone.utc)
        self.state = cfg().state_dir
        engine.activate(self.state, g8_verified=True, now=self.now - timedelta(days=2))

    def test_two_platform_scans_process_once_without_duplicate_detection(self):
        def detect(kind, platform):
            path = self.state / 'delta_state.json'
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                data = {}
            data[platform] = {'last_reconcile_at': self.now.isoformat(),
                              'last_reconcile_new_count': 1,
                              'last_observed_skipped': {}}
            path.write_text(json.dumps(data), encoding='utf-8')
            return 0
        detector = Mock(side_effect=detect)
        runtime = Runtime(detector=detector, process=True)
        runtime.clock = lambda: self.now
        self.addCleanup(runtime.close)
        with patch.object(engine, 'run', return_value=0) as process:
            runtime.scan('reconcile', 'facebook')
            runtime.scan('reconcile', 'instagram')
            process.assert_not_called()
            runtime.maintenance(self.now)
            process.assert_not_called()
            future = runtime.start_processing(self.now)
            self.assertIsNotNone(future)
            future.result(timeout=5)
            process.assert_called_once()
            self.assertFalse(process.call_args.kwargs['detect_updates'])
            runtime.maintenance(self.now)
            process.assert_called_once()
        self.assertEqual(detector.call_count, 2)

    def test_handed_off_prevents_all_automatic_paid_stages(self):
        review.transition(self.fixture.account, self.fixture.source, 'handed_off',
                          expected_revision=None,
                          expected_source_sha256=journal.text_sha256(self.fixture.source['text']))
        runner = Mock()
        result = engine.run(account_dirs=[self.fixture.account], state_dir=self.state,
                            settings=engine.pipeline_settings(), now=self.now,
                            runner=runner, detect_updates=False, report=lambda _: None)
        self.assertEqual(result, 0)
        runner.translate.assert_not_called()
        runner.image.assert_not_called()
        runner.delta.assert_not_called()

    def test_calendar_default_disabled_and_enabled_refresh_is_throttled(self):
        runtime = Runtime(detector=Mock(return_value=0))
        with patch('pipeline.service.planner_cache.refresh_cache',
                   new=AsyncMock(return_value={'refresh_status': 'busy'})) as refresh:
            runtime.refresh_calendar(self.now)
            refresh.assert_not_called()
            runtime.calendar_enabled = True
            runtime.refresh_calendar(self.now)
            runtime.refresh_calendar(self.now + timedelta(minutes=5))
            refresh.assert_called_once()
            runtime.refresh_calendar(self.now + timedelta(hours=1))
            self.assertEqual(refresh.call_count, 2)

    def test_bad_mirror_source_and_partial_state_do_not_block_frozen_dispatch(self):
        runtime = Runtime(detector=Mock(return_value=0))
        runtime.drive = Mock()
        runtime.mirror = Mock()
        runtime.mirror.queue_source.side_effect = RuntimeError('source changed')
        runtime.mirror.queue_state.side_effect = RuntimeError('partial jsonl')
        runtime.mirror.dispatch.return_value = {'completed': 1, 'pending': 0}
        runtime.mirror_sources(self.now)
        runtime.mirror.dispatch.assert_called_once_with(runtime.drive, now=self.now)

    def test_old_ready_cannot_be_approved_after_operator_snoozes(self):
        source = self.fixture.source
        ref = 'facebook:' + source['post_id']
        engine.append_human_item(self.state, engine.HumanItem(
            'ready-stale', 'ready_to_publish', (ref,), 'ready',
            {'canonical_ref': ref, 'account_dir': self.fixture.account.name,
             'post_id': source['post_id']}), self.now)
        review.transition(self.fixture.account, source, 'snoozed', expected_revision=None,
                          expected_source_sha256=journal.text_sha256(source['text']))
        with patch.object(engine, 'attach') as attach:
            with self.assertRaisesRegex(engine.PipelineRunError, '挂起'):
                engine.approve(item_ids=['ready-stale'], selections={}, state_dir=self.state,
                               now=self.now, assume_yes=True)
            attach.assert_not_called()

    def test_backlog_and_schedule_failure_have_durable_events(self):
        runtime = Runtime(detector=Mock(return_value=0))
        runtime.settings = FeishuSettings(True, 'http://review.internal', ('operator',), ('developer',))
        runtime.outbox = Outbox(self.state / 'feishu_outbox.json', runtime.settings)
        runtime.outbox.started_at(self.now - timedelta(days=1))
        source = self.fixture.source
        engine.append_human_item(self.state, engine.HumanItem(
            'ready-test', 'ready_to_publish', ('facebook:' + source['post_id'],), 'ready',
            {'text_de_preview': 'Deutsch', 'source_text_sha256': journal.text_sha256(source['text'])}), self.now)
        journal.append(self.state, journal.PublishAttempt(
            post_id=source['post_id'], platform='facebook', status=journal.STATUS_SUBMITTED_UNVERIFIED,
            scheduled_at=(self.now + timedelta(days=1)).isoformat(), recorded_at=self.now.isoformat(),
            text_de_sha256='fixture'))
        afternoon = datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc)
        runtime.collect([self.fixture.account], afternoon)
        runtime.collect([self.fixture.account], afternoon)
        events = json.loads(runtime.outbox.path.read_text(encoding='utf-8'))['events']
        self.assertEqual(sorted(item['kind'] for item in events.values()),
                         ['backlog', 'ready', 'schedule_failed'])
        self.assertIn('最早一篇', next(item['payload']['text'] for item in events.values()
                                  if item['kind'] == 'backlog'))

    def enabled_runtime(self):
        runtime = Runtime(detector=Mock(return_value=0))
        runtime.settings = FeishuSettings(True, 'http://review.internal', ('operator',), ('developer',))
        runtime.outbox = Outbox(self.state / 'feishu_outbox.json', runtime.settings)
        runtime.client = Mock()
        runtime.client.send_card.return_value = 'message'
        return runtime

    def test_overnight_skipped_ready_is_cancelled_before_delivery(self):
        runtime = self.enabled_runtime()
        source = self.fixture.source
        engine.append_human_item(self.state, engine.HumanItem(
            'ready-night', 'ready_to_publish', ('facebook:' + source['post_id'],), 'ready',
            {'source_text_sha256': journal.text_sha256(source['text'])}), self.now)
        runtime.collect([self.fixture.account], self.now)
        review.transition(self.fixture.account, source, 'skipped', reason='活动已结束',
                          expected_revision=None, expected_source_sha256=journal.text_sha256(source['text']))
        morning = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)
        runtime.collect([self.fixture.account], morning)
        runtime.outbox.dispatch(morning, runtime.client.send_card)
        runtime.client.send_card.assert_not_called()

    def test_broken_source_does_not_block_system_alert_dispatch(self):
        runtime = self.enabled_runtime()
        runtime.outbox.enqueue('test-alert', 'system', {'text': '请检查会话'}, self.now)
        with patch('pipeline.service.read_post_truth', side_effect=ValueError('invalid')):
            runtime.maintenance(self.now)
        self.assertGreater(runtime.client.send_card.call_count, 0)

    def test_stale_human_translation_gets_review_notification(self):
        runtime = self.enabled_runtime()
        source = self.fixture.source
        engine.append_human_item(self.state, engine.HumanItem(
            'stale-human', 'human_translation_stale', ('facebook:' + source['post_id'],),
            '原文已变更，请复核人工稿', {'source_text_sha256': journal.text_sha256(source['text'])}), self.now)
        runtime.collect([self.fixture.account], self.now)
        events = json.loads(runtime.outbox.path.read_text(encoding='utf-8'))['events']
        ready = [item for item in events.values() if item['kind'] == 'ready']
        self.assertEqual(len(ready), 1)
        self.assertIn('人工稿', ready[0]['payload']['risk'])

    def test_first_enable_does_not_replay_historical_success_receipts(self):
        runtime = self.enabled_runtime()
        source = self.fixture.source
        journal.append(self.state, journal.PublishAttempt(
            post_id=source['post_id'], platform='facebook', status=journal.STATUS_SCHEDULED,
            scheduled_at=self.now.isoformat(), recorded_at=(self.now - timedelta(days=1)).isoformat(),
            text_de_sha256='fixture'))
        runtime.collect([self.fixture.account], self.now)
        events = json.loads(runtime.outbox.path.read_text(encoding='utf-8'))['events']
        self.assertFalse(events)

    def test_notification_image_keys_cache_identical_source_bytes(self):
        runtime = self.enabled_runtime()
        source = self.fixture.source
        task_id = self.fixture.account.name + '/' + source['post_id']
        runtime.thumbnail_sources[task_id] = (self.fixture.account, source)
        runtime.client.upload_image.return_value = 'fixture-image-key'
        payload = {'task_id': task_id, 'source_text_sha256': journal.text_sha256(source['text'])}
        runtime.prepare_preview(payload)
        runtime.prepare_preview(payload)
        runtime.client.upload_image.assert_called_once()
        self.assertEqual(payload['image_key'], 'fixture-image-key')


if __name__ == '__main__':
    unittest.main()

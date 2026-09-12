"""Monitoring recovery and Windows scheduler lifecycle; no browser, model, or task mutation."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests_web_review as fixtures
from core.config import cfg
from core.process_identity import current_worker
from pipeline import engine
from pipeline.service import Runtime
from tools import schedule


NOW = datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc)


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = cfg().state_dir
        engine.activate(self.state, g8_verified=True, now=NOW)

    def test_successful_scan_records_facts_and_durable_pending_batch(self):
        def detector(kind, platform):
            path = self.state / 'delta_state.json'
            path.write_text(json.dumps({platform: {
                'account': 'neakasaofficial', 'last_success': NOW.isoformat(),
                'last_new_count': 2, 'last_observed_skipped': {
                    'video': 3, 'mixed_media': 1, 'no_media': 0, 'no_text': 2},
                'last_reconcile_at': NOW.isoformat(), 'last_reconcile_new_count': 2,
            }}), encoding='utf-8')
            return 0

        runtime = Runtime(detector=detector, process=True)
        runtime.clock = lambda: NOW
        self.addCleanup(runtime.close)
        self.assertEqual(runtime.scan('reconcile', 'facebook'), 0)
        pending = json.loads((self.state / 'processing_state.json').read_text(encoding='utf-8'))
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['platforms']['facebook']['discovered'], 2)
        self.assertEqual(pending['platforms']['facebook']['image_count'], 2)
        self.assertEqual(pending['platforms']['facebook']['skipped']['video'], 3)
        facts = [json.loads(line) for line in
                 (self.state / 'monitoring_facts.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([row['event'] for row in facts],
                         ['scan_started', 'content_discovered', 'scan_finished'])
        summary = runtime.activity_summary(NOW)
        self.assertEqual(summary['reconcile_discovered'], 2)
        self.assertEqual(summary['skipped']['video'], 3)

    def test_no_activity_produces_no_summary(self):
        runtime = Runtime(detector=Mock(return_value=0))
        runtime.clock = lambda: NOW
        self.addCleanup(runtime.close)
        runtime.scan('delta', 'facebook')
        self.assertIsNone(runtime.activity_summary(NOW))

    def test_running_batch_found_after_restart_becomes_interrupted_without_replay(self):
        marker = self.state / 'processing_state.json'
        marker.write_text(json.dumps({
            'version': 1, 'batch_id': 'batch-1', 'status': 'running',
            'requested_at': NOW.isoformat(), 'started_at': NOW.isoformat(),
            'owner': current_worker(),
            'platforms': {'facebook': {'kind': 'delta', 'discovered': 1, 'skipped': {}}},
        }), encoding='utf-8')
        with patch('core.monitoring.worker_alive', return_value=False), \
                patch.object(engine, 'run') as process:
            runtime = Runtime(detector=Mock(return_value=0), process=True)
            self.addCleanup(runtime.close)
            state = runtime.processing_status()
            self.assertEqual(state['status'], 'interrupted')
            self.assertTrue(state['requires_manual_recovery'])
            self.assertIsNone(runtime.start_processing(NOW))
            process.assert_not_called()

    def test_live_running_batch_is_left_owned_and_unknown_owner_requires_review(self):
        marker = self.state / 'processing_state.json'
        base = {'version': 1, 'batch_id': 'batch-live', 'status': 'running',
                'requested_at': NOW.isoformat(), 'started_at': NOW.isoformat(),
                'owner': current_worker(),
                'platforms': {'facebook': {'kind': 'delta', 'discovered': 1, 'skipped': {}}}}
        marker.write_text(json.dumps(base), encoding='utf-8')
        with patch('core.monitoring.worker_alive', return_value=True):
            runtime = Runtime(detector=Mock(return_value=0), process=True)
            self.addCleanup(runtime.close)
            self.assertEqual(runtime.processing_status()['status'], 'running')

        base.pop('owner')
        marker.write_text(json.dumps(base), encoding='utf-8')
        with patch('core.monitoring.worker_alive', return_value=None):
            unknown = Runtime(detector=Mock(return_value=0), process=True)
            self.addCleanup(unknown.close)
            state = unknown.processing_status()
            self.assertEqual(state['status'], 'uncertain')
            self.assertTrue(state['requires_manual_recovery'])


class SchedulerTaskLifecycleTests(unittest.TestCase):
    @staticmethod
    def result(code=0, stdout='', stderr=''):
        return Mock(returncode=code, stdout=stdout, stderr=stderr)

    def test_install_refuses_active_legacy_tasks_before_creating_scheduler(self):
        install = getattr(schedule, 'scheduler_install', None)
        self.assertTrue(callable(install), 'persistent scheduler install entry is missing')
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ['schtasks', '/Query'] and argv[3] == schedule.DAILY_TASK:
                return self.result(stdout='<Task><Settings><Enabled>true</Enabled></Settings></Task>')
            return self.result(code=1, stderr='not found')
        with patch.object(schedule.sys, 'platform', 'win32'), patch.object(schedule.subprocess, 'run', side_effect=run):
            self.assertEqual(install(), 2)
        self.assertFalse(any('/Create' in argv for argv in calls))

    def test_task_query_parses_schtasks_utf16_bytes(self):
        payload = ('<?xml version="1.0" encoding="UTF-16"?>'
                   '<Task><Settings><Enabled>true</Enabled></Settings></Task>')
        with patch.object(schedule.subprocess, 'run', return_value=self.result(
                stdout=payload.encode('utf-16'))) as run:
            self.assertTrue(schedule._task_state(schedule.SCHEDULER_TASK)['enabled'])
        self.assertNotIn('text', run.call_args.kwargs)

    def test_install_status_disable_and_resume_target_only_persistent_task(self):
        install = getattr(schedule, 'scheduler_install', None)
        status = getattr(schedule, 'scheduler_status', None)
        enabled = getattr(schedule, 'scheduler_set_enabled', None)
        self.assertTrue(all(callable(fn) for fn in (install, status, enabled)),
                        'persistent scheduler lifecycle entries are missing')
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ['schtasks', '/Query']:
                if argv[3] == schedule.SCHEDULER_TASK:
                    return self.result(stdout='<Task><Settings><Enabled>true</Enabled></Settings></Task>')
                return self.result(code=1, stderr='not found')
            return self.result(stdout='ok')
        with tempfile.TemporaryDirectory() as td, patch.object(schedule.sys, 'platform', 'win32'), \
                patch.object(schedule.subprocess, 'run', side_effect=run), \
                patch.object(schedule, '_write_xml', return_value=Path(td) / 'scheduler.xml'):
            self.assertEqual(install(), 0)
            self.assertEqual(status(), 0)
            self.assertEqual(enabled(False), 0)
            self.assertEqual(enabled(True), 0)
        creates = [argv for argv in calls if '/Create' in argv]
        changes = [argv for argv in calls if '/Change' in argv]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][3], schedule.SCHEDULER_TASK)
        self.assertEqual([argv[-1] for argv in changes], ['/DISABLE', '/ENABLE'])


if __name__ == '__main__':
    unittest.main()

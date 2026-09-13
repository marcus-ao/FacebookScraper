"""Monitoring recovery and Windows scheduler lifecycle; no browser, model, or task mutation."""
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activation_fixtures import activate as fixture_activate

import tests_web_review as fixtures
from core.config import cfg
from core.monitoring import MonitoringJournal
from core.process_identity import current_worker
from pipeline import engine
from pipeline.service import Runtime
from tools import schedule


NOW = datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc)
EMPTY_SKIPS = {'video': 0, 'mixed_media': 0, 'no_media': 0, 'no_text': 0}


class MonitoringJournalQueueTests(unittest.TestCase):
    def test_requests_during_running_batch_are_accumulated_into_successor(self):
        with tempfile.TemporaryDirectory() as td:
            journal = MonitoringJournal(Path(td), now=NOW)
            journal.request(NOW, 'facebook', 'delta', 1, EMPTY_SKIPS, image_count=1)
            running = journal.claim(NOW)

            journal.request(NOW + timedelta(minutes=1), 'instagram', 'delta', 2,
                            {**EMPTY_SKIPS, 'video': 1}, image_count=3)
            journal.request(NOW + timedelta(minutes=2), 'instagram', 'reconcile', 3,
                            {**EMPTY_SKIPS, 'video': 2}, image_count=4)
            journal.request(NOW + timedelta(minutes=3), 'facebook', 'delta', 2,
                            EMPTY_SKIPS, image_count=2)

            pending = journal.finish(running, NOW + timedelta(minutes=4), code=0)
            self.assertEqual(pending['status'], 'pending')
            self.assertNotEqual(pending['batch_id'], running['batch_id'])
            self.assertEqual(pending['platforms']['instagram']['discovered'], 5)
            self.assertEqual(pending['platforms']['instagram']['image_count'], 7)
            self.assertEqual(pending['platforms']['instagram']['skipped']['video'], 3)
            self.assertEqual(pending['platforms']['facebook']['discovered'], 2)
            self.assertEqual(journal.claim(NOW + timedelta(minutes=5))['batch_id'],
                             pending['batch_id'])

    def test_uncertain_running_batch_is_not_replayed_when_successor_exists(self):
        with tempfile.TemporaryDirectory() as td:
            journal = MonitoringJournal(Path(td), now=NOW)
            journal.request(NOW, 'facebook', 'delta', 1, EMPTY_SKIPS)
            running = journal.claim(NOW)
            journal.request(NOW + timedelta(minutes=1), 'instagram', 'delta', 2,
                            EMPTY_SKIPS)

            uncertain = journal.finish(running, NOW + timedelta(minutes=2), code=None,
                                       error='crashed')

            self.assertEqual(uncertain['status'], 'uncertain')
            self.assertEqual(uncertain['batch_id'], running['batch_id'])
            self.assertEqual(uncertain['next_batch']['platforms']['instagram']['discovered'], 2)
            self.assertIsNone(journal.claim(NOW + timedelta(minutes=3)))

    def test_concurrent_requests_do_not_lose_same_platform_counts(self):
        with tempfile.TemporaryDirectory() as td:
            journal = MonitoringJournal(Path(td), now=NOW)

            def request(index):
                return journal.request(NOW + timedelta(seconds=index), 'facebook', 'delta', 1,
                                       {**EMPTY_SKIPS, 'no_text': 1}, image_count=2)

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(request, range(24)))

            pending = journal.processing_status()
            self.assertEqual(pending['platforms']['facebook']['discovered'], 24)
            self.assertEqual(pending['platforms']['facebook']['image_count'], 48)
            self.assertEqual(pending['platforms']['facebook']['skipped']['no_text'], 24)

    def test_requests_from_separate_processes_share_the_state_lock(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            gate = state / 'go'
            code = "\n".join((
                "import sys, time",
                "from datetime import datetime, timezone",
                "from pathlib import Path",
                "from core.monitoring import MonitoringJournal",
                "state, gate = Path(sys.argv[1]), Path(sys.argv[2])",
                "while not gate.exists(): time.sleep(0.005)",
                "journal = MonitoringJournal(state, now=datetime.now(timezone.utc))",
                "journal.request(datetime.now(timezone.utc), 'facebook', 'delta', 1, {}, image_count=1)",
            ))
            children = [subprocess.Popen(
                [sys.executable, '-c', code, str(state), str(gate)],
                cwd=Path(__file__).resolve().parent.parent,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) for _ in range(8)]
            gate.touch()
            for child in children:
                stdout, stderr = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0,
                                 (stdout + stderr).decode(errors='replace'))

            pending = MonitoringJournal(state, now=NOW).processing_status()
            self.assertEqual(pending['platforms']['facebook']['discovered'], 8)

    def test_only_one_process_can_claim_a_paid_batch(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            journal = MonitoringJournal(state, now=NOW)
            journal.request(NOW, 'facebook', 'delta', 1, EMPTY_SKIPS)
            gate = state / 'claim-go'
            code = "\n".join((
                "import sys, time",
                "from datetime import datetime, timezone",
                "from pathlib import Path",
                "from core.monitoring import MonitoringJournal",
                "state, gate = Path(sys.argv[1]), Path(sys.argv[2])",
                "journal = MonitoringJournal(state, now=datetime.now(timezone.utc))",
                "while not gate.exists(): time.sleep(0.005)",
                "batch = journal.claim(datetime.now(timezone.utc))",
                "print('claimed' if batch else 'skipped', flush=True)",
                "time.sleep(0.2) if batch else None",
            ))
            children = [subprocess.Popen(
                [sys.executable, '-c', code, str(state), str(gate)],
                cwd=Path(__file__).resolve().parent.parent,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) for _ in range(8)]
            gate.touch()
            outcomes = []
            for child in children:
                stdout, stderr = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, stderr.decode(errors='replace'))
                outcomes.append(stdout.decode().strip())
            self.assertEqual(outcomes.count('claimed'), 1)


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = cfg().state_dir
        fixture_activate(engine, self.state, g8_verified=True, now=NOW)

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

    def test_task_query_reads_settings_enabled_instead_of_trigger_enabled(self):
        payload = ('<Task xmlns="%s"><Triggers><LogonTrigger><Enabled>true</Enabled>'
                   '</LogonTrigger></Triggers><Settings><Enabled>false</Enabled>'
                   '</Settings></Task>') % schedule.NS
        with patch.object(schedule.subprocess, 'run', return_value=self.result(
                stdout=payload.encode('utf-16'))):
            state = schedule._task_state(schedule.SCHEDULER_TASK)
        self.assertTrue(state['registered'])
        self.assertIs(state['enabled'], False)

    def test_task_query_keeps_missing_or_invalid_settings_enabled_unknown(self):
        fixtures = (
            '<Task><Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger>'
            '</Triggers><Settings /></Task>',
            '<Task><Settings><Enabled>sometimes</Enabled></Settings></Task>',
        )
        for payload in fixtures:
            with self.subTest(payload=payload), patch.object(
                    schedule.subprocess, 'run', return_value=self.result(stdout=payload)):
                self.assertIsNone(schedule._task_state(schedule.SCHEDULER_TASK)['enabled'])

    def test_resume_refuses_legacy_conflict_before_mutating_scheduler(self):
        with patch.object(schedule.sys, 'platform', 'win32'), \
                patch.object(schedule, '_legacy_scheduler_conflicts', return_value=[{
                    'name': schedule.DAILY_TASK, 'registered': True, 'enabled': True,
                }]), patch.object(schedule.subprocess, 'run') as run:
            self.assertEqual(schedule.scheduler_set_enabled(True), 2)
        run.assert_not_called()

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
        runtime_actions = [argv for argv in calls if '/End' in argv or '/Run' in argv]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][3], schedule.SCHEDULER_TASK)
        self.assertEqual([argv[-1] for argv in changes], ['/DISABLE', '/ENABLE'])
        self.assertEqual([argv[1] for argv in runtime_actions], ['/End', '/Run'])
        self.assertTrue(all(argv[3] == schedule.SCHEDULER_TASK for argv in runtime_actions))


if __name__ == '__main__':
    unittest.main()

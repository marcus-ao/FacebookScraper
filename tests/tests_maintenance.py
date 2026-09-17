"""Deployment admission races and durable queue/session protection; temporary control only."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import maintenance


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.control = Path(temporary.name)
        self.gate = maintenance.Gate(self.control)
        self.gate.initialize()
        self.env = patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(self.control))
        self.env.start()
        self.addCleanup(self.env.stop)

    def announce(self, now=100):
        return self.gate.announce(now=now, delay=0)

    def test_active_operation_prevents_quiescence_and_closed_gate_rejects_work(self):
        with maintenance.operation('image'):
            self.announce()
            self.assertFalse(self.gate.try_quiesce(now=100))
            self.assertEqual(len(self.gate.status(now=100)['operations']), 1)
        self.assertTrue(self.gate.try_quiesce(now=100))
        with self.assertRaises(maintenance.MaintenanceBlocked):
            with maintenance.operation('publish'):
                self.fail('A closed maintenance gate admitted a publication')
        self.gate.reopen()
        with maintenance.operation('save'):
            pass

    def test_nested_work_completes_under_the_original_admission(self):
        with maintenance.operation('capture'):
            self.announce()
            with maintenance.operation('archive'):
                self.assertEqual(len(self.gate.status(now=100)['operations']), 1)
        self.assertTrue(self.gate.try_quiesce(now=100))

    def test_queued_future_remains_registered_after_http_request_returns(self):
        hold, started, finished = Event(), Event(), Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            blocker = executor.submit(hold.wait)
            try:
                with maintenance.operation('http'):
                    future = maintenance.submit(executor, 'image', lambda: (started.set(), finished.set()))
                self.announce()
                self.assertFalse(started.is_set())
                self.assertFalse(self.gate.try_quiesce(now=100))
            finally:
                hold.set()
            blocker.result(timeout=3)
            future.result(timeout=3)
        self.assertTrue(finished.is_set())
        self.assertTrue(self.gate.try_quiesce(now=100))

    def test_cancelled_queued_future_releases_its_admission(self):
        hold = Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            blocker = executor.submit(hold.wait)
            try:
                future = maintenance.submit(executor, 'image', self.fail, 'Cancelled job ran')
                self.assertTrue(future.cancel())
                self.announce()
                self.assertTrue(self.gate.try_quiesce(now=100))
            finally:
                hold.set()
            blocker.result(timeout=3)

    def test_async_work_keeps_independent_admission_after_request_and_cancellation(self):
        async def scenario():
            started, finish = asyncio.Event(), asyncio.Event()
            async def work():
                with maintenance.operation('nested-publish'):
                    started.set()
                    await finish.wait()
            with maintenance.operation('http'):
                task = maintenance.create_task('publish', work)
                await started.wait()
            self.announce()
            self.assertEqual(len(self.gate.status(now=100)['operations']), 1)
            self.assertFalse(self.gate.try_quiesce(now=100))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(self.gate.try_quiesce(now=100))
            self.gate.reopen()
            queued = maintenance.create_task('publish', work)
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            self.announce()
            self.assertTrue(self.gate.try_quiesce(now=100))
        asyncio.run(scenario())

    def test_unsaved_session_does_not_become_safe_when_heartbeat_expires(self):
        self.gate.session('draft', runtime_id='a' * 64, dirty=True, busy=False, now=1)
        epoch = self.announce(now=1000)
        self.assertFalse(self.gate.try_quiesce(now=1000))
        self.gate.session('draft', runtime_id='a' * 64, dirty=False, busy=False,
                          ack_epoch=epoch, now=1001)
        self.assertTrue(self.gate.try_quiesce(now=1001))

    def test_delayed_clean_heartbeat_cannot_replace_newer_dirty_state(self):
        self.gate.session('draft', runtime_id='a' * 64, dirty=True, busy=False, sequence=2, now=1)
        self.gate.session('draft', runtime_id='a' * 64, dirty=False, busy=False, sequence=1, now=2)
        self.announce(now=1000)
        self.assertFalse(self.gate.try_quiesce(now=1000))

    def test_corrupt_session_or_announcement_is_not_treated_as_acknowledged(self):
        self.gate.session('draft', runtime_id='a' * 64, dirty=True, busy=False, now=1)
        self.announce()
        value = json.loads(self.gate.path.read_text(encoding='utf-8'))
        value['sessions']['draft']['dirty'] = None
        self.gate.path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(maintenance.MaintenanceBlocked):
            self.gate.try_quiesce(now=1000)

    def test_fresh_clean_session_requires_current_epoch_acknowledgment(self):
        self.gate.session('reader', runtime_id='a' * 64, dirty=False, busy=False, now=100)
        epoch = self.announce()
        self.assertFalse(self.gate.try_quiesce(now=100))
        self.gate.session('reader', runtime_id='a' * 64, dirty=False, busy=False,
                          ack_epoch='old', now=100)
        self.assertFalse(self.gate.try_quiesce(now=100))
        self.gate.session('reader', runtime_id='a' * 64, dirty=False, busy=False,
                          ack_epoch=epoch, now=100)
        self.assertTrue(self.gate.try_quiesce(now=100))

    def test_defer_revokes_quiescence_until_deadline(self):
        self.announce()
        self.gate.defer(now=100)
        self.assertFalse(self.gate.try_quiesce(now=200))
        self.assertEqual(self.gate.status(now=200)['deferred_until'], 1900)
        with maintenance.operation('save'):
            pass

    def test_unknown_process_liveness_cannot_authorize_switch(self):
        with maintenance.operation('paid'):
            self.announce()
            with patch.object(maintenance, 'worker_alive', return_value=None):
                self.assertFalse(self.gate.try_quiesce(now=100))

    def test_confirmed_dead_operation_is_reconciled_without_business_replay(self):
        with maintenance.operation('paid'):
            self.announce()
            with patch.object(maintenance, 'worker_alive', return_value=False):
                self.assertTrue(self.gate.try_quiesce(now=100))
        self.assertEqual(self.gate.status(now=100)['operations'], [])
        self.assertEqual(len(self.gate.status(now=100)['interrupted']), 1)

    def test_corrupt_or_missing_managed_gate_does_not_default_to_open(self):
        self.gate.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(maintenance.MaintenanceBlocked):
            with maintenance.operation('save'):
                self.fail('Corrupt control state admitted work')
        self.gate.path.unlink()
        with self.assertRaises(maintenance.MaintenanceBlocked):
            with maintenance.operation('save'):
                self.fail('Missing managed control state admitted work')

    def test_stop_request_requires_quiescence_and_matches_process_creation(self):
        owner = maintenance.current_worker()
        with self.assertRaises(maintenance.MaintenanceBlocked):
            self.gate.request_stop([owner])
        self.announce()
        self.assertTrue(self.gate.try_quiesce(now=100))
        self.gate.request_stop([owner])
        self.assertTrue(self.gate.should_stop(owner))
        self.assertFalse(self.gate.should_stop(dict(owner, started='different')))

    def test_public_business_entries_reject_before_validation_or_side_effects(self):
        from pipeline import refinement, initial_translation, engine
        from routes import delta
        self.announce()
        self.assertTrue(self.gate.try_quiesce(now=100))
        for fn in (refinement.submit, initial_translation.submit, engine.run, delta.main):
            with self.subTest(entry=fn.__module__), self.assertRaises(maintenance.MaintenanceBlocked):
                fn()

    def test_async_publication_cannot_bypass_the_closed_gate(self):
        import asyncio
        from pipeline import approval
        self.announce()
        self.assertTrue(self.gate.try_quiesce(now=100))
        with self.assertRaises(maintenance.MaintenanceBlocked):
            asyncio.run(approval.approve())


if __name__ == '__main__':
    unittest.main()

"""Durable deployment transitions run against isolated state and fake workers."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.maintenance import Gate
from deployment.controller import Controller, initial_state
from deployment.host import write_json

A, B, C = ('a' * 40, 'b' * 40, 'c' * 40)


class Backend:
    def __init__(self):
        self.calls = []
        self.good = True
        self.gone = True
        self.fail_prepare = False

    def prepare(self, candidate):
        self.calls.append(('prepare', candidate['sha']))
        if self.fail_prepare:
            raise ValueError('offline_dependencies')
        return candidate

    def start(self, sha, modes, persist):
        self.calls.append(('start', sha, dict(modes)))
        rows = {'web': {'sha': sha, 'worker': {'pid': 11, 'started': '12'}}}
        persist(rows)
        return rows

    def stop(self, workers):
        self.calls.append(('stop', workers))

    def exited(self, workers):
        return self.gone

    def ready(self, sha, workers, modes):
        return self.good or sha == A

    def manifest(self, sha):
        return {'sha': sha, 'runtime_id': sha[:1] * 64}


class Remote:
    def __init__(self):
        self.sha = B
        self.runtime = 'b' * 64

    def head(self):
        return self.sha

    def candidate(self):
        return {'sha': self.sha, 'runtime_id': self.runtime}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / 'control'
        self.control.mkdir()
        self.gate = Gate(self.control)
        self.gate.initialize()
        self.backend, self.remote = Backend(), Remote()
        self.settings = {'version': 1, 'instance_id': 'instance', 'paused': False,
                         'scheduler_enabled': False, 'process_enabled': False,
                         'request_id': '', 'request': None}
        write_json(self.control / 'host.json', self.settings)
        state = initial_state(A)
        state['workers'] = {'web': {'sha': A, 'worker': {'pid': 1, 'started': '2'}}}
        write_json(self.control / 'deployment.json', state)
        self.events = []
        self.controller = self.new()
        self.ledger = self.root / 'shared/state/published.jsonl'
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_bytes(b'precious\n')

    def new(self):
        return Controller(self.root, self.backend, self.remote,
                          notify=lambda kind, payload: self.events.append((kind, payload)))

    def advance(self):
        for now in (0, 60, 61, 62, 63):
            self.controller.tick(now=now)

    def test_success_preserves_ledgers_modes_and_duplicate_poll(self):
        self.advance()
        value = self.controller.state
        self.assertEqual(value['current_sha'], B)
        self.assertEqual(value['last_good_sha'], B)
        self.assertEqual(self.gate.status()['phase'], 'open')
        self.assertFalse(value['scheduler_enabled'])
        self.assertFalse(value['process_enabled'])
        self.assertEqual(self.ledger.read_bytes(), b'precious\n')
        self.controller.tick(now=150)
        self.assertEqual(len([call for call in self.backend.calls if call[:2] == ('start', B)]), 1)

    def test_bad_health_rolls_back_before_admission_and_blocks_repeat(self):
        self.backend.good = False
        for now in (0, 60, 61, 62, 155, 156, 157, 158):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.controller.state['blocked_sha'], B)
        self.assertEqual(self.gate.status()['phase'], 'open')
        self.assertEqual(self.ledger.read_bytes(), b'precious\n')
        self.assertTrue(any(event[0] == 'rollback' for event in self.events))

    def test_cooperative_rollback_wait_preserves_the_original_failure(self):
        self.backend.good = False
        for now in (0, 60, 61, 62, 155):
            self.controller.tick(now=now)
        self.backend.gone = False
        self.controller.tick(now=156)
        self.assertEqual(self.controller.state['phase'], 'rollback_stopping')
        self.assertEqual(self.controller.state['error_code'], 'candidate_health_failed')
        self.backend.gone = True
        for now in (157, 158):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.controller.state['error_code'], 'candidate_health_failed')

    def test_restart_in_each_switch_phase_restores_previous_good(self):
        for phase in ('stopping', 'starting', 'verifying', 'rollback_stopping', 'rollback_starting'):
            self.gate.reopen()
            self.gate.announce(now=0, delay=0)
            self.gate.try_quiesce(now=0)
            value = initial_state(A)
            value.update(phase=phase, candidate_sha=B, journal={
                'previous_sha': A, 'target_sha': B,
                'previous_modes': {'scheduler_enabled': False, 'process_enabled': False},
                'target_modes': {'scheduler_enabled': False, 'process_enabled': False},
                'old_workers': {}, 'new_workers': {}, 'started_at': 0})
            write_json(self.control / 'deployment.json', value)
            self.controller = self.new()
            for now in (1, 2, 3, 4):
                self.controller.tick(now=now)
            self.assertEqual(self.controller.state['current_sha'], A, phase)
            self.assertIsNone(self.controller.state['journal'], phase)
            self.assertEqual(self.gate.status()['phase'], 'open', phase)

    def test_unknown_cleanup_blocks_and_never_launches_replacement(self):
        self.backend.gone = None
        self.advance()
        self.assertEqual(self.gate.status()['phase'], 'quiesced')
        self.assertFalse(any(call[0] == 'start' for call in self.backend.calls))

    def test_prepare_failure_and_superseded_head_do_not_stop_old(self):
        self.backend.fail_prepare = True
        self.controller.tick(now=0)
        self.assertEqual(self.controller.state['blocked_sha'], B)
        self.assertEqual(self.gate.status()['phase'], 'open')
        self.assertFalse(any(call[0] == 'stop' for call in self.backend.calls))
        self.backend.fail_prepare = False
        self.remote.sha = C
        self.remote.runtime = 'c' * 64
        self.controller.tick(now=60)
        self.remote.sha = B
        self.controller.tick(now=120)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertFalse(any(call[0] == 'stop' for call in self.backend.calls))

    def test_busy_dirty_defer_pause_keep_old_working(self):
        self.gate.session('editor', runtime_id='a' * 64, dirty=True, busy=False, now=0)
        self.advance()
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.gate.status()['phase'], 'open')
        self.settings['paused'] = True
        write_json(self.control / 'host.json', self.settings)
        self.controller.tick(now=100)
        self.assertFalse(any(call[0] == 'stop' for call in self.backend.calls))

    def test_matching_runtime_observes_new_sha_without_restart(self):
        self.remote.runtime = 'a' * 64
        self.controller.tick(now=0)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.controller.state['observed_sha'], B)
        self.assertFalse(any(call[0] == 'stop' for call in self.backend.calls))

    def test_explicit_rollback_and_retry_use_the_same_announcement(self):
        self.advance()
        self.settings.update(request_id='rollback-1', request={'kind': 'rollback', 'sha': A})
        write_json(self.control / 'host.json', self.settings)
        self.controller.tick(now=100)
        self.assertEqual(self.gate.status()['phase'], 'announcing')
        self.assertEqual(self.controller.state['current_sha'], B)
        for now in (160, 161, 162):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.ledger.read_bytes(), b'precious\n')

    def test_modes_are_preserved_on_update_and_pause_persists_after_restart(self):
        self.settings.update(scheduler_enabled=True, process_enabled=True)
        write_json(self.control / 'host.json', self.settings)
        self.controller.state.update(scheduler_enabled=True, process_enabled=True)
        self.controller._save(0)
        self.advance()
        started = [call for call in self.backend.calls if call[0] == 'start']
        self.assertEqual(started[-1][2], {'scheduler_enabled': True, 'process_enabled': True})
        self.settings['paused'] = True
        write_json(self.control / 'host.json', self.settings)
        self.controller = self.new()
        self.controller.tick(now=200)
        self.assertTrue(self.controller.state['paused'])
        self.assertEqual(self.controller.state['current_sha'], B)

    def test_notification_failure_cannot_undo_committed_deployment(self):
        def fail(kind, payload):
            raise OSError('fake notification offline')
        self.controller.notify = fail
        self.advance()
        self.assertEqual(self.controller.state['current_sha'], B)
        self.assertEqual(self.gate.status()['phase'], 'open')

    def test_dead_workers_after_restart_go_through_maintenance(self):
        self.backend.running = lambda workers: False
        self.remote.candidate = lambda: None
        self.controller = self.new()
        self.controller.tick(now=0)
        self.assertEqual(self.gate.status()['phase'], 'announcing')
        self.assertFalse(any(call[0] == 'stop' for call in self.backend.calls))
        self.controller.tick(now=60)
        self.assertEqual(self.gate.status()['phase'], 'quiesced')

    def test_waiting_half_hour_sends_one_event_and_defer_is_honored(self):
        self.gate.defer(now=0)
        self.controller.tick(now=0)
        self.assertEqual(self.gate.status()['phase'], 'open')
        self.gate.session('editor', runtime_id='a' * 64, dirty=True, busy=False, now=0)
        for now in (1800, 1860, 1920):
            self.controller.tick(now=now)
        self.assertEqual(len([event for event in self.events if event[0] == 'blocked']), 1)

    def test_paused_windows_restart_restores_workers_without_enabling_modes(self):
        self.settings['paused'] = True
        write_json(self.control / 'host.json', self.settings)
        self.backend.running = lambda workers: False
        self.controller = self.new()
        self.controller.tick(now=0)
        self.assertEqual(self.controller.state['phase'], 'announcing')
        self.assertTrue(self.controller.state['paused'])
        self.assertEqual(self.controller.state['candidate_sha'], A)

    def test_corrupt_mode_types_fail_closed(self):
        self.settings['scheduler_enabled'] = True
        self.settings['process_enabled'] = 'false'
        write_json(self.control / 'host.json', self.settings)
        with self.assertRaises(ValueError):
            self.controller.tick(now=0)
        self.assertFalse(self.backend.calls)

    def test_failed_mode_change_does_not_repeatedly_restart_old_workers(self):
        self.settings.update(scheduler_enabled=True, process_enabled=True)
        write_json(self.control / 'host.json', self.settings)
        self.backend.ready = lambda sha, workers, modes: not modes['process_enabled']
        for now in (0, 60, 61, 62, 155, 156, 157, 158):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertFalse(self.controller.state['process_enabled'])
        count = len([call for call in self.backend.calls if call[0] == 'start'])
        self.controller.tick(now=300)
        self.assertEqual(len([call for call in self.backend.calls if call[0] == 'start']), count)

    def test_rollback_request_during_announcement_replaces_unstarted_target(self):
        self.controller.tick(now=0)
        self.settings.update(request_id='rollback-during-wait', request={'kind': 'rollback', 'sha': A})
        write_json(self.control / 'host.json', self.settings)
        for now in (60, 120, 121, 122, 123):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertFalse(any(call[:2] == ('start', B) for call in self.backend.calls))

    def test_changed_modes_cannot_promote_superseded_automatic_candidate(self):
        self.controller.tick(now=0)
        self.settings['scheduler_enabled'] = True
        write_json(self.control / 'host.json', self.settings)
        self.remote.sha, self.remote.runtime = C, 'c' * 64
        for now in (60, 120, 121, 122):
            self.controller.tick(now=now)
        self.assertFalse(any(call[:2] == ('start', B) for call in self.backend.calls))

    def test_normal_restart_restores_last_committed_successful_version(self):
        self.advance()
        self.backend.running = lambda workers: False
        self.controller = self.new()
        self.controller.tick(now=200)
        self.assertEqual(self.controller.state['candidate_sha'], B)

    def test_failed_running_version_stays_blocked_after_safe_fallback(self):
        self.advance()
        self.backend.running = lambda workers: True
        self.controller.tick(now=80)
        self.backend.running = lambda workers: False
        self.controller.tick(now=90)
        self.backend.running = lambda workers: True
        for now in (150, 151, 152, 213):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.controller.state['blocked_sha'], B)
        self.assertEqual(self.controller.state['phase'], 'idle')
        self.assertEqual(self.events[-1][0], 'rollback')
        self.assertEqual(self.controller.state['error_code'], 'running_worker_exited')
        self.assertEqual(self.ledger.read_bytes(), b'precious\n')

    def test_interrupted_runtime_fallback_never_restarts_known_failed_version(self):
        self.advance()
        self.backend.running = lambda workers: True
        self.controller.tick(now=80)
        self.backend.running = lambda workers: False
        for now in (90, 150):
            self.controller.tick(now=now)
        self.backend.calls.clear()
        self.controller = self.new()
        for now in (151, 152, 153):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], A)
        self.assertEqual(self.controller.state['blocked_sha'], B)
        self.assertFalse(any(call[:2] == ('start', B) for call in self.backend.calls))

    def test_failed_explicit_prepare_is_not_repeated_every_tick(self):
        self.backend.fail_prepare = True
        self.settings.update(request_id='rollback-fails', request={'kind': 'rollback', 'sha': A})
        write_json(self.control / 'host.json', self.settings)
        for now in (0, 1, 2):
            self.controller.tick(now=now)
        self.assertEqual(len([call for call in self.backend.calls if call[0] == 'prepare']), 1)
        self.assertEqual(len([event for event in self.events if event[0] == 'failure']), 1)

    def test_paused_restart_preserves_committed_modes_despite_pending_desired_change(self):
        self.advance()
        self.settings.update(paused=True, scheduler_enabled=True)
        write_json(self.control / 'host.json', self.settings)
        self.backend.running = lambda workers: False
        self.controller = self.new()
        for now in (200, 260, 261, 262):
            self.controller.tick(now=now)
        self.assertEqual(self.controller.state['current_sha'], B)
        self.assertFalse(self.controller.state['scheduler_enabled'])
        self.assertEqual(self.gate.status()['phase'], 'open')

    def test_failed_mode_prepare_is_suppressed_until_new_request_or_version(self):
        self.backend.fail_prepare = True
        self.settings['scheduler_enabled'] = True
        write_json(self.control / 'host.json', self.settings)
        for now in (0, 1, 2):
            self.controller.tick(now=now)
        self.assertEqual(len([call for call in self.backend.calls if call[0] == 'prepare']), 1)

    def test_local_manifest_failure_is_suppressed_for_rollback_and_modes(self):
        for kind in ('rollback', 'mode'):
            with self.subTest(kind=kind):
                write_json(self.control / 'deployment.json', initial_state(A))
                settings = dict(self.settings, scheduler_enabled=kind == 'mode', request_id=kind,
                                request={'kind': 'rollback', 'sha': A} if kind == 'rollback' else None)
                write_json(self.control / 'host.json', settings)
                calls = []
                def broken(sha):
                    calls.append(sha)
                    raise ValueError('payload hash mismatch')
                self.backend.manifest = broken
                controller = self.new()
                for now in (0, 1, 2):
                    controller.tick(now=now)
                self.assertEqual(calls, [A])


if __name__ == '__main__':
    unittest.main()

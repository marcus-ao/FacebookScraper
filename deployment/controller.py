"""Durable, tick-driven deployment state machine; business admission stays closed during switches."""
from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from core.maintenance import Gate, MaintenanceBlocked
from core.paid_model import atomic_write_json
from deployment.errors import DeploymentError

MODES = ('scheduler_enabled', 'process_enabled')
TRANSITIONS = {'stopping', 'starting', 'verifying', 'rollback_stopping', 'rollback_starting', 'rollback_verifying'}


def initial_state(sha):
    return {'version': 1, 'phase': 'idle', 'current_sha': sha, 'last_good_sha': sha,
            'candidate_sha': None, 'observed_sha': sha, 'blocked_sha': None,
            'paused': False, 'scheduler_enabled': False, 'process_enabled': False,
            'error_code': None, 'waiting_since': None, 'updated_at': 0,
            'journal': None, 'workers': {}, 'history': [sha], 'request_id': '',
            'next_poll': 0, 'poll_failures': 0, 'blocked_notified': False}


class Controller:
    def __init__(self, root, backend, remote, *, notify=None):
        self.root = Path(root)
        self.control = self.root / 'control'
        self.path = self.control / 'deployment.json'
        self.state = json.loads(self.path.read_text(encoding='utf-8'))
        if (self.state.get('version') != 1 or not isinstance(self.state.get('workers'), dict)
                or not isinstance(self.state.get('history'), list)):
            raise DeploymentError('deployment_state_invalid')
        self.backend, self.remote = backend, remote
        self.gate = Gate(self.control)
        self.notify = notify or (lambda kind, payload: None)
        self.recovering = bool(self.state.get('journal'))
        self.reconciling_startup = True

    def _save(self, now):
        self.state['updated_at'] = now
        atomic_write_json(self.path, self.state)

    def _event(self, kind, now):
        try:
            self.notify(kind, {'event_id': self.state.get('event_id') or uuid4().hex,
                'sha': self.state['current_sha'], 'candidate_sha': self.state['candidate_sha'],
                'error_code': self.state['error_code'], 'at': now})
        except Exception:
            # Notification failure cannot turn a committed deployment into a rollback.
            self.state['notification_error'] = 'deployment_notification_failed'
            self._save(now)

    def _waiting(self, now):
        if self.state['waiting_since'] is None:
            self.state['waiting_since'] = now
        if now - self.state['waiting_since'] >= 1800 and not self.state['blocked_notified']:
            self.state['blocked_notified'] = True
            self._save(now)
            self._event('blocked', now)

    def _persist_workers(self, key, now):
        def persist(workers):
            self.state['journal'][key] = workers
            self._save(now)
        return persist

    def _rollback(self, now, code):
        recovery = self.state['journal'].get('runtime_recovery')
        self.state.update(phase='rollback_stopping', error_code=code,
                          blocked_sha=recovery['failed_sha'] if recovery else self.state['journal']['target_sha'],
                          blocked_modes=recovery['failed_modes'] if recovery else self.state['journal']['target_modes'])
        self.state['journal']['deadline'] = now + 90
        self._save(now)

    def _combined_workers(self):
        journal = self.state['journal']
        return {f'{label}:{role}': row for label, key in (('old', 'old_workers'), ('new', 'new_workers'),
                                                       ('restored', 'restored_workers'))
                for role, row in journal.get(key, {}).items()}

    def _switch(self, now):
        journal = self.state['journal']
        phase = self.state['phase']
        if self.gate.status(now=now)['phase'] != 'quiesced':
            self.state.update(phase='blocked', error_code='switch_gate_not_closed')
            self._save(now)
            return
        if phase in {'stopping', 'rollback_stopping'}:
            workers = journal['old_workers'] if phase == 'stopping' else self._combined_workers()
            try:
                self.backend.stop(workers)
            except MaintenanceBlocked:
                if phase == 'stopping':
                    self.state['error_code'] = 'stop_waiting_for_session'
                self._waiting(now)
                self._save(now)
                return
            if self.backend.exited(workers) is not True:
                # Rollback can take several ticks; retain the failure that required it.
                if phase == 'stopping':
                    self.state['error_code'] = 'worker_exit_unconfirmed'
                self._waiting(now)
                self._save(now)
                return
            rollback = phase == 'rollback_stopping'
            self.state['phase'] = 'rollback_starting' if rollback else 'starting'
            self._save(now)
            sha = journal['previous_sha'] if rollback else journal['target_sha']
            modes = journal['previous_modes'] if rollback else journal['target_modes']
            # Clear requests for old owners before spawning the replacement.
            self.gate.request_stop([])
            key = 'restored_workers' if rollback else 'new_workers'
            try:
                workers = self.backend.start(sha, modes, self._persist_workers(key, now))
            except Exception:
                if rollback:
                    self.state.update(phase='blocked', error_code='rollback_start_failed')
                    self._save(now)
                    self._event('failure', now)
                else:
                    self._rollback(now, 'candidate_start_failed')
                return
            journal[key] = workers
            journal['deadline'] = now + 90
            self.state['phase'] = 'rollback_verifying' if rollback else 'verifying'
            self._save(now)
            return
        if phase in {'verifying', 'rollback_verifying'}:
            rollback = phase == 'rollback_verifying'
            sha = journal['previous_sha'] if rollback else journal['target_sha']
            modes = journal['previous_modes'] if rollback else journal['target_modes']
            workers = journal['restored_workers'] if rollback else journal['new_workers']
            try:
                healthy = self.backend.ready(sha, workers, modes)
            except Exception:
                healthy = False
            if healthy:
                recovery = journal.get('runtime_recovery')
                history = self.state['history']
                if not rollback:
                    history = [previous for previous in history if previous != sha] + [sha]
                self.state.update(phase='idle', current_sha=sha, last_good_sha=sha,
                    workers=workers, history=history, journal=None, candidate_sha=None,
                    waiting_since=None, blocked_notified=False, next_poll=now + 60,
                    **modes)
                if recovery:
                    self.state.update(blocked_sha=recovery['failed_sha'],
                        blocked_modes=recovery['failed_modes'], error_code='running_worker_exited')
                elif not rollback:
                    self.state['error_code'] = None
                self._save(now)  # Commit before reopening; crash here can safely adopt committed workers.
                self.gate.reopen()
                self._event('rollback' if rollback or recovery else 'success', now)
            elif now >= journal['deadline']:
                if rollback:
                    self.state.update(phase='blocked', error_code='rollback_health_failed')
                    self._save(now)
                    self._event('failure', now)
                else:
                    self._rollback(now, 'candidate_health_failed')
            else:
                self._save(now)

    def _begin(self, sha, modes, now):
        old_modes = {key: self.state[key] for key in MODES}
        recovery = self.state.pop('runtime_recovery', None)
        self.state.update(phase='stopping', event_id=uuid4().hex, journal={
            # A failed running version is not a recovery target, even if this switch is interrupted.
            'previous_sha': sha if recovery else self.state['current_sha'], 'target_sha': sha,
            'previous_modes': modes if recovery else old_modes, 'target_modes': modes,
            'runtime_recovery': recovery,
            'old_workers': self.state['workers'], 'new_workers': {},
            'started_at': now, 'deadline': now + 90})
        self._save(now)  # The target and previous identity are immutable after this write.
        try:
            self.backend.stop(self.state['workers'])
        except MaintenanceBlocked:
            # request_stop is atomic: no owner received a stop request on this first rejection.
            self.state.update(phase='waiting', journal=None, error_code='stop_waiting_for_session')
            if recovery:
                self.state['runtime_recovery'] = recovery
            self._save(now)
            self.gate.reopen()

    def _request(self, settings, now):
        if settings.get('request_id', '') == self.state['request_id']:
            return
        request = settings.get('request') or {}
        kind = request.get('kind')
        if kind == 'retry':
            self.state.update(blocked_sha=None, blocked_modes=None, error_code=None, next_poll=0)
            if self.state.get('failed_requested_sha'):
                self.state['requested_sha'] = self.state.pop('failed_requested_sha')
            if self.state['phase'] == 'blocked' and self.state['journal']:
                self.recovering = True
        elif kind == 'rollback':
            target = request.get('sha')
            if target not in self.state['history']:
                raise DeploymentError('rollback_not_retained_success')
            self.state.update(requested_sha=target, next_poll=0)
            self.state.pop('runtime_recovery', None)
        if kind in {'retry', 'rollback'} and self.state['phase'] == 'announcing':
            self._cancel_announcement(now)
        self.state['request_id'] = settings.get('request_id', '')
        self._save(now)

    def _cancel_announcement(self, now):
        self.gate.reopen()
        self.state.update(phase='idle', candidate_sha=None, next_poll=0)
        self._save(now)

    def _preparation_failed(self, sha, modes, explicit, code, now):
        self.state.update(blocked_sha=sha, blocked_modes=modes, candidate_sha=None,
                          error_code=code, phase='idle', failed_requested_sha=explicit)
        self.state.pop('requested_sha', None)
        self._save(now)
        self._event('failure', now)

    def tick(self, *, now=None):
        now = time.time() if now is None else now
        settings = json.loads((self.control / 'host.json').read_text(encoding='utf-8'))
        if any(type(settings.get(key)) is not bool for key in (*MODES, 'paused')):
            raise DeploymentError('invalid_host_settings')
        self.state['paused'] = settings['paused']
        if self.state['phase'] == 'installation_pending':
            raise DeploymentError('installation_probe_incomplete')
        # In-flight targets never change, including in response to a newer head or local request.
        if self.recovering:
            self.recovering = False
            self._rollback(now, 'controller_interrupted_switch')
        if self.state['journal']:
            if self.state['phase'] == 'blocked':
                self._request(settings, now)
                if not self.recovering:
                    return
                self.recovering = False
                self._rollback(now, 'operator_retry_recovery')
            self._switch(now)
            return
        self._request(settings, now)
        modes = {key: bool(settings[key]) for key in MODES}
        if modes['process_enabled'] and not modes['scheduler_enabled']:
            raise DeploymentError('invalid_persisted_modes')
        if (self.state['phase'] == 'announcing'
                and self.state.get('announced_settings') != modes):
            self._cancel_announcement(now)
        if self.state['phase'] == 'booting':
            if self.gate.status(now=now)['phase'] != 'quiesced':
                raise DeploymentError('bootstrap_gate_not_closed')
            self._begin(self.state['current_sha'], modes, now)
            return
        if self.gate.status(now=now)['phase'] == 'quiesced':
            if self.backend.ready(self.state['current_sha'], self.state['workers'],
                                  {key: self.state[key] for key in MODES}):
                self.gate.reopen()
            else:
                self._begin(self.state['current_sha'], {key: self.state[key] for key in MODES}, now)
            return
        explicit = self.state.get('requested_sha')
        mode_change = any(self.state[key] != modes[key] for key in MODES)
        if self.state['paused'] and explicit and self.state['error_code'] == 'running_worker_exited':
            modes = {key: self.state[key] for key in MODES}
        if (not explicit and self.state['phase'] != 'announcing'
                and hasattr(self.backend, 'running')):
            running = self.backend.running(self.state['workers'])
            if running is False:
                history = self.state['history']
                previous = self.state['current_sha']
                if not self.reconciling_startup and not self.state['paused']:
                    previous = next((sha for sha in reversed(history) if sha not in
                        {self.state['current_sha'], self.state['blocked_sha']}), previous)
                if previous != self.state['current_sha']:
                    self.state['runtime_recovery'] = {
                        'failed_sha': self.state['current_sha'],
                        'failed_modes': {key: self.state[key] for key in MODES}}
                self.state['requested_sha'] = explicit = previous
                self.state['error_code'] = 'running_worker_exited'
                if self.state['paused'] or self.reconciling_startup:
                    modes = {key: self.state[key] for key in MODES}
            elif running is None:
                self.state.update(error_code='running_worker_unknown', phase='waiting')
                self._waiting(now)
                self._save(now)
                return
            self.reconciling_startup = False
        if self.state['paused'] and not explicit:
            if self.gate.status(now=now)['phase'] == 'announcing':
                self.gate.reopen()
            self.state.update(phase='paused', candidate_sha=None)
            self._save(now)
            return
        if (mode_change and not explicit and self.state.get('blocked_modes') == modes
                and self.state['blocked_sha'] == self.state['current_sha']):
            # Keep polling for a new revision, but do not retry this failed mode on the same code.
            mode_change = False
        if self.state['phase'] == 'announcing':
            gate = self.gate.status(now=now)
            if gate['phase'] != 'announcing':
                self.state.update(phase='waiting', next_poll=now + 60)
                self._save(now)
                return
            if now < gate['not_before']:
                return
            sha = self.state['candidate_sha']
            if self.state.get('announcement_source') == 'automatic':
                try:
                    current_head = self.remote.head()
                except Exception:
                    self.gate.reopen()
                    self.state.update(phase='waiting', next_poll=now + 60, error_code='github_head_recheck_failed')
                    self._save(now)
                    return
                if current_head != sha:
                    self.gate.reopen()
                    self.state.update(phase='idle', candidate_sha=None, next_poll=now)
                    self._save(now)
                    return
            if self.gate.try_quiesce(now=now):
                self.state.pop('requested_sha', None)
                self._begin(sha, self.state['announced_modes'], now)
            else:
                self.gate.reopen()
                self.state.update(phase='waiting', next_poll=now + 60)
                self._waiting(now)
                self._save(now)
            return
        if now < self.state['next_poll'] and not explicit and not mode_change:
            self._waiting(now) if self.state['phase'] == 'waiting' else None
            return
        self.state['next_poll'] = now + 60
        candidate = None
        try:
            if explicit or mode_change:
                candidate = self.backend.manifest(explicit or self.state['current_sha'])
            else:
                candidate = self.remote.candidate()
            self.state['poll_failures'] = 0
        except Exception as exc:
            if explicit or mode_change:
                self._preparation_failed(explicit or self.state['current_sha'], modes, explicit,
                                         'candidate_manifest_invalid', now)
                return
            self.state['poll_failures'] += 1
            self.state['error_code'] = str(exc) if type(exc).__name__ == 'GitHubError' else 'github_poll_failed'
            self.state['next_poll'] = now + min(900, 60 * 2 ** min(self.state['poll_failures'] - 1, 4))
            self._save(now)
            return
        if candidate is None:
            self._save(now)
            return
        sha = candidate['sha']
        self.state['observed_sha'] = sha
        if not explicit and not mode_change and sha in {self.state['current_sha'], self.state['blocked_sha']}:
            self._save(now)
            return
        if self.state['candidate_sha'] != sha:
            self.state.update(event_id=uuid4().hex, candidate_sha=sha,
                              waiting_since=None, blocked_notified=False)
        try:
            manifest = self.backend.prepare(candidate)
        except Exception as exc:
            code = exc.code if isinstance(exc, DeploymentError) else 'candidate_prepare_failed'
            self._preparation_failed(sha, modes, explicit, code, now)
            return
        if (not explicit and not mode_change and manifest['runtime_id'] ==
                self.backend.manifest(self.state['current_sha'])['runtime_id']):
            self.state.update(phase='idle', candidate_sha=None)
            self._save(now)
            return
        self.state['candidate_sha'] = sha
        gate = self.gate.status(now=now)
        self._waiting(now)
        if gate['deferred_until'] > now or gate['operations'] or gate['blockers']:
            self.state['phase'] = 'waiting'
            self._save(now)
            return
        if not explicit and not mode_change:
            try:
                if self.remote.head() != sha:
                    self.state.update(phase='idle', candidate_sha=None)
                    self._save(now)
                    return
            except Exception:
                self.state.update(phase='waiting', error_code='github_head_recheck_failed')
                self._save(now)
                return
        self.gate.announce(now=now, delay=60)
        self.state.update(phase='announcing', announced_modes=modes,
                          announced_settings={key: settings[key] for key in MODES},
                          announcement_source='local' if explicit or mode_change else 'automatic')
        self._save(now)

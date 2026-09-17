"""Explicit offline Windows rehearsal: real artifact installs, workers, cutover and rollback.

Run manually with Python 3.12.9; never registers a task or contacts a remote service.
All fixture artifacts, installation data, logs and ownership evidence are retained.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.maintenance import Gate
from core.process_identity import worker_alive
from deployment.controller import Controller
from deployment.host import LocalBackend, install, offline_environment, read_json, update_settings, write_json
from deployment.release import REPOSITORY, _selected, build_release, runtime_fingerprint

A, B = 'a' * 40, 'b' * 40
DISABLED_MODES = {'scheduler_enabled': False, 'process_enabled': False}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root):
    return {path.relative_to(root).as_posix(): sha256(path)
            for path in sorted(root.rglob('*')) if path.is_file()}


def log(message):
    print(datetime.now(timezone.utc).isoformat(timespec='seconds') + ' ' + message, flush=True)


def command(args, *, cwd, output, env=None, timeout=300):
    with Path(output).open('wb') as stream:
        result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
    if result.returncode:
        raise AssertionError(f'Command failed ({result.returncode}); inspect {output}')


class FixtureRemote:
    """Only fixture provenance is simulated; archive extraction and every payload byte are real."""
    def __init__(self, artifacts, manifests):
        self.artifacts, self.manifests = artifacts, manifests
        self.sha = A
        self.downloads = []

    def head(self):
        return self.sha

    def candidate(self):
        manifest = self.manifests[self.sha]
        return {key: manifest[key] for key in ('sha', 'runtime_id', 'repository', 'workflow', 'run_id', 'run_attempt')}

    def download(self, candidate, destination):
        source = self.artifacts[candidate['sha']]
        shutil.copyfile(source, destination)
        assert sha256(source) == sha256(destination)
        self.downloads.append({'sha': candidate['sha'], 'artifact_sha256': sha256(source)})


class RehearsalBackend(LocalBackend):
    def __init__(self, root, remote, launches, health_faults):
        super().__init__(root, remote)
        self.launches, self.health_faults = launches, health_faults

    def prepare(self, candidate):
        try:
            log('Preparing retained/downloaded candidate ' + candidate['sha'][:8])
            return super().prepare(candidate)
        except Exception:
            path = self.root.parent / ('prepare-failure-' + candidate['sha'][:8] + '.log')
            path.write_text(traceback.format_exc(), encoding='utf-8', newline='')
            log('Candidate preparation failed; original exception retained at ' + str(path))
            raise

    def _spawn(self, release, role, modes, row, **kwargs):
        assert role == 'web' and not modes.get('process_enabled'), 'Rehearsal must not launch business schedulers'
        result = super()._spawn(release, role, modes, row, **kwargs)
        self.launches.append({'row': row, 'control': str(kwargs.get('control') or self.control)})
        return result

    def _health(self, port):
        health = super()._health(port)
        main_port = read_json(self.control / 'host.json')['web_port']
        if port == main_port and health.get('sha') in self.health_faults:
            return dict(health, deployment_ready=False)
        return health


def seed_sentinels(shared):
    from core import paid_requests, review, translated
    from core.monitor_access import AccessController
    from publish.journal import PublishAttempt, load as load_published

    state, account = shared / 'state', shared / 'archive/fa_rehearsal'
    account.mkdir()
    timestamp = '2026-09-16T00:00:00Z'
    post_id = 'offline-rehearsal-only'
    source_hash = hashlib.sha256(b'OFFLINE REHEARSAL SOURCE').hexdigest()
    attempt = PublishAttempt(post_id=post_id, platform='facebook', status='failed_pre_submit',
        scheduled_at=timestamp, recorded_at=timestamp, text_de_sha256=source_hash,
        target_channels=('facebook',), note='Synthetic offline fixture; no submission occurred')
    (state / 'published.jsonl').write_text(attempt.to_json() + '\n', encoding='utf-8', newline='')
    (state / 'paid_requests.jsonl').write_text(json.dumps({'event': 'accepted',
        'request_id': 'offline-fixture-no-provider-call', 'job_key': 'offline-fixture',
        'recorded_at': timestamp, 'cost_usd': 0, 'note': 'Synthetic fixture; no paid request'}) + '\n', encoding='utf-8', newline='')
    (account / 'review_items.jsonl').write_text(json.dumps({'post_id': post_id, 'account': account.name,
        'platform': 'facebook', 'status': 'skipped', 'action': 'skipped', 'reason': 'Offline fixture only',
        'source_text_sha256': source_hash, 'revision': str(uuid4()), 'recorded_at': timestamp}) + '\n', encoding='utf-8', newline='')
    (account / 'translated_human.jsonl').write_text(json.dumps({'post_id': post_id,
        'text_de': 'Nur eine lokale Testvorlage.', 'source_text_sha256': source_hash,
        'revision': str(uuid4()), 'recorded_at': timestamp}) + '\n', encoding='utf-8', newline='')
    (account / 'original-sentinel.bin').write_bytes(b'OFFLINE ORIGINAL\x00\xff\r\nPRESERVE EXACTLY')
    write_json(state / 'pipeline_state.json', {'schema_version': 1, 'activated_at': timestamp,
        'activation_evidence': 'offline-rehearsal-fixture-not-live-acceptance'})
    write_json(state / 'operator_preferences.json', {'default_times': ['09:37', '18:23'], 'snooze_default_days': 7})
    access = AccessController(state)
    access.initialize('Offline rehearsal fixture; scheduler stays disabled')
    assert len(load_published(state)) == len(paid_requests.load_events(state)) == 1
    assert len(review.history(account)) == len(translated.load_human_translated(account / 'translated_human.jsonl')) == 1
    access.status()
    return inventory(shared)


def build_artifacts(out, wheelhouse, report):
    import _winapi

    baseline = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in _selected(ROOT, frontend_inputs=True)}
    report['source_runtime_at_copy'] = runtime_fingerprint(ROOT)
    report['source_files'] = {name: hashlib.sha256(data).hexdigest() for name, data in baseline.items()}
    artifacts, manifests = {}, {}
    for label, sha in (('a', A), ('b', B)):
        source = out / ('source-' + label)
        for name, data in baseline.items():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if label == 'b':
            with (source / 'config.toml').open('ab') as stream:
                stream.write(b'\n# Offline rehearsal variant B; no operational setting changes.\n')
        runtime = runtime_fingerprint(source)
        # Reuse installed build tools through a junction excluded from the release allowlist.
        # The copied UI sources and its own Vite config determine all emitted bytes.
        _winapi.CreateJunction(str(ROOT / 'web/ui/node_modules'), str(source / 'web/ui/node_modules'))
        env = dict(os.environ, VITE_FBSCRAPER_RUNTIME_ID=runtime)
        log(f'Building copied UI variant {label} ({runtime})')
        command([shutil.which('node'), ROOT / 'web/ui/node_modules/vite/bin/vite.js', 'build'],
                cwd=source / 'web/ui', output=out / f'build-{label}.log', env=env)
        assert json.loads((source / 'web/ui/dist/runtime.json').read_text())['runtime_id'] == runtime
        payload = out / ('payload-' + label)
        manifest = build_release(source, payload, sha=sha, repository=REPOSITORY,
                                 run_id=1 if label == 'a' else 2, wheelhouse=wheelhouse)
        archive = out / ('artifact-' + label + '.zip')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zipped:
            for path in sorted(payload.rglob('*')):
                if path.is_file():
                    zipped.write(path, path.relative_to(payload).as_posix())
        artifacts[sha], manifests[sha] = archive, manifest
    assert manifests[A]['runtime_id'] != manifests[B]['runtime_id']
    assert manifests[A]['controller_id'] == manifests[B]['controller_id']
    report['artifacts'] = {sha: {'fixture_sha': sha, 'runtime_id': manifests[sha]['runtime_id'],
        'controller_id': manifests[sha]['controller_id'], 'zip': str(path), 'zip_sha256': sha256(path)}
        for sha, path in artifacts.items()}
    return artifacts, manifests


def cooperate_cleanup(root, launches):
    if not (root / 'control/maintenance.json').exists():
        return {'confirmed': True, 'workers': [], 'note': 'No initialized instance'}
    groups = {}
    for entry in launches:
        row = entry['row']
        groups.setdefault(entry['control'], {})[row['launch_id']] = row
    state_path = root / 'control/deployment.json'
    if state_path.exists():
        state = read_json(state_path)
        sources = [state.get('workers', {})]
        sources += [(state.get('journal') or {}).get(key, {}) for key in ('old_workers', 'new_workers', 'restored_workers')]
        for rows in sources:
            for row in rows.values():
                groups.setdefault(str(root / 'control'), {})[row['launch_id']] = row
    for path in (root / 'control').glob('probe-*/control/probe.json'):
        row = read_json(path)
        groups.setdefault(str(path.parent), {})[row['launch_id']] = row
    confirmed = []
    for directory, rows in groups.items():
        gate = Gate(Path(directory))
        if gate.status()['phase'] != 'quiesced':
            gate.announce(delay=0)
            assert gate.try_quiesce(), 'Cleanup admission remained busy; retaining all ownership files'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            metadata = gate.status()['workers']
            alive = []
            for row in rows.values():
                observed = metadata.get(row['role'], {})
                if all(observed.get(key) == row.get(key) for key in ('sha', 'runtime_id', 'instance_id', 'launch_id', 'role')):
                    row['worker'] = observed.get('worker')
                    row['launcher'] = row.get('launcher') or observed.get('launcher')
                launcher, worker = row.get('launcher'), row.get('worker')
                if not launcher:
                    raise AssertionError('Unknown launcher retained for manual inspection: ' + directory)
                if worker_alive(launcher) is not False or worker and worker_alive(worker) is not False:
                    alive.append(row)
            if not alive:
                confirmed.extend(copy.deepcopy(list(rows.values())))
                break
            gate.request_stop([row['worker'] for row in alive if row.get('worker')])
            time.sleep(.2)
        else:
            raise AssertionError('Cooperative exit unconfirmed; no force termination or deletion: ' + directory)
    return {'confirmed': True, 'workers': confirmed, 'all_artifacts_retained': True}


def rehearse(out, wheelhouse, report, *, lan=False):
    artifacts, manifests = build_artifacts(out, wheelhouse, report)
    remote = FixtureRemote(artifacts, manifests)
    root, launches, faults = out / 'instance', [], set()
    report['installation_root'] = str(root)
    try:
        def environment(path):
            started = time.monotonic()
            log('Installing offline environment at ' + str(path))
            offline_environment(path)
            report.setdefault('offline_environments', []).append({'path': str(path), 'seconds': time.monotonic() - started})

        log('Installing actual offline A release/controller environments and running actual preflight')
        started = time.monotonic()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        network = {'web_port': port, 'public_base_url': f'http://127.0.0.1:{port}'}
        if lan:
            # Documentation-only subnet cannot admit ordinary office clients; data and credentials are fixtures.
            network.update(web_host='0.0.0.0', public_base_url=f'http://review-lan.test:{port}',
                           allowed_client_cidrs=['192.0.2.0/24'])
        report['install'] = install(root, artifacts[A], task_query=lambda _: False, environment=environment, **network)
        report['install_seconds'] = time.monotonic() - started
        report['network_before'] = {key: read_json(root / 'control/host.json')[key] for key in (
            'web_host', 'web_port', 'public_base_url', 'allowed_client_cidrs')}
        before = seed_sentinels(root / 'shared')
        report['sentinel_before'] = before
        backend = RehearsalBackend(root, remote, launches, faults)
        events = []
        notify = lambda kind, payload: events.append({'kind': kind, **payload})
        controller = Controller(root, backend, remote, notify=notify)
        offset = 0.0

        def now():
            return time.time() + offset

        def tick():
            controller.tick(now=now())
            state = controller.state
            gate = controller.gate.status(now=now())
            assert all(state[key] is False for key in DISABLED_MODES)
            if state['journal']:
                assert gate['phase'] == 'quiesced'
            trace = {'wall_at': time.time(), 'logical_at': now(), 'phase': state['phase'],
                     'sha': state['current_sha'], 'candidate': state['candidate_sha'],
                     'maintenance': gate['phase'], 'error_code': state['error_code']}
            report.setdefault('trace', []).append(trace)
            if len(report['trace']) == 1 or report['trace'][-2]['phase'] != trace['phase']:
                log('Controller: ' + json.dumps(trace))

        def until(predicate, *, limit=100):
            deadline = time.monotonic() + limit
            while time.monotonic() < deadline:
                tick()
                if predicate():
                    return
                if controller.state['phase'] == 'idle' and controller.state['blocked_sha']:
                    raise AssertionError('Candidate blocked: ' + str(controller.state['error_code']))
                time.sleep(.2)
            raise AssertionError('Rehearsal transition timed out: ' + json.dumps(controller.state))

        def verify_open(sha):
            assert controller.state['current_sha'] == sha and controller.state['journal'] is None
            assert controller.gate.status()['phase'] == 'open'
            assert backend.ready(sha, controller.state['workers'], DISABLED_MODES)
            assert inventory(root / 'shared') == before, 'Shared sentinel bytes or file set changed'

        def announce(target, *, retry=False):
            nonlocal offset
            remote.sha = target
            if retry:
                update_settings(root, {'request_id': uuid4().hex, 'request': {'kind': 'retry'}})
            offset += 61
            until(lambda: controller.state['phase'] == 'announcing')
            assert inventory(root / 'shared') == before
            offset += 61

        until(lambda: controller.state['phase'] == 'idle')
        verify_open(A)
        report.setdefault('scenarios', []).append({'name': 'initial-bootstrap-A', 'status': 'passed'})

        announce(B)
        switched = time.monotonic()
        until(lambda: controller.state['phase'] == 'idle' and controller.state['current_sha'] == B)
        verify_open(B)
        report['scenarios'].append({'name': 'successful-A-to-B', 'status': 'passed',
                                    'real_cutover_seconds': time.monotonic() - switched})

        announce(A)
        faults.add(A)  # Probe completed for A; only the installed main-port readiness is overridden.
        switched = time.monotonic()
        until(lambda: controller.state['phase'] == 'verifying')
        # Verify the actual owned candidate became healthy before injecting the logical timeout.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                health = LocalBackend._health(backend, port)
                if health.get('sha') == A and health.get('deployment_ready'):
                    break
            except OSError:
                pass
            time.sleep(.2)
        else:
            raise AssertionError('Candidate did not actually start before injected readiness failure')
        offset += 91
        until(lambda: controller.state['phase'] == 'idle' and controller.state['current_sha'] == B)
        faults.clear()
        verify_open(B)
        assert controller.state['blocked_sha'] == A and controller.state['error_code'] == 'candidate_health_failed'
        report['scenarios'].append({'name': 'candidate-health-failure-safe-rollback', 'status': 'passed',
                                    'real_switch_and_recovery_seconds': time.monotonic() - switched,
                                    'injection': 'main-port health override after actual isolated probe; logical deadline advanced'})

        announce(A, retry=True)
        switched = time.monotonic()
        until(lambda: controller.state['phase'] == 'verifying')
        previous_launch = controller.state['journal']['new_workers']['web']['launch_id']
        update_settings(root, {'paused': True})
        # Discard in-memory controller/backend ownership; recover only the persisted journal and OS identity.
        backend = RehearsalBackend(root, remote, launches, faults)
        controller = Controller(root, backend, remote, notify=notify)
        until(lambda: controller.state['phase'] == 'idle' and controller.state['current_sha'] == B)
        verify_open(B)
        assert controller.state['error_code'] == 'controller_interrupted_switch'
        assert controller.state['workers']['web']['launch_id'] != previous_launch
        tick()
        assert controller.state['phase'] == 'paused' and read_json(root / 'control/host.json')['paused']
        report['scenarios'].append({'name': 'interrupted-verifying-restores-B-and-paused-mode', 'status': 'passed',
                                    'real_switch_and_recovery_seconds': time.monotonic() - switched,
                                    'injection': 'Controller and LocalBackend reinstantiation; no owned process killed'})
        report['events'] = events
        report['fixture_downloads'] = remote.downloads
        report['sentinel_after'] = inventory(root / 'shared')
        report['final_state_before_cleanup'] = copy.deepcopy(controller.state)
        for label, directory in (('controller', root / 'controller'), ('release-a', root / 'releases' / A), ('release-b', root / 'releases' / B)):
            command([directory / '.venv/Scripts/python.exe', '-c',
                'import sys, deployment.controller, fastapi, playwright, openai; from pathlib import Path; '
                'assert Path(sys.prefix).resolve() == Path(".venv").resolve(); '
                'assert sys.prefix != sys.base_prefix; '
                'print(sys.version); print(sys.prefix); print(deployment.controller.__file__)'],
                cwd=directory, output=out / (label + '-environment.log'))
            command([directory / '.venv/Scripts/python.exe', '-m', 'pip', 'check'],
                cwd=directory, output=out / (label + '-pip-check.log'))
        report['status'] = 'offline_pass'
    finally:
        log('Cooperatively stopping exact owned workers; retaining all artifacts and logs')
        report['cleanup'] = cooperate_cleanup(root, launches)
        if 'sentinel_before' in report:
            report['sentinel_after'] = inventory(root / 'shared')
            assert report['sentinel_after'] == report['sentinel_before'], 'Shared bytes changed during rehearsal or cleanup'
        if 'network_before' in report:
            report['network_after'] = {key: read_json(root / 'control/host.json')[key] for key in report['network_before']}
            assert report['network_after'] == report['network_before'], 'Network settings changed during cutover/rollback'
        assert all(row['role'] == 'web' for row in report['cleanup']['workers'])
        traffic = []
        for path in (root / 'logs').glob('*.log'):
            for method, target in re.findall(r'"(GET|POST|PUT|PATCH|DELETE|HEAD) ([^ ]+) HTTP/', path.read_text(encoding='utf-8')):
                traffic.append({'log': path.name, 'method': method, 'target': target})
                assert (method, target) == ('GET', '/api/health'), 'Unexpected business HTTP request'
        report['worker_http_requests'] = traffic
        if (root / 'shared/.env').exists():
            assert (root / 'shared/.env').read_bytes() == b''
            assert (root / 'control/github.token').read_bytes() == b''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, default=ROOT / 'state/release-wheelhouse')
    parser.add_argument('--out', type=Path, default=ROOT / 'state/deployment-implementation' / ('wr-' + uuid4().hex[:8]))
    parser.add_argument('--lan', action='store_true', help='Exercise wildcard worker binding with an isolated documentation subnet')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:3] != (3, 12, 9):
        parser.error('Use 64-bit Windows Python 3.12.9')
    out = args.out.resolve()
    if out.exists():
        parser.error('--out must name a NEW directory; evidence is never replaced')
    out.mkdir(parents=True)
    # Never pass developer credentials or inherited application bindings to installed workers.
    essentials = {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'COMSPEC', 'PATHEXT',
                  'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES'}
    clean = {key: value for key, value in os.environ.items() if key.upper() in essentials}
    os.environ.clear()
    os.environ.update(clean, PYTHONIOENCODING='utf-8')
    report = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'evidence': str(out), 'fixture_commit_shas': [A, B], 'real_github': False,
              'real_business_calls': False, 'task_registration': False, 'notifications': 'captured locally only',
              'scope': 'Actual offline final-path venv installs, real LocalBackend workers, production Controller ticks; fixture remote and accelerated logical deadlines',
              'wheelhouse': {path.name: sha256(path) for path in sorted(args.wheelhouse.resolve().glob('*.whl'))}}
    code = 0
    try:
        rehearse(out, args.wheelhouse.resolve(), report, lan=args.lan)
    except Exception:
        code = 1
        report['status'] = 'failed'
        report['failure'] = traceback.format_exc()
        log(report['failure'])
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['source_runtime_at_finish'] = runtime_fingerprint(ROOT)
        report['source_drift'] = report.get('source_runtime_at_copy') != report['source_runtime_at_finish']
        write_json(out / 'report.json', report)
        log('Report: ' + str(out / 'report.json'))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

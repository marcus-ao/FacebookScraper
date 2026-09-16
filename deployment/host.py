"""Local installation, immutable environments and cooperative process ownership."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4
from xml.sax.saxutils import escape

from core.maintenance import Gate
from core.paid_model import FileLock, atomic_write_json
from core.process_identity import process_identity, worker_alive
from deployment.release import REPOSITORY, extract_release, verify_release
from deployment.errors import DeploymentError

TASK_NAME = 'FBScraperService'
LEGACY_TASKS = ('FBScraperScheduler', 'FBScraperDelta', 'FBScraperDeltaCatchup')


def write_json(path: Path, value):
    atomic_write_json(Path(path), value)


def read_json(path: Path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise DeploymentError('invalid_control_state')
    return value


def validate_root(root: Path):
    root = Path(root).resolve()
    marker = read_json(root / 'control/instance.json')
    if (set(marker) != {'version', 'instance_id', 'shared'} or marker['version'] != 1
            or not re.fullmatch(r'[0-9a-f]{32}|[0-9a-f-]{36}', str(marker['instance_id']))
            or marker['shared'] != str((root / 'shared').resolve())
            or read_json(root / 'shared/instance.json') != marker):
        raise DeploymentError('instance_binding_mismatch')
    for part in ('control', 'shared', 'releases', 'controller'):
        path = root / part
        if path.exists() and (path.is_symlink() or path.resolve().parent != root
                             or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise DeploymentError('instance_binding_link')
    return marker


def _quiet_run(command, **kwargs):
    try:
        result = subprocess.run(command, capture_output=True, text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        raise DeploymentError('host_command_failed') from None
    if result.returncode:
        raise DeploymentError('host_command_failed')
    return result.stdout


def query_task(name):
    if os.name != 'nt':
        raise DeploymentError('windows_required')
    # COM distinguishes a missing task from a query failure without localized text parsing.
    script = ("$ErrorActionPreference='Stop'; $s=New-Object -ComObject 'Schedule.Service'; "
              "$s.Connect(); $f=$s.GetFolder('\\'); "
              f"try {{$t=$f.GetTask('{name}'); if($t.Enabled){{'enabled'}}else{{'disabled'}}}} "
              "catch {if($_.Exception.HResult -eq -2147024894){'missing'}else{throw}}")
    answer = _quiet_run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], timeout=30).strip()
    if answer not in {'enabled', 'disabled', 'missing'}:
        return None
    return answer == 'enabled'


def check_legacy_tasks(task_query=query_task):
    for name in LEGACY_TASKS:
        enabled = task_query(name)
        if enabled is None:
            raise DeploymentError('task_query_unknown')
        if enabled:
            raise DeploymentError('legacy_scheduler_enabled:' + name)


def task_xml(root: Path, user_sid: str):
    root = Path(root).resolve()
    python = root / 'controller/.venv/Scripts/python.exe'
    args = subprocess.list2cmdline(['-m', 'deployment', 'supervise', '--root', str(root)])
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>FBScraper cooperative deployment controller</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user_sid)}</UserId></LogonTrigger>
    <BootTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></BootTrigger></Triggers>
  <Principals><Principal id="Owner"><UserId>{escape(user_sid)}</UserId>
    <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled><ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings>
  <Actions Context="Owner"><Exec><Command>{escape(str(python))}</Command>
    <Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(str(root / 'controller'))}</WorkingDirectory>
  </Exec></Actions>
</Task>'''


def install_task(root: Path, *, dry_run=False):
    root = Path(root).resolve()
    validate_root(root)
    check_legacy_tasks()
    sid = _quiet_run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                     '[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value'], timeout=30).strip()
    if not re.fullmatch(r'S-1-[0-9-]+', sid):
        raise DeploymentError('task_owner_unknown')
    xml = task_xml(root, sid)
    if dry_run:
        return xml
    target = root / 'control/service-task.xml'
    target.write_text(xml, encoding='utf-16', newline='')
    _quiet_run(['schtasks.exe', '/Create', '/TN', TASK_NAME, '/XML', str(target)], timeout=30)
    return {'task': TASK_NAME, 'installed': True}


def bind_config(path: Path, release: Path, shared: Path):
    def quoted(value):
        return json.dumps(str(value.resolve()).replace('\\', '/'))
    text = ('[paths]\narchive = ' + quoted(shared / 'archive') + '\nstate = ' + quoted(shared / 'state')
            + '\n\n[runtime]\npython = ' + quoted(release / '.venv/Scripts/python.exe')
            + '\nenv_file = ' + quoted(shared / '.env') + '\n')
    path.write_text(text, encoding='utf-8', newline='')


def offline_environment(release: Path):
    """Always create at the final path; Windows virtual environments are not portable."""
    manifest = verify_release(release)
    if tuple(sys.version_info[:3]) != tuple(map(int, manifest['python'].split('.'))):
        raise DeploymentError('python_version_mismatch')
    if os.name != 'nt' or sys.maxsize <= 2 ** 32:
        raise DeploymentError('windows_amd64_required')
    python = release / '.venv/Scripts/python.exe'
    if not python.exists():
        _quiet_run([sys.executable, '-m', 'venv', str(release / '.venv')], timeout=120)
    _quiet_run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
        '--no-index', '--only-binary=:all:', '--require-hashes', '--find-links', str(release / 'wheelhouse'),
        '-r', str(release / 'requirements.lock')], timeout=300,
        env=dict(os.environ, PIP_NO_INDEX='1', PIP_CONFIG_FILE=os.devnull))
    _quiet_run([str(python), '-m', 'pip', 'check'], timeout=60)


def _copy_verified(source: Path, destination: Path, *, expected_sha=None):
    if source.is_file():
        return extract_release(source, destination, expected_sha=expected_sha)
    manifest = verify_release(source, expected_sha=expected_sha)
    destination.mkdir(parents=True, exist_ok=False)
    for row in manifest['files']:
        target = destination / row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / row['path'], target)
    shutil.copyfile(source / 'release.json', destination / 'release.json')
    return verify_release(destination, expected_sha=manifest['sha'])


def install(root: Path, source: Path, *, task_query=query_task, environment=offline_environment, probe=None):
    root, source = Path(root).resolve(), Path(source).resolve()
    if root.exists() and any(root.iterdir()):
        raise DeploymentError('install_root_not_empty')
    check_legacy_tasks(task_query)
    # Validate before creating any instance path. A zip is inspected in a temporary directory.
    if source.is_dir():
        manifest = verify_release(source)
    else:
        with tempfile.TemporaryDirectory(prefix='fbscraper-release-') as temp:
            manifest = extract_release(source, Path(temp) / 'payload')
    root.mkdir(parents=True, exist_ok=True)
    for directory in ('control', 'shared/archive', 'shared/state', 'releases', 'logs'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    shared = root / 'shared'
    marker = {'version': 1, 'instance_id': str(uuid4()), 'shared': str(shared)}
    write_json(root / 'control/instance.json', marker)
    write_json(shared / 'instance.json', marker)
    (shared / '.env').write_text('', encoding='utf-8')
    (root / 'control/github.token').write_text('', encoding='utf-8')
    if os.name == 'nt':
        owner = _quiet_run(['whoami.exe'], timeout=15).strip()
        _quiet_run(['icacls.exe', str(root / 'control/github.token'), '/inheritance:r',
                    '/grant:r', owner + ':F'], timeout=15)
    release = root / 'releases' / manifest['sha']
    _copy_verified(source, release, expected_sha=manifest['sha'])
    environment(release)
    _copy_verified(source, root / 'controller', expected_sha=manifest['sha'])
    environment(root / 'controller')
    bind_config(release / 'config.local.toml', release, shared)
    gate = Gate(root / 'control')
    gate.initialize()
    gate.announce(delay=0)
    if not gate.try_quiesce():
        raise DeploymentError('initial_maintenance_failed')
    config = dict(marker, root=str(root), repository=REPOSITORY, web_port=8765,
                  paused=False, scheduler_enabled=False, process_enabled=False,
                  request=None, request_id='')
    write_json(root / 'control/host.json', config)
    # 延迟导入：只有安装完成绑定后才装配初始控制器状态。
    from deployment.controller import initial_state
    state = initial_state(manifest['sha'])
    state['phase'] = 'installation_pending'
    write_json(root / 'control/deployment.json', state)
    backend = LocalBackend(root)
    (probe or backend.probe)(release, manifest)
    state['phase'] = 'booting'
    write_json(root / 'control/deployment.json', state)
    return {'root': str(root), 'sha': manifest['sha'], 'instance_id': marker['instance_id'],
            'scheduler_enabled': False, 'process_enabled': False}


def worker_environment(control: Path, config: Path, launch_id: str, *, isolated=False):
    # Probe processes inherit only OS essentials, never model/bot/browser credentials.
    if isolated:
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'COMSPEC', 'PATHEXT',
                                  'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES'}}
    else:
        env = dict(os.environ)
    env.update(FBSCRAPER_CONTROL_DIR=str(control), FBSCRAPER_RUNTIME_CONFIG=str(config),
               FBSCRAPER_CONTROLLER_PID=str(os.getpid()),
               FBSCRAPER_LAUNCH_ID=launch_id, PYTHONIOENCODING='utf-8')
    return env


class LocalBackend:
    def __init__(self, root: Path, remote=None):
        self.root = Path(root).resolve()
        self.control = self.root / 'control'
        self.gate = Gate(self.control)
        self.marker = validate_root(self.root)
        self.remote = remote
        self.processes = {}

    def manifest(self, sha):
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise DeploymentError('invalid_release_sha')
        return verify_release(self.root / 'releases' / sha, expected_sha=sha)

    def prepare(self, candidate):
        sha = candidate['sha']
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise DeploymentError('invalid_release_sha')
        release = self.root / 'releases' / sha
        if release.exists():
            manifest = self.manifest(sha)
        else:
            if self.remote is None:
                raise DeploymentError('release_not_retained')
            with tempfile.TemporaryDirectory(prefix='download-', dir=self.control) as temp:
                archive = Path(temp) / 'artifact.zip'
                self.remote.download(candidate, archive)
                manifest = extract_release(archive, release, expected_sha=sha)
        for key in ('repository', 'workflow', 'run_id', 'run_attempt'):
            if key in candidate and candidate[key] != manifest.get(key):
                raise DeploymentError('artifact_manifest_identity')
        controller = read_json(self.root / 'controller/release.json')
        if (not manifest.get('controller_id')
                or manifest['controller_id'] != controller.get('controller_id')):
            raise DeploymentError('controller_upgrade_required')
        prepared_path = self.control / ('prepared-' + sha + '.json')
        prepared = read_json(prepared_path) if prepared_path.exists() else None
        if prepared != {'sha': sha, 'runtime_id': manifest['runtime_id']}:
            offline_environment(release)
            self.probe(release, manifest)
            write_json(prepared_path, {'sha': sha, 'runtime_id': manifest['runtime_id']})
        bind_config(release / 'config.local.toml', release, self.root / 'shared')
        return manifest

    def _port_free(self, port):
        with socket.socket() as sock:
            if os.name == 'nt':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                return False
        return True

    def _spawn(self, release, role, modes, row, *, control=None, config=None, isolated=False):
        command = [str(release / '.venv/Scripts/python.exe'), '-m', 'deployment.worker', '--role', role]
        if role == 'web':
            command += ['--port', str(row['port'])]
        elif modes['process_enabled']:
            command += ['--process']
        log = self.root / 'logs' / (row['launch_id'] + '.log')
        with log.open('ab') as stream:
            process = subprocess.Popen(command, cwd=release, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT,
                env=worker_environment(control or self.control, config or release / 'config.local.toml',
                                       row['launch_id'], isolated=isolated),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.processes[row['launch_id']] = process
        row['launcher'] = process_identity(process.pid)
        return row

    def _refresh(self, workers, gate=None):
        metadata = (gate or self.gate).status()['workers']
        for row in workers.values():
            observed = metadata.get(row['role'], {})
            if all(observed.get(key) == row.get(key) for key in
                   ('launch_id', 'sha', 'runtime_id', 'instance_id', 'role')):
                row['worker'] = observed.get('worker')
                row['seen_at'] = observed.get('seen_at')
                if not row.get('launcher') and observed.get('launcher'):
                    row['launcher'] = observed['launcher']
                    row['uncertain_launch'] = False
            elif row.get('spawn_pending') and not row.get('launcher'):
                # A crash between durable launch intent and Popen result is ambiguous.
                row['uncertain_launch'] = True
        return workers

    def start(self, sha, modes, persist):
        manifest = self.manifest(sha)
        if self.gate.status()['phase'] != 'quiesced':
            raise DeploymentError('start_requires_maintenance')
        port = read_json(self.control / 'host.json')['web_port']
        if not self._port_free(port):
            raise DeploymentError('web_port_owned')
        rows = {}
        for role in ('web', 'scheduler') if modes['scheduler_enabled'] else ('web',):
            row = {'role': role, 'sha': sha, 'runtime_id': manifest['runtime_id'],
                   'instance_id': self.marker['instance_id'], 'launch_id': uuid4().hex,
                   'port': port, 'spawn_pending': True, 'launcher': None, 'worker': None}
            rows[role] = row
            persist(rows)  # Launch intent is durable before the child can exist.
            self._spawn(self.root / 'releases' / sha, role, modes, row)
            row['spawn_pending'] = False
            persist(rows)
        return rows

    def stop(self, workers):
        self._refresh(workers)
        owners = [row['worker'] for row in workers.values() if row.get('worker')]
        self.gate.request_stop(owners)

    def exited(self, workers):
        self._refresh(workers)
        for row in workers.values():
            process = self.processes.get(row['launch_id'])
            if process is not None:
                if process.poll() is not None:
                    row['launcher_exited'] = True
            launcher = row.get('launcher')
            owner = row.get('worker')
            if row.get('uncertain_launch') or not launcher:
                return None
            if not row.get('launcher_exited') and worker_alive(launcher) is not False:
                return False
            if owner is None:
                # The known Windows venv launcher waits for its owned interpreter before exiting.
                continue
            if worker_alive(owner) is not False:
                return False
        return True

    def running(self, workers):
        self._refresh(workers)
        if not workers:
            return False
        states = [worker_alive(row.get('worker')) for row in workers.values()]
        if None in states:
            return None
        return all(states)

    def _health(self, port):
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return json.load(response)

    def ready(self, sha, workers, modes):
        self._refresh(workers)
        required = {'web', 'scheduler'} if modes['scheduler_enabled'] else {'web'}
        if set(workers) != required:
            return False
        for role, row in workers.items():
            if (row.get('sha') != sha or not row.get('worker')
                    or worker_alive(row['worker']) is not True
                    or time.time() - row.get('seen_at', 0) > 10):
                return False
        try:
            health = self._health(workers['web']['port'])
        except (OSError, ValueError):
            return False
        web = workers['web']
        return (health.get('managed') is True and health.get('deployment_ready') is True
                and all(health.get(key) == web.get(key) for key in
                        ('sha', 'runtime_id', 'instance_id', 'worker', 'launch_id', 'role'))
                and health.get('frontend_runtime_id') == web['runtime_id'])

    def probe(self, release: Path, manifest: dict):
        # Keep a failed probe's ownership record for diagnosis; never remove a live child's control files.
        for record in self.control.glob('probe-*/control/probe.json'):
            previous = read_json(record)
            if previous.get('exited'):
                continue
            previous_gate = Gate(record.parent)
            self._refresh({'web': previous}, previous_gate)
            if not previous.get('launcher') or not previous.get('worker'):
                raise DeploymentError('probe_cleanup_unknown')
            previous_gate.request_stop([previous['worker']])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (worker_alive(previous['launcher']) is False
                        and worker_alive(previous['worker']) is False):
                    write_json(record, dict(previous, exited=True))
                    break
                time.sleep(.2)
            else:
                raise DeploymentError('probe_cleanup_unknown')
        probe_root = Path(tempfile.mkdtemp(prefix='probe-', dir=self.control))
        control, shared = probe_root / 'control', probe_root / 'shared'
        control.mkdir()
        (shared / 'state').mkdir(parents=True)
        (shared / 'archive').mkdir()
        (shared / '.env').write_text('', encoding='utf-8')
        marker = {'version': 1, 'instance_id': str(uuid4()), 'shared': str(shared.resolve())}
        write_json(control / 'instance.json', marker)
        write_json(shared / 'instance.json', marker)
        config = probe_root / 'config.local.toml'
        bind_config(config, release, shared)
        gate = Gate(control)
        gate.initialize()
        gate.announce(delay=0)
        if not gate.try_quiesce():
            raise DeploymentError('probe_maintenance')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        row = {'role': 'web', 'sha': manifest['sha'], 'runtime_id': manifest['runtime_id'],
               'instance_id': marker['instance_id'], 'launch_id': uuid4().hex,
               'port': port, 'worker': None, 'launcher': None}
        write_json(control / 'probe.json', row)
        self._spawn(release, 'web', {'process_enabled': False}, row,
                    control=control, config=config, isolated=True)
        write_json(control / 'probe.json', row)
        ready = False
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            self._refresh({'web': row}, gate)
            try:
                health = self._health(port)
                ready = (health.get('managed') is True and health.get('deployment_ready') is True
                    and all(health.get(key) == row.get(key) for key in
                            ('sha', 'runtime_id', 'instance_id', 'worker', 'launch_id', 'role'))
                    and health.get('frontend_runtime_id') == manifest['runtime_id'])
            except (OSError, ValueError):
                pass
            if ready or self.processes[row['launch_id']].poll() is not None:
                break
            time.sleep(.2)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._refresh({'web': row}, gate)
            if row.get('worker'):
                gate.request_stop([row['worker']])
            process = self.processes[row['launch_id']]
            if process.poll() is not None and (not row.get('worker') or worker_alive(row['worker']) is False):
                break
            time.sleep(.2)
        else:
            raise DeploymentError('probe_cleanup_unknown')
        write_json(control / 'probe.json', dict(row, ready=ready, exited=True))
        if not ready:
            raise DeploymentError('probe_health_failed')


def update_settings(root: Path, updates):
    validate_root(root)
    path = Path(root) / 'control/host.json'
    with FileLock(path.with_suffix('.lock'), busy_message='host_settings_busy'):
        value = read_json(path)
        value.update(updates)
        write_json(path, value)
        return value

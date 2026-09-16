"""Installer/host tests use temporary releases and no installed Windows tasks."""
import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.host import (task_xml, check_legacy_tasks, install, validate_root, LocalBackend,
                             write_json, read_json, worker_environment)
from deployment.release import build_release, runtime_fingerprint
from deployment.notifications import deliver
from core.feishu import FeishuSettings
from core.maintenance import Gate


class HostTests(unittest.TestCase):
    def test_managed_cli_rejects_a_release_selected_before_cutover(self):
        from deployment.cli import _exec_worker
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp)
            gate = Gate(control)
            gate.initialize()
            write_json(control / 'deployment.json', {'current_sha': 'b' * 40})
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(control)), \
                    patch('core.runtime_identity.validate_binding', return_value={}), \
                    patch('core.runtime_identity.read_release', return_value={'sha': 'a' * 40}), \
                    patch('deployment.cli.runpy.run_module') as run:
                with self.assertRaisesRegex(ValueError, 'stale_cli_release'):
                    _exec_worker(['-m', 'pipeline'])
                run.assert_not_called()
            self.assertEqual(gate.status()['operations'], [])

    def test_task_has_interactive_least_privilege_and_hidden_command(self):
        xml = task_xml(Path('C:/Managed Service'), 'S-1-5-21-123')
        for value in ('InteractiveToken', 'LeastPrivilege', 'IgnoreNew',
                      'LogonTrigger', 'BootTrigger', 'RestartOnFailure', 'supervise'):
            self.assertIn(value, xml)
        self.assertNotIn('Password', xml)

    def test_enabled_legacy_task_rejected_disabled_allowed_unknown_fails(self):
        check_legacy_tasks(lambda name: False)
        with self.assertRaisesRegex(ValueError, 'legacy_scheduler'):
            check_legacy_tasks(lambda name: name == 'FBScraperScheduler')
        with self.assertRaisesRegex(ValueError, 'task_query'):
            check_legacy_tasks(lambda name: None)

    def test_nonempty_unknown_install_root_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / 'published.jsonl'
            ledger.write_bytes(b'precious')
            with self.assertRaises(ValueError):
                install(root, root / 'missing-release', task_query=lambda name: False)
            self.assertEqual(ledger.read_bytes(), b'precious')

    def test_binding_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'control').mkdir()
            (root / 'shared').mkdir()
            marker = {'version': 1, 'instance_id': 'a', 'shared': str(root / 'shared')}
            (root / 'control/instance.json').write_text(json.dumps(marker))
            (root / 'shared/instance.json').write_text(json.dumps(dict(marker, instance_id='b')))
            with self.assertRaises(ValueError):
                validate_root(root)

    def test_installer_creates_disabled_isolated_instance_and_final_venv_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'
            for name in ('config.toml', 'requirements.txt', 'requirements.lock', '.env.example',
                         'prompts/a.md', 'web/ui/dist/index.html', 'scripts/run.bat',
                         *[folder + '/__init__.py' for folder in
                           ('core', 'routes', 'localize', 'publish', 'pipeline', 'web/api', 'tools', 'deployment')]):
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('fixture', encoding='utf-8')
            wheels = base / 'wheels'
            wheels.mkdir()
            (wheels / 'fixture-1-py3-none-any.whl').write_bytes(b'fixture')
            write_json(source / 'web/ui/dist/runtime.json', {'runtime_id': runtime_fingerprint(source)})
            payload = base / 'payload'
            build_release(source, payload, sha='a' * 40, repository='marcus-ao/FacebookScraper',
                          run_id=1, wheelhouse=wheels)
            environments, probes = [], []
            root = base / 'installation'
            with patch('deployment.host._quiet_run', return_value='fixture-owner'):
                result = install(root, payload, task_query=lambda name: False,
                                 environment=environments.append,
                                 probe=lambda release, manifest: probes.append(release))
            self.assertFalse(result['scheduler_enabled'])
            self.assertFalse(result['process_enabled'])
            self.assertEqual(environments, [root / 'releases' / ('a' * 40), root / 'controller'])
            self.assertEqual((root / 'shared/.env').read_bytes(), b'')
            self.assertEqual(list((root / 'shared/state').iterdir()), [])
            self.assertEqual(read_json(root / 'control/instance.json'), read_json(root / 'shared/instance.json'))
            self.assertEqual(probes, [environments[0]])
            self.assertEqual(read_json(root / 'control/deployment.json')['phase'], 'booting')

    def test_probe_environment_drops_secrets_and_sets_controller_identity(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'FEISHU_ALERT_WEBHOOK': 'secret'}):
            env = worker_environment(Path('control'), Path('config'), 'launch', isolated=True)
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('FEISHU_ALERT_WEBHOOK', env)
        self.assertEqual(env['FBSCRAPER_CONTROLLER_PID'], str(os.getpid()))

    def test_crash_launch_adoption_requires_complete_matching_identity(self):
        backend = LocalBackend.__new__(LocalBackend)
        row = {'role': 'web', 'launch_id': 'launch', 'sha': 'a' * 40,
               'runtime_id': 'b' * 64, 'instance_id': 'instance', 'spawn_pending': True,
               'launcher': None, 'worker': None}
        owner = {'pid': 123, 'started': '12'}
        observed = dict(row, worker=owner, launcher=owner, seen_at=1)
        class FakeGate:
            def status(self):
                return {'workers': {'web': observed}}
        backend.gate = FakeGate()
        observed['launch_id'] = 'unrelated'
        backend._refresh({'web': row})
        self.assertIsNone(row['worker'])
        self.assertTrue(row['uncertain_launch'])
        observed['launch_id'] = 'launch'
        backend._refresh({'old:web': row})
        self.assertEqual(row['worker'], owner)
        self.assertEqual(row['launcher'], owner)
        self.assertFalse(row['uncertain_launch'])

    def test_uncertain_notification_is_not_replayed_and_disabled_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp)
            class Bot:
                calls = 0
                def send(self, *args):
                    self.calls += 1
                    raise TimeoutError('uncertain fixture')
            bot = Bot()
            event = {'event_id': 'one', 'kind': 'success', 'sha': 'a' * 40}
            disabled = FeishuSettings(False, 'http://localhost:8765')
            deliver(control, event, settings=disabled, bot=bot)
            self.assertEqual(bot.calls, 0)
            settings = FeishuSettings(True, 'http://localhost:8765')
            deliver(control, event, settings=settings, bot=bot)
            deliver(control, event, settings=settings, bot=bot)
            self.assertEqual(bot.calls, 1)
            self.assertTrue((control / 'deployment_outbox.json').exists())

    def test_controller_upgrade_is_refused_before_environment_or_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = 'b' * 40
            (root / 'releases' / sha).mkdir(parents=True)
            (root / 'controller').mkdir()
            write_json(root / 'controller/release.json', {'controller_id': 'old'})
            backend = LocalBackend.__new__(LocalBackend)
            backend.root, backend.control = root, root / 'control'
            backend.manifest = lambda selected: {'sha': selected, 'controller_id': 'new'}
            with patch('deployment.host.offline_environment') as environment:
                with self.assertRaisesRegex(ValueError, 'controller_upgrade_required'):
                    backend.prepare({'sha': sha})
                environment.assert_not_called()

    def test_unresolved_previous_probe_prevents_another_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalBackend.__new__(LocalBackend)
            backend.control = Path(tmp)
            control = backend.control / 'probe-unresolved/control'
            control.mkdir(parents=True)
            gate = Gate(control)
            gate.initialize()
            write_json(control / 'probe.json', {'role': 'web', 'worker': None, 'launcher': None,
                                               'launch_id': 'unknown'})
            with patch.object(backend, '_spawn') as spawn:
                with self.assertRaisesRegex(ValueError, 'probe_cleanup_unknown'):
                    backend.probe(Path(tmp) / 'release', {'sha': 'a' * 40})
                spawn.assert_not_called()


class BackendLifecycleTests(unittest.TestCase):
    def test_real_venv_launcher_is_adopted_and_stopped_without_data_writes(self):
        # Reuse the root-owned temporary application fixture; no production archive or credentials.
        from tests_deployment_worker import WorkerTests
        fixture = WorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        base = Path(fixture.temp.name)
        release = base / 'releases' / fixture.manifest['sha']
        release.parent.mkdir()
        fixture.release.rename(release)
        fixture.release = release
        for source in (Path(__file__).resolve().parents[1] / 'tools').glob('*.py'):
            target = release / 'tools' / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
        (base / 'logs').mkdir()
        completed = subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages',
                                    str(release / '.venv')], capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        (release / '.venv/Lib/site-packages/fixture.pth').write_text(sysconfig.get_paths()['purelib'] + '\n')
        # This is fixture-only: production environments use offline pinned wheels, never system site packages.
        (release / 'config.local.toml').write_bytes((base / 'binding.toml').read_bytes())
        backend = LocalBackend.__new__(LocalBackend)
        backend.root, backend.control, backend.gate = base, fixture.control, fixture.gate
        backend.marker = {'instance_id': 'isolated'}
        backend.remote, backend.processes = None, {}
        backend.manifest = lambda sha: fixture.manifest
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        write_json(fixture.control / 'host.json', {'web_port': port})
        modes = {'scheduler_enabled': True, 'process_enabled': False}
        workers = {}
        def persist(rows):
            workers.update(rows)
            write_json(fixture.control / 'fixture-workers.json', rows)
        workers = backend.start(fixture.manifest['sha'], modes, persist)
        fixture.processes.extend(backend.processes.values())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not backend.ready(fixture.manifest['sha'], workers, modes):
            time.sleep(.1)
        self.assertTrue(backend.ready(fixture.manifest['sha'], workers, modes), '\n'.join(
            path.read_text(encoding='utf-8') for path in (base / 'logs').glob('*.log')))
        self.assertTrue(all(row.get('worker') and row.get('launcher') for row in workers.values()))
        self.assertEqual(list((fixture.shared / 'state').iterdir()), [])
        # Simulate a controller restart: process handles are gone, durable owners remain.
        backend.processes = {}
        self.assertTrue(backend.running(workers))
        backend.stop(workers)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and backend.exited(workers) is not True:
            time.sleep(.1)
        self.assertTrue(backend.exited(workers))
        self.assertEqual(list((fixture.shared / 'state').iterdir()), [])
        backend.probe(release, fixture.manifest)
        probes = list(fixture.control.glob('probe-*/control/probe.json'))
        self.assertEqual(len(probes), 1)
        self.assertTrue(read_json(probes[0])['ready'])
        self.assertTrue(read_json(probes[0])['exited'])
        self.assertEqual(list((fixture.shared / 'state').iterdir()), [])


if __name__ == '__main__':
    unittest.main()

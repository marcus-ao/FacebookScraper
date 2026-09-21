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
    def test_install_uses_service_machine_profile_without_changing_local_defaults(self):
        from deployment.cli import main
        from core.web_access import WebAccess
        profile = Path(__file__).resolve().parents[1] / 'ops/service-machine.network.json'
        expected = read_json(profile)
        policy = WebAccess.from_mapping(expected)
        self.assertEqual(policy.web_host, '0.0.0.0')
        self.assertFalse(policy.permits_client('8.8.8.8'))
        with patch('deployment.cli.install', return_value={}) as installer, patch('builtins.print'):
            main(['install', '--root', 'fixture-root', '--release', 'fixture-release',
                  '--network-config', str(profile)])
            self.assertEqual(installer.call_args.kwargs, expected)
            main(['install', '--root', 'fixture-root', '--release', 'fixture-release'])
            self.assertEqual(installer.call_args.kwargs, {'web_host': '127.0.0.1', 'web_port': 8765,
                'public_base_url': None, 'allowed_client_cidrs': []})

    def test_network_file_cannot_override_modes_or_be_combined_with_flags(self):
        from deployment.cli import main
        from deployment.errors import DeploymentError
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'network.json'
            root = Path(tmp) / 'must-not-exist'
            base = ['install', '--root', str(root), '--release', tmp, '--network-config', str(profile)]
            policy = {'web_host': '0.0.0.0', 'web_port': 8765,
                      'public_base_url': 'http://10.66.4.9:8765', 'allowed_client_cidrs': ['10.66.4.0/24']}
            for value in ({}, dict(policy, process_enabled=True), dict(policy, allowed_client_cidrs=['0.0.0.0/0']),
                          dict(policy, public_base_url='http://0.0.0.0:8765')):
                write_json(profile, value)
                with self.subTest(value=value), patch('deployment.host.verify_release') as verify:
                    with self.assertRaises((ValueError, DeploymentError)):
                        main(base)
                    verify.assert_not_called()
                    self.assertFalse(root.exists())
            write_json(profile, policy)
            for flags in (['--web-host', '0.0.0.0'], ['--web-port', '8765'],
                          ['--public-base-url', policy['public_base_url']], ['--allow-client-subnet', '10.66.4.0/24']):
                with self.subTest(flags=flags), patch('deployment.cli.install') as installer:
                    with self.assertRaisesRegex(DeploymentError, 'network_config_conflicts'):
                        main(base + flags)
                    installer.assert_not_called()

    def test_install_cli_passes_explicit_network_policy(self):
        from deployment.cli import main
        with patch('deployment.cli.install', return_value={}) as installer, patch('builtins.print'):
            main(['install', '--root', 'fixture-root', '--release', 'fixture-release',
                  '--web-host', '0.0.0.0', '--web-port', '9876',
                  '--public-base-url', 'http://192.168.20.10:9876',
                  '--allow-client-subnet', '192.168.20.0/24',
                  '--allow-client-subnet', '10.40.0.0/16'])
        self.assertEqual(installer.call_args.kwargs, {
            'web_host': '0.0.0.0', 'web_port': 9876,
            'public_base_url': 'http://192.168.20.10:9876',
            'allowed_client_cidrs': ['192.168.20.0/24', '10.40.0.0/16'],
        })

    def test_invalid_network_policy_is_rejected_before_install_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'untouched'
            with patch('deployment.host.verify_release') as verify:
                with self.assertRaises(ValueError):
                    install(root, Path(tmp), web_host='0.0.0.0',
                            task_query=lambda name: False)
            self.assertFalse(root.exists())
            verify.assert_not_called()

    def test_readiness_requires_the_expected_network_identity(self):
        backend = LocalBackend.__new__(LocalBackend)
        worker = {'pid': 123, 'started': '12'}
        row = {'role': 'web', 'sha': 'a' * 40, 'runtime_id': 'b' * 64,
               'instance_id': 'fixture', 'launch_id': 'launch', 'worker': worker,
               'seen_at': time.time(), 'port': 9876, 'web_host': '0.0.0.0',
               'web_port': 9876, 'public_base_url': 'http://192.168.20.10:9876'}
        health = dict(row, managed=True, deployment_ready=True, frontend_runtime_id='b' * 64)
        with patch.object(backend, '_refresh'), patch('deployment.host.worker_alive', return_value=True), \
                patch.object(backend, '_health', return_value=health) as fetch:
            self.assertTrue(backend.ready(row['sha'], {'web': row}, {'scheduler_enabled': False}))
            fetch.assert_called_with(9876)
            for key, wrong in (('web_host', '127.0.0.1'), ('web_port', 8765),
                               ('public_base_url', 'http://127.0.0.1:9876')):
                with self.subTest(field=key):
                    health[key] = wrong
                    self.assertFalse(backend.ready(row['sha'], {'web': row}, {'scheduler_enabled': False}))
                    health[key] = row[key]

    def test_wildcard_port_check_rejects_an_existing_loopback_listener(self):
        backend = LocalBackend.__new__(LocalBackend)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.assertFalse(backend._port_free(port, '0.0.0.0'))

    def test_lan_start_passes_persisted_network_policy_to_owned_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'logs').mkdir()
            backend = LocalBackend.__new__(LocalBackend)
            backend.root, backend.control = root, root / 'control'
            backend.gate = Gate(backend.control)
            backend.gate.initialize()
            backend.gate.announce(delay=0)
            self.assertTrue(backend.gate.try_quiesce())
            backend.marker, backend.processes = {'instance_id': 'fixture'}, {}
            sha = 'a' * 40
            backend.manifest = lambda selected: {'sha': selected, 'runtime_id': 'b' * 64}
            policy = {'web_host': '0.0.0.0', 'web_port': 9876,
                      'public_base_url': 'http://192.168.20.10:9876',
                      'allowed_client_cidrs': ['192.168.20.0/24']}
            write_json(backend.control / 'host.json', policy)
            saved = []
            with patch.object(backend, '_port_free', return_value=True) as available, \
                    patch('deployment.host.subprocess.Popen') as process, \
                    patch('deployment.host.process_identity', return_value={'pid': 123, 'started': '12'}):
                workers = backend.start(sha, {'scheduler_enabled': False, 'process_enabled': False},
                                        lambda rows: saved.append(json.loads(json.dumps(rows))))
            available.assert_called_once_with(9876, '0.0.0.0')
            command = process.call_args.args[0]
            self.assertEqual(command[-4:], ['--host', '0.0.0.0', '--port', '9876'])
            self.assertTrue(saved[0]['web']['spawn_pending'])
            self.assertFalse(saved[-1]['web']['spawn_pending'])
            for key in ('web_host', 'web_port', 'public_base_url'):
                self.assertEqual(workers['web'][key], policy[key])

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
            settings = read_json(root / 'control/host.json')
            self.assertEqual(settings['web_host'], '127.0.0.1')
            self.assertEqual(settings['web_port'], 8765)
            self.assertEqual(settings['public_base_url'], 'http://127.0.0.1:8765')
            self.assertEqual(settings['allowed_client_cidrs'], [])
            lan_root = base / 'lan-installation'
            network = {'web_host': '0.0.0.0', 'web_port': 9876,
                       'public_base_url': 'http://192.168.20.10:9876',
                       'allowed_client_cidrs': ['192.168.20.0/24']}
            with patch('deployment.host._quiet_run', return_value='fixture-owner'):
                result = install(lan_root, payload, **network, task_query=lambda name: False,
                                 environment=environments.append,
                                 probe=lambda release, manifest: probes.append(release))
            settings = read_json(lan_root / 'control/host.json')
            for key, value in network.items():
                self.assertEqual(settings[key], value)
                self.assertEqual(result[key], value)
            self.assertFalse(settings['scheduler_enabled'])
            self.assertFalse(settings['process_enabled'])

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
        lan_policy = {'web_host': '0.0.0.0', 'web_port': port,
                      'public_base_url': f'http://192.168.20.10:{port}',
                      'allowed_client_cidrs': ['192.168.20.0/24']}
        write_json(fixture.control / 'host.json', lan_policy)
        backend.probe(release, fixture.manifest)
        probes = list(fixture.control.glob('probe-*/control/probe.json'))
        self.assertEqual(len(probes), 1)
        self.assertTrue(read_json(probes[0])['ready'])
        self.assertTrue(read_json(probes[0])['exited'])
        probe = read_json(probes[0])
        probe_policy = read_json(probes[0].with_name('host.json'))
        self.assertEqual(probe_policy, {'web_host': '127.0.0.1', 'web_port': probe['port'],
                                        'public_base_url': f"http://127.0.0.1:{probe['port']}",
                                        'allowed_client_cidrs': []})
        self.assertEqual(read_json(fixture.control / 'host.json'), lan_policy)
        self.assertEqual((probes[0].parents[1] / 'shared/.env').read_bytes(), b'')
        self.assertEqual(list((fixture.shared / 'state').iterdir()), [])


if __name__ == '__main__':
    unittest.main()

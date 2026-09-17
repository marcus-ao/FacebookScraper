"""Real local Windows/Python workers against empty temporary business data."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.maintenance import Gate
from core.process_identity import worker_alive


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.release = base / 'release'
        self.release.mkdir()
        for folder in ('core', 'pipeline', 'routes', 'localize', 'publish', 'web/api', 'deployment', 'tools'):
            for source in (ROOT / folder).glob('*.py'):
                target = self.release / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        shutil.copyfile(ROOT / 'config.toml', self.release / 'config.toml')
        self.manifest = {'version': 1, 'protocol': 1, 'truth_contract': 1,
                         'sha': 'a' * 40, 'runtime_id': 'b' * 64}
        (self.release / 'release.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        dist = self.release / 'web/ui/dist'
        dist.mkdir(parents=True)
        (dist / 'index.html').write_text('<html>isolated worker</html>', encoding='utf-8')
        (dist / 'runtime.json').write_text(json.dumps({'runtime_id': 'b' * 64}), encoding='utf-8')
        self.shared, self.control = base / 'shared', base / 'control'
        for path in (self.shared / 'archive', self.shared / 'state', self.control):
            path.mkdir(parents=True)
        (self.shared / '.env').write_text('', encoding='utf-8')
        marker = {'version': 1, 'instance_id': 'isolated', 'shared': str(self.shared)}
        for path in (self.control, self.shared):
            (path / 'instance.json').write_text(json.dumps(marker), encoding='utf-8')
        config = '[paths]\narchive = ' + json.dumps(str(self.shared / 'archive')) + '\nstate = ' + json.dumps(str(self.shared / 'state'))
        config += '\n[runtime]\nenv_file = ' + json.dumps(str(self.shared / '.env')) + '\n'
        (base / 'binding.toml').write_text(config, encoding='utf-8')
        self.environment = dict(os.environ, FBSCRAPER_RUNTIME_CONFIG=str(base / 'binding.toml'),
                                FBSCRAPER_CONTROL_DIR=str(self.control), FBSCRAPER_LAUNCH_ID='isolated-launch',
                                PYTHONIOENCODING='utf-8', PYTHONPATH=str(self.release))
        self.gate = Gate(self.control)
        self.gate.initialize()
        self.gate.announce(delay=0)
        self.assertTrue(self.gate.try_quiesce())
        self.processes = []
        self.addCleanup(self.stop_all)
        self.logs = []

    def stop_all(self):
        owners = [row['worker'] for row in self.gate.status()['workers'].values()]
        self.gate.request_stop(owners)
        try:
            for process in self.processes:
                process.wait(timeout=15)
        finally:
            for stream in self.logs:
                stream.close()

    def start(self, role, port):
        stream = (Path(self.temp.name) / (role + str(len(self.processes)) + '.log')).open('wb')
        self.logs.append(stream)
        process = subprocess.Popen([sys.executable, '-m', 'deployment.worker', '--role', role, '--port', str(port)],
                                    cwd=self.release, env=self.environment, stdout=stream, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    def wait_for(self, fn):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                value = fn()
                if value:
                    return value
            except (KeyError, httpx.HTTPError):
                pass
            time.sleep(.05)
        self.fail('Owned worker did not become ready; logs: ' + '\n'.join(
            path.read_text(encoding='utf-8') for path in Path(self.temp.name).glob('*.log')))

    def test_real_workers_are_idle_during_validation_and_exit_cooperatively(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        web = self.start('web', port)
        scheduler = self.start('scheduler', port)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=1) as client:
            ready = self.wait_for(lambda: client.get('/api/health').json())
            self.assertTrue(ready['deployment_ready'], ready)
            self.assertEqual(ready['sha'], 'a' * 40)
            self.assertEqual(ready['frontend_runtime_id'], 'b' * 64)
            self.assertEqual(ready['instance_id'], 'isolated')
            self.assertEqual(client.post('/api/tasks/missing/skip').status_code, 503)
            self.wait_for(lambda: self.gate.status()['workers']['scheduler'])
            self.assertEqual(list((self.shared / 'state').iterdir()), [])

            duplicate = self.start('web', port)
            self.assertNotEqual(duplicate.wait(timeout=10), 0)
            owners = [row['worker'] for row in self.gate.status()['workers'].values()]
            self.assertTrue(all(worker_alive(owner) for owner in owners))
            self.gate.request_stop(owners)
            self.assertEqual(web.wait(timeout=15), 0)
            self.assertEqual(scheduler.wait(timeout=15), 0)
            self.assertTrue(all(worker_alive(owner) is False for owner in owners))
            self.assertEqual(list((self.shared / 'state').iterdir()), [])

    def test_invalid_scheduler_cannot_advertise_readiness_while_quiesced(self):
        path = self.release / 'config.toml'
        content = path.read_text(encoding='utf-8')
        import re
        content = re.sub(r'(refresh_minutes\s*=\s*)[\d.]+', r'\g<1>0', content)
        path.write_text(content, encoding='utf-8')
        scheduler = self.start('scheduler', 8765)
        self.assertNotEqual(scheduler.wait(timeout=10), 0)
        self.assertNotIn('scheduler', self.gate.status()['workers'])
        self.assertIn('calendar.enabled', (Path(self.temp.name) / 'scheduler0.log').read_text(encoding='utf-8'))

    def test_broken_processing_state_fails_readiness_without_recovering_data(self):
        state = self.shared / 'state/processing_state.json'
        state.write_bytes(b'{broken')
        scheduler = self.start('scheduler', 8765)
        self.assertNotEqual(scheduler.wait(timeout=10), 0)
        self.assertNotIn('scheduler', self.gate.status()['workers'])
        self.assertEqual(state.read_bytes(), b'{broken')

    def test_web_health_rejects_frontend_path_that_is_not_served_from_release(self):
        path = self.release / 'config.toml'
        content = path.read_text(encoding='utf-8').replace('web_dist = "web/ui/dist"', 'web_dist = "missing-ui"')
        path.write_text(content, encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.start('web', port)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=1) as client:
            ready = self.wait_for(lambda: client.get('/api/health').json())
            self.assertFalse(ready['deployment_ready'])


if __name__ == '__main__':
    unittest.main()

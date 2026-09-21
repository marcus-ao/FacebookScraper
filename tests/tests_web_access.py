"""Office HTTP policy and links, with isolated control data and no external calls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from core.maintenance import Gate
from core.web_access import WebAccess, http_origin, load_web_access
from core.feishu import FeishuSettings, notification_card
from web.api.app import app
from web.api import reader, deployment

LAN = {'web_host': '0.0.0.0', 'web_port': 8765, 'public_base_url': 'http://192.168.10.20:8765',
       'allowed_client_cidrs': ['192.168.10.0/24']}


class PolicyTests(unittest.TestCase):
    def test_source_policy_is_opt_in_and_managed_policy_takes_priority(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'network.json'
            source.write_text(json.dumps(LAN), encoding='utf-8')
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR='', FBSCRAPER_NETWORK_CONFIG=''):
                self.assertEqual(load_web_access(), WebAccess())
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR='', FBSCRAPER_NETWORK_CONFIG=str(source)):
                self.assertEqual(load_web_access().as_dict(), LAN)
                (Path(root) / 'host.json').write_text('{}', encoding='utf-8')
                self.assertEqual(load_web_access(Path(root)), WebAccess())
                with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=root):
                    self.assertEqual(load_web_access(), WebAccess())
                for contents in ('{broken', '[]', '{"web_host":"0.0.0.0"}'):
                    source.write_text(contents, encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'invalid_source_web_settings'):
                        load_web_access()
                source.unlink()
                with self.assertRaisesRegex(ValueError, 'invalid_source_web_settings'):
                    load_web_access()

    def test_source_lan_preserves_client_host_origin_checks_and_reports_bad_config(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'network.json'
            source.write_text(json.dumps(LAN), encoding='utf-8')
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR='', FBSCRAPER_NETWORK_CONFIG=str(source)):
                for address, url, origin, status in (
                    ('192.168.10.21', LAN['public_base_url'], LAN['public_base_url'], 409),
                    ('192.168.11.21', LAN['public_base_url'], LAN['public_base_url'], 403),
                    ('192.168.10.21', 'http://wrong.invalid:8765', LAN['public_base_url'], 403),
                    ('192.168.10.21', LAN['public_base_url'], 'http://wrong.invalid:8765', 403),
                ):
                    with self.subTest(address=address, url=url, origin=origin), TestClient(
                            app, base_url=url, client=(address, 41000)) as client:
                        response = client.post('/api/deployment/defer', json={}, headers={'Origin': origin})
                        self.assertEqual(response.status_code, status)
                source.unlink()
                with TestClient(app, base_url=LAN['public_base_url'], client=('192.168.10.21', 41000)) as client:
                    self.assertEqual(client.get('/api/deployment/status').status_code, 503)

    def test_default_binding_and_legacy_port_are_local(self):
        self.assertEqual(WebAccess.from_mapping({}), WebAccess())
        policy = WebAccess.from_mapping({'web_port': 4321})
        self.assertEqual(policy.public_base_url, 'http://127.0.0.1:4321')
        self.assertFalse(policy.permits_client('192.168.10.21'))

    def test_lan_requires_explicit_valid_origin_and_bounded_ipv4_subnets(self):
        invalid = [dict(LAN, public_base_url=value) for value in (
            None, '', 'http://0.0.0.0:8765', 'http://localhost:8765', 'http://127.0.0.1:8765',
            'http://[::1]:8765', 'http://192.168.10.20:80', 'http://name:8765/a',
            'http://user:password@name:8765', 'http://name:8765?', 'http://name:8765#',
            'http://name:8765/\\bad', 'https://name:8765', 'http://127.1:8765')]
        invalid += [dict(LAN, allowed_client_cidrs=value) for value in (
            [], ['0.0.0.0/0'], ['128.0.0.0/1'], ['8.8.8.0/24'], ['::/0'], ['192.168.10.3/24'],
            ['224.0.0.0/4'], '192.168.10.0/24')]
        invalid += [dict(LAN, web_port=True), dict(LAN, web_host='::'),
                    dict(LAN, web_port=80, public_base_url='http://name:0')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                WebAccess.from_mapping(value)

    def test_named_origin_and_office_clients(self):
        policy = WebAccess.from_mapping(dict(LAN, public_base_url='http://review-lan.test:8765/'))
        self.assertEqual(http_origin(policy.public_base_url), ('http', 'review-lan.test', 8765))
        for address in ('127.0.0.1', '::1', '::ffff:127.0.0.1', '192.168.10.90'):
            self.assertTrue(policy.permits_client(address))
        self.assertFalse(policy.permits_client('192.168.11.90'))

    def test_missing_managed_config_never_defaults_to_a_new_installation(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaises(ValueError):
            load_web_access(Path(root))

    def test_unmanaged_loopback_proxy_preserves_browser_origin(self):
        with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=''), TestClient(
                app, base_url='http://127.0.0.1:5174', client=('127.0.0.1', 41000)) as client:
            response = client.post('/api/deployment/defer', json={}, headers={'Origin': 'http://127.0.0.1:5174'})
            self.assertEqual(response.status_code, 409)  # The real route reports unmanaged, not access denial.
            response = client.post('/api/deployment/defer', json={}, headers={'Origin': 'http://attacker.invalid'})
            self.assertEqual(response.status_code, 403)


class OfficeApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.control = Path(directory.name)
        self.gate = Gate(self.control)
        self.gate.initialize()
        self.path = self.control / 'host.json'
        self.path.write_text(json.dumps(LAN), encoding='utf-8')
        env = patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(self.control))
        env.start()
        self.addCleanup(env.stop)

    def client(self, address='192.168.10.21', base_url=LAN['public_base_url']):
        client = TestClient(app, base_url=base_url, client=(address, 41000))
        self.addCleanup(client.close)
        return client

    def test_remote_status_is_projected_and_healthy_admin_route_stays_local(self):
        self.gate.session('other-private-id', runtime_id='a'*64, dirty=True, busy=False, sequence=1)
        payload = self.client().get('/api/deployment/status').json()
        self.assertEqual(payload['public_base_url'], LAN['public_base_url'])
        self.assertEqual(payload['maintenance']['blockers'], [{'reason': 'unsaved'}])
        for hidden in ('other-private-id', 'workers', 'stop_workers', str(self.control)):
            self.assertNotIn(hidden, json.dumps(payload))
        self.assertEqual(self.client().get('/api/health').status_code, 403)
        local = self.client('127.0.0.1', 'http://127.0.0.1:8765').get('/api/health')
        self.assertNotEqual(local.status_code, 403)
        self.assertEqual(local.json()['web_host'], '0.0.0.0')

    def test_source_host_origin_and_forwarded_headers_fail_before_business(self):
        cases = [
            ('192.168.11.21', LAN['public_base_url'], {}),
            ('192.168.11.21', LAN['public_base_url'], {'X-Forwarded-For': '127.0.0.1'}),
            ('192.168.10.21', 'http://wrong.invalid:8765', {}),
            ('192.168.10.21', LAN['public_base_url'], {'Origin': 'http://attacker.invalid'}),
            ('192.168.10.21', LAN['public_base_url'], {'Origin': 'null'}),
            ('192.168.10.21', LAN['public_base_url'], {'Origin': LAN['public_base_url']+':bad'}),
        ]
        with patch.object(reader, 'list_tasks') as business, patch.object(Gate, 'acquire') as admission:
            for address, base, headers in cases:
                with self.subTest(address=address, base=base, headers=headers):
                    response = self.client(address, base).get('/api/tasks', headers=headers)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()['code'], 'access_denied')
            business.assert_not_called()
            admission.assert_not_called()

    def test_all_routes_are_protected_not_only_api(self):
        client = self.client('192.168.11.21')
        for path in ('/', '/assets/app.js', '/review/facebook', '/api/tasks/missing/image/0',
                     '/api/tasks/missing/export', '/openapi.json'):
            self.assertEqual(client.get(path).status_code, 403)

    def test_remote_json_session_and_beacon_are_accepted_and_retained_when_offline(self):
        client = self.client()
        body = {'session_id': 'first', 'runtime_id': 'a'*64, 'dirty': True, 'busy': False, 'sequence': 1}
        for headers in ({}, {'Origin': 'null'}, {'Origin': 'http://attacker.invalid'}):
            self.assertEqual(client.post('/api/deployment/session', json=body, headers=headers).status_code, 403)
        headers = {'Origin': LAN['public_base_url'], 'Content-Type': 'application/json; charset=utf-8'}
        response = client.post('/api/deployment/session', content=json.dumps(body), headers=headers)
        self.assertEqual(response.status_code, 200)
        self.gate.announce(delay=0)
        self.assertFalse(self.gate.try_quiesce())
        self.assertEqual(self.gate.status(now=10**11)['blockers'][0]['reason'], 'unsaved')
        self.assertEqual(client.post('/api/deployment/defer', content='{}', headers=headers).status_code, 200)
        self.assertEqual(client.post('/api/deployment/defer', content='{}', headers={
            'Origin': LAN['public_base_url'], 'Content-Type': 'text/plain'}).status_code, 403)

    def test_missing_policy_is_explicit_and_does_not_reach_data(self):
        self.path.write_text('{broken', encoding='utf-8')
        with patch.object(reader, 'list_tasks') as business:
            response = self.client().get('/api/tasks')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['code'], 'access_config_invalid')
            business.assert_not_called()

    def test_health_checks_actual_heartbeat_binding_after_settings_edit(self):
        manifest = {'sha': 'a'*40, 'runtime_id': 'b'*64}
        root = self.control / 'release'
        dist = root / 'web/ui/dist'
        dist.mkdir(parents=True)
        (dist / 'index.html').write_text('fixture', encoding='utf-8')
        (dist / 'runtime.json').write_text(json.dumps({'runtime_id': 'b'*64}), encoding='utf-8')
        archive = self.control / 'archive'
        archive.mkdir()
        fake = SimpleNamespace(archive_dir=archive, state_dir=self.control / 'state',
                               get=lambda section, key, default=None: default)
        metadata = dict(manifest, worker=deployment.current_worker(), launch_id='fixture', role='web',
                        web_host='127.0.0.1', web_port=8765, public_base_url='http://127.0.0.1:8765')
        self.gate.heartbeat('web', metadata)
        client = self.client('127.0.0.1', 'http://127.0.0.1:8765')
        with patch.object(deployment, 'read_release', return_value=manifest), patch.object(deployment, 'ROOT', root), \
             patch.object(deployment, 'cfg', return_value=fake), \
             patch.object(deployment, 'validate_binding', return_value={'instance_id': 'fixture'}), \
             patch.dict(os.environ, FBSCRAPER_LAUNCH_ID='fixture'):
            # Persistent settings say LAN while the owned worker still listens only locally.
            response = client.get('/api/health')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()['deployment_ready'])
            self.assertEqual(response.json()['web_host'], '127.0.0.1')
            self.path.write_text(json.dumps({'web_port': 8765}), encoding='utf-8')
            response = client.get('/api/health')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['deployment_ready'])

    def test_managed_feishu_links_share_persistent_public_url(self):
        fake = SimpleNamespace(get=lambda section, key, default=None: default)
        with patch('core.feishu.cfg', return_value=fake):
            settings = FeishuSettings.load()
        self.assertEqual(settings.base_url, LAN['public_base_url'])
        card = notification_card('system', [{'run_id': 'fixture'}], settings)
        self.assertIn(LAN['public_base_url'], json.dumps(card))
        with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=''), patch('core.feishu.cfg', return_value=fake):
            self.assertEqual(FeishuSettings.load().base_url, '')


if __name__ == '__main__':
    unittest.main()

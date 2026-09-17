"""Managed HTTP admission, identity and local control; no external business requests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from core import maintenance
from web.api.app import app
from web.api import deployment


class DeploymentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = Path(self.temp.name) / 'control'
        self.gate = maintenance.Gate(self.control)
        self.gate.initialize()
        (self.control / 'host.json').write_text(json.dumps({'web_port': 8765}), encoding='utf-8')
        self.env = patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(self.control))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 41000))
        self.addCleanup(self.client.close)

    def test_status_separates_maintenance_from_business_readiness(self):
        response = self.client.get('/api/deployment/status')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['managed'])
        self.assertEqual(response.json()['maintenance']['phase'], 'open')

    def test_quiesced_gate_blocks_business_reads_and_writes_before_router(self):
        self.gate.announce(delay=0)
        self.assertTrue(self.gate.try_quiesce())
        for method, url in [('get', '/api/tasks'), ('post', '/api/tasks/missing/skip')]:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['code'], 'maintenance')
        self.assertEqual(self.client.get('/api/deployment/status').status_code, 200)

    def test_remote_origin_cannot_defer_or_forge_clean_sessions(self):
        for route in ('defer', 'session'):
            response = self.client.post('/api/deployment/' + route,
                                        headers={'Origin': 'https://attacker.invalid'}, json={})
            self.assertEqual(response.status_code, 403)

    def test_draft_session_protects_switch_and_local_defer_reopens_admission(self):
        response = self.client.post('/api/deployment/session', json={
            'session_id': 'editing', 'runtime_id': 'a' * 64, 'dirty': True, 'busy': False, 'sequence': 1})
        self.assertEqual(response.status_code, 200)
        self.gate.announce(delay=0)
        self.assertFalse(self.gate.try_quiesce())
        self.assertEqual(self.client.post('/api/deployment/defer', json={}).status_code, 200)
        self.assertEqual(self.gate.status()['phase'], 'open')

    def test_unmanaged_development_does_not_claim_a_deployed_sha(self):
        with patch.dict(os.environ, {'FBSCRAPER_CONTROL_DIR': ''}):
            response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['managed'])
        self.assertIsNone(response.json()['sha'])

    def test_old_page_write_rejected_without_calling_business(self):
        with patch.object(deployment, 'read_release', return_value={'sha': 'b' * 40, 'runtime_id': 'b' * 64}):
            response = self.client.post('/api/tasks/missing/skip', headers={'X-FBScraper-Runtime': 'a' * 64})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'runtime_changed')

    def test_health_cannot_use_another_instance_or_frontend(self):
        with patch.object(deployment, 'read_release', return_value={'sha': 'a' * 40, 'runtime_id': 'a' * 64}):
            response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['deployment_ready'])


if __name__ == '__main__':
    unittest.main()

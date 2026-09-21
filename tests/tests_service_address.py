"""Address changes touch only isolated repository configuration, never live services."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import update_service_address as updater
from core import config
from core.feishu import FeishuSettings, Outbox, notification_card
from core.web_access import load_web_access

CONFIG = '''# Keep the model endpoint and comments.
[translate]
base_url = "https://model.example/v1"
[feishu]
enabled = true
base_url = "http://10.66.4.9:8765" # card URL
[mirror]
enabled = false
'''
NETWORK = {'web_host': '0.0.0.0', 'web_port': 8765,
           'public_base_url': 'http://10.66.4.9:8765', 'allowed_client_cidrs': ['10.66.4.0/24']}


class AddressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='service address ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'ops').mkdir()
        self.network = self.root / 'ops/service-machine.network.json'
        self.config = self.root / 'config.toml'
        self.network.write_text(json.dumps(NETWORK), encoding='utf-8', newline='')
        self.config.write_text(CONFIG, encoding='utf-8', newline='')
        env = patch.dict(os.environ, FBSCRAPER_CONTROL_DIR='')
        env.start()
        self.addCleanup(env.stop)

    def update(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return updater.update_address(self.root, *args, **kwargs)

    def snapshot(self):
        return self.network.read_bytes(), self.config.read_bytes()

    def test_explicit_prefix_synchronizes_only_network_and_feishu_and_is_idempotent(self):
        self.update('10.66.6.3/24')
        network = json.loads(self.network.read_bytes())
        self.assertEqual(network, dict(NETWORK, public_base_url='http://10.66.6.3:8765',
                                      allowed_client_cidrs=['10.66.6.0/24']))
        self.assertEqual(self.config.read_text('utf-8'), CONFIG.replace('10.66.4.9', '10.66.6.3'))
        self.assertNotIn(b'\r', self.config.read_bytes())
        before = self.snapshot()
        with patch.object(updater, 'atomic_write_text', side_effect=AssertionError('unnecessary write')):
            self.update('10.66.6.3/24')
        self.assertEqual(self.snapshot(), before)

    def test_ip_only_preserves_existing_subnets_and_port_can_change(self):
        self.update('10.66.4.20', port=9876)
        network = json.loads(self.network.read_bytes())
        self.assertEqual(network['allowed_client_cidrs'], ['10.66.4.0/24'])
        self.assertEqual(network['web_port'], 9876)
        self.assertEqual(tomllib.loads(self.config.read_text('utf-8'))['feishu']['base_url'],
                         'http://10.66.4.20:9876')

    def test_explicit_multiple_client_networks_support_routed_clients(self):
        self.update('10.66.6.3', cidrs=['10.66.4.0/24', '10.66.8.0/24'])
        self.assertEqual(json.loads(self.network.read_bytes())['allowed_client_cidrs'],
                         ['10.66.4.0/24', '10.66.8.0/24'])

    def test_invalid_inputs_or_unconfirmed_subnet_do_not_write_either_file(self):
        for address, kwargs in (
            ('10.66.6.3', {}), ('10.66.6.3/24', {'cidrs': ['10.66.4.0/24']}),
            ('http://10.66.6.3:8765', {}), ('10.66.6.999', {}), ('127.0.0.1/24', {}),
            ('0.0.0.0/24', {}), ('8.8.8.8/24', {}), ('10.66.6.3/0', {}),
            ('10.66.6.0/24', {}), ('10.66.6.255/24', {}), ('::1', {}),
            ('10.66.4.20', {'port': 0}), ('10.66.6.3', {'cidrs': ['0.0.0.0/0']}),
        ):
            before = self.snapshot()
            with self.subTest(address=address, kwargs=kwargs), self.assertRaises(ValueError):
                self.update(address, **kwargs)
            self.assertEqual(self.snapshot(), before)

    def test_malformed_toml_and_missing_feishu_url_never_partially_update_json(self):
        for contents in ('broken = [', CONFIG.replace('[feishu]', '[other]'),
                         CONFIG.replace('base_url = "http://10.66.4.9:8765" # card URL\n', '')):
            self.config.write_text(contents, encoding='utf-8', newline='')
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.update('10.66.6.3/24')
            self.assertEqual(self.snapshot(), before)

    def test_second_file_write_failure_rolls_back_first(self):
        original = updater.atomic_write_text
        def fail_config(path, content, **kwargs):
            if path == self.config:
                raise OSError('fixture write failure')
            return original(path, content, **kwargs)
        before = self.snapshot()
        with patch.object(updater, 'atomic_write_text', side_effect=fail_config), self.assertRaises(OSError):
            self.update('10.66.6.3/24')
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_and_managed_environment_do_not_change_files(self):
        before = self.snapshot()
        self.update('10.66.6.3/24', dry_run=True)
        self.assertEqual(self.snapshot(), before)
        with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR='installed/control'), self.assertRaises(ValueError):
            self.update('10.66.6.3/24')
        self.assertEqual(self.snapshot(), before)

    def test_interactive_new_subnet_requires_explicit_input(self):
        with patch.object(updater, 'ROOT', self.root), patch('builtins.input', side_effect=[
                '10.66.6.3', '10.66.6.0/24']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(updater.main([]), 0)
        self.assertEqual(json.loads(self.network.read_bytes())['public_base_url'], 'http://10.66.6.3:8765')

    def test_cancelled_prompt_does_not_write(self):
        before = self.snapshot()
        with patch.object(updater, 'ROOT', self.root), patch('builtins.input', side_effect=EOFError), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(updater.main([]), 0)
        self.assertEqual(self.snapshot(), before)

    def test_updated_config_reaches_every_generated_card_link_and_source_web(self):
        cached = config.Config(self.config)
        new_base = 'http://10.66.6.3:9876'
        cases = [
            ('ready', {'task_id': 'in_example/a'}, '/?task=in_example%2Fa'),
            ('scheduled', {'task_id': 'fa_example/b'}, '/?task=fa_example%2Fb'),
            ('schedule_failed', {'task_id': 'fa_example/c'}, '/?task=fa_example%2Fc'),
            ('backlog', {'platform': 'facebook'}, '/review/facebook'),
            ('backlog', {'platform': 'instagram'}, '/review/instagram'),
            ('monitor_found', {'run_id': 'scan/a'}, '/runtime?scan=scan%2Fa'),
            ('monitor_saved', {'capture_key': 'fb/a'}, '/runtime?capture=fb%2Fa'),
            ('monitor_saved', {'capture_key': 'fb/a', 'archived': True, 'account_dir': 'fa_example',
                              'post_id': 'a'}, '/history/fa_example/a'),
            ('system', {'run_id': 'run/a'}, '/runtime?scan=run%2Fa'),
            ('morning', {'task_id': 'in_example/d'}, '/?task=in_example%2Fd'),
            ('selftest', {'run_id': 'self/a'}, '/runtime?scan=self%2Fa'),
        ]
        with patch.object(config, '_cfg', cached), patch.dict(os.environ, FBSCRAPER_NETWORK_CONFIG=''):
            self.assertEqual(FeishuSettings.load().base_url, 'http://10.66.4.9:8765')
            self.update('10.66.6.3/24', port=9876)
            for kind, payload, suffix in cases:
                with self.subTest(kind=kind, suffix=suffix):
                    payload = {**payload, 'permalink': 'https://www.instagram.com/p/original/'}
                    card = notification_card(kind, [payload], FeishuSettings.load())
                    urls = [action['url'] for item in card['elements'] if item['tag'] == 'action'
                            for action in item['actions']]
                    self.assertEqual(urls, [new_base + suffix, payload['permalink']])
            with patch.dict(os.environ, FBSCRAPER_NETWORK_CONFIG=str(self.network)):
                policy = load_web_access()
                self.assertEqual(policy.public_base_url, new_base)
                self.assertEqual(FeishuSettings.load().base_url, new_base)
                self.assertEqual(policy.web_port, 9876)

    def test_address_update_preserves_sent_and_uncertain_cards_and_new_deliveries_use_new_url(self):
        now = datetime(2026, 9, 21, 4, tzinfo=timezone.utc)
        path = self.root / 'feishu_outbox.json'
        with patch.object(config, '_cfg', config.Config(self.config)), \
                patch.dict(os.environ, FBSCRAPER_NETWORK_CONFIG=''):
            old = Outbox(path, FeishuSettings.load())
            old.enqueue('sent', 'system', {'run_id': 'sent'}, now)
            old.dispatch(now, lambda *_: 'accepted')
            old.enqueue('uncertain', 'system', {'run_id': 'uncertain'}, now)
            def timeout(*_):
                raise TimeoutError('fixture')
            old.dispatch(now, timeout)
            before = path.read_bytes()
            self.update('10.66.6.3/24')
            self.assertEqual(path.read_bytes(), before)
            current = Outbox(path, FeishuSettings.load())
            current.enqueue('new', 'system', {'run_id': 'new'}, now)
            delivered = []
            current.dispatch(now + timedelta(minutes=20), lambda role, card, ident: delivered.append(card) or 'new')
            self.assertEqual(len(delivered), 1)
            url = delivered[0]['elements'][0]['actions'][0]['url']
            self.assertEqual(url, 'http://10.66.6.3:8765/runtime?scan=new')
            previous = json.loads(before)['deliveries']
            after = json.loads(path.read_bytes())['deliveries']
            for identifier, row in previous.items():
                self.assertEqual(after[identifier], row)

    @unittest.skipUnless(os.name == 'nt', 'Windows batch entry point')
    def test_batch_updates_from_another_directory_with_bound_python(self):
        source = Path(__file__).resolve().parents[1]
        for folder in ('scripts', 'tools'):
            (self.root / folder).mkdir()
        for name in ('scripts/run_python.bat', 'scripts/update_service_address.bat',
                     'tools/runtime.py', 'tools/update_service_address.py'):
            shutil.copyfile(source / name, self.root / name)
        shutil.copytree(source / 'core', self.root / 'core', ignore=shutil.ignore_patterns('__pycache__'))
        binding = self.root / 'config.local.toml'
        binding.write_text('[runtime]\npython = ' + json.dumps(sys.executable) + '\n', encoding='utf-8', newline='')
        env = dict(os.environ, FBSCRAPER_RUNTIME_CONFIG=str(binding), PYTHONPATH=str(self.root))
        launcher = self.root / 'scripts/update_service_address.bat'
        for arguments, stdin in ((['10.66.6.3/24'], None), ([], '10.66.6.4\n\n')):
            result = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(launcher), *arguments],
                                    input=stdin, cwd=self.root.parent, env=env, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.network.read_bytes())['public_base_url'], 'http://10.66.6.4:8765')
        self.assertEqual(tomllib.loads(self.config.read_text('utf-8'))['feishu']['base_url'],
                         'http://10.66.6.4:8765')


if __name__ == '__main__':
    unittest.main()

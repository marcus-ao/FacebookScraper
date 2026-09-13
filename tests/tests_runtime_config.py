"""Runtime path binding is explicit, preserves policy, and shares all entry points."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config
from core.paid_model import ModelCredentials


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'config.toml'
        self.base.write_text('[paths]\narchive = "archive"\nstate = "state"\n[pipeline]\nautonomy = "manual"\n', encoding='utf-8')
        self.local = self.root / 'config.local.toml'

    def test_local_paths_do_not_replace_business_config(self):
        self.local.write_text('[paths]\narchive = ' + json.dumps(str(self.root / 'real-archive')) +
                              '\nstate = ' + json.dumps(str(self.root / 'real-state')), encoding='utf-8')
        c = config.Config(self.base, runtime_path=self.local)
        self.assertEqual(c.archive_dir, self.root / 'real-archive')
        self.assertEqual(c.get('pipeline', 'autonomy'), 'manual')
        self.assertEqual(config.Config(self.base).get('paths', 'archive'), 'archive')

    def test_local_config_cannot_override_paid_or_publish_gates(self):
        self.local.write_text('[pipeline]\nautonomy = "supervised"', encoding='utf-8')
        with self.assertRaises(ValueError):
            config.Config(self.base, runtime_path=self.local)

    def test_secret_lookup_uses_bound_file_without_copying_it(self):
        secret = self.root / '.env'
        secret.write_text('OFFLINE_TEST_KEY=local-value', encoding='utf-8')
        self.local.write_text('[runtime]\nenv_file = ' + json.dumps(str(secret)), encoding='utf-8')
        c = config.Config(self.base, runtime_path=self.local)
        with patch.object(config, '_cfg', c), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ModelCredentials('OFFLINE_TEST_KEY').api_key(), 'local-value')


if __name__ == '__main__':
    unittest.main()

"""Release code cannot silently create or use another empty business directory."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.runtime_identity import validate_binding
from core import config, maintenance, operating_settings


class BindingTests(unittest.TestCase):
    def test_operator_choices_survive_release_change_without_modifying_payload(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shared, control = root / 'shared', root / 'control'
            for path in (shared / 'archive', shared / 'state', control):
                path.mkdir(parents=True)
            (shared / '.env').write_text('', encoding='utf-8')
            marker = {'version': 1, 'instance_id': 'business-instance', 'shared': str(shared)}
            for path in (control, shared):
                (path / 'instance.json').write_text(json.dumps(marker), encoding='utf-8')
            maintenance.Gate(control).initialize()
            (root / 'release.json').write_text('{}', encoding='utf-8')
            source = '[review]\nsnooze_default_days = 3\n[publish.schedule_rule]\ntimes = ["10:00"]\n'
            (root / 'config.toml').write_text(source, encoding='utf-8', newline='')
            local = root / 'config.local.toml'
            local.write_text('[paths]\narchive = ' + json.dumps(str(shared / 'archive'))
                             + '\nstate = ' + json.dumps(str(shared / 'state'))
                             + '\n[runtime]\nenv_file = ' + json.dumps(str(shared / '.env')), encoding='utf-8')
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(control), FBSCRAPER_RUNTIME_CONFIG=str(local)), patch.object(config, 'ROOT', root), patch.object(config, '_cfg', None):
                before = operating_settings.read()
                operating_settings.save({'snooze_default_days': 7}, before['version'])
                self.assertEqual((root / 'config.toml').read_text(encoding='utf-8'), source)
                self.assertEqual(config.cfg().get('review', 'snooze_default_days'), 7)
                with self.assertRaises(operating_settings.SettingsConflict):
                    operating_settings.save({'snooze_default_days': 8}, before['version'])
                (root / 'config.toml').write_text(source.replace('= 3', '= 4'), encoding='utf-8')
                self.assertEqual(config.cfg().get('review', 'snooze_default_days'), 7)

    def test_binding_requires_shared_instance_and_exact_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shared, control = root / 'shared', root / 'control'
            for path in (shared / 'archive', shared / 'state', control):
                path.mkdir(parents=True)
            (shared / '.env').write_text('', encoding='utf-8')
            marker = {'version': 1, 'instance_id': 'business-instance', 'shared': str(shared)}
            for path in (control, shared):
                (path / 'instance.json').write_text(json.dumps(marker), encoding='utf-8')
            values = {'paths': {'archive': str(shared / 'archive'), 'state': str(shared / 'state')},
                      'runtime': {'env_file': str(shared / '.env')}}
            config = SimpleNamespace(get=lambda section, key, default=None: values.get(section, {}).get(key, default))
            with patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(control)):
                self.assertEqual(validate_binding(config, root)['instance_id'], 'business-instance')
                values['paths']['state'] = str(root / 'empty-state')
                with self.assertRaises(ValueError):
                    validate_binding(config, root)
                self.assertFalse((root / 'empty-state').exists())
                values['paths']['state'] = str(shared / 'state')
                (shared / 'instance.json').write_text('{broken', encoding='utf-8')
                with self.assertRaises(ValueError):
                    validate_binding(config, root)


if __name__ == '__main__':
    unittest.main()

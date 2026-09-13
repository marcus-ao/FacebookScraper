"""运营设置只改白名单字段，保留注释和并发编辑。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, operating_settings


class OperatingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.toml'
        self.original = ('# 保留运营说明\r\n[publish.schedule_rule]\r\n'
                         'times = ["10:00", "17:00"] # 柏林时刻\r\n'
                         '\r\n[review]\r\nsnooze_default_days = 3 # 工作日\r\n'
                         '[pipeline]\r\ndaily_budget_usd = 5\r\n')
        self.path.write_bytes(self.original.encode())
        self.c = config.Config(self.path)
        self.addCleanup(patch.stopall)
        patch.object(config, '_cfg', self.c).start()

    def test_preserves_other_bytes_and_reloads_current_values(self):
        before = operating_settings.read()
        result = operating_settings.save({'default_times': ['11:30', '18:00'],
                                         'snooze_default_days': 4}, before['version'])
        expected = self.original.replace('["10:00", "17:00"]', '["11:30", "18:00"]').replace(
            'snooze_default_days = 3', 'snooze_default_days = 4')
        self.assertEqual(self.path.read_bytes(), expected.encode())
        self.assertNotEqual(result['version'], before['version'])
        self.assertEqual(config.cfg().get('review', 'snooze_default_days'), 4)
        self.assertEqual(config.cfg().get('pipeline', 'daily_budget_usd'), 5)

    def test_concurrent_edit_is_not_overwritten(self):
        version = operating_settings.read()['version']
        self.path.write_bytes(self.path.read_bytes() + b'# human edit\r\n')
        with self.assertRaises(operating_settings.SettingsConflict):
            operating_settings.save({'snooze_default_days': 4}, version)
        self.assertTrue(self.path.read_bytes().endswith(b'# human edit\r\n'))

    def test_controlled_settings_and_bad_values_are_rejected(self):
        version = operating_settings.read()['version']
        for values in ({'daily_budget_usd': 100}, {'snooze_default_days': True},
                       {'snooze_default_days': 0}, {'default_times': ['25:00']},
                       {'default_times': ['10:00', '10:00']}, {'default_times': []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                operating_settings.save(values, version)
        self.assertEqual(self.path.read_bytes(), self.original.encode())

    def test_external_change_refreshes_cfg_in_long_running_process(self):
        self.path.write_bytes(self.original.replace('snooze_default_days = 3',
                                                    'snooze_default_days = 12').encode())
        self.assertEqual(config.cfg().get('review', 'snooze_default_days'), 12)

    def test_readonly_fields_carry_comments_without_exposing_other_config(self):
        self.path.write_bytes((self.original + '[publish]\n# 核验账号\ninstagram_account = "brand#de" # 完整匹配\n'
                               '[private]\nsecret = "do-not-expose" # 私密说明\n').encode('utf-8'))
        result = operating_settings.read()
        field = result['controlled_fields']['publish_identity'][0]
        self.assertEqual(field, {'key': 'publish.instagram_account', 'value': 'brand#de',
                                 'help': '核验账号\n完整匹配'})
        self.assertEqual(result['editable_help']['default_times'], '柏林时刻')
        self.assertNotIn('do-not-expose', str(result))
        self.assertNotIn('私密说明', str(result))


if __name__ == '__main__':
    unittest.main()

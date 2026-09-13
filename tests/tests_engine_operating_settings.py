"""可编辑默认时刻会进入选期规则，但不会放宽其它发布约束。"""
from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import cfg
from pipeline import engine


class EngineOperatingSettingsTests(unittest.TestCase):
    def publish(self, times):
        raw = deepcopy(cfg()._d["publish"])
        raw["schedule_rule"]["times"] = times
        return engine.publish_rules(raw)

    def test_one_to_twelve_unique_berlin_times_are_valid_defaults(self):
        rules = self.publish(["08:15", "11:30", "18:45"])
        self.assertEqual([slot.isoformat(timespec="minutes") for slot in rules.slots],
                         ["08:15", "11:30", "18:45"])
        self.assertEqual(rules.timezone, "Europe/Berlin")

    def test_invalid_or_duplicate_defaults_remain_rejected(self):
        for values in ([], ["10:00", "10:00"], ["24:00"],
                       ["%02d:00" % hour for hour in range(13)]):
            with self.subTest(values=values), self.assertRaises(engine.PipelineRunError):
                self.publish(values)


if __name__ == "__main__":
    unittest.main()

"""Read-only preflight and latency reports use durable facts, not inferred success."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config
from core.config import MonitorSchedule
from pipeline import metrics, runtime_status
from pipeline.cli import main

NOW = datetime(2026, 9, 13, 3, tzinfo=timezone.utc)


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        c = config.Config()
        c._d['paths'] = {'archive': str(self.root / 'archive'), 'state': str(self.state)}
        c._d['runtime'] = {'env_file': str(self.root / 'empty.env')}
        (self.root / 'empty.env').write_bytes(b'')
        p = patch.object(config, '_cfg', c)
        p.start()
        self.addCleanup(p.stop)

    def facts(self, rows):
        self.state.mkdir(exist_ok=True)
        (self.state / 'monitoring_facts.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def batch(self, key, requested, elapsed, count=1):
        return [{'event': 'processing_started', 'batch_id': key,
                 'recorded_at': (requested + timedelta(minutes=2)).isoformat(),
                 'requested_at': requested.isoformat()},
                {'event': 'content_ready', 'batch_id': key, 'ready_count': count,
                 'recorded_at': (requested + timedelta(minutes=elapsed)).isoformat()}]

    def test_missing_and_damaged_timing_facts_never_become_zero_latency(self):
        empty = metrics.timings(self.state, NOW)
        self.assertEqual(empty['status'], 'missing')
        self.assertIsNone(empty['p50_minutes'])
        self.facts([{'event': 'content_ready', 'batch_id': [], 'ready_count': '1',
                     'recorded_at': NOW.isoformat()}])
        self.assertEqual(metrics.timings(self.state, NOW)['status'], 'state_unreadable')
        self.assertIsNone(metrics.timings(self.state, NOW)['morning_deadline_rate'])

    def test_percentiles_count_completed_batches_with_discovery_facts_once(self):
        rows = []
        for key, minutes in enumerate((5, 10, 30, 40, 60)):
            rows.extend(self.batch(str(key), NOW - timedelta(hours=2), minutes))
        rows.append(rows[-1])
        rows.extend(self.batch('zero-new', NOW - timedelta(hours=2), 120, count=0))
        rows.extend(self.batch('old', NOW - timedelta(days=40), 100))
        rows.append({'event': 'content_ready', 'batch_id': 'no-discovery',
                     'ready_count': 1, 'recorded_at': NOW.isoformat()})
        self.facts(rows)
        result = metrics.timings(self.state, NOW)
        self.assertEqual(result['sample_count'], 5)
        self.assertEqual((result['p50_minutes'], result['p95_minutes']), (30, 60))
        self.assertEqual(result['morning_sample_count'], 0)
        self.assertIsNone(result['morning_deadline_rate'])

    def test_morning_deadline_uses_next_shanghai_duty_start_and_counts_late_ready(self):
        # UTC 22:50 is Shanghai 06:50; UTC 12:00 is Shanghai 20:00.
        rows = self.batch('morning-on-time', NOW.replace(day=12, hour=22, minute=50), 30)
        rows += self.batch('morning-late', NOW.replace(day=12, hour=23, minute=30), 120)
        rows += self.batch('evening-on-time', NOW.replace(day=12, hour=12), 600)
        self.facts(rows)
        result = metrics.timings(self.state, NOW, schedule=MonitorSchedule())
        self.assertEqual(result['morning_sample_count'], 3)
        self.assertEqual(result['morning_deadline_rate'], .6667)

    def test_source_published_latency_includes_discovery_wait_and_counts_post_once(self):
        first = {'event': 'post_content_ready', 'source_ref': 'facebook:1',
                 'source_created_at': (NOW - timedelta(hours=5)).isoformat(),
                 'recorded_at': (NOW - timedelta(hours=1)).isoformat()}
        self.facts([first, dict(first, recorded_at=NOW.isoformat()),
                    dict(first, source_ref='instagram:2', source_created_at=None)])
        result = metrics.timings(self.state, NOW)
        self.assertEqual(result['published_to_ready']['sample_count'], 1)
        self.assertEqual(result['published_to_ready']['p50_minutes'], 240)
        self.assertIsNone(result['p50_minutes'])

    def test_cli_and_runtime_share_capabilities_without_creating_missing_state(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['preflight', '--json']), 0)
        cli = json.loads(output.getvalue())
        direct = runtime_status.snapshot(NOW)
        self.assertEqual(cli['stages'][4]['checks'], direct['stages'][4]['checks'])
        self.assertTrue(direct['read_only'])
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertIsNone(direct['business']['last_successful_run'])

    def test_alive_process_does_not_fabricate_business_success(self):
        self.state.mkdir()
        (self.state / 'scheduler_heartbeat.json').write_text(json.dumps(
            {'worker': {'pid': 42}, 'tick_at': NOW.isoformat()}), encoding='utf-8')
        with patch.object(runtime_status, 'worker_alive', return_value=True):
            result = runtime_status.snapshot(NOW)
        self.assertTrue(result['process']['alive'])
        self.assertIsNone(result['business']['last_successful_run'])


if __name__ == '__main__':
    unittest.main()

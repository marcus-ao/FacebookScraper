"""Isolated navigation-intent, stop-state and fixed-window monitoring regressions."""
import asyncio
import json
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import Config
from core.integrity import parse_ts
from core.monitor_access import AccessController, AccessDenied
from pipeline.scheduler import Scheduler
from routes import delta


class MonitorAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.now = datetime(2026, 9, 15, 1, tzinfo=timezone.utc)
        self.access = AccessController(self.directory, clock=lambda: self.now)
        self.config = Config()
        self.config._d['paths'].update(state=str(self.directory), archive=str(self.directory / 'archive'))

    def initialize(self):
        return self.access.initialize('isolated fixture verified')

    def reserve(self, scan='scan'):
        return self.access.reserve_homepage('facebook', scan)

    def test_missing_and_corrupt_state_fail_closed(self):
        with self.assertRaises(AccessDenied):
            self.reserve()
        self.access.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(AccessDenied):
            self.access.status()
        with self.assertRaises(AccessDenied):
            self.initialize()

    def test_initialization_imports_legacy_hard_stop_and_failures(self):
        self.directory.joinpath('delta_state.json').write_text(json.dumps({
            'detect_hard_blocked': {'reason': '429', 'platform': 'facebook', 'recorded_at': self.now.isoformat()}, 'facebook': {'consecutive_failures': 3}}), encoding='utf-8')
        status = self.initialize()
        self.assertEqual(status['revision'], 1)
        with self.assertRaises(AccessDenied):
            self.access.check('instagram')
        recovered = self.access.recover(1, 'human reviewed profile', platform='facebook')
        self.assertIsNotNone(recovered['hard_stop'])

    def test_corrupt_nonnull_stop_cannot_be_read_or_migrated_as_open(self):
        for stop in ({}, {'reason': '429'}, {'reason': '429', 'platform': 'facebook', 'at': 'broken'}):
            self.directory.joinpath('delta_state.json').write_text(json.dumps({'detect_hard_blocked': stop}), encoding='utf-8')
            with self.assertRaises(AccessDenied):
                self.initialize()
            self.assertFalse(self.access.path.exists())
        self.directory.joinpath('delta_state.json').unlink()
        status = self.initialize()
        status['hard_stop'] = {}
        self.access.path.write_text(json.dumps(status), encoding='utf-8')
        with self.assertRaises(AccessDenied):
            self.access.check('instagram')

    def test_manual_detail_has_no_homepage_but_retains_detail_quota_and_stop(self):
        self.initialize()
        for i in range(12):
            self.access.reserve_detail('facebook', str(i), 'post', manual=True)
        self.assertFalse(any(row['kind'] == 'homepage' for row in self.access.status()['platforms']['facebook']['intents']))
        with self.assertRaises(AccessDenied):
            self.access.reserve_detail('facebook', '13', 'post', manual=True)
        self.access.outcome('facebook', success=False, hard=True, reason='429')
        with self.assertRaises(AccessDenied):
            self.access.reserve_detail('instagram', 'new', 'post', manual=True)

    def test_initialization_imports_legacy_trends_profile_stop(self):
        self.directory.joinpath('trends_export_state.json').write_text(json.dumps({
            'status': 'blocked', 'reason': 'Google Trends HTTP 429', 'blocked_at': self.now.isoformat()}), encoding='utf-8')
        status = self.initialize()
        self.assertEqual(status['hard_stop']['platform'], 'google_trends')
        with self.assertRaises(AccessDenied):
            self.access.check('facebook')

    def test_corrupt_legacy_cannot_be_initialized_away(self):
        self.directory.joinpath('delta_state.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(AccessDenied):
            self.initialize()
        self.assertFalse(self.access.path.exists())

    def test_intent_survives_restart_and_failed_navigation(self):
        self.initialize()
        first = self.reserve()
        restarted = AccessController(self.directory, clock=lambda: self.now)
        restarted.outcome('facebook', success=False, reason='navigation crashed')
        self.assertEqual(len(restarted.status()['platforms']['facebook']['intents']), 1)
        self.assertEqual(restarted.status()['platforms']['facebook']['next_due_at'],
                         first['platforms']['facebook']['next_due_at'])
        with self.assertRaises(AccessDenied):
            restarted.reserve_homepage('facebook', 'second')

    def test_homepage_rolling_24h_quota_includes_morning_and_failed_intents(self):
        self.initialize()
        for i in range(24):
            self.access.plan_homepage('facebook', self.now)
            self.reserve(str(i))
            self.now += timedelta(minutes=45)
        self.access.plan_homepage('facebook', self.now)
        with self.assertRaisesRegex(AccessDenied, 'quota'):
            self.reserve('overflow')
        self.now += timedelta(hours=7)
        self.reserve('after-expiry')

    def test_detail_requires_homepage_once_per_post_and_three_per_scan(self):
        self.initialize()
        with self.assertRaises(AccessDenied):
            self.access.reserve_detail('facebook', 'missing', 'post')
        self.reserve()
        self.access.reserve_detail('facebook', 'scan', 'one')
        with self.assertRaises(AccessDenied):
            self.access.reserve_detail('facebook', 'scan', 'one')
        self.access.reserve_detail('facebook', 'scan', 'two')
        self.access.reserve_detail('facebook', 'scan', 'three')
        with self.assertRaises(AccessDenied):
            self.access.reserve_detail('facebook', 'scan', 'four')

    def test_detail_rolling_quota_shared_across_controller_instances(self):
        self.initialize()
        for scan in range(4):
            self.access.plan_homepage('facebook', self.now)
            self.reserve(str(scan))
            for post in range(3):
                AccessController(self.directory, clock=lambda: self.now).reserve_detail('facebook', str(scan), str(post))
            self.now += timedelta(hours=1)
        self.access.plan_homepage('facebook', self.now)
        self.reserve('five')
        with self.assertRaisesRegex(AccessDenied, '24h'):
            self.access.reserve_detail('facebook', 'five', 'one')

    def test_three_ordinary_failures_pause_only_platform_and_success_cannot_unpause(self):
        self.initialize()
        for _ in range(3):
            self.access.outcome('facebook', success=False, reason='ordinary failure')
        self.access.outcome('facebook', success=True)
        with self.assertRaises(AccessDenied):
            self.access.check('facebook')
        self.access.check('instagram')

    def test_hard_stop_immediately_blocks_other_platform_and_cas_keeps_quota(self):
        self.initialize()
        self.reserve()
        status = self.access.outcome('facebook', success=False, hard=True, reason='CDN 429')
        with self.assertRaises(AccessDenied):
            self.access.check('instagram')
        with self.assertRaises(AccessDenied):
            self.access.recover(status['revision'] - 1, 'stale UI')
        recovered = self.access.recover(status['revision'], 'profile reviewed')
        self.assertEqual(recovered['platforms']['facebook']['intents'], status['platforms']['facebook']['intents'])
        self.assertEqual(recovered['platforms']['facebook']['next_due_at'], status['platforms']['facebook']['next_due_at'])
        self.assertIsNone(recovered['hard_stop'])

    def test_morning_once_per_shanghai_date_and_hard_45_minute_floor(self):
        self.now = datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)
        self.initialize()
        status = self.access.reserve_homepage('facebook', 'morning', run_kind='reconcile')
        self.assertEqual(parse_ts(status['platforms']['facebook']['next_due_at']), self.now + timedelta(minutes=45))
        self.now += timedelta(minutes=45)
        with self.assertRaises(AccessDenied):
            self.access.reserve_homepage('facebook', 'morning-again', run_kind='reconcile')

    def test_delayed_off_duty_visit_defers_to_first_white_shift(self):
        self.now = datetime(2026, 9, 14, 23, 40, tzinfo=timezone.utc)
        self.initialize()
        with self.assertRaises(AccessDenied):
            self.reserve()
        status = self.access.status()['platforms']['facebook']
        due = parse_ts(status['next_due_at'])
        self.assertGreaterEqual(due, self.now.replace(hour=0, minute=0) + timedelta(days=1))
        self.assertLessEqual(due, self.now.replace(hour=0, minute=15) + timedelta(days=1))
        self.assertEqual(status['intents'], [])

    def test_malformed_morning_marker_or_intent_cannot_repeat_scan(self):
        self.now = datetime(2026, 9, 14, 22, 30, tzinfo=timezone.utc)
        self.initialize()
        valid = self.access.reserve_homepage('facebook', 'morning', run_kind='reconcile')
        self.now += timedelta(minutes=45)
        for marker in ([], 'invalid', None, '2026-09-14', '2026-9-15'):
            damaged = json.loads(json.dumps(valid))
            damaged['platforms']['facebook']['morning_date'] = marker
            self.access.path.write_text(json.dumps(damaged), encoding='utf-8')
            with self.assertRaises(AccessDenied):
                self.access.reserve_homepage('facebook', 'repeat', run_kind='reconcile')
        damaged = json.loads(json.dumps(valid))
        damaged['platforms']['facebook']['intents'][0].pop('run_kind')
        self.access.path.write_text(json.dumps(damaged), encoding='utf-8')
        with self.assertRaises(AccessDenied):
            self.access.status()
        self.access.path.write_text(json.dumps(valid), encoding='utf-8')
        self.access.plan_homepage('facebook', self.now)
        with self.assertRaisesRegex(AccessDenied, 'already consumed'):
            self.access.reserve_homepage('facebook', 'repeat', run_kind='reconcile')

    def test_evening_crossing_keeps_draw_and_restart(self):
        self.now = datetime(2026, 9, 15, 10, 50, tzinfo=timezone.utc)
        self.initialize()
        status = self.reserve()
        drawn = parse_ts(status['platforms']['facebook']['next_due_at'])
        self.assertGreaterEqual(drawn - self.now, timedelta(minutes=45))
        self.assertLessEqual(drawn - self.now, timedelta(minutes=75))
        self.now += timedelta(minutes=11)
        self.assertEqual(AccessController(self.directory).status()['platforms']['facebook']['next_due_at'], drawn.isoformat())

    def test_scheduler_persisted_due_restart_and_global_lock_busy(self):
        self.initialize()
        calls = []
        path = self.directory / 'scheduler.json'
        with Scheduler(path, lambda *args: calls.append(args), config=self.config,
                       clock=lambda: self.now, rng=random.Random(2)) as runner:
            due = runner.state['jobs']['delta:facebook']['next_at']
            with delta.DeltaRunLock(self.directory / 'delta.lock'):
                results = runner.tick()
            self.assertTrue(all(row['exit_code'] == 75 for row in results))
            self.assertEqual(runner.state['jobs']['delta:facebook']['next_at'], due)
        with Scheduler(path, lambda *_: 0, config=self.config, clock=lambda: self.now) as restarted:
            self.assertEqual(restarted.state['jobs']['delta:facebook']['next_at'], due)
        self.assertEqual(calls, [])

    def test_scheduler_morning_merges_nearby_regular(self):
        self.now = datetime(2026, 9, 14, 23, 0, tzinfo=timezone.utc)
        self.initialize()
        calls = []
        def callback(kind, platform):
            calls.append((kind, platform))
            self.access.reserve_homepage(platform, kind + platform, run_kind=kind)
            return 0
        with Scheduler(self.directory / 'scheduler.json', callback, config=self.config,
                       clock=lambda: self.now, rng=random.Random(2)) as runner:
            for job in runner.state['jobs'].values():
                job['next_at'] = self.now.isoformat()
            runner.tick()
            runner.tick()
        self.assertEqual(calls, [('reconcile', 'facebook'), ('reconcile', 'instagram')])
        self.assertEqual(len(self.access.status()['platforms']['facebook']['intents']), 1)

    def test_cli_missing_access_and_reset_cannot_attach(self):
        with patch.object(delta, 'cfg', return_value=self.config), patch.object(delta, 'cdp_ready') as ready:
            self.assertEqual(delta.main(['--platform', 'facebook', '--no-jitter']), 2)
            self.assertEqual(delta.main(['--reset-failures']), 2)
            ready.assert_not_called()

    def test_dry_run_persists_hard_failure_before_next_platform(self):
        self.initialize()
        calls = []
        async def scan(ctx, platform, account, archive, config, **kwargs):
            calls.append(platform)
            config.access.reserve_homepage(platform, config.scan_id)
            raise delta.DeltaBlocked('fixture CDN 429', hard=True)
        dcfg = delta.DeltaConfig(access=self.access)
        state = {}
        with patch.object(delta, 'cfg', return_value=self.config), \
                patch.object(delta, 'attach', AsyncMock(return_value=(AsyncMock(), AsyncMock(), object()))), \
                patch.object(delta, 'delta_once', scan):
            result = asyncio.run(delta._run_due(list(delta.PLATFORMS), dcfg, state,
                                               self.directory / 'delta_state.json', True))
        self.assertEqual(result, 1)
        self.assertEqual(calls, ['facebook'])
        status = self.access.status()
        self.assertIsNotNone(status['hard_stop'])
        self.assertEqual(len(status['platforms']['facebook']['intents']), 1)
        self.assertTrue(self.directory.joinpath('delta_state.json').exists())


if __name__ == '__main__':
    unittest.main()

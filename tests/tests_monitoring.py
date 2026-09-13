"""Stage 1 behavior tests: clocks and callbacks are local; no browser or network."""
import json
import contextlib
import io
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config, MonitorSchedule  # noqa: E402
from core.capture import prune_captures_days  # noqa: E402
from core.integrity import parse_ts  # noqa: E402
from pipeline.scheduler import Scheduler, SchedulerAlreadyRunning  # noqa: E402
from routes import delta, reconcile  # noqa: E402
from core.console import force_utf8  # noqa: E402

force_utf8()


def utc(hour, minute=0):
    return datetime(2026, 9, 12, hour, minute, tzinfo=timezone.utc)


class MonitoringTests(unittest.TestCase):
    def test_active_targets_and_browser_isolation(self):
        c = Config()
        self.assertEqual(c.active_accounts(), ("fa_neakasaofficial", "in_neakasa.global"))
        self.assertEqual(c.detect_debug_port, 9224)
        c.assert_chrome_profiles_isolated()
        c._d["detect"]["port"] = c.publish_debug_port
        with self.assertRaises(SystemExit):
            c.assert_chrome_profiles_isolated()
        c._d["detect"]["port"] = 9224
        c._d["detect"]["profile_dir"] = str(c.profile_dir)
        with self.assertRaises(SystemExit):
            c.assert_chrome_profiles_isolated()

    def test_shanghai_windows_and_quiet_slowdown(self):
        s = MonitorSchedule.load()
        self.assertEqual(s.interval_minutes(utc(0)), 60)
        self.assertEqual(s.interval_minutes(utc(10, 59)), 60)
        self.assertEqual(s.interval_minutes(utc(11)), 180)
        self.assertEqual(s.interval_minutes(utc(23, 59)), 180)
        self.assertEqual(s.interval_minutes(utc(1), quiet=True), 180)
        self.assertEqual(s.minimum_interval_minutes(utc(1)), 45)
        self.assertEqual(s.minimum_interval_minutes(utc(11)), 135)
        with self.assertRaises(ValueError):
            s.interval_minutes(datetime(2026, 9, 12))

    def test_deadline_must_include_jitter_two_scans_and_processing(self):
        s = MonitorSchedule.load()
        self.assertGreater(s.reconcile_deadline_margin_minutes(), 0)
        c = Config()
        c._d["delta"]["reconcile_jitter_min"] = 50
        with self.assertRaisesRegex(ValueError, "截止"):
            MonitorSchedule.load(c)

    def test_reconcile_window_moves_before_full_batch_deadline(self):
        class Latest:
            @staticmethod
            def uniform(_lower, upper):
                return upper

        now = utc(22)  # Shanghai 06:00 on the next local day.
        with tempfile.TemporaryDirectory() as td:
            runner = Scheduler(Path(td) / "scheduler.json", lambda *_args: 0,
                               clock=lambda: now, rng=Latest())
            # Exercise the scheduling guard independently from config validation.
            object.__setattr__(runner.schedule, "processing_budget_min", 40)
            planned = runner.schedule.local(runner._next_reconcile(now))
            duty = datetime.combine(planned.date(), runner.schedule.parse_time(
                runner.schedule.on_duty_window[0]), planned.tzinfo)
            expected_latest = duty - timedelta(minutes=43)
            self.assertLessEqual(planned, expected_latest)

    def test_batch_budget_scales_with_posts_images_and_observed_duration(self):
        from core.monitoring import batch_budget_minutes
        schedule = MonitorSchedule.load()
        baseline = batch_budget_minutes(schedule, 2, {
            "platforms": {"facebook": {"discovered": 1, "image_count": 1}}})
        backlog = batch_budget_minutes(schedule, 2, {
            "platforms": {"facebook": {"discovered": 3, "image_count": 5}}})
        observed = batch_budget_minutes(schedule, 2, {
            "platforms": {}, "last_success_duration_minutes": 180})
        self.assertGreater(backlog, baseline)
        self.assertGreaterEqual(observed, 183)

    def test_reconcile_schedule_uses_last_successful_batch_duration(self):
        class Latest:
            @staticmethod
            def uniform(_lower, upper):
                return upper

        with tempfile.TemporaryDirectory() as td:
            config = Config()
            state = Path(td) / "state"
            state.mkdir()
            config._d["paths"]["state"] = str(state)
            (state / "processing_state.json").write_text(json.dumps({
                "version": 1, "status": "ready", "platforms": {},
                "last_success_duration_minutes": 180,
            }), encoding="utf-8")
            now = utc(18)  # Shanghai 02:00.
            runner = Scheduler(Path(td) / "scheduler.json", lambda *_args: 0,
                               config=config, clock=lambda: now, rng=Latest())
            planned = runner.schedule.local(runner._next_reconcile(now))
            duty = datetime.combine(planned.date(), runner.schedule.parse_time(
                runner.schedule.on_duty_window[0]), planned.tzinfo)
            self.assertLessEqual(planned, duty - timedelta(minutes=183))

    def test_existing_reconcile_draw_tightens_when_batch_history_grows(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            state = Path(td) / "state"
            state.mkdir()
            config._d["paths"]["state"] = str(state)
            now = utc(18)
            with Scheduler(Path(td) / "scheduler.json", lambda *_args: 0,
                           config=config, clock=lambda: now,
                           rng=random.Random(4)) as runner:
                before = parse_ts(runner.state["jobs"]["reconcile:facebook"]["next_at"])
                (state / "processing_state.json").write_text(json.dumps({
                    "version": 1, "status": "ready", "platforms": {},
                    "last_success_duration_minutes": 180,
                }), encoding="utf-8")
                runner._ensure_jobs(now)
                after = parse_ts(runner.state["jobs"]["reconcile:facebook"]["next_at"])
                self.assertLess(after, before)
                local = runner.schedule.local(after)
                duty = datetime.combine(local.date(), runner.schedule.parse_time(
                    runner.schedule.on_duty_window[0]), local.tzinfo)
                self.assertLessEqual(local, duty - timedelta(minutes=183))

    def test_cross_midnight_reconcile_keeps_its_target_morning_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            state = Path(td) / "state"
            state.mkdir()
            config._d["paths"]["state"] = str(state)
            (state / "processing_state.json").write_text(json.dumps({
                "version": 1,
                "status": "ready",
                "platforms": {
                    "facebook": {"discovered": 10, "image_count": 10},
                    "instagram": {"discovered": 10, "image_count": 10},
                },
            }), encoding="utf-8")
            now = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)  # 上海 9 月 12 日 00:00
            with Scheduler(Path(td) / "scheduler.json", lambda *_args: 0,
                           config=config, clock=lambda: now,
                           rng=random.Random(1)) as runner:
                job = runner.state["jobs"]["reconcile:facebook"]
                planned = parse_ts(job["next_at"])
                deadline = parse_ts(job["deadline_at"])

                self.assertEqual(runner.schedule.local(deadline).isoformat(),
                                 "2026-09-13T08:00:00+08:00")
                self.assertEqual(job["budget_minutes"], 503.0)
                self.assertGreaterEqual((deadline - planned).total_seconds() / 60, 503.0)
                self.assertFalse(runner._reconcile_expired(job, planned))

    def test_reconcile_budget_can_move_execution_across_multiple_calendar_days(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            state = Path(td) / "state"
            state.mkdir()
            config._d["paths"]["state"] = str(state)
            (state / "processing_state.json").write_text(json.dumps({
                "version": 1, "status": "ready", "platforms": {},
                "last_success_duration_minutes": 3000,
            }), encoding="utf-8")
            now = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)
            with Scheduler(Path(td) / "scheduler.json", lambda *_args: 0,
                           config=config, clock=lambda: now,
                           rng=random.Random(2)) as runner:
                job = runner.state["jobs"]["reconcile:instagram"]
                planned = parse_ts(job["next_at"])
                deadline = parse_ts(job["deadline_at"])

                self.assertGreaterEqual((deadline - planned).total_seconds() / 60, 3003.0)
                self.assertGreaterEqual((runner.schedule.local(deadline).date()
                                         - runner.schedule.local(now).date()).days, 2)
                self.assertFalse(runner._reconcile_expired(job, planned))

    def test_delta_first_screen_and_window_minimum(self):
        d = delta.DeltaConfig.load()
        self.assertEqual(d.max_scrolls, 0)
        entry = {"last_success": "2026-09-12T00:00:00Z"}
        hours = delta.effective_stale_hours(entry, d, "facebook", now=utc(0, 44))
        self.assertEqual(hours, 0.75)
        self.assertFalse(delta.stale_enough(entry, utc(0, 44), hours)[0])
        self.assertTrue(delta.stale_enough(entry, utc(0, 45), hours)[0])

    def test_reconcile_randomizes_depth_reusing_detect_entry(self):
        seen = []
        with patch.object(delta, "main", side_effect=lambda argv, **kw: seen.append(kw) or 0):
            for seed in range(12):
                self.assertEqual(reconcile.main(["--platform", "instagram"], rng=random.Random(seed)), 0)
        depths = {row["config"].max_scrolls for row in seen}
        self.assertEqual(depths, {2, 3, 4})
        self.assertTrue(all(row["config"].run_kind == "reconcile" for row in seen))

    def test_capture_retention_is_days_not_count_and_preserves_backfill(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            now = utc(1)
            old = base / f"_capture_delta_{int((now - timedelta(days=8)).timestamp())}.json"
            old.write_text("{}")
            recent = []
            for i in range(24):
                p = base / f"_capture_delta_{int((now - timedelta(hours=i)).timestamp())}.json"
                p.write_text("{}")
                recent.append(p)
            backfill = base / "_capture_ancient.json"
            backfill.write_text("{}")
            self.assertEqual(prune_captures_days(base, 7, now=now), 1)
            self.assertFalse(old.exists())
            self.assertTrue(backfill.exists())
            self.assertTrue(all(p.exists() for p in recent))

    def test_schedule_restart_keeps_draws_and_suppresses_missed_burst(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scheduler.json"
            calls = []
            clock = [utc(1)]
            def make(seed):
                return Scheduler(path, lambda kind, platform: calls.append((kind, platform)) or 0,
                                 clock=lambda: clock[0], rng=random.Random(seed))
            first = make(1)
            with first:
                first.tick()
                saved = json.loads(path.read_text())
            with make(500) as resumed:
                self.assertEqual(resumed.snapshot()["jobs"], saved["jobs"])
                clock[0] += timedelta(days=2)
                resumed.tick()
                self.assertLessEqual(len(calls), 2)
                self.assertTrue(all(datetime.fromisoformat(v["next_at"]) > clock[0]
                                    for v in resumed.snapshot()["jobs"].values()))

    def test_scheduler_lock_prevents_second_callback_and_recovers_after_release(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scheduler.json"
            calls = []
            with Scheduler(path, lambda *args: calls.append(args), clock=lambda: utc(1)):
                with self.assertRaises(SchedulerAlreadyRunning):
                    with Scheduler(path, lambda *args: calls.append(args), clock=lambda: utc(1)):
                        self.fail("second instance entered")
            with Scheduler(path, lambda *args: calls.append(args), clock=lambda: utc(1)):
                pass
            self.assertEqual(calls, [])

    def test_one_shared_hard_block_stops_separate_platform_jobs(self):
        args = delta._parse_args(["--platform", "instagram", "--no-jitter"])
        state = {"detect_hard_blocked": {"reason": "facebook 429"},
                 "instagram": delta.blank_entry()}
        with tempfile.TemporaryDirectory() as td, patch.object(delta, "cdp_ready") as browser, \
                patch.object(delta.asyncio, "run", side_effect=lambda coro: (coro.close(), 0)[1]), \
                patch.object(delta, "notify"):
            self.assertEqual(delta._run_locked(args, delta.DeltaConfig.load(),
                                              Path(td) / "state.json", state, ["instagram"]), 2)
            browser.assert_not_called()

    def test_global_target_does_not_inherit_old_tech_success_or_failures(self):
        state = {"instagram": {"last_success": "2026-09-12T00:00:00Z",
                                "consecutive_failures": 3}}
        delta.bind_target_state(state, Config())
        self.assertEqual(state["instagram"]["account"], "neakasa.global")
        self.assertEqual(state["instagram"]["consecutive_failures"], 0)
        self.assertEqual(state["retired_targets"]["instagram:neakasa.tech"]["consecutive_failures"], 3)

    def test_regular_probe_and_reconcile_due_together_do_not_double_visit(self):
        with tempfile.TemporaryDirectory() as td:
            now = utc(23) - timedelta(days=1)  # Shanghai 07:00
            calls = []
            with Scheduler(Path(td) / "s.json", lambda *args: calls.append(args) or 0,
                           clock=lambda: now, rng=random.Random(2)) as scheduler:
                for job in scheduler.state["jobs"].values():
                    job["next_at"] = now.isoformat()
                scheduler.tick()
            self.assertEqual(sorted(calls), [("reconcile", "facebook"), ("reconcile", "instagram")])

    def test_default_cli_scope_freezes_tech_and_explicit_paid_retry_is_blocked(self):
        import translate
        import localize_images
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            config._d["paths"]["archive"] = td
            for name in (*config.active_accounts(), "in_neakasa.tech"):
                folder = Path(td) / name
                folder.mkdir()
                (folder / "manifest.jsonl").write_text("")
            with patch.object(translate, "cfg", return_value=config), \
                    patch.object(translate, "run_translate", return_value=(0, 0)) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(translate.main(["--dry-run"]), 0)
                self.assertEqual({call.args[2].name for call in run.call_args_list}, set(config.active_accounts()))
                run.reset_mock()
                self.assertEqual(translate.main(["--account", "in_neakasa.tech"]), 2)
                run.assert_not_called()
            with patch.object(localize_images, "cfg", return_value=config), \
                    patch.object(localize_images, "select_rows", return_value={}) as select, \
                    contextlib.redirect_stdout(io.StringIO()):
                localize_images.main(["--dry-run"])
                self.assertEqual({path.name for path in select.call_args.args[1]}, set(config.active_accounts()))
                select.reset_mock()
                self.assertEqual(localize_images.main(["--account", "in_neakasa.tech"]), 2)
                select.assert_not_called()

    def test_scheduler_preview_has_no_files_or_callbacks(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "not_created" / "scheduler.json"
            calls = []
            runner = Scheduler(path, lambda *args: calls.append(args), clock=lambda: utc(1))
            preview = runner.preview()
            self.assertEqual(len(preview["jobs"]), 4)
            self.assertEqual(set(preview["posting_distribution"]["coverage_by_archive"]),
                             set(runner.config.active_accounts()))
            self.assertFalse(preview["posting_distribution"]["archive_coverage_complete"])
            self.assertFalse(path.parent.exists())
            self.assertEqual(calls, [])

    def test_monthly_distribution_uses_latest_manifest_rows_in_shanghai(self):
        from pipeline.scheduler import posting_distribution
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            rows = [dict(post_id="p", created_at="2026-08-12T02:00:00Z", text="old",
                         media=[dict(kind="image")]),
                    dict(post_id="p", created_at="2026-08-12T14:00:00Z", text="latest",
                         media=[dict(kind="image")]),
                    dict(post_id="v", created_at="2026-08-12T02:00:00Z", text="video",
                         media=[dict(kind="video")])]
            (path / "manifest.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            summary = posting_distribution([path], MonitorSchedule.load(), utc(1))
            self.assertEqual(summary["month"], "2026-08")
            self.assertEqual(summary["by_hour"][22], 1)
            self.assertEqual(summary["on_duty"], 0)
            self.assertEqual(summary["off_duty"], 1)

    def test_supported_previous_month_window_only_slows_outside_observed_hours(self):
        from pipeline.scheduler import posting_distribution
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            rows = [
                dict(post_id="before", created_at="2026-07-31T15:59:00Z", text="boundary",
                     media=[dict(kind="video")]),
                *[dict(post_id=f"sample-{i}", created_at=f"2026-08-{i + 1:02d}T01:30:00Z",
                       text="image", media=[dict(kind="image")]) for i in range(10)],
                dict(post_id="after", created_at="2026-09-01T00:00:00Z", text="boundary",
                     media=[dict(kind="video")]),
            ]
            (path / "manifest.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            scheduler = Scheduler(Path(td) / "scheduler.json", lambda *args: 0,
                                  clock=lambda: utc(1), rng=random.Random(1))
            summary = posting_distribution([path], scheduler.schedule, utc(1))
            self.assertTrue(summary["archive_coverage_complete"])
            self.assertEqual(summary["sample_count"], 10)
            self.assertTrue(summary["adapted"])
            self.assertGreaterEqual(summary["coverage_ratio"], 0.9)
            self.assertEqual(summary["observed_window"], [9, 10])
            scheduler.state = {"version": 1, "jobs": {}, "posting_distribution": summary}
            self.assertEqual(scheduler._monitor_interval(utc(1)), 60)   # 上海 09:00，在观测窗内
            self.assertEqual(scheduler._monitor_interval(utc(3)), 180)  # 上海 11:00，只允许降频
            self.assertEqual(scheduler._monitor_interval(utc(15)), 180) # 上海 23:00，绝不提频

    def test_incomplete_or_small_previous_month_sample_keeps_configured_plan(self):
        from pipeline.scheduler import posting_distribution
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            rows = [dict(post_id=f"sample-{i}", created_at=f"2026-08-{i + 1:02d}T01:30:00Z",
                         text="image", media=[dict(kind="image")]) for i in range(9)]
            (path / "manifest.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            scheduler = Scheduler(Path(td) / "scheduler.json", lambda *args: 0,
                                  clock=lambda: utc(1), rng=random.Random(1))
            summary = posting_distribution([path], scheduler.schedule, utc(1))
            self.assertFalse(summary["archive_coverage_complete"])
            self.assertFalse(summary["adapted"])
            scheduler.state = {"version": 1, "jobs": {}, "posting_distribution": summary}
            self.assertEqual(scheduler._monitor_interval(utc(1)), 60)
            self.assertEqual(scheduler._monitor_interval(utc(11)), 180)

    def test_detect_launcher_never_uses_backfill_profile(self):
        from tools import start_chrome_detect
        with patch.object(start_chrome_detect, "cdp_ready", return_value=False), \
                patch.object(start_chrome_detect, "port_open", return_value=False), \
                patch.object(start_chrome_detect, "launch", return_value=True) as launch, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(start_chrome_detect.main(), 0)
            self.assertEqual(launch.call_args.kwargs["port"], 9224)
            self.assertEqual(launch.call_args.kwargs["profile"], Config().detect_profile_dir)

    def test_scheduler_template_is_persistent_and_explicitly_opt_in(self):
        import xml.etree.ElementTree as ET
        from tools.schedule import scheduler_xml, NS
        root = ET.fromstring(scheduler_xml(Path("D:/Workspace/project")))
        def query(name):
            return root.find(f".//{{{NS}}}{name}")
        self.assertEqual(query("ExecutionTimeLimit").text, "PT0S")
        self.assertIsNotNone(query("RestartOnFailure"))
        self.assertIsNotNone(query("LogonTrigger"))
        self.assertEqual(query("Arguments").text, "--run")
        self.assertIsNone(query("CalendarTrigger"))

    def test_expired_reconcile_does_not_swallow_evening_probe(self):
        with tempfile.TemporaryDirectory() as td:
            now = utc(12)  # Shanghai 20:00, still the same day as missed reconciliation
            calls = []
            with Scheduler(Path(td) / "s.json", lambda *args: calls.append(args) or 0,
                           clock=lambda: now, rng=random.Random(2)) as scheduler:
                for job in scheduler.state["jobs"].values():
                    job["next_at"] = (utc(23) - timedelta(days=1)).isoformat()
                    if job["kind"] == "reconcile":
                        local = scheduler.schedule.local(now)
                        job["deadline_at"] = local.replace(
                            hour=8, minute=0, second=0, microsecond=0).isoformat()
                scheduler.tick()
            self.assertEqual(sorted(calls), [("delta", "facebook"), ("delta", "instagram")])

    def test_reconcile_expiring_behind_other_platform_keeps_ordinary_probe(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            config._d["paths"]["state"] = td
            config._d["paths"]["archive"] = td
            clock = [utc(23, 31) - timedelta(days=1)]  # Shanghai 07:31; cutoff 07:32.
            path = Path(td) / "scheduler.json"
            calls = []

            def scan(kind, platform):
                saved = json.loads(path.read_text(encoding="utf-8"))
                calls.append((kind, platform, parse_ts(saved["jobs"][f"{kind}:{platform}"]["next_at"])))
                if (kind, platform) == ("reconcile", "facebook"):
                    clock[0] += timedelta(minutes=2)
                return 0

            with Scheduler(path, scan, config=config, clock=lambda: clock[0],
                           rng=random.Random(2)) as scheduler:
                deadline = clock[0].replace(hour=0, minute=0) + timedelta(days=1)
                for job in scheduler.state["jobs"].values():
                    job["next_at"] = (clock[0] - timedelta(minutes=2 if job["kind"] == "delta" else 1)).isoformat()
                    if job["kind"] == "reconcile":
                        job["deadline_at"] = deadline.isoformat()
                results = scheduler.tick()
                self.assertEqual([(kind, platform) for kind, platform, _ in calls],
                                 [("reconcile", "facebook"), ("delta", "instagram")])
                self.assertTrue(all(next_at > clock[0] for _, _, next_at in calls))
                skipped = [result for result in results if result.get("skipped")]
                self.assertEqual(len(skipped), 1)
                self.assertEqual(skipped[0]["platform"], "instagram")
                self.assertEqual(parse_ts(skipped[0]["deadline_at"]), deadline)
                self.assertEqual(skipped[0]["business_date"], "2026-09-12")
                self.assertEqual(parse_ts(skipped[0]["started_at"]), clock[0])
                self.assertEqual(scheduler.tick(), [])
                self.assertEqual(len(calls), 2)

    def test_maintenance_observes_saved_results_even_when_next_tick_is_idle(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            config._d["paths"]["state"] = td
            config._d["paths"]["archive"] = td
            now = utc(12)
            path = Path(td) / "scheduler.json"
            observations = []

            def maintenance(observed_at, results):
                observations.append((observed_at, results, json.loads(path.read_text(encoding="utf-8"))))

            with Scheduler(path, lambda *_args: 0, config=config,
                           clock=lambda: now, rng=random.Random(2), maintenance=maintenance) as scheduler:
                job = scheduler.state["jobs"]["reconcile:facebook"]
                job.update(next_at=(utc(23) - timedelta(days=1)).isoformat(),
                           deadline_at=utc(0).isoformat())
                results = scheduler.tick()
                scheduler.tick()
            self.assertEqual(len(observations), 2)
            self.assertEqual(observations[0][:2], (now, results))
            self.assertTrue(results[0].get("skipped"))
            self.assertGreater(parse_ts(observations[0][2]["jobs"]["reconcile:facebook"]["next_at"]), now)
            self.assertEqual(observations[1][1], [])

    def test_failed_reconcile_does_not_spend_another_probe_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            config._d["paths"]["state"] = td
            config._d["paths"]["archive"] = td
            now = utc(23) - timedelta(days=1)
            calls = []

            def scan(kind, platform):
                calls.append((kind, platform))
                if platform == "facebook":
                    raise RuntimeError("offline scan failure")
                return 2

            with Scheduler(Path(td) / "scheduler.json", scan, config=config,
                           clock=lambda: now, rng=random.Random(2)) as scheduler:
                for job in scheduler.state["jobs"].values():
                    job["next_at"] = now.isoformat()
                results = scheduler.tick()
            self.assertEqual(calls, [("reconcile", "facebook"), ("reconcile", "instagram")])
            self.assertEqual([result["exit_code"] for result in results], [1, 2])

    def test_morning_summary_reports_missed_reconcile_without_new_posts(self):
        from core.monitoring import MonitoringJournal
        with tempfile.TemporaryDirectory() as td:
            journal = MonitoringJournal(Path(td), now=utc(0), inspect_running=False)
            journal.fact("scan_skipped", utc(23, 33) - timedelta(days=1), kind="reconcile",
                         platform="instagram", reason="missed deadline", business_date="2026-09-12")
            journal.fact("scan_skipped", utc(23, 34) - timedelta(days=1), kind="reconcile",
                         platform="facebook", reason="missed deadline", business_date="2026-09-12")
            summary = journal.activity_summary(utc(0))
            self.assertIsNotNone(summary)
            self.assertEqual(summary["discovered"], 0)
            self.assertEqual(summary["reconcile_skipped"], 2)
            self.assertEqual(summary["reconcile_skipped_platforms"], ["facebook", "instagram"])
            self.assertIsNone(journal.activity_summary(utc(0) + timedelta(days=1)))

    def test_missed_reconcile_summary_uses_target_morning_across_midnight(self):
        from core.monitoring import MonitoringJournal
        with tempfile.TemporaryDirectory() as td:
            previous_evening = utc(12) - timedelta(days=1)  # Shanghai Sep 11, 20:00.
            journal = MonitoringJournal(Path(td), now=previous_evening, inspect_running=False)
            journal.fact("scan_skipped", previous_evening, kind="reconcile", platform="instagram",
                         reason="missed shifted deadline", business_date="2026-09-12")
            summary = journal.activity_summary(utc(0))  # The budget belongs to Sep 12, 08:00.
            self.assertIsNotNone(summary)
            self.assertEqual(summary["reconcile_skipped"], 1)
            self.assertIsNone(journal.activity_summary(previous_evening))

    def test_crashed_process_releases_os_lock_and_keeps_random_plan(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scheduler.json"
            code = "\n".join([
                "import os, sys",
                "from pathlib import Path",
                "from pipeline.scheduler import Scheduler",
                "runner = Scheduler(Path(sys.argv[1]), lambda *args: 0)",
                "runner.__enter__()",
                "os._exit(17)",
            ])
            child = subprocess.run([sys.executable, "-c", code, str(path)],
                                   cwd=Path(__file__).resolve().parent.parent,
                                   capture_output=True, timeout=15)
            self.assertEqual(child.returncode, 17, child.stderr.decode(errors="replace"))
            saved = json.loads(path.read_text())
            with Scheduler(path, lambda *args: 0) as recovered:
                self.assertEqual(recovered.snapshot()["jobs"], saved["jobs"])

    def test_cli_preview_never_constructs_runtime_and_once_closes_it(self):
        from pipeline import scheduler, service
        with tempfile.TemporaryDirectory() as td, patch.object(service, "Runtime") as runtime, \
                contextlib.redirect_stdout(io.StringIO()):
            path = Path(td) / "scheduler.json"
            self.assertEqual(scheduler.main(["--preview", "--state", str(path)]), 0)
            runtime.assert_not_called()
            self.assertFalse(path.exists())
            self.assertEqual(scheduler.main(["--run", "--once", "--state", str(path)]), 0)
            runtime.return_value.close.assert_called_once()
            runtime.return_value.scan.assert_not_called()

    def test_nonfinite_timing_and_forged_job_identity_fail_before_callback(self):
        with self.assertRaises(ValueError):
            MonitorSchedule(on_duty_interval_min=float("nan"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scheduler.json"
            with Scheduler(path, lambda *args: 0, clock=lambda: utc(1)):
                pass
            payload = json.loads(path.read_text())
            payload["jobs"]["delta:facebook"]["platform"] = "all"
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                with Scheduler(path, lambda *args: self.fail("invalid job reached callback")):
                    pass

    def test_global_participation_accepts_unknown_partner_and_rejects_invitation_only(self):
        from core.parse import extract, partition_by_owner
        nodes = []
        for pid, owner, coauthors, invited in (
                ("1", "neakasa.global", [], []),
                ("2", "new.partner", ["neakasa.global"], []),
                ("3", "ad.creator", [], ["neakasa.global"])):
            nodes.append(dict(pk=pid, code=f"C{pid}", taken_at=1756000000,
                              user={"username": owner}, caption={"text": "An image caption"},
                              coauthor_producers=[{"username": name} for name in coauthors],
                              invited_coauthor_producers=[{"username": name} for name in invited],
                              image_versions2={"candidates": [{"url": f"https://example.invalid/{pid}.jpg"}]}))
        parsed = extract([{"items": nodes}], "instagram", "neakasa.global", route="delta")
        accepted, rejected = partition_by_owner(parsed, "neakasa.global")
        self.assertEqual({post.post_id for post in accepted}, {"1", "2"})
        self.assertEqual(len(rejected), 1)

    def test_custom_scheduler_path_still_reads_configured_delta_state(self):
        with tempfile.TemporaryDirectory() as td:
            config = Config()
            config._d["paths"]["state"] = str(Path(td) / "actual_state")
            config.state_dir.joinpath("delta_state.json").write_text(json.dumps({
                "facebook": {"account": "neakasaofficial", "first_success": "2026-08-01T00:00:00Z",
                             "last_new_at": "2026-08-01T00:00:00Z"}}))
            runner = Scheduler(Path(td) / "separate_schedule" / "scheduler.json",
                               lambda *args: 0, config=config, clock=lambda: utc(1))
            self.assertTrue(runner._quiet("facebook"))


if __name__ == "__main__":
    unittest.main()

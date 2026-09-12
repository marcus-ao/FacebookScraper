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
            self.assertEqual(len(runner.preview()["jobs"]), 4)
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
                scheduler.tick()
            self.assertEqual(sorted(calls), [("delta", "facebook"), ("delta", "instagram")])

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

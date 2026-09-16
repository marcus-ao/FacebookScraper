"""Persistent Shanghai monitoring draws; missed work is never replayed as a backlog."""
from __future__ import annotations

import argparse
import copy
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import ROOT, Config, MonitorSchedule, cfg
from core.integrity import parse_ts
from core.monitor_access import AccessController, next_homepage_due
from core.paid_model import FileLock, atomic_write_json
from routes import delta, reconcile


class SchedulerAlreadyRunning(RuntimeError):
    pass


def default_callback(kind: str, platform: str) -> int:
    if kind == "reconcile":
        return reconcile.main(["--platform", platform])
    return delta.main(["--platform", platform, "--no-jitter"])


class Scheduler:
    def __init__(self, path: Path, callback=default_callback, *,
                 config: Config | None = None, clock=None, rng=None, maintenance=None):
        self.path = Path(path)
        self.config = config or cfg()
        self.schedule = MonitorSchedule.load(self.config)
        self.callback = callback
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = rng or random.SystemRandom()
        self.maintenance = maintenance
        self.state = {}
        self._held = False
        self.access = AccessController(ROOT / self.config.get("paths", "state", "state"), clock=self.clock, schedule=self.schedule, rng=self.rng)
        self.lock = FileLock(self.path.with_suffix(".lock"), error_type=SchedulerAlreadyRunning,
                             busy_message="监测调度器已在运行")

    def __enter__(self):
        self.config.assert_chrome_profiles_isolated()
        self.lock.__enter__()
        self._held = True
        try:
            self.state = self._load()
            self._ensure_jobs(self.clock())
            self._save()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self._held = False
        return self.lock.__exit__(*args)

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "jobs": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("jobs"), dict):
            raise ValueError("scheduler 状态损坏；保留现场")
        for key, job in data["jobs"].items():
            if (not isinstance(job, dict) or parse_ts(job.get("next_at")) is None
                    or job.get("kind") not in {"delta", "reconcile"}
                    or job.get("platform") not in delta.PLATFORMS
                    or key != f"{job['kind']}:{job['platform']}"):
                raise ValueError("scheduler 作业或下次时刻损坏")
            if job["kind"] == "reconcile" and "deadline_at" in job and parse_ts(job["deadline_at"]) is None:
                raise ValueError("scheduler 晨扫截止时刻损坏")
        return data

    def _save(self):
        if not self._held:
            raise RuntimeError("scheduler 写入必须持锁")
        atomic_write_json(self.path, self.state)

    def _next_delta(self, now, platform):
        return next_homepage_due(now, self.schedule, self.rng)

    def _monitor_interval(self, now):
        return self.schedule.interval_minutes(now)

    def _next_reconcile_plan(self, now):
        local = self.schedule.local(now)
        target = local.date()
        while True:
            base = datetime.combine(target, self.schedule.parse_time(self.schedule.reconcile_at), local.tzinfo)
            earliest = base - timedelta(minutes=self.schedule.reconcile_jitter_min)
            latest = base + timedelta(minutes=self.schedule.reconcile_jitter_min)
            if now <= latest:
                lower = max(now, earliest)
                planned = lower + timedelta(seconds=self.rng.uniform(0, (latest - lower).total_seconds()))
                return planned.astimezone(timezone.utc), latest.astimezone(timezone.utc), 0
            target += timedelta(days=1)

    def _ensure_jobs(self, now):
        jobs = self.state["jobs"]
        expected = {}
        for platform in delta.PLATFORMS:
            for kind in ("delta", "reconcile"):
                key = f"{kind}:{platform}"
                account = self.config["targets"][platform]
                job = jobs.get(key)
                if not job or job.get("account") != account:
                    when = self._next_delta(now, platform) if kind == "delta" else self._next_reconcile_plan(now)[0]
                    job = {"kind": kind, "platform": platform, "account": account, "next_at": when.isoformat()}
                if kind == "reconcile":
                    local = self.schedule.local(parse_ts(job["next_at"]))
                    deadline = datetime.combine(local.date(), self.schedule.parse_time(self.schedule.reconcile_at), local.tzinfo)
                    deadline += timedelta(minutes=self.schedule.reconcile_jitter_min)
                    job["deadline_at"] = deadline.isoformat()
                    job["business_date"] = str(local.date())
                expected[key] = job
        self.state["jobs"] = expected
        # next_at for ordinary visits is only a projection of the authoritative access draw.
        if self.access.path.exists():
            status = self.access.status()
            for platform in delta.PLATFORMS:
                expected[f"delta:{platform}"]["next_at"] = status["platforms"][platform]["next_due_at"]

    def snapshot(self):
        return copy.deepcopy(self.state)

    def preview(self):
        self.config.assert_chrome_profiles_isolated()
        self.state = self._load()
        self._ensure_jobs(self.clock())
        return self.snapshot()

    def _reconcile_expired(self, job, now):
        return now > parse_ts(job["deadline_at"])

    def tick(self):
        if not self._held:
            raise RuntimeError("tick 必须在 Scheduler 上下文内执行")
        self._ensure_jobs(self.clock())
        results = []
        for platform in delta.PLATFORMS:
            now = self.clock()
            regular = self.state["jobs"][f"delta:{platform}"]
            morning = self.state["jobs"][f"reconcile:{platform}"]
            morning_at = parse_ts(morning["next_at"])
            regular_at = parse_ts(regular["next_at"])
            # A nearby ordinary slot belongs to the morning scan; do not spend two visits.
            merge = abs((regular_at - morning_at).total_seconds()) < 45 * 60
            if merge and now <= parse_ts(morning["deadline_at"]):
                regular_at = morning_at
                if self.access.path.exists():
                    try:
                        with delta.DeltaRunLock(self.access.path.with_name("delta.lock")):
                            status = self.access.plan_homepage(platform, morning_at)
                            regular_at = parse_ts(status["platforms"][platform]["next_due_at"])
                    except delta.DeltaRunAlreadyActive:
                        results.append({"kind": "reconcile", "platform": platform, "exit_code": 75})
                        continue
                regular["next_at"] = regular_at.isoformat()
                self._save()
            morning_attempted = False
            candidates = [morning, regular] if morning_at <= regular_at else [regular, morning]
            for job in candidates:
                started = self.clock()
                if parse_ts(job["next_at"]) > started:
                    continue
                kind = job["kind"]
                if kind == "delta" and (morning_attempted or (merge and started <= parse_ts(morning["deadline_at"]))):
                    continue
                expired = kind == "reconcile" and self._reconcile_expired(job, started)
                old_job = copy.deepcopy(job)
                if expired:
                    merge = False
                    result = {"kind": kind, "platform": platform, "exit_code": None,
                              "skipped": "已错过晨扫窗口，不补跑", "business_date": job["business_date"],
                              "started_at": started.isoformat(), "deadline_at": job["deadline_at"]}
                else:
                    before_access = self.access.status() if self.access.path.exists() else None
                    try:
                        # Probe the same global lock before changing the authoritative plan.
                        with delta.DeltaRunLock(self.access.path.with_name("delta.lock")):
                            if self.access.path.exists():
                                self.access.check(platform)
                                if kind == "reconcile":
                                    planned = self.access.plan_homepage(platform, started)
                                    if parse_ts(planned["platforms"][platform]["next_due_at"]) > started:
                                        job["next_at"] = planned["platforms"][platform]["next_due_at"]
                                        self._save()
                                        continue
                        callback_result = self.callback(kind, platform)
                        rc = int(callback_result.get("exit_code", 0) if isinstance(callback_result, dict) else callback_result or 0)
                        result = {"kind": kind, "platform": platform, "exit_code": rc}
                        if kind == "reconcile" and rc != 75:
                            after_access = self.access.status() if self.access.path.exists() else None
                            before_intents = before_access["platforms"][platform]["intents"] if before_access else []
                            entry = after_access["platforms"][platform] if after_access else {}
                            navigated = any(item["kind"] == "homepage" and item["run_kind"] == "reconcile"
                                            for item in entry.get("intents", [])[len(before_intents):])
                            outcome = entry.get("last_outcome", {})
                            reason = (outcome.get("reason") if parse_ts(outcome.get("at")) is not None
                                      and parse_ts(outcome["at"]) >= started else None)
                            if isinstance(callback_result, dict):
                                reason = callback_result.get("skipped") or callback_result.get("error") or reason
                            if rc:
                                result["error"] = reason or f"晨扫回调退出码 {rc}；" + (
                                    "已记录主页访问意图" if navigated else "未记录主页访问意图")
                            elif after_access is not None and not navigated:
                                result["skipped"] = reason or ("晨扫回调未记录主页访问意图；下次访问 "
                                                               + str(entry.get("next_due_at")))
                    except delta.DeltaRunAlreadyActive:
                        result = {"kind": kind, "platform": platform, "exit_code": 75}
                    except Exception as exc:
                        result = {"kind": kind, "platform": platform, "exit_code": 1,
                                  "error": f"{type(exc).__name__}: {exc}"}
                    if result["exit_code"] == 75:
                        job.update(old_job)
                        results.append(result)
                        self._save()
                        continue
                finished = self.clock()
                job.update(last_started=started.isoformat(), last_finished=finished.isoformat(),
                           last_exit_code=result["exit_code"], last_error=result.get("error") or result.get("skipped"))
                if kind == "reconcile":
                    tomorrow = self.schedule.local(finished).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    following, deadline, _ = self._next_reconcile_plan(tomorrow)
                    job.update(next_at=following.isoformat(), deadline_at=deadline.isoformat(),
                               business_date=str(self.schedule.local(following).date()))
                    if not expired:
                        morning_attempted = True
                        regular["next_at"] = self._next_delta(finished, platform).isoformat()
                        merge = False
                else:
                    job["next_at"] = self._next_delta(finished, platform).isoformat()
                if self.access.path.exists():
                    regular["next_at"] = self.access.status()["platforms"][platform]["next_due_at"]
                self.state["last_tick"] = finished.isoformat()
                results.append(result)
                self._save()
        if self.maintenance is not None:
            self.maintenance(self.clock(), results)
        return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="上海窗口监测；默认离线预览")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="常驻运行，可能抓取社媒")
    mode.add_argument("--preview", action="store_true", help="只查看计划，不访问浏览器")
    parser.add_argument("--once", action="store_true", help="配合 --run，仅执行当前到期轮次")
    parser.add_argument("--state", type=Path, help="调度状态文件路径")
    parser.add_argument("--process", action="store_true",
                        help="配合 --run，扫描后处理新内容（会消耗模型 API，仍受预算和审核闸约束）")
    args = parser.parse_args(argv)
    if args.once and not args.run:
        parser.error("--once 必须与 --run 一起显式使用")
    if args.process and not args.run:
        parser.error("--process 必须与 --run 一起显式使用")
    path = args.state or ROOT / cfg().get("paths", "state", "state") / "scheduler.json"
    runner = Scheduler(path)
    runtime = None
    try:
        if not args.run:
            print(json.dumps(runner.preview(), ensure_ascii=False, indent=2))
            return 0
        from pipeline.service import Runtime
        runtime = Runtime(process=args.process, detector=default_callback)
        runner.callback = runtime.scan
        runner.maintenance = runtime.maintenance
        with runner:
            while True:
                for result in runner.tick():
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                now = datetime.now(timezone.utc)
                processing = runtime.start_processing(now)
                if args.once:
                    if args.process and processing is not None:
                        # 首轮投递早于翻译/出图；内容完成后再汇总一次，才会收到本轮待审卡。
                        # 不再 tick/maintenance，避免额外扫描或唤醒；关闭执行器前完整排空投递。
                        processing.result()
                        runtime.await_delivery(timeout=None)
                        runtime.delivery_future = runtime.delivery_executor.submit(
                            runtime._deliver, datetime.now(timezone.utc))
                        runtime.await_delivery(timeout=None)
                    else:
                        runtime.await_delivery()
                    return 0
                time.sleep(30)
    except SchedulerAlreadyRunning as exc:
        print(str(exc))
        return 0
    except (ValueError, OSError) as exc:
        print(f"[!] 调度器未运行：{exc}")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())

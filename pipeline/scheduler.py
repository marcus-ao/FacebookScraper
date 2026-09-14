"""持久化抖动时刻的单实例调度器；默认预览，--run 才访问社媒，不补跑全部错过轮次。"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import ROOT, Config, MonitorSchedule, cfg
from core.integrity import parse_ts
from core.monitoring import (batch_budget_minutes, monitor_interval_minutes,
                             posting_distribution)
from core.paid_model import FileLock, atomic_write_json
from routes import delta, reconcile


class SchedulerAlreadyRunning(RuntimeError):
    pass


def default_callback(kind: str, platform: str) -> int:
    if kind == "reconcile":
        return reconcile.main(["--platform", platform])
    return delta.main(["--platform", platform, "--if-stale", "--no-jitter"])


class Scheduler:
    def __init__(self, path: Path, callback=default_callback, *,
                 config: Config | None = None, clock=None, rng=None,
                 maintenance=None):
        self.path = Path(path)
        self.config = config or cfg()
        self.schedule = MonitorSchedule.load(self.config)
        self.callback = callback
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rng = rng or random.SystemRandom()
        self.maintenance = maintenance
        self.state: dict = {}
        self._held = False
        self.lock = FileLock(self.path.with_suffix(".lock"),
                             error_type=SchedulerAlreadyRunning,
                             busy_message="监测调度器已在运行，本次不启动第二个实例")

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
            raise ValueError("scheduler 状态损坏；保留现场，不自动重置后密集补跑")
        for key, value in data["jobs"].items():
            if not isinstance(value, dict) or parse_ts(value.get("next_at")) is None:
                raise ValueError("scheduler 下次运行时刻损坏；需先检查状态文件")
            if (value.get("kind") not in {"delta", "reconcile"}
                    or value.get("platform") not in delta.PLATFORMS
                    or key != f"{value['kind']}:{value['platform']}"):
                raise ValueError("scheduler 作业身份损坏；不能猜测要访问的平台")
            if (value.get("kind") == "reconcile" and "deadline_at" in value
                    and parse_ts(value.get("deadline_at")) is None):
                raise ValueError("scheduler 早班截止时刻损坏；不能猜测兜底所属业务日")
        return data

    def _save(self):
        if not self._held:
            raise RuntimeError("scheduler 写入必须持锁")
        atomic_write_json(self.path, self.state)

    def _quiet(self, platform: str) -> bool:
        # --state 仅重定位计划；只读 preview 不创建业务状态目录。
        path = ROOT / self.config.get("paths", "state", "state") / "delta_state.json"
        entry = delta.load_state(path).get(platform, {})
        expected = self.config["targets"][platform]
        if entry.get("account", expected) != expected:
            return False
        dcfg = delta.DeltaConfig.load(self.config)
        threshold = dcfg.quiet_days_before_slowdown(platform)
        return threshold > 0 and delta.quiet_days(entry, self.clock()) >= threshold

    def _next_delta(self, now: datetime, platform: str) -> datetime:
        minutes = self._monitor_interval(now, quiet=self._quiet(platform))
        interval = minutes * self.rng.uniform(1 - self.schedule.jitter_ratio,
                                               1 + self.schedule.jitter_ratio)
        proposed = now + timedelta(minutes=interval)
        local = self.schedule.local(now)
        candidate = self.schedule.local(proposed)
        reconcile_base = datetime.combine(candidate.date(), self.schedule.parse_time(self.schedule.reconcile_at), local.tzinfo)
        reconcile_start = reconcile_base - timedelta(minutes=self.schedule.reconcile_jitter_min)
        reconcile_end = reconcile_base + timedelta(minutes=self.schedule.reconcile_jitter_min)
        if reconcile_start <= proposed <= reconcile_end:
            # 兜底本身已经是该时段的一次探测，不在深扫附近额外打首屏。
            proposed = reconcile_end + timedelta(minutes=interval)
        start = datetime.combine(local.date(), self.schedule.parse_time(self.schedule.on_duty_window[0]), local.tzinfo)
        if local >= start:
            start += timedelta(days=1)
        # 离岗跨到上班时，不能把三小时余量一直带进上午。
        if now < start < proposed and not self._quiet(platform):
            proposed = start + timedelta(minutes=self.rng.uniform(
                0, self.schedule.on_duty_interval_min * self.schedule.jitter_ratio))
        return proposed.astimezone(timezone.utc)

    def _monitor_interval(self, now: datetime, *, quiet: bool = False) -> float:
        return monitor_interval_minutes(
            self.schedule, self.state.get("posting_distribution"), now, quiet=quiet)

    def _batch_budget(self) -> float:
        path = ROOT / self.config.get("paths", "state", "state") / "processing_state.json"
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            batch = None
        return batch_budget_minutes(self.schedule, len(delta.PLATFORMS), batch)

    def _reconcile_shift(self, local: datetime, budget: float) -> timedelta:
        base = datetime.combine(local.date(), self.schedule.parse_time(
            self.schedule.reconcile_at), local.tzinfo)
        configured_latest = base + timedelta(minutes=self.schedule.reconcile_jitter_min)
        duty = datetime.combine(local.date(), self.schedule.parse_time(
            self.schedule.on_duty_window[0]), local.tzinfo)
        return max(timedelta(), configured_latest - (duty - timedelta(minutes=budget)))

    def _next_reconcile_plan(self, now: datetime) -> tuple[datetime, datetime, float]:
        local = self.schedule.local(now)
        budget = self._batch_budget()
        target = local.date()
        while True:
            base = datetime.combine(target, self.schedule.parse_time(
                self.schedule.reconcile_at), local.tzinfo)
            earliest = base - timedelta(minutes=self.schedule.reconcile_jitter_min)
            latest = base + timedelta(minutes=self.schedule.reconcile_jitter_min)
            shift = self._reconcile_shift(base, budget)
            if shift:
                # 整体前移随机窗口，保留抖动宽度，同时给两平台扫描和整批处理留足预算。
                earliest -= shift
                latest -= shift
            if now < latest:
                break
            # 大批次可能把窗口前移到前一日甚至更早；逐日寻找尚未错过的目标早班。
            target += timedelta(days=1)
        lower = max(now, earliest)
        planned = lower + timedelta(seconds=self.rng.uniform(
            0, (latest - lower).total_seconds()))
        deadline = datetime.combine(target, self.schedule.parse_time(
            self.schedule.on_duty_window[0]), local.tzinfo)
        return planned.astimezone(timezone.utc), deadline.astimezone(timezone.utc), budget

    def _next_reconcile(self, now: datetime) -> datetime:
        return self._next_reconcile_plan(now)[0]

    def _infer_reconcile_deadline(self, job: dict) -> datetime:
        """Migrate an old draw by finding the first morning its saved budget can serve."""
        planned = parse_ts(job["next_at"])
        baseline = batch_budget_minutes(self.schedule, len(delta.PLATFORMS))
        budget = job.get("budget_minutes", baseline)
        if (not isinstance(budget, (int, float)) or isinstance(budget, bool)
                or not math.isfinite(budget) or budget < baseline):
            budget = baseline
        ready = self.schedule.local(planned + timedelta(minutes=budget))
        deadline = datetime.combine(ready.date(), self.schedule.parse_time(
            self.schedule.on_duty_window[0]), ready.tzinfo)
        if ready > deadline:
            deadline += timedelta(days=1)
        return deadline.astimezone(timezone.utc)

    def _ensure_jobs(self, now):
        sample_month = (self.schedule.local(now).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if self.state.get("posting_distribution", {}).get("month") != sample_month:
            # Missing active archives mean incomplete coverage and prohibit adaptive windows.
            directories = [self.config.archive_dir / name for name in self.config.active_accounts()]
            self.state["posting_distribution"] = posting_distribution(directories, self.schedule, now)
        expected = {}
        for platform in delta.PLATFORMS:
            account = self.config["targets"][platform]
            for kind in ("delta", "reconcile"):
                key = f"{kind}:{platform}"
                current = self.state["jobs"].get(key)
                if not current or current.get("account") != account:
                    if kind == "delta":
                        next_at = self._next_delta(now, platform)
                        deadline = budget = None
                    else:
                        next_at, deadline, budget = self._next_reconcile_plan(now)
                    current = {"kind": kind, "platform": platform, "account": account,
                               "next_at": next_at.isoformat()}
                    if kind == "reconcile":
                        current.update(budget_minutes=budget, deadline_at=deadline.isoformat())
                elif kind == "reconcile" and parse_ts(current.get("deadline_at")) is None:
                    current["deadline_at"] = self._infer_reconcile_deadline(current).isoformat()
                expected[key] = current
        self.state["jobs"] = expected
        self._tighten_reconcile_jobs()

    def _tighten_reconcile_jobs(self):
        """Move existing draws earlier when observed workload raises the batch budget."""
        required = self._batch_budget()
        baseline = batch_budget_minutes(self.schedule, len(delta.PLATFORMS))
        for job in self.state["jobs"].values():
            if job["kind"] != "reconcile":
                continue
            old = job.get("budget_minutes", baseline)
            if not isinstance(old, (int, float)) or isinstance(old, bool) or old < baseline:
                old = baseline
            if required <= old:
                continue
            deadline = self.schedule.local(parse_ts(job["deadline_at"]))
            extra = (self._reconcile_shift(deadline, required)
                     - self._reconcile_shift(deadline, old))
            if extra > timedelta():
                job["next_at"] = (parse_ts(job["next_at"]) - extra).isoformat()
            job["budget_minutes"] = required

    def snapshot(self):
        return copy.deepcopy(self.state)

    def preview(self):
        """读取已有随机计划或在内存里预测，不建锁、不落状态。"""
        self.config.assert_chrome_profiles_isolated()
        self.state = self._load()
        self._ensure_jobs(self.clock())
        return self.snapshot()

    def _reconcile_expired(self, job, now):
        self.schedule.local(now)
        deadline = parse_ts(job.get("deadline_at"))
        if deadline is None:
            deadline = self._infer_reconcile_deadline(job)
        budget = self._batch_budget()
        return now > deadline - timedelta(minutes=budget)

    def tick(self) -> list[dict]:
        if not self._held:
            raise RuntimeError("tick 必须在 Scheduler 上下文内执行")
        now = self.clock()
        self.schedule.local(now)
        self._ensure_jobs(now)
        due = sorted((key for key, job in self.state["jobs"].items()
                      if parse_ts(job["next_at"]) <= now),
                     key=lambda key: (self.state["jobs"][key]["kind"] != "reconcile",
                                      self.state["jobs"][key]["next_at"]))
        reconciled_platforms = set()
        self._save()
        results = []
        for key in due:
            job = self.state["jobs"][key]
            kind, platform = job["kind"], job["platform"]
            started = self.clock()
            if kind == "delta" and platform in reconciled_platforms:
                # Only an attempted deep scan replaces the ordinary probe; expired jobs do not.
                job["next_at"] = self._next_delta(started, platform).isoformat()
                job["coalesced_with"] = "reconcile"
                self._save()
                continue
            if kind == "reconcile":
                local = self.schedule.local(started)
                expired = self._reconcile_expired(job, started)
                deadline = parse_ts(job["deadline_at"])
                # 先推进到明天，避免本轮在 ±窗口内又排一次深扫。
                tomorrow = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                following, following_deadline, following_budget = self._next_reconcile_plan(tomorrow)
            else:
                expired = False
                following = self._next_delta(started, platform)
            job.update(next_at=following.isoformat(), last_started=started.isoformat(),
                       last_exit_code=None, last_error=None)
            if kind == "reconcile":
                job.update(budget_minutes=following_budget,
                           deadline_at=following_deadline.isoformat())
            self._save()
            if expired:
                result = {"kind": kind, "platform": platform, "exit_code": None,
                          "skipped": "已错过早班处理截止线，保留下次兜底",
                          "started_at": started.isoformat(), "deadline_at": deadline.isoformat(),
                          "business_date": str(self.schedule.local(deadline).date())}
                job["last_error"] = result["skipped"]
            else:
                if kind == "reconcile":
                    # A failed deep scan consumes this probe too, avoiding a second failure-budget attempt.
                    reconciled_platforms.add(platform)
                try:
                    rc = self.callback(kind, platform)
                    result = {"kind": kind, "platform": platform, "exit_code": int(rc or 0)}
                    job["last_exit_code"] = result["exit_code"]
                except Exception as exc:
                    job["last_exit_code"] = 1
                    job["last_error"] = f"{type(exc).__name__}: {exc}"
                    result = {"kind": kind, "platform": platform, "exit_code": 1,
                              "error": job["last_error"]}
            finished = self.clock()
            job["last_finished"] = finished.isoformat()
            # 回调耗时跨过下一轮时也不立刻追补；按完成时刻重新排下轮。
            if parse_ts(job["next_at"]) <= finished:
                if kind == "delta":
                    job["next_at"] = self._next_delta(finished, platform).isoformat()
                else:
                    following, following_deadline, following_budget = self._next_reconcile_plan(finished)
                    job.update(next_at=following.isoformat(), budget_minutes=following_budget,
                               deadline_at=following_deadline.isoformat())
            self.state["last_tick"] = finished.isoformat()
            self._save()
            results.append(result)
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
                runtime.start_processing(now)
                if args.once:
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

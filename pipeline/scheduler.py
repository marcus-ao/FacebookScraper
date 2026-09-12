"""单常驻监测调度器。默认仅预览；--run 才可能访问目标社媒。

随机时刻先落盘再执行，因此重启不会重抽、也不会连续补跑睡眠期间每个轮次。
抓取仍受 delta.lock 和失败预算约束。回调可由应用组装后续处理，本模块不调用模型。
"""
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
from core.paid_model import FileLock, atomic_write_json
from core.store import account_dirs
from routes import delta, reconcile


class SchedulerAlreadyRunning(RuntimeError):
    pass


def default_callback(kind: str, platform: str) -> int:
    if kind == "reconcile":
        return reconcile.main(["--platform", platform])
    return delta.main(["--platform", platform, "--if-stale", "--no-jitter"])


def posting_distribution(directories, schedule: MonitorSchedule, now: datetime) -> dict:
    """上个完整自然月的静态图文发布时间；由追加式 manifest 最新版本重算。

    作息仍取配置，不根据美国发帖时间猜运营人员的上班时间。该统计供 preflight
    检查源团队换作息后的余量，缺记录/坏行会显式计数，不能冒充完整样本。
    """
    month = (schedule.local(now).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    summary = {"month": month, "by_hour": [0] * 24, "on_duty": 0,
               "off_duty": 0, "unreadable": 0, "skipped": 0,
               "reconcile_margin_min": schedule.reconcile_deadline_margin_minutes()}
    for directory in directories:
        try:
            lines = (Path(directory) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            summary["unreadable"] += 1
            continue
        rows = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not row.get("post_id"):
                    raise ValueError("not a post")
                rows[str(row["post_id"])] = row
            except (ValueError, TypeError):
                summary["unreadable"] += 1
        for row in rows.values():
            created = parse_ts(row.get("created_at"))
            if created is None or schedule.local(created).strftime("%Y-%m") != month:
                continue
            media = row.get("media")
            if (not isinstance(media, list) or not media or not (row.get("text") or "").strip()
                    or any(not isinstance(item, dict) or item.get("kind") != "image" for item in media)):
                summary["skipped"] += 1
                continue
            local = schedule.local(created)
            summary["by_hour"][local.hour] += 1
            summary["on_duty" if schedule.is_on_duty(created) else "off_duty"] += 1
    return summary


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
        return data

    def _save(self):
        if not self._held:
            raise RuntimeError("scheduler 写入必须持锁")
        atomic_write_json(self.path, self.state)

    def _quiet(self, platform: str) -> bool:
        # --state 只重定位调度计划；实际抓取状态仍由统一 [paths].state 定位。
        # 这里直接派生只读路径，避免 preview 调用 state_dir 属性创建目录。
        path = ROOT / self.config.get("paths", "state", "state") / "delta_state.json"
        entry = delta.load_state(path).get(platform, {})
        expected = self.config["targets"][platform]
        if entry.get("account", expected) != expected:
            return False
        dcfg = delta.DeltaConfig.load(self.config)
        threshold = dcfg.quiet_days_before_slowdown(platform)
        return threshold > 0 and delta.quiet_days(entry, self.clock()) >= threshold

    def _next_delta(self, now: datetime, platform: str) -> datetime:
        minutes = self.schedule.interval_minutes(now, quiet=self._quiet(platform))
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

    def _next_reconcile(self, now: datetime) -> datetime:
        local = self.schedule.local(now)
        base = datetime.combine(local.date(), self.schedule.parse_time(self.schedule.reconcile_at), local.tzinfo)
        # 已过窗口就只安排明天；尚在窗口时从剩余区间抽取，不突然追补昨天。
        earliest = base - timedelta(minutes=self.schedule.reconcile_jitter_min)
        latest = base + timedelta(minutes=self.schedule.reconcile_jitter_min)
        if now >= latest:
            earliest += timedelta(days=1)
            latest += timedelta(days=1)
        lower = max(now, earliest)
        return (lower + timedelta(seconds=self.rng.uniform(
            0, (latest - lower).total_seconds()))).astimezone(timezone.utc)

    def _ensure_jobs(self, now):
        expected = {}
        for platform in delta.PLATFORMS:
            account = self.config["targets"][platform]
            for kind in ("delta", "reconcile"):
                key = f"{kind}:{platform}"
                current = self.state["jobs"].get(key)
                if not current or current.get("account") != account:
                    next_at = self._next_delta(now, platform) if kind == "delta" else self._next_reconcile(now)
                    current = {"kind": kind, "platform": platform, "account": account,
                               "next_at": next_at.isoformat()}
                expected[key] = current
        self.state["jobs"] = expected
        sample_month = (self.schedule.local(now).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if self.state.get("posting_distribution", {}).get("month") != sample_month:
            active = set(self.config.active_accounts())
            directories = [p for p in account_dirs(self.config.archive_dir) if p.name in active]
            self.state["posting_distribution"] = posting_distribution(directories, self.schedule, now)

    def snapshot(self):
        return copy.deepcopy(self.state)

    def preview(self):
        """读取已有随机计划或在内存里预测，不建锁、不落状态。"""
        self.config.assert_chrome_profiles_isolated()
        self.state = self._load()
        self._ensure_jobs(self.clock())
        return self.snapshot()

    def _reconcile_expired(self, job, now):
        local = self.schedule.local(now)
        deadline = datetime.combine(local.date(), self.schedule.parse_time(
            self.schedule.on_duty_window[0]), local.tzinfo)
        budget = self.schedule.processing_budget_min + 2 * self.schedule.reconcile_max_session_seconds / 60
        planned_day = self.schedule.local(parse_ts(job["next_at"])).date()
        return local.date() != planned_day or now > deadline - timedelta(minutes=budget)

    def tick(self) -> list[dict]:
        if not self._held:
            raise RuntimeError("tick 必须在 Scheduler 上下文内执行")
        now = self.clock()
        self.schedule.local(now)
        self._ensure_jobs(now)
        due = sorted((key for key, job in self.state["jobs"].items()
                      if parse_ts(job["next_at"]) <= now),
                     key=lambda key: self.state["jobs"][key]["next_at"])
        reconciled_platforms = {self.state["jobs"][key]["platform"] for key in due
                               if self.state["jobs"][key]["kind"] == "reconcile"
                               and not self._reconcile_expired(self.state["jobs"][key], now)}
        for key in list(due):
            job = self.state["jobs"][key]
            if job["kind"] == "delta" and job["platform"] in reconciled_platforms:
                job["next_at"] = self._next_delta(now, job["platform"]).isoformat()
                job["coalesced_with"] = "reconcile"
                due.remove(key)
        self._save()
        results = []
        for key in due:
            job = self.state["jobs"][key]
            kind, platform = job["kind"], job["platform"]
            started = self.clock()
            if kind == "reconcile":
                local = self.schedule.local(started)
                expired = self._reconcile_expired(job, started)
                # 先推进到明天，避免本轮在 ±窗口内又排一次深扫。
                tomorrow = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                following = self._next_reconcile(tomorrow)
            else:
                expired = False
                following = self._next_delta(started, platform)
            job.update(next_at=following.isoformat(), last_started=started.isoformat(),
                       last_exit_code=None, last_error=None)
            self._save()
            if expired:
                result = {"kind": kind, "platform": platform, "exit_code": None,
                          "skipped": "已错过早班处理截止线，保留下次兜底"}
                job["last_error"] = result["skipped"]
            else:
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
                job["next_at"] = (self._next_delta(finished, platform) if kind == "delta"
                                  else self._next_reconcile(finished)).isoformat()
            self.state["last_tick"] = finished.isoformat()
            self._save()
            results.append(result)
        if self.maintenance is not None:
            self.maintenance(self.clock())
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

"""Monitoring analysis and durable scan/processing facts.

This module is intentionally browser- and model-free.  The scheduler may use its
previous-month analysis only to reduce polling outside a supported observed
window; it can never make a configured interval shorter.
"""
from __future__ import annotations

from core import paid_requests
import json
import hashlib
import math
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from core.config import MonitorSchedule
from core.integrity import parse_ts
from core.paid_model import FileLock, FileLockBusy, append_jsonl, atomic_write_json
from core.process_identity import current_worker, worker_alive


MIN_POSTING_SAMPLES = 10
TARGET_POSTING_COVERAGE = 0.90
SKIP_REASONS = ("video", "mixed_media", "no_media", "no_text")


def _month_bounds(schedule: MonitorSchedule, now: datetime) -> tuple[datetime, datetime, str]:
    local = schedule.local(now)
    current = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (current - timedelta(days=1)).replace(day=1)
    return previous, current, previous.strftime("%Y-%m")


def _static_image(row: dict) -> bool:
    media = row.get("media")
    return (isinstance(media, list) and bool(media) and bool(str(row.get("text") or "").strip())
            and all(isinstance(item, dict) and item.get("kind") == "image" for item in media))


def _skip_reason(row: dict) -> str | None:
    media = row.get("media")
    if not isinstance(media, list) or not media:
        return "no_media"
    kinds = {item.get("kind") for item in media if isinstance(item, dict)}
    if "video" in kinds:
        return "mixed_media" if "image" in kinds else "video"
    if not str(row.get("text") or "").strip():
        return "no_text"
    return None


def _observed_window(by_hour: list[int], sample_count: int,
                     target: float = TARGET_POSTING_COVERAGE) -> tuple[list[int] | None, float]:
    """Return the shortest circular, end-exclusive hour window covering target samples."""
    if sample_count <= 0:
        return None, 0.0
    wanted = max(1, math.ceil(sample_count * target))
    best = None
    for width in range(1, 25):
        for start in range(24):
            covered = sum(by_hour[(start + offset) % 24] for offset in range(width))
            if covered < wanted:
                continue
            candidate = (width, -covered, start, covered)
            if best is None or candidate < best:
                best = candidate
        if best is not None:
            break
    if best is None:
        return None, 0.0
    width, _negative, start, covered = best
    return [start, (start + width) % 24], covered / sample_count


def posting_distribution(directories, schedule: MonitorSchedule, now: datetime) -> dict:
    """Analyze the previous complete Shanghai month from latest manifest rows.

    Coverage is conservative: every active archive must contain a dated row on
    both sides of the month.  Without that evidence, absence inside the month is
    not treated as an observed posting pattern.
    """
    month_start, month_end, month = _month_bounds(schedule, now)
    summary = {
        "month": month, "by_hour": [0] * 24, "on_duty": 0, "off_duty": 0,
        "unreadable": 0, "skipped": 0, "skipped_by_reason": dict.fromkeys(SKIP_REASONS, 0),
        "sample_count": 0, "archive_coverage_complete": False,
        "coverage_by_archive": {}, "observed_window": None, "coverage_ratio": 0.0,
        "adapted": False, "minimum_samples": MIN_POSTING_SAMPLES,
        "target_coverage": TARGET_POSTING_COVERAGE,
        "reconcile_margin_min": schedule.reconcile_deadline_margin_minutes(),
    }
    coverage = []
    for directory in [Path(value) for value in directories]:
        try:
            lines = (directory / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            summary["unreadable"] += 1
            summary["coverage_by_archive"][directory.name] = False
            coverage.append(False)
            continue
        latest = {}
        dates = []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not row.get("post_id"):
                    raise ValueError("not a post")
                created = parse_ts(row.get("created_at"))
                if created is not None:
                    dates.append(created)
                latest[str(row["post_id"])] = row
            except (ValueError, TypeError):
                summary["unreadable"] += 1
        complete = bool(dates and min(dates) <= month_start.astimezone(timezone.utc)
                        and max(dates) >= month_end.astimezone(timezone.utc))
        summary["coverage_by_archive"][directory.name] = complete
        coverage.append(complete)
        for row in latest.values():
            created = parse_ts(row.get("created_at"))
            if created is None:
                continue
            local = schedule.local(created)
            if not month_start <= local < month_end:
                continue
            if not _static_image(row):
                reason = _skip_reason(row)
                if reason:
                    summary["skipped_by_reason"][reason] += 1
                    summary["skipped"] += 1
                continue
            summary["by_hour"][local.hour] += 1
            summary["sample_count"] += 1
            summary["on_duty" if schedule.is_on_duty(created) else "off_duty"] += 1
    summary["archive_coverage_complete"] = bool(coverage) and all(coverage)
    window, ratio = _observed_window(summary["by_hour"], summary["sample_count"])
    summary["observed_window"], summary["coverage_ratio"] = window, ratio
    summary["adapted"] = bool(summary["archive_coverage_complete"]
                              and summary["sample_count"] >= MIN_POSTING_SAMPLES
                              and ratio >= TARGET_POSTING_COVERAGE and window is not None)
    return summary


def _in_hour_window(hour: int, window: list[int] | tuple[int, int]) -> bool:
    start, end = window
    return start <= hour < end if start < end else hour >= start or hour < end


def monitor_interval_minutes(schedule: MonitorSchedule, distribution: dict | None,
                             now: datetime, *, quiet: bool = False) -> float:
    """Apply the evidence window as a slowdown-only overlay."""
    base = schedule.interval_minutes(now, quiet=quiet)
    if (quiet or not isinstance(distribution, dict) or not distribution.get("adapted")
            or not isinstance(distribution.get("observed_window"), list)):
        return base
    if _in_hour_window(schedule.local(now).hour, distribution["observed_window"]):
        return base
    return max(base, schedule.off_duty_interval_min)


def batch_budget_minutes(schedule: MonitorSchedule, platform_count: int,
                         batch: dict | None = None) -> float:
    """Budget both scans and a whole content batch.

    The configured processing budget represents one post with one image.  More
    posts/images expand it proportionally, while the last successful observed
    duration remains a conservative lower bound for future batches.
    """
    if platform_count < 1:
        raise ValueError("platform_count must be positive")
    platforms = batch.get("platforms") if isinstance(batch, dict) else {}
    platforms = platforms if isinstance(platforms, dict) else {}

    def count(value) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    posts = sum(count(item.get("discovered")) for item in platforms.values()
                if isinstance(item, dict))
    images = sum(count(item.get("image_count")) for item in platforms.values()
                 if isinstance(item, dict))
    posts = max(1, posts)
    images = max(posts, images)
    scaled = schedule.processing_budget_min * (posts + images) / 2
    observed = batch.get("last_success_duration_minutes", 0) if isinstance(batch, dict) else 0
    if (not isinstance(observed, (int, float)) or isinstance(observed, bool)
            or not math.isfinite(observed) or observed < 0):
        observed = 0
    processing = max(scaled, float(observed))
    return processing + platform_count * schedule.reconcile_max_session_seconds / 60


class MonitoringJournal:
    """Append scan facts and maintain one durable processing-batch marker."""

    def __init__(self, state_dir: Path, *, now: datetime, inspect_running: bool = True):
        self.state_dir = Path(state_dir)
        self.facts_path = self.state_dir / "monitoring_facts.jsonl"
        self.processing_path = self.state_dir / "processing_state.json"
        self.processing_lock_path = self.state_dir / "processing_state.lock"
        self._thread_lock = threading.RLock()
        if not inspect_running:
            return
        event = None
        with self._processing_lock():
            state = self._read_processing_status()
            if state.get("status") == "running":
                alive = worker_alive(state.get("owner"))
                if alive is False:
                    state.update(status="interrupted", interrupted_at=now.isoformat(),
                                 requires_manual_recovery=True,
                                 recovery_reason="原处理进程已退出；可能已经产生付费请求，未自动重放")
                    event = "processing_interrupted"
                elif alive is None:
                    state.update(status="uncertain", inspected_at=now.isoformat(),
                                 requires_manual_recovery=True,
                                 recovery_reason="无法确认原处理进程身份；核对付费请求前不自动重放")
                    event = "processing_recovery_unknown"
                if event is not None:
                    atomic_write_json(self.processing_path, state)
        if event is not None:
            self.fact(event, now, batch_id=state.get("batch_id"))

    @contextmanager
    def _processing_lock(self):
        """Serialize read-modify-write state transitions across threads and processes."""
        with self._thread_lock:
            while True:
                lock = FileLock(self.processing_lock_path,
                                busy_message="内容处理状态正由另一个进程更新")
                try:
                    lock.__enter__()
                    break
                except FileLockBusy:
                    time.sleep(0.01)
            try:
                yield
            finally:
                lock.__exit__(None, None, None)

    def fact(self, event: str, now: datetime, **details) -> dict:
        row = {"event": event, "recorded_at": now.astimezone(timezone.utc).isoformat(), **details}
        append_jsonl(self.facts_path, row)
        return row

    def _read_processing_status(self) -> dict:
        try:
            data = json.loads(self.processing_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "status": "idle", "platforms": {}}
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("platforms"), dict):
            raise ValueError("内容处理标记损坏；保留现场，不自动运行付费步骤")
        successor = data.get("next_batch")
        if successor is not None and (not isinstance(successor, dict)
                or successor.get("version") != 1 or successor.get("status") != "pending"
                or not isinstance(successor.get("platforms"), dict)):
            raise ValueError("后继内容处理标记损坏；保留现场，不自动运行付费步骤")
        return data

    def processing_status(self) -> dict:
        return self._read_processing_status()

    @staticmethod
    def _merge_request(batch: dict, now: datetime, platform: str, kind: str,
                       discovered: int, skipped: dict[str, int], image_count: int | None) -> None:
        discovered = max(0, int(discovered))
        images = max(discovered, int(image_count or 0))
        incoming_skips = {key: max(0, int(skipped.get(key, 0))) for key in SKIP_REASONS}
        previous = batch["platforms"].get(platform)
        if not isinstance(previous, dict):
            batch["platforms"][platform] = {
                "kind": kind, "kinds": [kind], "discovered": discovered,
                "image_count": images, "skipped": incoming_skips,
                "scanned_at": now.isoformat(),
            }
            return
        kinds = set(previous.get("kinds") or [previous.get("kind")])
        kinds.discard(None)
        kinds.add(kind)
        previous.update(
            kind=kind,
            kinds=sorted(kinds),
            discovered=max(0, int(previous.get("discovered", 0))) + discovered,
            image_count=max(0, int(previous.get("image_count", 0))) + images,
            scanned_at=now.isoformat(),
        )
        old_skips = previous.get("skipped") if isinstance(previous.get("skipped"), dict) else {}
        previous["skipped"] = {
            key: max(0, int(old_skips.get(key, 0))) + incoming_skips[key]
            for key in SKIP_REASONS
        }

    @staticmethod
    def _new_batch(now: datetime, history: dict | None = None) -> dict:
        return {"version": 1, "batch_id": uuid4().hex, "status": "pending",
                "requested_at": now.isoformat(), "platforms": {}, 'operation_tracked': True, **(history or {})}

    def request(self, now: datetime, platform: str, kind: str, discovered: int,
                skipped: dict[str, int], *, image_count: int | None = None) -> dict:
        with self._processing_lock():
            current = self._read_processing_status()
            if current.get("status") in {"running", "interrupted", "uncertain"}:
                pending = current.get("next_batch")
                if not isinstance(pending, dict) or pending.get("status") != "pending":
                    pending = self._new_batch(now)
                    current["next_batch"] = pending
                self._merge_request(pending, now, platform, kind, discovered, skipped, image_count)
                atomic_write_json(self.processing_path, current)
                return current
            if current.get("status") != "pending":
                history = {key: current[key] for key in (
                    "last_success_duration_minutes", "last_success_posts", "last_success_images")
                           if key in current}
                current = current.get('next_batch') or self._new_batch(now, history)
            self._merge_request(current, now, platform, kind, discovered, skipped, image_count)
            atomic_write_json(self.processing_path, current)
            return current

    def claim(self, now: datetime) -> dict | None:
        with self._processing_lock():
            current = self._read_processing_status()
            if current.get("status") != "pending":
                return None
            current.update(status="running", started_at=now.isoformat(), owner=current_worker(),
                           requires_manual_recovery=False)
            atomic_write_json(self.processing_path, current)
        self.fact("processing_started", now, batch_id=current["batch_id"],
                  platforms=sorted(current["platforms"]), requested_at=current.get('requested_at'))
        return current

    def finish(self, batch: dict, now: datetime, *, code: int | None,
               ready_count: int = 0, error: str | None = None) -> dict:
        with self._processing_lock():
            current = self._read_processing_status()
            if (current.get("batch_id") != batch.get("batch_id")
                    or current.get("status") != "running"):
                raise ValueError("内容处理标记已变化；不能覆盖恢复决定")
            if error is not None:
                status, event, manual = "uncertain", "processing_uncertain", True
            elif code:
                status, event, manual = "failed", "processing_failed", False
            else:
                status, event, manual = "ready", "content_ready", False
            current.update(status=status, finished_at=now.isoformat(), exit_code=code,
                           ready_count=int(ready_count), requires_manual_recovery=manual, worker_finished=True)
            if status == "ready":
                started = parse_ts(current.get("started_at"))
                duration = max(0.0, (now - started).total_seconds() / 60) if started else 0.0
                current.update(
                    last_success_duration_minutes=duration,
                    last_success_posts=sum(int(item.get("discovered", 0))
                                           for item in current["platforms"].values()),
                    last_success_images=sum(int(item.get("image_count", 0))
                                            for item in current["platforms"].values()))
            if error:
                current["error"] = error
            successor = current.get("next_batch")
            if status != "uncertain" and isinstance(successor, dict):
                for key in ("last_success_duration_minutes", "last_success_posts",
                            "last_success_images"):
                    if key in current:
                        successor[key] = current[key]
                current = successor
            atomic_write_json(self.processing_path, current)
        self.fact(event, now, batch_id=batch["batch_id"], exit_code=code,
                  ready_count=int(ready_count))
        return current

    @staticmethod
    def revision(row):
        return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def recover(self, *, batch_id, expected_revision, now, outputs_reviewed=False):
        """Close a stopped batch after reconciliation. Never enqueue paid work."""
        with self._processing_lock():
            current = self._read_processing_status()
            if current.get('batch_id') != batch_id or self.revision(current) != expected_revision:
                raise ValueError('处理批次已有变化，请刷新后核对')
            if current.get('status') not in {'running', 'interrupted', 'uncertain'}:
                return current
            if not current.get('worker_finished') and worker_alive(current.get('owner')) is not False:
                raise ValueError('尚不能确认处理已退出，不关闭批次')
            if not current.get('operation_tracked'):
                raise ValueError('旧批次缺少请求关联，请先人工核对历史账本，不能自动恢复')
            events = [e for e in paid_requests.load_events(self.state_dir) if e.get('operation_id') == batch_id]
            requests = {e['request_id']: e for e in events}
            usage = {e['request_id'] for e in events if e['event'] == paid_requests.EVENT_USAGE}
            if any(e['event'] in paid_requests._GLOBAL_BLOCKING or
                   e['event'] == paid_requests.EVENT_ACCEPTED and e['request_id'] not in usage for e in requests.values()):
                raise ValueError('付费请求或用量尚未核对，保持阻塞；请先核账')
            if requests and outputs_reviewed is not True:
                raise ValueError('此批次已经调用模型，请先核对已保存的文案与图片')
            self.fact('processing_recovered', now, batch_id=batch_id, paid_request_ids=list(requests),
                      outputs_reviewed=outputs_reviewed is True, actor=None)
            current.update(status='failed', requires_manual_recovery=False, recovered_at=now.isoformat(),
                           recovery_reason='中断批次已关闭；保留已生成内容，新请求另行受理')
            atomic_write_json(self.processing_path, current)
            return current

    def activity_summary(self, now: datetime) -> dict | None:
        target = MonitorSchedule.local(now).date()
        discovered = reconcile_discovered = 0
        skipped = dict.fromkeys(SKIP_REASONS, 0)
        try:
            lines = self.facts_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        for line in lines:
            if not line.strip():
                continue
            row = json.loads(line)
            recorded = parse_ts(row.get("recorded_at"))
            if recorded is None or MonitorSchedule.local(recorded).date() != target:
                continue
            if row.get("event") == "content_discovered":
                count = int(row.get("count", 0))
                discovered += count
                if row.get("kind") == "reconcile":
                    reconcile_discovered += count
            if row.get("event") == "scan_finished":
                for key in SKIP_REASONS:
                    skipped[key] += int((row.get("skipped") or {}).get(key, 0))
        if discovered == 0 and not any(skipped.values()):
            return None
        return {"date": str(target), "discovered": discovered,
                "reconcile_discovered": reconcile_discovered, "skipped": skipped}

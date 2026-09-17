"""Durable scan/processing facts and display-only posting statistics."""
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
# ⚠️ 中文标签只维护这一份。晨报曾另抄一套键（mixed / text_only / …），和 SKIP_REASONS 对不上，
# 结果四类里三类在卡片上显示成英文键名，而 get(key, key) 的兜底让它一直没报错。
SKIP_LABELS = {"video": "视频", "mixed_media": "图文混合", "no_media": "无媒体", "no_text": "无正文"}
# 抓取路径逐篇记下的三件事。"发现"与"落档"必须分开，否则"发现了但没抓下来"看不出来。
SCAN_POST_EVENTS = ("post_discovered", "post_captured", "post_capture_incomplete")


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
    """Analyze the previous complete Shanghai month; require coverage on both sides for every active archive."""
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


def batch_budget_minutes(schedule: MonitorSchedule, platform_count: int,
                         batch: dict | None = None) -> float:
    """Budget both scans and the whole batch, accounting for post/image counts and observed duration."""
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


_PROCESSING_HISTORY_FIELDS = (
    "last_success_duration_minutes", "last_success_posts", "last_success_images",
    "capture_event_ids",
)


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
        receipts = data.get("capture_event_ids", [])
        if not isinstance(receipts, list) or any(not isinstance(item, str) for item in receipts):
            raise ValueError("采集结果的处理回执损坏；保留现场，不自动重放")
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
    def _new_batch(now: datetime) -> dict:
        return {"version": 1, "batch_id": uuid4().hex, "status": "pending",
                "requested_at": now.isoformat(), "platforms": {}, 'operation_tracked': True}

    def request(self, now: datetime, platform: str, kind: str, discovered: int,
                skipped: dict[str, int], *, image_count: int | None = None,
                capture_event_id: str | None = None) -> dict:
        with self._processing_lock():
            current = self._read_processing_status()
            if capture_event_id and capture_event_id in current.get("capture_event_ids", []):
                return current
            if current.get("status") in {"running", "interrupted", "uncertain"}:
                pending = current.get("next_batch")
                if not isinstance(pending, dict) or pending.get("status") != "pending":
                    pending = self._new_batch(now)
                    current["next_batch"] = pending
                self._merge_request(pending, now, platform, kind, discovered, skipped, image_count)
            else:
                if current.get("status") != "pending":
                    history = {key: current[key] for key in _PROCESSING_HISTORY_FIELDS if key in current}
                    current = current.get('next_batch') or self._new_batch(now)
                    current.update(history)
                self._merge_request(current, now, platform, kind, discovered, skipped, image_count)
            if capture_event_id:
                # 与入队原子保存；飞书确认、进程重启或重复维护不能重新受理同一结果。
                current.setdefault("capture_event_ids", []).append(capture_event_id)
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
                for key in _PROCESSING_HISTORY_FIELDS:
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

    def scan_posts(self, since: datetime, platform: str) -> list[dict]:
        """读回某一轮扫描的逐篇发现/落档事实，供群播报组卡。

        坏行跳过而不抛：这个文件是观测记录，不是真相源，读不出来只该少一条播报，
        不该让抓取退出码跟着变。
        """
        rows = []
        try:
            lines = self.facts_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return rows
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (not isinstance(row, dict) or row.get("event") not in SCAN_POST_EVENTS
                    or row.get("platform") != platform):
                continue
            recorded = parse_ts(row.get("recorded_at"))
            if recorded is not None and recorded >= since:
                rows.append(row)
        return rows

    def activity_summary(self, now: datetime) -> dict | None:
        target = MonitorSchedule.local(now).date()
        discovered = reconcile_discovered = 0
        reconcile_skipped = 0
        reconcile_skipped_platforms = set()
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
            if recorded is None:
                continue
            if row.get("event") == "scan_skipped" and row.get("kind") == "reconcile":
                # A scan moved to the previous day still belongs to its saved target morning.
                if row.get("business_date", str(MonitorSchedule.local(recorded).date())) == str(target):
                    reconcile_skipped += 1
                    reconcile_skipped_platforms.add(row["platform"])
                continue
            if MonitorSchedule.local(recorded).date() != target:
                continue
            if row.get("event") == "content_discovered":
                count = int(row.get("count", 0))
                discovered += count
                if row.get("kind") == "reconcile":
                    reconcile_discovered += count
            if row.get("event") == "scan_finished":
                for key in SKIP_REASONS:
                    skipped[key] += int((row.get("skipped") or {}).get(key, 0))
        if discovered == 0 and not any(skipped.values()) and reconcile_skipped == 0:
            return None
        return {"date": str(target), "discovered": discovered,
                "reconcile_discovered": reconcile_discovered, "skipped": skipped,
                "reconcile_skipped": reconcile_skipped,
                "reconcile_skipped_platforms": sorted(reconcile_skipped_platforms)}


def detection_failure_kind(error: str | None) -> str | None:
    """仅归类已发生的探测失败文字，不新增登录/账号探测。"""
    value = str(error or "").lower()
    if not value:
        return None
    if "/checkpoint" in value or "/challenge" in value:
        return "account_checkpoint"
    if "429" in value:
        return "rate_limited"
    if any(marker in value for marker in ("/login", "401", "403", "会话失效")):
        return "session_or_permission"
    if any(marker in value for marker in ("net::err_", "timeout", "timed out", "连接失败", "connection")):
        return "connection_or_timeout"
    return "unclassified"

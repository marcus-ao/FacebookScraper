"""审校行为的追加式真相源。展示状态可派生，业务决定不得由抓取或重译覆盖。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from core.paid_model import FileLock, FileLockBusy, append_jsonl
from core.store import Archive, assert_physical_direct_path, read_post_truth
from core.translated import source_text_sha256

STATUSES = frozenset({"pending_review", "edited", "snoozed", "approved",
                      "scheduled", "skipped", "handed_off"})
TERMINAL = frozenset({"skipped", "handed_off", "scheduled"})
ACTIONS = frozenset({"edited", "snoozed", "woke", "skipped", "handed_off",
                     "handoff_link", "approved", "scheduled", "submit_failed"})


class ReviewConflict(ValueError):
    """页面版本过期或当前状态不允许该动作。"""


class ReviewValidationError(ValueError):
    """业务输入尚未完整，例如不发缺少理由。"""


def _moment(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except ValueError as exc:
        raise ReviewValidationError("请填写带时区的有效时刻") from exc


def business_day_wakeup(now: datetime, days: int = 3) -> datetime:
    """保持上海当地时刻，跨过指定数量的周一至周五；不猜测法定节假日调休。"""
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
        raise ReviewValidationError("挂起工作日数须在 1 至 30 之间")
    value = _moment(now).astimezone(ZoneInfo("Asia/Shanghai"))
    while days:
        value += timedelta(days=1)
        if value.weekday() < 5:
            days -= 1
    return value.astimezone(timezone.utc)


def _path(account_dir: Path) -> Path:
    account_dir = Path(account_dir)
    assert_physical_direct_path(account_dir.parent, account_dir, kind="directory", label="审校账号目录")
    return assert_physical_direct_path(account_dir, account_dir / "review_items.jsonl",
                                       kind="file", label="审校记录")


def history(account_dir: Path, post_id: str | None = None) -> list[dict]:
    path = _path(account_dir)
    if not path.exists():
        return []
    events = []
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                if not line.endswith(b'\n'):
                    raise ValueError('incomplete record')
                event = json.loads(line.decode("utf-8"))
                if (not isinstance(event, dict) or event.get("status") not in STATUSES
                        or event.get("action") not in ACTIONS
                        or not isinstance(event.get("post_id"), str)
                        or not event.get("post_id")
                        or event.get("account") != Path(account_dir).name
                        or event.get("platform") not in {"facebook", "instagram"}
                        or not re.fullmatch(r"[0-9a-f]{64}", event.get("source_text_sha256", ""))):
                    raise ValueError('invalid review record')
                UUID(event["revision"])
                _moment(event["recorded_at"])
                if event["status"] == "snoozed":
                    if not event.get("wake_at"):
                        raise ValueError('missing wake time')
                    _moment(event["wake_at"])
                if event["status"] == "skipped" and not str(event.get("reason") or "").strip():
                    raise ValueError('missing skip reason')
                if post_id is None or event["post_id"] == post_id:
                    events.append(event)
            except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
                raise ReviewConflict(
                    f"审校记录第 {number} 行损坏或未写完；请先核对，不能按未审核继续处理") from exc
    return events


def latest(account_dir: Path) -> dict[str, dict]:
    return {event["post_id"]: event for event in history(account_dir)}


def state_for(account_dir: Path, source: dict, *, default_status: str = "pending_review",
               scheduled: bool = False, events: dict[str, dict] | None = None) -> dict:
    """``events`` 传 ``latest(account_dir)`` 的结果可复用一次读取，必须是同一账号的快照。

    展示索引要为一个账号连算上千篇的状态。每篇各读一次账本时，真正读文件只花
    0.1 秒，而 ``_path()`` 的两次物理路径核对要 6.0 秒 —— Windows 上一次
    ``Path.resolve()`` 就是四回 ``_getfinalpathname``，1,067 篇重复核对同一条路径。
    不传就照旧自己读，单篇调用的语义不变。
    """
    event = (latest(account_dir) if events is None else events).get(source["post_id"])
    state = dict(event) if event else {
        "status": default_status, "revision": None, "wake_at": None,
        "reason": "", "handoff_url": "", "recorded_at": None,
    }
    stale = bool(event and event["source_text_sha256"] != source_text_sha256(source["text"]))
    state["source_stale"] = stale
    if scheduled:
        state["status"] = "scheduled"
    elif stale and state["status"] not in TERMINAL:
        state["status"] = "pending_review"
        state["wake_at"] = None
    return state


class transaction:
    """文案保存、状态动作和资源导出共用账号锁，允许在同一事务窗口内核对版本。"""
    def __init__(self, account_dir: Path):
        self.account_dir = Path(account_dir)
        _path(self.account_dir)
        lock_path = assert_physical_direct_path(
            self.account_dir, self.account_dir / "review_write.lock", kind="file", label="审校写入锁")
        self.lock = FileLock(lock_path, busy_message="这篇的审校操作正在保存，请稍后重试")
        self.active = False

    def __enter__(self):
        self.lock.__enter__()
        self.active = True
        return self

    def __exit__(self, *args):
        self.active = False
        return self.lock.__exit__(*args)

    def validate(self, indexed: dict, *, expected_revision: str | None,
                 expected_source_sha256: str, default_status: str = "pending_review",
                 scheduled: bool = False) -> tuple[dict, dict]:
        if not self.active:
            raise RuntimeError("审校写入必须持有事务锁")
        source, _ = read_post_truth(self.account_dir, indexed)
        if source_text_sha256(source["text"]) != expected_source_sha256:
            raise ReviewConflict("源帖已更新，请载入最新内容后核对")
        state = state_for(self.account_dir, source, default_status=default_status, scheduled=scheduled)
        if state["revision"] != expected_revision:
            raise ReviewConflict("审校状态已有新版本，请刷新后重试")
        return source, state

    def change(self, indexed: dict, action: str, *, expected_revision: str | None,
               expected_source_sha256: str, reason: str = "", wake_at=None,
               handoff_url: str = "", now=None, scheduled: bool = False,
               snooze_days: int = 3, default_status: str = "pending_review", snapshot_id: str = '') -> dict:
        if not isinstance(action, str) or action not in ACTIONS:
            raise ReviewValidationError("未知的审校动作")
        if snapshot_id and not re.fullmatch(r'[0-9a-f]{32}', snapshot_id):
            raise ReviewValidationError('批准快照编号无效')
        source, current = self.validate(
            indexed, expected_revision=expected_revision, expected_source_sha256=expected_source_sha256,
            default_status=default_status, scheduled=scheduled)
        previous = current["status"]
        if previous in TERMINAL and not (
                (previous == "handed_off" and action == "handoff_link")
                or (previous == "scheduled" and action == "scheduled" and scheduled)):
            raise ReviewConflict("这篇已结束审校，不能直接改变其处理决定")
        if previous == "approved" and action not in {"scheduled", "submit_failed"}:
            raise ReviewConflict("这篇正在提交，暂时不能修改审校决定")
        if action == "woke" and previous != "snoozed" and not current["source_stale"]:
            raise ReviewConflict("只有挂起的帖子可以恢复审校")
        if action == "handoff_link" and previous != "handed_off":
            raise ReviewConflict("只有已交人工处理的帖子可以回填链接")
        if action == "scheduled" and not scheduled:
            raise ReviewConflict("尚无提交回读确认，不能记为已排期")
        moment = _moment(now)
        if not isinstance(reason, str) or len(reason) > 2000:
            raise ReviewValidationError("理由须为不超过 2000 字的文字")
        reason = reason.strip()
        if action == "skipped" and not reason:
            raise ReviewValidationError("请填写这篇不发的理由")
        if not isinstance(handoff_url, str) or len(handoff_url) > 2048:
            raise ReviewValidationError("手工发布链接无效")
        handoff_url = handoff_url.strip()
        if handoff_url:
            parsed = urlsplit(handoff_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
                raise ReviewValidationError("请填写 http 或 https 开头的帖子链接")
        status = {"woke": "pending_review", "submit_failed": "pending_review",
                  "handoff_link": "handed_off"}.get(action, action)
        deadline = None
        if action == "snoozed":
            deadline = _moment(wake_at) if wake_at is not None else business_day_wakeup(moment, snooze_days)
            if deadline <= moment:
                raise ReviewValidationError("挂起到期时间须晚于当前时间")
        elif action == "edited" and previous == "snoozed":
            status, deadline = "snoozed", _moment(current["wake_at"])
            reason = current["reason"]
        event = {
            "post_id": source["post_id"], "platform": source["platform"],
            "account": self.account_dir.name, "status": status, "action": action,
            "revision": str(uuid4()), "previous_revision": expected_revision,
            "source_text_sha256": source_text_sha256(source["text"]),
            "recorded_at": moment.isoformat(), "actor": None, "reason": reason,
            "wake_at": deadline.isoformat() if deadline is not None else None,
            "handoff_url": handoff_url or current.get("handoff_url") or "",
            "snapshot_id": snapshot_id or current.get('snapshot_id') or '',
        }
        append_jsonl(_path(self.account_dir), event, guard=lambda path: _path(path.parent))
        return event


def transition(account_dir: Path, indexed_source: dict, action: str, **kwargs) -> dict:
    with transaction(account_dir) as session:
        return session.change(indexed_source, action, **kwargs)


def wake_due(account_dirs, *, now=None, scheduled_refs=()) -> list[dict]:
    moment = _moment(now)
    scheduled = set(scheduled_refs)
    awakened = []
    for account_dir in account_dirs:
        account_dir = Path(account_dir)
        try:
            with transaction(account_dir) as session:
                current = latest(account_dir)
                waiting = {key: event for key, event in current.items() if event["status"] == "snoozed"}
                if not waiting:
                    continue
                rows = {row["post_id"]: row for row in Archive(account_dir.parent, account_dir.name).rows()}
                for post_id, event in waiting.items():
                    if post_id not in rows or "%s:%s" % (event["platform"], post_id) in scheduled:
                        continue
                    source, _ = read_post_truth(account_dir, rows[post_id])
                    digest = source_text_sha256(source["text"])
                    if _moment(event["wake_at"]) <= moment or digest != event["source_text_sha256"]:
                        awakened.append(session.change(
                            source, "woke", expected_revision=event["revision"],
                            expected_source_sha256=digest, now=moment))
        except FileLockBusy:
            continue
    return awakened

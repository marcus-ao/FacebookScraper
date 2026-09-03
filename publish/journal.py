r"""追加式发布状态机与幂等证据：``state/published.jsonl``。

只有 ``scheduled`` 表示远端内容日历已经回读成功。点击提交后没看见成功信号、
或看见信号但日历回读失败，都属于不可安全自动重试的状态；历史行永不重写。
旧版 ``prepared/scheduled/failed`` 行在读取时会补齐兼容字段。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextvars import ContextVar, Token
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from core.paid_model import FileLock

JOURNAL_NAME = "published.jsonl"

STATUS_PREPARED = "prepared"
STATUS_SUBMIT_AMBIGUOUS = "submit_ambiguous"
STATUS_SUBMITTED_UNVERIFIED = "submitted_unverified"
STATUS_SCHEDULED = "scheduled"
STATUS_FAILED_PRE_SUBMIT = "failed_pre_submit"
# 兼容旧调用方；新写盘不会再产生字面值 ``failed``。
STATUS_FAILED = STATUS_FAILED_PRE_SUBMIT
LEGACY_STATUS_FAILED = "failed"

_STATUSES = {
    STATUS_PREPARED,
    STATUS_SUBMIT_AMBIGUOUS,
    STATUS_SUBMITTED_UNVERIFIED,
    STATUS_SCHEDULED,
    STATUS_FAILED_PRE_SUBMIT,
}
BLOCKING_STATUSES = {
    STATUS_PREPARED,
    STATUS_SUBMIT_AMBIGUOUS,
    STATUS_SUBMITTED_UNVERIFIED,
}
NO_AUTO_RETRY_STATUSES = {
    STATUS_SUBMIT_AMBIGUOUS,
    STATUS_SUBMITTED_UNVERIFIED,
}


_HELD_PUBLISH_LOCKS: ContextVar[frozenset[str]] = ContextVar(
    "held_publish_locks", default=frozenset())


class PublishOperationLock(AbstractContextManager):
    """单帖 CLI 与 pipeline approve 共用的跨进程发布互斥。

    OS 层的文件锁交给 :class:`core.paid_model.FileLock`；这里只加发布侧
    特有的那一层——**同一执行上下文内的显式重入**。
    """

    def __init__(self, path: Path, *, allow_reentrant: bool = False) -> None:
        self.path = Path(path)
        self.allow_reentrant = allow_reentrant
        self._lock = FileLock(
            self.path, error_type=RuntimeError,
            busy_message="另一个单帖/批量发布正在运行；本次没有接触浏览器")
        self._context_token: Token | None = None
        self._reentrant = False

    def __enter__(self):
        key = str(self.path.resolve())
        held = _HELD_PUBLISH_LOCKS.get()
        if key in held and self.allow_reentrant:
            # ``pipeline approve`` 从远端槽位回读到整批提交始终持有发布锁；
            # 单帖入口在同一 Context 内再次进入时复用它。别的进程/Context 仍会
            # 经过下面的 OS 文件锁，不能借此并发越过。
            self._reentrant = True
            return self
        if key in held:
            raise RuntimeError("同一执行上下文已经持有发布锁；未显式授权重入")
        self._lock.__enter__()
        self._context_token = _HELD_PUBLISH_LOCKS.set(held | {key})
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._reentrant:
            self._reentrant = False
            return False
        self._lock.__exit__(exc_type, exc, tb)
        if self._context_token is not None:
            _HELD_PUBLISH_LOCKS.reset(self._context_token)
            self._context_token = None
        return False


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_ref(platform: str, post_id: str) -> str:
    return "%s:%s" % ((platform or "").strip().lower(), (post_id or "").strip())


def effective_source_refs(platform: str, post_id: str,
                          refs: Iterable[str] = ()) -> tuple[str, ...]:
    """冻结一组来源引用，并保证 canonical 自身永远包含在内。"""
    canonical = source_ref(platform, post_id)
    values = [canonical]
    values.extend(str(value).strip() for value in refs if str(value).strip())
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class PublishAttempt:
    """一次状态转换的完整内容寻址证据。"""

    post_id: str
    platform: str
    status: str
    scheduled_at: str
    recorded_at: str
    text_de_sha256: str
    images: tuple[str, ...] = ()
    image_sources: tuple[str, ...] = ()
    ui_timezone: str = ""
    ui_readback: str = ""
    step: str = ""
    screenshot: str = ""
    note: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    schema_version: int = 2
    attempt_id: str = ""
    source_refs: tuple[str, ...] = ()
    target_channels: tuple[str, ...] = ("facebook", "instagram")
    source_text_sha256: str = ""
    original_text_sha256: str = ""
    final_text_sha256: str = ""
    image_sha256: tuple[str, ...] = ()
    ui_scheduled_at: str = ""
    success_signal: str = ""
    readback_signal: str = ""
    remote_id: str = ""
    #: 每个渠道各一个远端 ID，形如 ``("facebook=188…", "instagram=437…")``。
    #: ⚠️ 实测 FB 与 IG 是**两个独立远端对象**（`docs/PROBE_FINDINGS_20260901.md`），
    #: 单个 `remote_id` 只能记住其中一个，跨渠道对账会缺一半。
    remote_ids: tuple[str, ...] = ()
    verification: str = ""
    channels_verified: tuple[str, ...] = ()
    manual_evidence: bool = False

    def __post_init__(self) -> None:
        if not (self.post_id or "").strip():
            raise ValueError("留痕必须记 post_id")
        if self.status not in _STATUSES:
            raise ValueError("未知发布状态：%r" % self.status)
        for label, value in (("scheduled_at", self.scheduled_at),
                             ("recorded_at", self.recorded_at)):
            try:
                parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("%s 不是 ISO 时间：%r" % (label, value)) from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("%s 必须显式带时区：%r" % (label, value))
        if not self.attempt_id:
            object.__setattr__(self, "attempt_id", str(uuid.uuid4()))
        object.__setattr__(self, "source_refs", effective_source_refs(
            self.platform, self.post_id, self.source_refs))
        if not self.final_text_sha256:
            object.__setattr__(self, "final_text_sha256", self.text_de_sha256)
        channels = tuple(dict.fromkeys(
            str(value).strip().lower() for value in self.target_channels
            if str(value).strip()))
        if not channels:
            raise ValueError("留痕必须记至少一个目标渠道")
        unknown = set(channels) - {"facebook", "instagram"}
        if unknown:
            raise ValueError("未知目标渠道：%s" % "、".join(sorted(unknown)))
        object.__setattr__(self, "target_channels", channels)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


# 老模块名兼容；新代码与文档使用 PublishAttempt。
PublishRecord = PublishAttempt


def journal_path(state_dir: Path) -> Path:
    return Path(state_dir) / JOURNAL_NAME


def _compat_row(row: dict) -> dict:
    out = dict(row)
    if out.get("status") == LEGACY_STATUS_FAILED:
        out["legacy_status"] = LEGACY_STATUS_FAILED
        out["status"] = STATUS_FAILED_PRE_SUBMIT
    out.setdefault("schema_version", 1)
    post_id = str(out.get("post_id") or "")
    platform = str(out.get("platform") or "")
    out.setdefault("source_refs", [source_ref(platform, post_id)] if post_id else [])
    out.setdefault("target_channels", ["facebook", "instagram"])
    out.setdefault("attempt_id", "legacy:%s:%s:%s" % (
        platform, post_id, out.get("recorded_at") or "unknown"))
    out.setdefault("source_text_sha256", "")
    out.setdefault("original_text_sha256", out.get("text_de_sha256") or "")
    out.setdefault("final_text_sha256", out.get("text_de_sha256") or "")
    out.setdefault("image_sha256", [])
    out.setdefault("success_signal", "")
    out.setdefault("readback_signal", "")
    out.setdefault("remote_id", "")
    out.setdefault("remote_ids", [])
    out.setdefault("verification", "")
    out.setdefault("channels_verified", [])
    out.setdefault("manual_evidence", False)
    return out


def _validate_loaded_row(row: dict, *, path: Path, number: int) -> dict:
    """最小 journal 契约；任一坏行都不能被幂等检查当作“不存在”。"""
    status = row.get("status")
    if status not in _STATUSES:
        raise ValueError(
            "%s 第 %d 行 status=%r 未知；不能在发布证据损坏时继续"
            % (path, number, status))
    platform = row.get("platform")
    post_id = row.get("post_id")
    if platform not in {"facebook", "instagram"} or not isinstance(
            post_id, str) or not post_id.strip():
        raise ValueError(
            "%s 第 %d 行缺少有效 platform/post_id" % (path, number))
    refs = row.get("source_refs")
    if (not isinstance(refs, list) or not refs
            or any(not isinstance(ref, str) or not ref.strip()
                   or ref.split(":", 1)[0] not in {"facebook", "instagram"}
                   or ":" not in ref or not ref.split(":", 1)[1].strip()
                   for ref in refs)):
        raise ValueError(
            "%s 第 %d 行 source_refs 为空或格式无效" % (path, number))
    if not isinstance(row.get("attempt_id"), str) or not row["attempt_id"].strip():
        raise ValueError("%s 第 %d 行缺少 attempt_id" % (path, number))
    for key in ("scheduled_at", "recorded_at"):
        value = row.get(key)
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "%s 第 %d 行 %s 不是 ISO 时间" % (path, number, key)) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(
                "%s 第 %d 行 %s 必须显式带时区" % (path, number, key))
    channels = row.get("target_channels")
    if (not isinstance(channels, list) or not channels
            or any(value not in {"facebook", "instagram"} for value in channels)):
        raise ValueError(
            "%s 第 %d 行 target_channels 无效" % (path, number))
    if status == STATUS_SCHEDULED:
        fingerprint = row.get("final_text_sha256") or row.get("text_de_sha256")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise ValueError(
                "%s 第 %d 行 scheduled 缺少最终正文指纹" % (path, number))
    return row


def load(state_dir: Path) -> list[dict]:
    """读全部留痕；坏 JSON 行失败闭合，旧行补兼容字段。"""
    path = journal_path(state_dir)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "%s 第 %d 行不是合法 JSON：%s。不要手工改发布证据。"
                % (path, number, exc)) from exc
        if not isinstance(row, dict):
            raise ValueError(
                "%s 第 %d 行不是对象；不能静默丢弃发布证据" % (path, number))
        rows.append(_validate_loaded_row(
            _compat_row(row), path=path, number=number))
    return rows


def append(state_dir: Path, record: PublishAttempt) -> Path:
    """耐久追加一行，不重写历史。"""
    path = journal_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(record.to_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _row_refs(row: dict) -> set[str]:
    refs = {str(value) for value in (row.get("source_refs") or []) if str(value)}
    if not refs and row.get("post_id"):
        refs.add(source_ref(str(row.get("platform") or ""), str(row["post_id"])))
    return refs


def history_for(state_dir: Path, post_id: str,
                platform: str | None = None) -> list[dict]:
    wanted_id = (post_id or "").strip()
    wanted_ref = source_ref(platform or "", wanted_id) if platform else ""
    return [row for row in load(state_dir)
            if (wanted_ref in _row_refs(row) if platform
                else any(ref.rsplit(":", 1)[-1] == wanted_id
                         for ref in _row_refs(row)))]


def last_resolvable(state_dir: Path, post_id: str) -> dict | None:
    """返回最近未闭合证据；已经人工结转后不再穿透到旧状态。"""
    for row in reversed(history_for(state_dir, post_id)):
        status = row.get("status")
        if status == STATUS_SCHEDULED or (
                status == STATUS_FAILED_PRE_SUBMIT
                and row.get("manual_evidence")):
            return None
        if status in {
                STATUS_PREPARED, STATUS_SUBMIT_AMBIGUOUS,
                STATUS_SUBMITTED_UNVERIFIED, STATUS_FAILED_PRE_SUBMIT}:
            return row
    return None


def scheduled_record(state_dir: Path, post_id: str,
                     platform: str) -> dict | None:
    return scheduled_record_for_refs(
        state_dir, (source_ref(platform, post_id),))


def scheduled_record_for_refs(state_dir: Path,
                              refs: Iterable[str]) -> dict | None:
    """任一来源曾被最终回读为 scheduled，就永久视为已发布。"""
    wanted = {str(value).strip() for value in refs if str(value).strip()}
    for row in reversed(load(state_dir)):
        if not (_row_refs(row) & wanted):
            continue
        if row.get("status") == STATUS_SCHEDULED:
            return row
    return None


def pending_draft_record(state_dir: Path, post_id: str,
                         platform: str) -> dict | None:
    """返回最近未闭合尝试；模糊/未回读状态尤其禁止自动重试。"""
    return pending_record_for_refs(
        state_dir, (source_ref(platform, post_id),))


def pending_record_for_refs(state_dir: Path,
                            refs: Iterable[str]) -> dict | None:
    """按每个来源的最近转换找未闭合状态，返回最危险的一条。

    合并候选可能以 FB 为 canonical，却同时覆盖 IG 来源。只查 canonical 会让
    secondary 已经 ambiguous 的内容再次被提交。这里逐 ref 求最近状态；人工
    “未排期”与 scheduled 都会闭合该 ref，自动失败仍保持阻塞。
    """
    wanted = tuple(dict.fromkeys(
        str(value).strip() for value in refs if str(value).strip()))
    rows = load(state_dir)
    pending: list[dict] = []
    for ref in wanted:
        for row in reversed(rows):
            if ref not in _row_refs(row):
                continue
            status = row.get("status")
            if status == STATUS_SCHEDULED:
                break
            if status == STATUS_FAILED_PRE_SUBMIT:
                if not row.get("manual_evidence"):
                    pending.append(row)
                break
            if status in BLOCKING_STATUSES:
                pending.append(row)
                break
    if not pending:
        return None
    priority = {
        STATUS_SUBMIT_AMBIGUOUS: 4,
        STATUS_SUBMITTED_UNVERIFIED: 3,
        STATUS_FAILED_PRE_SUBMIT: 2,
        STATUS_PREPARED: 1,
    }
    return max(pending, key=lambda row: priority.get(str(row.get("status")), 0))


def scheduled_source_refs(state_dir: Path) -> set[str]:
    """只把最终 scheduled 的 source_refs 视为已发布。"""
    out: set[str] = set()
    for row in load(state_dir):
        if row.get("status") == STATUS_SCHEDULED:
            out.update(_row_refs(row))
    return out


def transition(record: PublishAttempt, status: str, *, recorded_at: str,
               **changes) -> PublishAttempt:
    """沿用同一个 attempt_id/内容指纹创建下一条追加式转换。"""
    values = asdict(record)
    values.update(changes)
    values.update({"status": status, "recorded_at": recorded_at})
    return PublishAttempt(**values)


def attempt_from_row(row: dict) -> PublishAttempt:
    """把兼容化 journal 行恢复成类型；忽略旧版额外字段。"""
    names = set(PublishAttempt.__dataclass_fields__)
    values = {key: value for key, value in row.items() if key in names}
    for key in (
            "images", "image_sources", "warnings", "source_refs",
            "target_channels", "image_sha256", "channels_verified",
            "remote_ids"):
        values[key] = tuple(values.get(key) or ())
    return PublishAttempt(**values)

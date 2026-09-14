"""付费请求账本与互斥；先写 started，响应即记 usage，产出拒绝也保留费用。"""
from __future__ import annotations

import json
import math
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from core.paid_model import FileLock

LEDGER_NAME = "paid_requests.jsonl"
LOCK_NAME = "paid_requests.lock"

EVENT_STARTED = "started"
EVENT_USAGE = "usage_recorded"
EVENT_ACCEPTED = "accepted"
EVENT_REJECTED = "output_rejected"
EVENT_UNCERTAIN = "uncertain"
EVENT_USAGE_UNKNOWN = "usage_unknown"

_EVENTS = {
    EVENT_STARTED, EVENT_USAGE, EVENT_ACCEPTED, EVENT_REJECTED,
    EVENT_UNCERTAIN, EVENT_USAGE_UNKNOWN,
}
_GLOBAL_BLOCKING = {
    EVENT_STARTED, EVENT_USAGE, EVENT_UNCERTAIN, EVENT_USAGE_UNKNOWN,
}

_operation = ContextVar('paid_operation', default=None)


@contextmanager
def operation_scope(operation_id):
    """Bind synchronous stage CLIs to the durable batch that requested them."""
    token = _operation.set(operation_id)
    try:
        yield
    finally:
        _operation.reset(token)


class PaidRequestBlocked(RuntimeError):
    """账本/预算不能证明下一次请求安全，失败闭合。"""


def PaidRequestLock(path: Path) -> FileLock:   # noqa: N802 - preserve the public factory name
    """所有文本与图片付费入口共用的一把跨进程锁。"""
    return FileLock(path, error_type=PaidRequestBlocked,
                    busy_message="另一个翻译/调图付费请求正在进行；本次未发请求")


@dataclass(frozen=True)
class PaidReceipt:
    request_id: str
    job_key: str
    stage: str
    operation_id: str | None = None


@dataclass(frozen=True)
class LedgerSnapshot:
    daily_usd: float
    monthly_usd: float
    request_ids: frozenset[str]
    unknown: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerMonthSnapshot:
    translation_usd: float
    image_usd: float
    request_ids: frozenset[str]
    unknown: tuple[str, ...] = ()


def ledger_path(state_dir: Path) -> Path:
    return Path(state_dir) / LEDGER_NAME


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def _append(state_dir: Path, event: Mapping[str, Any]) -> None:
    path = ledger_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(
            dict(event), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_events(state_dir: Path) -> list[dict]:
    path = ledger_path(state_dir)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PaidRequestBlocked("付费请求账本读不了：%s" % exc) from exc
    rows: list[dict] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PaidRequestBlocked(
                "%s 第 %d 行损坏；不能在漏算费用时继续" % (path, number)) from exc
        if (not isinstance(row, dict)
                or row.get("event") not in _EVENTS
                or not isinstance(row.get("request_id"), str)
                or not row["request_id"].strip()
                or not isinstance(row.get("job_key"), str)
                or not row["job_key"].strip()):
            raise PaidRequestBlocked(
                "%s 第 %d 行不满足付费账本契约" % (path, number))
        rows.append(row)
    return rows


def _latest_by_request(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        latest[str(row["request_id"])] = row
    return latest


# 限制同一 job_key 的付费产出拒绝次数；总费用另受日/月预算约束。
REJECTED_RETRY_BUDGET = 2


def _assert_startable(state_dir: Path, job_key: str) -> None:
    rows = load_events(state_dir)
    latest = _latest_by_request(rows)
    blocking = [row for row in latest.values()
                if row.get("event") in _GLOBAL_BLOCKING]
    if blocking:
        row = blocking[0]
        raise PaidRequestBlocked(
            "付费账本存在未闭合请求 %s（%s）；为把未知超额限制在一次请求，"
            "人工核账前已停止全部后续付费。\n"
            "    确认那次请求的真实结果后，用以下命令闭合它再重跑：\n"
            "        python -m core.paid_requests --status\n"
            "        python -m core.paid_requests --resolve %s --as rejected"
            % (row.get("request_id"), row.get("event"), row.get("request_id")))
    rejected = [row for row in latest.values()
                if row.get("job_key") == job_key
                and row.get("event") == EVENT_REJECTED]
    if len(rejected) >= REJECTED_RETRY_BUDGET:
        raise PaidRequestBlocked(
            "同一付费任务已被拒 %d 次（上限 %d，最近 request_id=%s）；"
            "这不像是偶发抖动，禁止继续自动重试。\n"
            "    先看上面每条的拒绝原因：金额/标签闸报的是产出内容问题，"
            "该改提示词（改完把 PROMPT_VERSION +1 即可解锁），"
            "不是靠重跑碰运气。"
            % (len(rejected), REJECTED_RETRY_BUDGET,
               rejected[-1].get("request_id")))


def _base_event(receipt: PaidReceipt, event: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event": event,
        "request_id": receipt.request_id,
        "job_key": receipt.job_key,
        "stage": receipt.stage,
        "operation_id": receipt.operation_id,
        "recorded_at": _now_text(),
    }


class RequestController:
    """耐久记录 started→usage；预算策略由调用方通过必填 preflight 注入。"""

    def __init__(self, state_dir: Path, *,
                 preflight: Callable[[], None], operation_id: str | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.preflight = preflight
        self.operation_id = _operation.get() or operation_id

    def run(self, *, stage: str, job_key: str, source_ref: str,
            media_index: int | None, model: str,
            request: Callable[[], Any], usage_getter: Callable[[], Mapping[str, Any]],
            usage_errors: Callable[[Mapping[str, Any]], list[str]],
            usage_cost: Callable[[Mapping[str, Any]], float | None]
            ) -> tuple[Any, PaidReceipt]:
        receipt = PaidReceipt(str(uuid.uuid4()), job_key, stage, self.operation_id)
        with PaidRequestLock(self.state_dir / LOCK_NAME):
            try:
                self.preflight()
            except PaidRequestBlocked:
                raise
            except Exception as exc:
                raise PaidRequestBlocked(
                    "付费前预算/账本检查失败；本次未发请求：%s" % exc) from exc
            _assert_startable(self.state_dir, job_key)
            started = _base_event(receipt, EVENT_STARTED)
            started.update({
                "source_ref": str(source_ref),
                "media_index": media_index,
                "model": str(model),
            })
            _append(self.state_dir, started)
            try:
                result = request()
            except BaseException as exc:
                failed_usage = dict(usage_getter() or {})
                failed_errors = (usage_errors(failed_usage)
                                 if failed_usage else ["usage missing"])
                failed_cost = (usage_cost(failed_usage)
                               if not failed_errors else None)
                self._record_failed_response(
                    receipt, failed_usage, usage_errors, usage_cost,
                    reason="request_or_contract_error:%s" % type(exc).__name__)
                if (failed_errors or failed_cost is None
                        or not math.isfinite(float(failed_cost))
                        or float(failed_cost) < 0):
                    raise PaidRequestBlocked(
                        "请求异常且无法取得完整 usage；已记不确定费用并停止全部后续付费"
                    ) from exc
                raise
            usage = dict(usage_getter() or {})
            errors = usage_errors(usage)
            cost = usage_cost(usage) if not errors else None
            if (errors or cost is None or not math.isfinite(float(cost))
                    or float(cost) < 0):
                unknown = _base_event(receipt, EVENT_USAGE_UNKNOWN)
                unknown.update({"usage": usage, "usage_errors": list(errors)})
                _append(self.state_dir, unknown)
                raise PaidRequestBlocked(
                    "付费响应 usage/费率不完整；已记 usage_unknown 并停止后续付费")
            recorded = _base_event(receipt, EVENT_USAGE)
            recorded.update({"usage": usage, "cost_usd": float(cost)})
            _append(self.state_dir, recorded)
            return result, receipt

    def _record_failed_response(
            self, receipt: PaidReceipt, usage: Mapping[str, Any],
            usage_errors: Callable[[Mapping[str, Any]], list[str]],
            usage_cost: Callable[[Mapping[str, Any]], float | None], *,
            reason: str) -> None:
        clean = dict(usage or {})
        errors = usage_errors(clean) if clean else ["usage missing"]
        cost = usage_cost(clean) if not errors else None
        if (not errors and cost is not None and math.isfinite(float(cost))
                and float(cost) >= 0):
            recorded = _base_event(receipt, EVENT_USAGE)
            recorded.update({"usage": clean, "cost_usd": float(cost)})
            _append(self.state_dir, recorded)
            rejected = _base_event(receipt, EVENT_REJECTED)
            rejected["reason"] = reason
            _append(self.state_dir, rejected)
        elif clean:
            unknown = _base_event(receipt, EVENT_USAGE_UNKNOWN)
            unknown.update({"usage": clean, "usage_errors": list(errors)})
            _append(self.state_dir, unknown)
        else:
            uncertain = _base_event(receipt, EVENT_UNCERTAIN)
            uncertain["reason"] = reason
            _append(self.state_dir, uncertain)

    def finalize(self, receipt: PaidReceipt, *, accepted: bool,
                 reason: str = "") -> None:
        with PaidRequestLock(self.state_dir / LOCK_NAME):
            latest = _latest_by_request(load_events(self.state_dir)).get(
                receipt.request_id)
            if latest is None or latest.get("event") != EVENT_USAGE:
                raise PaidRequestBlocked(
                    "request_id=%s 不在 usage_recorded，不能伪造完成状态"
                    % receipt.request_id)
            event = _base_event(
                receipt, EVENT_ACCEPTED if accepted else EVENT_REJECTED)
            if reason:
                event["reason"] = str(reason)[:300]
            _append(self.state_dir, event)


def ledger_snapshot(state_dir: Path, *, now: datetime,
                    zone_name: str = "Europe/Berlin") -> LedgerSnapshot:
    """只按独立 usage 账本计费，并报告未闭合/损坏状态。"""
    rows = load_events(state_dir)
    latest = _latest_by_request(rows)
    usage_rows: dict[str, dict] = {}
    unknown: list[str] = []
    for row in rows:
        if row.get("event") != EVENT_USAGE:
            continue
        request_id = str(row["request_id"])
        if request_id in usage_rows:
            unknown.append("request_id=%s 重复 usage_recorded" % request_id)
        usage_rows[request_id] = row
    for request_id, row in latest.items():
        event = row.get("event")
        if event in _GLOBAL_BLOCKING:
            unknown.append("request_id=%s 未闭合(%s)" % (request_id, event))
        elif event == EVENT_ACCEPTED and request_id not in usage_rows:
            unknown.append("request_id=%s 完成态缺 usage" % request_id)

    zone = ZoneInfo(zone_name)
    local = now.astimezone(zone)
    day_start = datetime.combine(local.date(), time.min, tzinfo=zone).astimezone(
        timezone.utc)
    month_start = datetime(local.year, local.month, 1, tzinfo=zone).astimezone(
        timezone.utc)
    daily = monthly = 0.0
    for request_id, row in usage_rows.items():
        try:
            when = datetime.fromisoformat(
                str(row.get("recorded_at") or "").replace("Z", "+00:00"))
            cost = float(row.get("cost_usd"))
        except (TypeError, ValueError):
            unknown.append("request_id=%s usage 时间/金额无效" % request_id)
            continue
        if (when.tzinfo is None or when.utcoffset() is None
                or not math.isfinite(cost) or cost < 0):
            unknown.append("request_id=%s usage 时间/金额无效" % request_id)
            continue
        when = when.astimezone(timezone.utc)
        if when >= month_start:
            monthly += cost
            if when >= day_start:
                daily += cost
    return LedgerSnapshot(
        daily, monthly, frozenset(usage_rows), tuple(dict.fromkeys(unknown)))


def ledger_month_snapshot(state_dir: Path, *, month: str,
                          zone_name: str = "Europe/Berlin"
                          ) -> LedgerMonthSnapshot:
    """按独立账本汇总一个业务月份，并保留所有未闭合告警。"""
    try:
        year, mon = (int(part) for part in month.split("-"))
        start_local = datetime(year, mon, 1, tzinfo=ZoneInfo(zone_name))
        if mon == 12:
            end_local = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(zone_name))
        else:
            end_local = datetime(year, mon + 1, 1, tzinfo=ZoneInfo(zone_name))
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise PaidRequestBlocked("无效账单月份/时区：%s/%s" % (
            month, zone_name)) from exc
    start = start_local.astimezone(timezone.utc)
    end = end_local.astimezone(timezone.utc)
    rows = load_events(state_dir)
    latest = _latest_by_request(rows)
    usage_rows: dict[str, dict] = {}
    unknown: list[str] = []
    for row in rows:
        if row.get("event") != EVENT_USAGE:
            continue
        request_id = str(row["request_id"])
        if request_id in usage_rows:
            unknown.append("request_id=%s 重复 usage_recorded" % request_id)
        usage_rows[request_id] = row
    for request_id, row in latest.items():
        event = row.get("event")
        if event in _GLOBAL_BLOCKING:
            unknown.append("request_id=%s 未闭合(%s)" % (request_id, event))
        elif event == EVENT_ACCEPTED and request_id not in usage_rows:
            unknown.append("request_id=%s 完成态缺 usage" % request_id)

    text_cost = image_cost = 0.0
    for request_id, row in usage_rows.items():
        try:
            when = datetime.fromisoformat(
                str(row.get("recorded_at") or "").replace("Z", "+00:00"))
            cost = float(row.get("cost_usd"))
        except (TypeError, ValueError):
            unknown.append("request_id=%s usage 时间/金额无效" % request_id)
            continue
        if (when.tzinfo is None or when.utcoffset() is None
                or not math.isfinite(cost) or cost < 0):
            unknown.append("request_id=%s usage 时间/金额无效" % request_id)
            continue
        if not start <= when.astimezone(timezone.utc) < end:
            continue
        stage = row.get("stage")
        if stage in {"translation", "risk_scan"}:
            text_cost += cost
        elif stage == "image":
            image_cost += cost
        else:
            unknown.append("request_id=%s stage=%r 未知" % (request_id, stage))
    return LedgerMonthSnapshot(
        text_cost, image_cost, frozenset(usage_rows),
        tuple(dict.fromkeys(unknown)))


# 人工核对后结转未决请求。

def _cli(argv=None) -> int:
    import argparse

    from core.config import cfg
    from core.console import force_utf8
    force_utf8()

    parser = argparse.ArgumentParser(
        prog="python -m core.paid_requests",
        description="付费账本的只读体检与人工结转。不发任何付费请求。")
    parser.add_argument("--status", action="store_true",
                        help="列出未闭合请求与各 job_key 的被拒次数")
    parser.add_argument("--resolve", metavar="REQUEST_ID",
                        help="把一个未闭合请求闭合掉")
    parser.add_argument("--as", dest="outcome",
                        choices=("accepted", "rejected"),
                        help="--resolve 时必填：那次请求的真实结果")
    parser.add_argument("--reason", default="人工结转",
                        help="写进账本的说明")
    args = parser.parse_args(argv)

    state_dir = cfg().state_dir
    rows = load_events(state_dir)
    latest = _latest_by_request(rows)

    if args.resolve:
        row = latest.get(args.resolve)
        if row is None:
            print("账本里没有 request_id=%s" % args.resolve)
            return 1
        if row.get("event") not in _GLOBAL_BLOCKING:
            print("request_id=%s 当前状态是 %s，已经是闭合态，无需结转"
                  % (args.resolve, row.get("event")))
            return 1
        if not args.outcome:
            print("必须用 --as accepted|rejected 说明那次请求的真实结果。\n"
                  "  查不清就先去看服务商后台的用量：把已经计费的请求记成\n"
                  "  accepted 才不会让预算闸低估花销。")
            return 2
        event = dict(row)
        event["event"] = (EVENT_ACCEPTED if args.outcome == "accepted"
                          else EVENT_REJECTED)
        event["reason"] = str(args.reason)[:300]
        event["resolved_by"] = "manual"
        event["recorded_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        with PaidRequestLock(state_dir / LOCK_NAME):
            _append(state_dir, event)
        print("已把 request_id=%s 结转为 %s" % (args.resolve, event["event"]))
        return 0

    unclosed = [(rid, row) for rid, row in latest.items()
                if row.get("event") in _GLOBAL_BLOCKING]
    print("付费账本：%s" % (state_dir / LEDGER_NAME))
    print("  事件 %d 条 / 请求 %d 个" % (len(rows), len(latest)))
    if unclosed:
        print("\n⛔ 未闭合请求 %d 个 —— 它们会阻断全部后续付费：" % len(unclosed))
        for rid, row in unclosed:
            print("    %s  %s  stage=%s  job_key=%s"
                  % (rid, row.get("event"), row.get("stage"),
                     str(row.get("job_key"))[:40]))
        print("\n  确认真实结果后逐个结转：")
        print("    python -m core.paid_requests --resolve <ID> --as accepted|rejected")
    else:
        print("\n✅ 没有未闭合请求。")

    rejected_counts: dict[str, int] = {}
    for row in latest.values():
        if row.get("event") == EVENT_REJECTED:
            key = str(row.get("job_key") or "?")
            rejected_counts[key] = rejected_counts.get(key, 0) + 1
    blocked = {k: n for k, n in rejected_counts.items()
               if n >= REJECTED_RETRY_BUDGET}
    if blocked:
        print("\n⚠️ 已用满被拒重试预算（%d 次）的 job_key %d 个："
              % (REJECTED_RETRY_BUDGET, len(blocked)))
        for key, n in sorted(blocked.items()):
            print("    %s  被拒 %d 次" % (key[:60], n))
        print("\n  这类要改提示词而不是重跑：改完把 PROMPT_VERSION +1 即可解锁。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

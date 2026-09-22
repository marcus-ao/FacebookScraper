"""离线判断人工排期时刻；提交前须提供实时 Planner 占用。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.config import cfg
from publish.business_suite import (ProbeRequired, RemoteSlotInventory,
                                    resolve_ui_timezone, ui_time_is_ambiguous)
from publish.compose import ScheduleWindow, verified_constraints_from_config


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("排期时刻必须显式带时区")
    utc = value.astimezone(timezone.utc)
    if utc.astimezone(value.tzinfo).replace(tzinfo=None) != value.replace(tzinfo=None):
        raise ValueError("该当地时刻在夏令时切换中不存在")
    return utc


@dataclass(frozen=True)
class CalendarBounds:
    earliest: datetime
    latest: datetime
    start_inclusive: datetime
    end_exclusive: datetime
    ui_timezone: str


@dataclass(frozen=True)
class SlotDecision:
    allowed: bool
    reason: str
    conflicts: tuple[datetime, ...] = ()
    suggestions: tuple[datetime, ...] = ()


AUDIENCE_TIMEZONE = "Europe/Berlin"
# 德国受众的深夜区间：落在这里不拦，但必须让人看见——北京 10:00 就是柏林 03:00。
AUDIENCE_QUIET_HOURS = range(0, 6)


def audience_local(target: datetime) -> dict:
    """把业务时刻换算成德国受众当地时刻，供界面并排显示。"""
    local = aware_utc(target).astimezone(resolve_ui_timezone(AUDIENCE_TIMEZONE))
    return {"timezone": AUDIENCE_TIMEZONE, "at": local.isoformat(),
            "quiet_hours": local.hour in AUDIENCE_QUIET_HOURS}


def configured_window(platform: str) -> ScheduleWindow:
    """使用已确认的 UI 时区；平台提前量上限交给实际排期界面。"""
    return verified_constraints_from_config(platform)[1]


def config_window() -> ScheduleWindow:
    """审校台选时刻用的窗口。只读配置时区，不回查 G1 录证。"""
    ui_timezone = str(cfg().get("publish", "ui_timezone", "") or "").strip()
    if not ui_timezone:
        raise ValueError("[publish].ui_timezone 为空；无法计算可选排期范围")
    resolve_ui_timezone(ui_timezone)
    return ScheduleWindow("", timedelta(0), None, ui_timezone)


def next_selectable_minute(now: datetime) -> datetime:
    """时间输入只到分钟。最早可选是严格晚于现在的下一个完整分钟。"""
    utc = aware_utc(now).astimezone(timezone.utc)
    return utc.replace(second=0, microsecond=0) + timedelta(minutes=1)


def slot_refusal(decision: SlotDecision) -> str:
    """把空档判定写成页面能直接显示的原因，不把内部代码留给运营猜。"""
    messages = {
        "outside_ui_month": "所选时刻不在当前月历范围内",
        "outside_window": "所选时刻已经过去或不在本月可选范围内",
        "ambiguous_ui_time": "这个时刻在时区切换中不明确，请重新选择",
        "calendar_incomplete": "某条月历记录未读完整，不能确认这个时刻空闲",
        "channels_unavailable": "月历里的渠道无法识别，不能确认这个时刻空闲",
        "conflict": "所选时刻冲突",
    }
    return messages.get(decision.reason, "这个时刻暂不能排期（%s）" % decision.reason)


def calendar_bounds(now: datetime, *, window: ScheduleWindow) -> CalendarBounds:
    """UTC 边界供日期选择器换算展示；月份来自实测 UI 时区。"""
    utc_now = aware_utc(now)
    local = utc_now.astimezone(resolve_ui_timezone(window.ui_timezone))
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    latest = end - timedelta(microseconds=1)
    if window.max_ahead is not None:
        latest = min(latest, utc_now + window.max_ahead)
    return CalendarBounds(max(start, utc_now + window.min_ahead), latest,
                          start, end, window.ui_timezone)


def evaluate_slot(target: datetime, channel: str, inventory: RemoteSlotInventory, *,
                  now: datetime, window: ScheduleWindow,
                  gap_minutes: float | None = None) -> SlotDecision:
    """同渠道保留最小间隔；冲突只给互不冲突的邻近建议，不改变人工时刻。"""
    utc_target = aware_utc(target)
    aware_utc(now)
    if channel not in {"facebook", "instagram"}:
        raise ValueError("未知目标渠道：%s" % channel)
    if gap_minutes is None:
        gap_minutes = cfg().get("publish", "min_channel_gap_min", 90)
    if (isinstance(gap_minutes, bool) or not isinstance(gap_minutes, (int, float))
            or not math.isfinite(gap_minutes) or gap_minutes <= 0):
        raise ValueError("同渠道排期间隔必须是大于零的有限分钟数")
    gap = timedelta(minutes=gap_minutes)
    bounds = calendar_bounds(now, window=window)
    if not bounds.start_inclusive <= utc_target < bounds.end_exclusive:
        return SlotDecision(False, "outside_ui_month")
    if utc_target <= aware_utc(now) or not bounds.earliest <= utc_target <= bounds.latest:
        return SlotDecision(False, "outside_window")
    if ui_time_is_ambiguous(utc_target, window.ui_timezone):
        return SlotDecision(False, "ambiguous_ui_time")
    if inventory.ui_timezone != window.ui_timezone:
        return SlotDecision(False, "calendar_incomplete")
    def covered(at):
        # 目标本身可见不足以判断前后90分钟；跨可见边界时不能猜另一月为空。
        return inventory.covers([at - gap, at + gap])

    if not covered(utc_target):
        return SlotDecision(False, "calendar_incomplete")

    def occupancy(at):
        return tuple(aware_utc(card.at) for card in inventory.cards_in_range(
            channel, at-gap, at+gap, include_bounds=False))

    try:
        occupied = occupancy(utc_target)
    except ProbeRequired:
        return SlotDecision(False, "channels_unavailable")
    conflicts = tuple(at.astimezone(target.tzinfo) for at in occupied if abs(at - utc_target) < gap)
    if not conflicts:
        return SlotDecision(True, "available")

    suggestions: list[datetime] = []
    # 按分钟搜索，范围受已确认 UI 能力及排期窗口限制。
    anchor = utc_target.replace(second=0, microsecond=0)
    horizon = max(abs(bounds.earliest - anchor), abs(bounds.latest - anchor))
    for distance in range(int(horizon.total_seconds() // 60) + 2):
        for sign in ((1,) if distance == 0 else (1, -1)):
            candidate = anchor + timedelta(minutes=sign * distance)
            if (candidate <= aware_utc(now)
                    or not bounds.earliest <= candidate <= bounds.latest or not covered(candidate)
                    or any(abs(candidate - at) < gap for at in suggestions)
                    or ui_time_is_ambiguous(candidate, window.ui_timezone)):
                continue
            try:
                if any(abs(candidate - at) < gap for at in occupancy(candidate)):
                    continue
            except ProbeRequired:
                continue
            suggestions.append(candidate)
            if len(suggestions) == 3:
                return SlotDecision(False, "conflict", conflicts,
                                    tuple(at.astimezone(target.tzinfo) for at in suggestions))
    return SlotDecision(False, "conflict", conflicts,
                        tuple(at.astimezone(target.tzinfo) for at in suggestions))

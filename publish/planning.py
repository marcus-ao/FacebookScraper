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
    try:
        occupied = tuple(aware_utc(at) for at in inventory.occupied_for_channel(channel))
    except ProbeRequired:
        return SlotDecision(False, "channels_unavailable")

    def covered(at):
        # 目标本身可见不足以判断前后90分钟；跨可见边界时不能猜另一月为空。
        return inventory.covers([at - gap, at + gap])

    if not covered(utc_target):
        return SlotDecision(False, "calendar_incomplete")
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
                    or any(abs(candidate - at) < gap for at in (*occupied, *suggestions))
                    or ui_time_is_ambiguous(candidate, window.ui_timezone)):
                continue
            suggestions.append(candidate)
            if len(suggestions) == 3:
                return SlotDecision(False, "conflict", conflicts,
                                    tuple(at.astimezone(target.tzinfo) for at in suggestions))
    return SlotDecision(False, "conflict", conflicts,
                        tuple(at.astimezone(target.tzinfo) for at in suggestions))

"""月历只读展示与显式刷新；浏览器访问复用业务读取器与发布锁。"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.config import ROOT, cfg
from publish import business_suite as bs
from publish import local_schedule, planning
from publish import journal
from publish.planner_cache import inventory_from_cache, read_cache, read_live_inventory, refresh_cache
from publish.planner_content import public_permalink
from publish.planning import calendar_bounds, configured_window

router = APIRouter()

_ERRORS = {
    "timeout": "刷新超时，保留上次读取的数据。",
    "coverage_unavailable": "未能确认本次日历覆盖的月份，保留上次读取的数据。",
    "read_failed": "未能读取发布日历，请检查发布浏览器后重试。",
    "cache_unavailable": "月历缓存无法读取，请联系维护人员检查。",
}

_DETAIL_ERRORS = {
    'unsupported_type': '内容类型尚未适配', 'missing_fields': '必要业务字段缺失',
    'identity_unverified': '无法核实独立渠道身份', 'identity_mismatch': '目标账号或渠道不符',
    'time_mismatch': '日期或时刻不一致', 'navigation_failed': '详情导航失败',
    'load_timeout': '详情业务字段加载超时', 'permission_denied': '详情不可访问或权限不足',
    'structure_unknown': '详情结构尚未识别', 'read_failed': '条目读取失败',
}


def source_permalinks_by_remote() -> dict[tuple[str, str], str]:
    """审核归档里的公开地址，按发布记录的远端编号对上月历卡片。对不上就不填。"""
    state = Path(cfg().get("paths", "state", "state"))
    archived: dict[tuple[str, str], str] = {}
    database = state / "index.sqlite"
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                for platform, post_id, permalink in connection.execute(
                        "SELECT platform, post_id, permalink FROM posts WHERE ifnull(permalink, '') != ''"):
                    checked = public_permalink(permalink)
                    if checked:
                        archived[(platform, post_id)] = checked
        except sqlite3.Error:
            archived = {}
    try:
        attempts = journal.load(state)
    except (OSError, ValueError):
        return {}
    found: dict[tuple[str, str], str] = {}
    for row in attempts:
        permalink = archived.get((row.get("platform"), row.get("post_id")))
        if not permalink:
            continue
        for channel, remote in re.findall(r"(facebook|instagram)=(\d{6,})", str(row.get("remote_id") or "")):
            found[(channel, remote)] = permalink
    return found


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def _readiness() -> str | None:
    try:
        cfg().assert_publish_chrome_isolated()
        bs.require_readback_evidence()
    except (Exception, SystemExit) as exc:
        return str(exc)
    return None


def calendar_payload(*, snapshot: dict | None = None, now: datetime | None = None) -> dict:
    config, now = cfg(), now or current_time()
    # Config.state_dir 会创建目录；GET 必须保留无缓存时的纯读取行为。
    state_dir = ROOT / config.get("paths", "state", "state")
    snapshot = snapshot or read_cache(state_dir / "planner_cache.json", now=now)
    ui_timezone = str(config.get("publish", "ui_timezone", ""))
    business_timezone = bs.business_timezone()
    ui_zone, business_zone = bs.resolve_ui_timezone(ui_timezone), bs.resolve_business_timezone()
    local = now.astimezone(ui_zone)
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = month_start.replace(year=month_start.year + (month_start.month == 12),
                                    month=month_start.month % 12 + 1)
    bounds = {}
    for platform in ("facebook", "instagram"):
        try:
            window = configured_window(platform)
            value = calendar_bounds(now, window=window)
            bounds[platform] = {"available": True, "error": None,
                                **{name: getattr(value, name).isoformat() for name in
                                   ("earliest", "latest", "start_inclusive", "end_exclusive")}}
        except Exception as exc:
            bounds[platform] = {"available": False, "error": str(exc)}
    data = snapshot.get("inventory") or {}
    inventory = inventory_from_cache(snapshot)
    start, end = data.get("visible_start"), data.get("visible_end")
    matches = bool(start and end and data.get("ui_timezone") == ui_timezone
                   and start <= month_start.date().isoformat()
                   and end >= (month_end.date() - timedelta(days=1)).isoformat())
    cards = []
    remote_links = source_permalinks_by_remote()
    for card in data.get("cards", []):
        at = datetime.fromisoformat(card["at"])
        if at.astimezone(ui_zone).strftime("%Y-%m") == local.strftime("%Y-%m"):
            links = dict(card.get("permalinks") or {})
            for channel, remote in (card.get("remote_ids") or {}).items():
                found = remote_links.get((channel, str(remote)))
                if found:
                    links[channel] = found
            cards.append({**card, "permalinks": links,
                          "at_business": at.astimezone(business_zone).isoformat(),
                          "audience": planning.audience_local(at)})
    cards.sort(key=lambda item: item["at"])

    def coverage(value):
        return {'grid_complete': bool(value and value.cards_loaded),
                'entries_complete': bool(value and value.cards_loaded),
                'classification_complete': bool(value and value.classification_complete),
                'decision_complete': bool(value and value.decision_complete),
                'channels_complete': bool(value and value.channels_complete),
                'occupancy_complete': bool(value and value.occupancy_complete),
                'unresolved_count': sum(card.read_status!='complete' for card in value.cards) if value else 0,
                'visible_start': str(value.visible_start) if value and value.visible_start else None,
                'visible_end': str(value.visible_end) if value and value.visible_end else None,
                'matches_current_month': bool(value and value.ui_timezone==ui_timezone
                    and value.covers([month_start, month_end-timedelta(microseconds=1)]))}
    # ⛔ 本地图层只画给人看，不进 planning.evaluate_slot 的占用判定。
    local_layer, local_error = [], None
    try:
        for entry in local_schedule.entries():
            at = datetime.fromisoformat(entry["at"]) if entry["at"] else None
            if at is not None and at.astimezone(ui_zone).strftime("%Y-%m") != local.strftime("%Y-%m"):
                continue
            local_layer.append({
                **entry, "audience": planning.audience_local(at) if at else None,
                "at_business": at.astimezone(business_zone).isoformat() if at else None})
    except Exception as exc:
        # 坏账本必须显式失败，不能当成「本地没有排期」继续画（HANDOFF 红线 9）。
        local_error = str(exc)
    unavailable = _readiness()
    diagnostic = snapshot.get('refresh_diagnostic')
    error = _ERRORS.get(snapshot.get('refresh_error'))
    if error and diagnostic:
        stages = {'item_ready': '条目日期与正文', 'scheduled_detail': '排期详情',
                  'published_detail': '已发布详情'}
        error += ' 条目 %s %s，读取阶段：%s。' % (
            diagnostic['date'], diagnostic['time'], stages.get(diagnostic['stage'], '条目读取'))
        error += _DETAIL_ERRORS.get(diagnostic.get('code'), '条目读取失败') + '。'
    return {"status": snapshot["status"], "cached_at": snapshot.get("observed_at"),
            "stale": snapshot["status"] in {"stale", "clock_skew", "unavailable"} or
                     snapshot.get('refresh_status')=='failed' or
                     bool(snapshot.get("observed_at") and not matches),
            "error": error, "refresh_diagnostic": diagnostic,
            "refresh_status": snapshot.get("refresh_status"),
            "age_seconds": snapshot.get("age_seconds"), "cards": cards,
            "local": local_layer, "local_error": local_error,
            "coverage": coverage(inventory),
            "bounds": bounds, "gap_minutes": config.get("publish", "min_channel_gap_min", 90),
            "refresh_available": unavailable is None, "refresh_unavailable_reason": unavailable,
            "advisory_only": True, "month_ui": local.strftime("%Y-%m"),
            "ui_timezone": ui_timezone, "business_timezone": business_timezone,
            "audience_timezone": planning.AUDIENCE_TIMEZONE,
            "display_start": month_start.astimezone(business_zone).isoformat(),
            "display_end_exclusive": month_end.astimezone(business_zone).isoformat()}


@router.get("/api/calendar")
def get_calendar() -> JSONResponse:
    return JSONResponse(calendar_payload(), headers={"Cache-Control": "no-store"})


@router.post("/api/calendar/refresh")
async def post_calendar_refresh() -> JSONResponse:
    unavailable = _readiness()
    if unavailable:
        return JSONResponse({**calendar_payload(), "detail": unavailable}, status_code=409)
    config = cfg()
    result = await refresh_cache(config.state_dir / "planner_cache.json", read_live_inventory,
                                 state_dir=config.state_dir, clock=current_time)
    payload = calendar_payload(snapshot=result, now=current_time())
    if result.get("refresh_status") == "busy":
        return JSONResponse({**payload, "detail": "有发布操作正在进行，本次刷新已跳过。"}, status_code=409)
    if result.get("refresh_status") == "failed":
        return JSONResponse({**payload, "detail": payload["error"]}, status_code=502)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})

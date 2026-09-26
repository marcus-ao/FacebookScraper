"""月历只读展示与显式刷新；浏览器访问复用业务读取器与发布锁。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.config import ROOT, cfg
from publish import business_suite as bs
from publish import local_schedule, planning
from publish.planner_cache import inventory_from_cache, read_cache, read_live_inventory, refresh_cache
from publish.planner_content import public_permalink
from publish.planning import calendar_bounds, configured_window
from web.api import reader

router = APIRouter()

# 跨发实测渠道分钟相差 1 分钟（REQUIREMENTS F5-5：7:17/7:18）；
# 这只是来源关联的候选窗口，不是排期间隔；相邻分钟多帖须唯一匹配，歧义不填。
SOURCE_MATCH_TOLERANCE = timedelta(minutes=5)

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


def _source_for(card: dict, scheduled_entries: list[dict]) -> dict | None:
    """排期与发布 ID 不同；逐渠道优先精确编号，再按时刻匹配，歧义不填。"""
    if card.get('delivery') not in {'published', 'scheduled'} or not card.get('channels'):
        return None
    at = datetime.fromisoformat(card['at'])
    matched = {}
    for channel in card['channels']:
        candidates = [entry for entry in scheduled_entries
                      if entry['kind'] == local_schedule.SCHEDULED and channel in entry['channels']]
        remote = (card.get('remote_ids') or {}).get(channel)
        exact = [entry for entry in candidates
                 if remote and f'{channel}={remote}' in entry['remote_id'].split(';')]
        matches = exact or [entry for entry in candidates if entry['at']
                           and abs(at - datetime.fromisoformat(entry['at'])) <= SOURCE_MATCH_TOLERANCE]
        by_task = {entry['task_id']: entry for entry in matches}
        if len(by_task) != 1:
            return None
        matched.update(by_task)
    return next(iter(matched.values())) if len(matched) == 1 else None


def _sources_for(cards: list[dict], scheduled_entries: list[dict]) -> list[dict | None]:
    """同一任务/渠道只能由一张卡认领；远端卡间的竞争也不能靠时刻猜。"""
    sources = [_source_for(card, scheduled_entries) for card in cards]
    claims, exact = {}, {}
    for index, (card, source) in enumerate(zip(cards, sources)):
        if source is None:
            continue
        for channel in card['channels']:
            key = (source['task_id'], channel)
            claims.setdefault(key, set()).add(index)
            remote = (card.get('remote_ids') or {}).get(channel)
            if remote and f'{channel}={remote}' in source['remote_id'].split(';'):
                exact.setdefault(key, set()).add(index)
    for index, (card, source) in enumerate(zip(cards, sources)):
        if source is not None and any(
                (exact.get((source['task_id'], channel)) or claims[(source['task_id'], channel)]) != {index}
                for channel in card['channels']):
            sources[index] = None
    return sources


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
    # 本地图层只作展示，不进 planning.evaluate_slot；来源匹配使用过滤月份前的全量条目。
    entries, local_layer, local_error = [], [], None
    try:
        entries = local_schedule.entries()
        for entry in entries:
            at = datetime.fromisoformat(entry["at"]) if entry["at"] else None
            if at is not None and at.astimezone(ui_zone).strftime("%Y-%m") != local.strftime("%Y-%m"):
                continue
            local_layer.append({
                **entry, "audience": planning.audience_local(at) if at else None,
                "at_business": at.astimezone(business_zone).isoformat() if at else None})
    except Exception as exc:
        # 坏账本必须显式失败，不能当成「本地没有排期」继续画（HANDOFF 红线 9）。
        entries, local_layer = [], []
        local_error = str(exc)
    cards = []
    represented = {}
    remote_cards = data.get('cards', [])
    for card, source in zip(remote_cards, _sources_for(remote_cards, entries)):
        at = datetime.fromisoformat(card["at"])
        if at.astimezone(ui_zone).strftime("%Y-%m") == local.strftime("%Y-%m"):
            permalink = None
            if source:
                try:
                    post = reader.source_post(source['task_id'])
                    if post:
                        permalink = public_permalink(post.row.get('permalink')) or None
                except (OSError, ValueError):
                    pass
            cards.append({**card,
                          "source_task_id": source['task_id'] if source else None,
                          "source_platform": source['platform'] if source else None,
                          "source_permalink": permalink,
                          "at_business": at.astimezone(business_zone).isoformat(),
                          "audience": planning.audience_local(at)})
            if source and card.get('time_verified') and card['delivery'] in {'scheduled', 'published'}:
                # Time-near source links are navigation hints, not identity.
                # Collapse a receipt only for its exact per-channel remote IDs.
                exact_channels = {channel for channel, remote_id in card['remote_ids'].items()
                                  if remote_id and f'{channel}={remote_id}' in source['remote_id'].split(';')}
                represented.setdefault(source['task_id'], set()).update(exact_channels)
    cards.sort(key=lambda item: item["at"])
    # Merge only uniquely identified scheduled receipts into their displayed
    # remote cards. Unmatched/pending receipts remain visible; no ledger edit.
    local_layer = [entry for entry in local_layer if not (
        entry['kind'] == local_schedule.SCHEDULED and entry['channels']
        and set(entry['channels']) <= represented.get(entry['task_id'], set()))]

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
            "bounds": bounds, "gap_minutes": config.get("publish", "min_channel_gap_min", 1),
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

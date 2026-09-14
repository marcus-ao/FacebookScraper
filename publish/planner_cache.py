"""展示用月历缓存，刷新共用发布锁；提交前仍须读取远端，不能将缓存当新证据。"""
from __future__ import annotations

from publish.observations import record as record_publication
import json
from datetime import date, datetime, timezone
from pathlib import Path

from core.chrome import attach
from core.config import cfg
from core.paid_model import atomic_write_json
from core.store import assert_physical_direct_path
from publish.business_suite import (ProbeRequired, RemotePlannerCard, RemoteSlotInventory,
                                    resolve_ui_timezone)
from publish.journal import PublishOperationLock
from publish.planning import aware_utc
from publish import business_suite as bs
from publish import month_inventory


async def read_live_inventory() -> RemoteSlotInventory:
    """供已持发布锁的调用者读取；只新开并关闭自己的页，不启动/登录浏览器。"""
    bs.require_readback_evidence()
    config = cfg()
    config.assert_publish_chrome_isolated()
    pw = page = None
    try:
        pw, _browser, context = await attach(
            port=config.publish_debug_port, profile=config.publish_profile_dir,
            start_script=r"scripts\start_chrome_publish.bat", login_hint="DE 发布账号")
        page = await context.new_page()
        return await month_inventory.read(
            page, ui_timezone=str(config.get("publish", "ui_timezone", "")),
            business_timezone=str(config.get("publish", "timezone", "Europe/Berlin")),
            timeout=float(config.get("publish", "ui_timeout_seconds", bs.DEFAULT_UI_TIMEOUT)))
    finally:
        try:
            if page is not None:
                await page.close()
        finally:
            if pw is not None:
                await pw.stop()


def _moment(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return aware_utc(value)


def _guard(path: Path, _role: str = "target") -> Path:
    path = Path(path)
    assert_physical_direct_path(path.parent.parent, path.parent,
                                kind="directory", label="月历缓存目录")
    return assert_physical_direct_path(path.parent, path, kind="file", label="月历缓存文件")


def _empty() -> dict:
    return {"version": 1, "observed_at": None, "last_attempt_at": None,
            "refresh_status": "idle", "refresh_error": None, "inventory": None}


def _serialize(inventory: RemoteSlotInventory) -> dict:
    return {"occupied": [_moment(at).isoformat() for at in inventory.occupied],
            "ui_timezone": inventory.ui_timezone,
            "visible_start": inventory.visible_start.isoformat() if inventory.visible_start else None,
            "visible_end": inventory.visible_end.isoformat() if inventory.visible_end else None,
            "cards_loaded": inventory.cards_loaded,
            "cards": [{"at": _moment(card.at).isoformat(), "channels": list(card.channels),
                       "remote_ids": dict(card.remote_ids), "rendered": card.rendered,
                       "card_sha256": card.card_sha256, 'delivery': card.delivery} for card in inventory.cards]}


def inventory_from_cache(snapshot: dict) -> RemoteSlotInventory | None:
    """仅作离线建议。返回值没有实时读取凭据，不授予提交权限。"""
    data = snapshot.get("inventory")
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cards_loaded"), bool):
        raise ValueError("月历缓存格式无效")
    resolve_ui_timezone(data["ui_timezone"])
    cards = []
    for row in data["cards"]:
        channels = row["channels"]
        if (not isinstance(channels, list) or any(channel not in {"facebook", "instagram"}
                                                for channel in channels)
                or not isinstance(row["remote_ids"], dict)
                or any(channel not in channels or not isinstance(remote, str)
                       for channel, remote in row["remote_ids"].items())
                or not isinstance(row["rendered"], str) or not isinstance(row["card_sha256"], str)):
            raise ValueError("月历卡片格式无效")
        if row.get('delivery', 'unknown') not in {'unknown', 'scheduled', 'published'}:
            raise ValueError('月历公开发布状态无效')
        cards.append(RemotePlannerCard(_moment(row["at"]), tuple(channels),
                                      tuple(sorted(row["remote_ids"].items())),
                                      row["rendered"], row["card_sha256"], row.get('delivery', 'unknown')))
    return RemoteSlotInventory(
        tuple(_moment(at) for at in data["occupied"]), data["ui_timezone"],
        date.fromisoformat(data["visible_start"]) if data.get("visible_start") else None,
        date.fromisoformat(data["visible_end"]) if data.get("visible_end") else None,
        tuple(cards), data["cards_loaded"])


def _load(path: Path) -> dict:
    _guard(path)
    if not path.exists():
        return _empty()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("月历缓存版本无效")
    for key in ("observed_at", "last_attempt_at"):
        if value.get(key) is not None:
            _moment(value[key])
    inventory = inventory_from_cache(value)
    if (inventory is None) != (value.get("observed_at") is None):
        raise ValueError("月历数据与成功观测时间必须同时存在")
    return value


def read_cache(path: Path, *, now: datetime | None = None,
               stale_after_seconds: float = 3600) -> dict:
    """无文件写入。status 区分缺失、部分数据、过期和时钟倒退。"""
    now = _moment(now or datetime.now(timezone.utc))
    try:
        record = _load(Path(path))
    except Exception:
        return {**_empty(), "status": "unavailable", "advisory_only": True,
                "age_seconds": None, "refresh_error": "cache_unavailable"}
    age = ((now - _moment(record["observed_at"])).total_seconds()
           if record.get("observed_at") else None)
    if age is None:
        status = "unavailable" if record.get("refresh_error") else "missing"
    elif age < 0:
        status = "clock_skew"
    elif age >= stale_after_seconds:
        status = "stale"
    elif not inventory_from_cache(record).channels_complete:
        status = "partial"
    else:
        status = "ready"
    return {**record, "status": status, "advisory_only": True, "age_seconds": age}


async def refresh_cache(path: Path, reader, *, state_dir: Path,
                        now: datetime | None = None, clock=None) -> dict:
    """低优先级尝试一次；reader 异常不损失之前的数据及观测时间。"""
    path, state_dir = Path(path), Path(state_dir)
    clock = clock or (lambda: datetime.now(timezone.utc))
    attempted = _moment(now or clock())
    try:
        _guard(path)
        _guard(state_dir / "publish.lock")
    except Exception:
        return {**read_cache(path, now=attempted), "refresh_status": "failed",
                "refresh_error": "cache_unavailable"}
    lock = PublishOperationLock(state_dir / "publish.lock")
    try:
        lock.__enter__()
    except RuntimeError:
        return {**read_cache(path, now=attempted), "refresh_status": "busy"}
    try:
        corrupt = False
        try:
            record = _load(path)
        except Exception:
            record, corrupt = _empty(), True
        record["last_attempt_at"] = attempted.isoformat()
        try:
            inventory = await reader()
            if (not isinstance(inventory, RemoteSlotInventory) or not inventory.cards_loaded
                    or inventory.visible_start is None or inventory.visible_end is None
                    or inventory.visible_start > inventory.visible_end):
                raise ProbeRequired("coverage_unavailable")
            # 再做一次序列化往返，防止坏数据让此前成功缓存无法读取。
            data = _serialize(inventory)
            inventory_from_cache({"inventory": data})
            observed = _moment(now or clock())
            record.update(inventory=data, observed_at=observed.isoformat(),
                          refresh_status="refreshed", refresh_error=None)
        except Exception as exc:
            code = ("timeout" if isinstance(exc, TimeoutError) else
                    "coverage_unavailable" if isinstance(exc, ProbeRequired) else "read_failed")
            record.update(refresh_status="failed", refresh_error=code)
            if corrupt:
                # 失败读取不能用空壳抹去原文件；后续成功完整读取可以重建缓存。
                return {**read_cache(path, now=attempted), "refresh_status": "failed",
                        "refresh_error": code, "last_attempt_at": attempted.isoformat()}
        atomic_write_json(path, record, guard=_guard)
        if record['refresh_status'] == 'refreshed':
            record_publication(state_dir, inventory, observed)
        return read_cache(path, now=now or clock())
    except Exception:
        return {**read_cache(path, now=attempted), "refresh_status": "failed",
                "refresh_error": "cache_unavailable"}
    finally:
        lock.__exit__(None, None, None)

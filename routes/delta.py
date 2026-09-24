"""登录态监测：首屏轮询与每日有限深扫共用 detect 会话及失败预算。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from core import maintenance
from core.archive_integrity import archive_report, reconcile_instagram
from core.capture import (Collector, atomic_write_json, download_media,
                          prune_captures_days, MediaRateLimited, reuse_image)
from core.capture_state import CaptureState, verified_images
from core.chrome import attach, cdp_ready, launch
from core import account_roles
from core.config import MonitorSchedule, cfg, per_platform
from core import integrity
from core.integrity import parse_ts
from core.monitor_access import AccessController, AccessDenied
from core.monitoring import MonitoringJournal
from core.notify import notify
from core.parse import extract, merge_post, partition_by_owner, walk
from core.store import Archive, Media, Post, refresh_signed_media_urls, signed_url_expiry
from core.paid_model import FileLock

PLATFORMS = ("facebook", "instagram")

# 登录墙 / 安全挑战的 URL 特征。命中任一即**当次立即停止**，不重试、不换 UA。
LOGIN_URL_MARKERS = ("/accounts/login", "/login.php", "/login/", "/login?",
                     "/checkpoint", "/challenge")

# 每屏滚动的像素区间。定成区间而不是定值：匀速等距滚动本身就是行为指纹。
WHEEL_PX = (700, 1500)


def profile_url(platform: str, account: str) -> str:
    return {"instagram": "https://www.instagram.com/%s/",
            "facebook": "https://www.facebook.com/%s/"}[platform] % account


@dataclass
class DeltaConfig:
    """`[delta]` 的全部参数。**代码里不写死数字**（计划第 2 节）。"""
    request_gap_seconds: float = 8.0
    max_scrolls: int = 0
    first_screen_seconds: float = 6.0
    first_screen_timeout_seconds: float = 18.0
    max_session_seconds: float = 300.0
    failure_budget: int = 3
    autostart_chrome: bool = True
    keep_captures_days: int = 7
    min_own_posts: int = 3
    schedule: MonitorSchedule = field(default_factory=MonitorSchedule)
    run_kind: str = "delta"
    access: object = None
    scan_id: str = ""
    source_failures: list[str] = field(default_factory=list)
    source_attempts: int = 0
    deadline: float = 0.0
    diagnostics: dict = field(default_factory=dict)

    @classmethod
    def load(cls, c=None) -> "DeltaConfig":
        c = c if c is not None else cfg()

        def g(key, default):
            return c.get("delta", key, default)

        return cls(
            request_gap_seconds=float(g("request_gap_seconds", 8.0)),
            max_scrolls=int(g("max_scrolls", 0)),
            first_screen_seconds=float(g("first_screen_seconds", 6.0)),
            first_screen_timeout_seconds=float(g("first_screen_timeout_seconds", 18.0)),
            max_session_seconds=float(g("max_session_seconds", 300.0)),
            failure_budget=int(g("failure_budget", 3)),
            autostart_chrome=bool(g("autostart_chrome", True)),
            keep_captures_days=int(g("keep_captures_days", 7)),
            min_own_posts=int(g("min_own_posts", 3)),
            schedule=MonitorSchedule.load(c),
        )

    def scroll_pause(self) -> tuple[float, float]:
        """滚动之间的停顿区间。配置给下界，上界取两倍——随机化本身比倍数重要。"""
        return self.request_gap_seconds, self.request_gap_seconds * 2


class DeltaBlocked(RuntimeError):
    """中止扫描并记录失败；hard=True 同停两平台，False 仅停当前平台。"""

    def __init__(self, reason: str, hard: bool = False):
        super().__init__(reason)
        self.hard = hard


class DeltaRunAlreadyActive(RuntimeError):
    """另一个每日/补跑实例已经持有全局增量锁。"""


def DeltaRunLock(path: Path) -> FileLock:      # noqa: N802（保留原名，调用点不变）
    """跨计划任务的独占锁，覆盖状态读取、抖动、浏览器操作和状态写入。"""
    return FileLock(path, error_type=DeltaRunAlreadyActive,
                    busy_message="另一个增量实例正在运行；本次不再附着浏览器或写状态。")


def state_path() -> Path:
    return cfg().state_dir / "delta_state.json"


def blank_entry() -> dict:
    return {"first_success": None, "last_run": None, "last_success": None,
            "last_new_at": None, "last_new_count": 0,
            "consecutive_quiet_days": 0, "consecutive_failures": 0,
            "last_error": None}


def bind_target_state(state: dict, c) -> None:
    """换号不能继承旧账号的成功时刻/预算；原状态另存，便于历史排查。"""
    for platform in PLATFORMS:
        target = c["targets"][platform]
        entry = state.setdefault(platform, blank_entry())
        previous = entry.get("account")
        # 唯一没有身份字段的历史 IG 状态属于已退休的 .tech。
        if previous is None and platform == "instagram" and target == "neakasa.global":
            previous = "neakasa.tech" if any(entry.get(k) for k in ("last_success", "last_run", "consecutive_failures")) else None
        if previous and previous != target:
            state.setdefault("retired_targets", {})[f"{platform}:{previous}"] = dict(entry)
            entry = state[platform] = blank_entry()
        entry["account"] = target


def load_state(path: Path) -> dict:
    """Legacy safety evidence must remain readable until explicitly migrated."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state must be an object")
        for platform in PLATFORMS:
            if platform in data and not isinstance(data[platform], dict):
                raise ValueError("platform state must be an object")
        return data
    except (OSError, ValueError) as exc:
        raise AccessDenied("delta state unreadable; preserve and repair safety evidence") from exc


def save_state(path: Path, state: dict) -> None:
    """原子保存状态，失败时保留上一份完整 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=".%s." % path.name, suffix=".tmp")
        tmp_path = Path(tmp_name)
        stream = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        fd = -1  # stream 从这里起拥有描述符；异常退出也由 with 负责关闭。
        with stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        tmp_path.replace(path)
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                # replace 成功后源路径已经不存在；失败时则在这里清掉残片。
                pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def quiet_days(entry: dict, now: datetime) -> int:
    """距最近发现新帖的天数；尚无新帖时从首次成功起算，不按扫描次数计算。"""
    anchor = parse_ts(entry.get("last_new_at")) or parse_ts(entry.get("first_success"))
    if anchor is None:
        return 0
    return max(0, int((now - anchor).total_seconds() // 86400))


def record_success(entry: dict, now: datetime, new_count: int) -> dict:
    entry["last_run"] = iso(now)
    entry["last_success"] = iso(now)
    entry.setdefault("first_success", None)
    if not entry["first_success"]:
        entry["first_success"] = iso(now)
    if new_count:
        entry["last_new_at"] = iso(now)
    entry["last_new_count"] = new_count
    entry["consecutive_failures"] = 0
    entry["last_error"] = None
    entry["consecutive_quiet_days"] = quiet_days(entry, now)
    return entry


def record_failure(entry: dict, now: datetime, error: str) -> dict:
    """记录失败且不刷新 last_success，保持 stale 判据有效。"""
    entry["last_run"] = iso(now)
    entry["last_error"] = error
    entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
    return entry


def budget_exhausted(entry: dict, budget: int) -> bool:
    """连续失败达到预算后停止自动运行。"""
    if budget <= 0:
        return False
    return int(entry.get("consecutive_failures") or 0) >= budget


def effective_stale_hours(entry: dict, dcfg: DeltaConfig, platform: str,
                          *, now: datetime | None = None) -> float:
    """按当前上海窗口的最短随机间隔去重。

    连续零新增不再降频（2026-09-15 决定）：访问预算已经把主页封在 24 次/天，
    再叠一层降频只会让"今天真没帖"和"探测坏了"更难分。`consecutive_quiet_days`
    仍然记录，但只喂 `integrity.alert_after_quiet_days` 的零新增告警。
    """
    return dcfg.schedule.minimum_interval_minutes(now or utcnow()) / 60


def stale_enough(entry: dict, now: datetime, hours: float) -> tuple[bool, str]:
    """`--if-stale` 的判定。返回 (要不要跑, 给人看的理由)。"""
    last = parse_ts(entry.get("last_success"))
    if last is None:
        return True, "还没有成功跑过"
    elapsed = (now - last).total_seconds() / 3600.0
    if elapsed < hours:
        return False, "距上次成功 %.0f 分钟，不足 %.0f 分钟" % (elapsed * 60, hours * 60)
    return True, "距上次成功 %.1f 小时" % elapsed


def login_wall_reason(final_url: str, blocked: tuple[int, str] | None = None) -> str | None:
    """从落地 URL 和接口状态码识别登录墙或限流；页面外壳正常不代表可访问。"""
    url = (final_url or "").lower()
    for marker in LOGIN_URL_MARKERS:
        if marker in url:
            return "页面进入登录墙或验证挑战（%s），会话可能已失效，请人工核对" % marker
    if blocked:
        status, endpoint = blocked
        if status == 429:
            return "接口返回 429（限流），探测身份已暂停"
        return "接口返回 %d（会话失效或权限不足）" % status
    return None


async def _pause(window: tuple[float, float]) -> None:
    """异步随机等待，保持响应体读取的事件循环运行。"""
    await asyncio.sleep(random.uniform(*window))


async def _guarded_wait(seconds, guard):
    while seconds > 0:
        guard()
        step = min(seconds, 0.2)
        await asyncio.sleep(step)
        seconds -= step
    guard()


async def _eval(page, expr: str, default):
    """页面求值，失败返回默认值。这些都是辅助信息，不该让抓取失败。"""
    try:
        value = await page.evaluate(expr)
    except Exception:
        return default
    return default if value is None else value


async def human_scroll(page, dcfg: DeltaConfig, guard=None) -> tuple[int, float]:
    """有限滚动并返回 (屏数, 实际位移)；滚轮先定位到主视口。"""
    screens = max(0, dcfg.max_scrolls)
    if not screens:
        return 0, 0.0
    size = await _eval(page, "() => [window.innerWidth, window.innerHeight]",
                       [1280, 800])
    await page.mouse.move(int(size[0]) // 2, int(size[1]) // 2)
    before = float(await _eval(page, "() => window.scrollY", 0) or 0)
    for _ in range(screens):
        if guard:
            guard()
        await page.mouse.wheel(0, random.randint(*WHEEL_PX))
        if guard:
            await _guarded_wait(random.uniform(*dcfg.scroll_pause()), guard)
        else:
            await _pause(dcfg.scroll_pause())
    after = float(await _eval(page, "() => window.scrollY", 0) or 0)
    return screens, after - before


async def scan_page(ctx, url: str, dcfg: DeltaConfig) -> tuple[Collector, str]:
    """打开主页、滚有限几屏、把接口响应捞下来。返回 (collector, 落地 URL)。"""
    platform = 'instagram' if 'instagram.com' in url else 'facebook'
    def on_block(status):
        if dcfg.access:
            dcfg.access.outcome(platform, success=False, hard=True,
                                reason=f'平台内容接口返回 {status}，探测身份已暂停')
    col = Collector(on_block=on_block)
    page = await ctx.new_page()
    def guard():
        reason = login_wall_reason(page.url, col.blocked_status())
        if reason:
            if not col.blocked_status():
                on_block('登录墙或验证挑战')
            raise DeltaBlocked(reason, hard=True)
        if dcfg.access:
            dcfg.access.check(platform)
    handler = col.submit
    started = time.monotonic()
    deadline = dcfg.deadline or started + dcfg.max_session_seconds
    try:
        page.on("response", handler)
        guard()
        response = await page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status in (401, 403, 429):
            on_block(response.status)
            raise DeltaBlocked(f'平台页面返回 {response.status}，探测身份已暂停', hard=True)
        # 首屏的接口响应是异步来的，goto 返回时通常还没到齐
        await _guarded_wait(dcfg.first_screen_seconds, guard)
        dcfg.diagnostics['initial_response_payloads'] = len(col.payloads)
        if platform == 'facebook':
            # 首屏 Relay 可能在 HTML JSON 中；只读当前页面，不滚动、刷新或请求额外接口。
            wait_until = min(started + max(dcfg.first_screen_seconds, dcfg.first_screen_timeout_seconds),
                             dcfg.deadline or started + dcfg.max_session_seconds)
            while True:
                scripts = await _eval(page, "() => Array.from(document.querySelectorAll('script[type=\"application/json\"]'), s => s.textContent)", [])
                col.add_document_json(scripts)
                account = urlsplit(url).path.strip('/').split('/')[0]
                own, _ = partition_by_owner(extract(col.payloads, platform, account, route='delta'), account)
                if own or time.monotonic() >= wait_until or dcfg.first_screen_seconds == 0 or dcfg.run_kind == 'detail':
                    break
                await _guarded_wait(min(.2, max(0, wait_until - time.monotonic())), guard)
        screens, moved = await human_scroll(page, dcfg, guard)
        if screens and moved <= 0:
            print("[!] 滚了 %d 屏但页面没有移动（scrollY 未变）——"
                  "滚轮事件可能没落在可滚动区域" % screens)
        final_url = page.url
        page.remove_listener("response", handler)
        # 停止监听后 drain，避免页面关闭时取消未读响应。
        await col.drain(timeout=max(0, deadline - time.monotonic()))
        guard()
        return col, final_url
    finally:
        try:
            page.remove_listener("response", handler)
            await col.drain(timeout=max(0, deadline - time.monotonic()))
        finally:
            dcfg.diagnostics.update(page_seconds=round(time.monotonic() - started, 3),
                response_payloads=len(col.payloads), embedded_payloads=col.embedded_payloads,
                incomplete_response_reads=col.incomplete_reads,
                timeline_seen=any(walk(col.payloads, lambda n: bool(n.get('post_id')) or 'timeline_list_feed_units' in n))
                    if platform == 'facebook' else bool(col.payloads))
            await page.close()


@dataclass
class ScanResult:
    """增量结果与诊断，用于区分没有新帖和未看到目标时间线。"""
    new: int = 0
    upgraded: int = 0
    own: int = 0
    rejected: int = 0
    newest_seen: str = ""
    oldest_seen: str = ""
    newest_known: str = ""
    payloads: int = 0
    # 分开记录原创与合作数量，使归属解析异常可见。
    authored: int = 0
    collab: int = 0
    # 被丢弃、但作者是已知合作方的那些（core.integrity.check_dropped_partners）
    suspect: list[dict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    deferred: int = 0
    outside_baseline: int = 0

    def summary(self) -> str:
        span = ("%s ~ %s" % (self.oldest_seen[:10], self.newest_seen[:10])
                if self.newest_seen else "—")
        result = ("新增 %d 篇 · 处理已有帖 %d 篇 · 本轮看到本账号 %d 篇（原创 %d · 合作 %d，%s）"
                "· 丢弃 %d · 归档最新 %s"
                % (self.new, self.upgraded, self.own, self.authored, self.collab, span,
                   self.rejected, self.newest_known[:10] or "—"))
        return result + f" · 基线前仅观察 {self.outside_baseline} 篇 · 尚未开始 {self.deferred} 篇" + " · 发布范围外 " + " / ".join(
            f"{label} {self.skipped.get(key, 0)}" for key, label in
            (("video", "视频"), ("mixed_media", "混合"),
             ("no_media", "无媒体"), ("no_text", "无正文")))

    def stale_view(self) -> bool:
        """看到的最新一篇比归档里最新的还旧 —— 强烈提示"没看到时间线"。"""
        return bool(self.newest_seen and self.newest_known
                    and self.newest_seen < self.newest_known)


def scope_skip_counts(posts) -> dict[str, int]:
    """观察到的内容分类；抓取仍留档，发布范围由下游另行判断。"""
    counts = dict.fromkeys(("video", "mixed_media", "no_media", "no_text"), 0)
    for post in posts:
        kinds = {media.kind for media in post.media}
        if not kinds:
            counts["no_media"] += 1
        elif "video" in kinds:
            counts["mixed_media" if "image" in kinds else "video"] += 1
        elif not (post.text or "").strip():
            counts["no_text"] += 1
    return counts


async def delta_once(ctx, platform: str, account: str, arc: Archive,
                     dcfg: DeltaConfig, *, dry_run: bool = False, facts=None) -> ScanResult:
    """扫描单个平台；使用 should_append 保留残缺补齐机会，被拦时抛 DeltaBlocked。

    ``facts`` 是 MonitoringJournal，用来逐篇记下"发现"与"落档"。只追加 JSONL，不改会话、
    请求节奏或滚动行为；``None`` 或 dry-run 时一个字也不写。
    """
    url = profile_url(platform, account)
    started = time.monotonic()
    dcfg.deadline = started + dcfg.max_session_seconds
    dcfg.diagnostics.update(stage='homepage', budget_seconds=dcfg.max_session_seconds)
    lifecycle = None
    if dcfg.access and not dry_run:
        lifecycle = CaptureState(dcfg.access.path.parent)
        lifecycle.status()
        lifecycle.recover_interrupted(utcnow())
    if dcfg.access:
        dcfg.access.reserve_homepage(platform, dcfg.scan_id, run_kind=dcfg.run_kind)
    try:
        col, final_url = await asyncio.wait_for(
            scan_page(ctx, url, dcfg), timeout=dcfg.max_session_seconds)
    except asyncio.TimeoutError:
        raise DeltaBlocked("页面在 %.0f 秒内没有跑完，已放弃本次"
                           % dcfg.max_session_seconds) from None

    reason = login_wall_reason(final_url, col.blocked_status())
    if reason:
        # 对面在拦我们 —— 硬停，同一次运行不再碰另一个平台
        raise DeltaBlocked(reason, hard=True)
    if not col.payloads:
        raise DeltaBlocked("一个接口响应都没捞到（命中 %d 次）—— "
                           "页面可能没加载出来，或接口路径变了" % col.hits)

    # 解析前保存原始响应，结构漂移时可离线复查。
    if not dry_run:
        dump = arc.base / ("_capture_delta_%d.json" % int(time.time()))
        atomic_write_json(dump, col.payloads)
        prune_captures_days(arc.base, dcfg.keep_captures_days)

    posts = extract(col.payloads, platform, account, route="delta")
    if not posts:
        if not any(walk(col.payloads, lambda n: bool(n.get('post_id')) or 'timeline_list_feed_units' in n)) and platform == 'facebook':
            raise DeltaBlocked("捕获到 %d 段辅助响应，但未取得目标主页帖子时间线数据；请核对首屏加载诊断" % len(col.payloads))
        if platform == 'instagram' and not any(walk(col.payloads, lambda n: bool(n.get('pk') or n.get('shortcode')))):
            raise DeltaBlocked("捕获到辅助响应，但未取得目标主页帖子数据")
        raise DeltaBlocked("捕获到 %d 段响应但一篇都没解析出来 —— "
                           "解析器可能已经与真实结构不符" % len(col.payloads))

    posts, rejected = partition_by_owner(posts, account)
    if rejected and not dry_run:
        arc.record_rejected(rejected)

    known = arc.rows()
    dates = sorted(p.created_at for p in posts if p.created_at)
    target = (account or "").strip().lower()
    n_authored = sum(1 for p in posts if p.owner == target)
    # 合作名单同时读取归档与本批结果，覆盖首次扫描。
    suspect = integrity.check_dropped_partners(
        rejected,
        integrity.known_partners(known + [p.to_row() for p in posts], account))
    res = ScanResult(
        own=len(posts), rejected=len(rejected),
        authored=n_authored, collab=len(posts) - n_authored, suspect=suspect,
        newest_seen=dates[-1] if dates else "", oldest_seen=dates[0] if dates else "",
        newest_known=max((r.get("created_at") or "" for r in known), default=""),
        payloads=len(col.payloads), skipped=scope_skip_counts(posts))

    prepared_at = time.monotonic()
    dcfg.diagnostics.update(stage='prepare', observed=len(posts), capture_started=0, capture_finished=0)
    known_by_id = {r['post_id']: r for r in known}
    baseline = lifecycle.status()['baselines'][platform] if lifecycle else None
    if baseline and baseline['account'] != account.lower():
        raise ValueError('目标账号与监测基线不同，请先核对基线')
    candidates = []
    for post in sorted(posts, key=lambda p: p.created_at or '', reverse=True):
        stamp = parse_ts(post.created_at)
        if (baseline and post.post_id not in known_by_id and stamp is not None
                and stamp < parse_ts(baseline['enabled_at']) - timedelta(days=baseline.get('lookback_days', 30))):
            res.outside_baseline += 1
            if facts is not None and not dry_run:
                facts.fact('post_observed', utcnow(), platform=platform, scan_id=dcfg.scan_id,
                           post_id=post.post_id, created_at=post.created_at, reason='outside_baseline')
            continue
        if post.post_id in known_by_id:
            for media in post.media:
                if media.kind == 'image':
                    reuse_image(arc, post, media)
            if lifecycle and lifecycle.reconcile_local(post, arc, utcnow()):
                res.upgraded += 1
                continue
        if arc.should_append(post):
            candidates.append(post)
    if lifecycle:
        candidates = lifecycle.begin(dcfg.scan_id, candidates,
                                     known_by_id, utcnow())
    # 先保存全部发现，再执行任何下载；第一篇中断也不能抹掉响应里的后续候选。
    for post in candidates:
        if facts is not None and not dry_run:
            facts.fact('post_discovered', utcnow(), platform=platform, scan_id=dcfg.scan_id,
                       post_id=post.post_id, account=post.account, owner=post.owner,
                       coauthors=list(post.coauthors), created_at=post.created_at,
                       permalink=post.permalink, head=(post.text or '')[:300],
                       images=sum(m.kind == 'image' for m in post.media),
                       videos=sum(m.kind == 'video' for m in post.media), known=arc.has(post.post_id))

    # 按目标帖数判断覆盖；空归档仍保留最低门槛，最新帖日期倒退不作失败依据。
    floor = (0 if dcfg.min_own_posts == 0 else
             min(max(1, dcfg.min_own_posts), max(1, len(known))))
    if len(posts) < floor:
        # 中止原因补充疑似合作误丢，便于区分归属错误与访问受阻。
        # 自家已授权账号跨账号重发不是合作判定失效的证据，两类分开说。
        authorized, third_party = integrity.split_suspect_sources(suspect, platform)
        hint = "".join((
            ("；其中 %d 篇来自**已知合作方**（%s）—— 优先怀疑合作帖判定失效，而不是被拦"
             % (len(third_party), integrity.name_suspect_owners(third_party))) if third_party else "",
            ("；另有 %d 篇来自**已授权来源**（%s），多半只是本品牌另一账号重发，不作判据"
             % (len(authorized), integrity.name_suspect_owners(authorized))) if authorized else ""))
        if lifecycle:
            lifecycle.interrupt(dcfg.scan_id, utcnow(), '主页覆盖不足，已发现候选保留并转人工核对')
        raise DeltaBlocked(
            "只看到 %d 篇属于 %s 的帖子（丢弃 %d 篇他人内容，期望至少 %d 篇）"
            " —— 大概率没有拿到时间线，而不是没有新帖%s"
            % (len(posts), account, len(rejected), floor, hint))

    try:
        dcfg.diagnostics['prepare_seconds'] = round(time.monotonic() - prepared_at, 3)
        capture_started_at = time.monotonic()
        reserve_seconds = max(1.0, dcfg.request_gap_seconds)
        for index, post in enumerate(candidates):
            was_known = arc.has(post.post_id)
            head = (post.text or "").replace("\n", " ")[:38]
            if dry_run:
                print("  ~ %s  %s  %s" % (post.post_id, post.created_at or "(无日期)", head))
                if was_known:
                    res.upgraded += 1
                else:
                    res.new += 1
                continue
            if dcfg.deadline - time.monotonic() < reserve_seconds:
                res.deferred = len(candidates) - index
                break
            post_started = time.monotonic()
            dcfg.diagnostics.update(stage='capture', current_post=post.post_id,
                capture_started=dcfg.diagnostics['capture_started'] + 1)
            post = await capture_post(ctx, platform, account, arc, dcfg, post, lifecycle=lifecycle)
            reserve_seconds = max(reserve_seconds, time.monotonic() - post_started)
            dcfg.diagnostics['capture_finished'] += 1
            imgs = verified_images(arc.base, post.to_row())
            vids = sum(1 for m in post.media if m.kind == "video")
            print("  + %s  %d图/%d视频  %s" % (post.post_id, imgs, vids, head))
            if facts is not None:
                # 落点用相对归档根的路径：月份 + 产品 tag 都在里面，是"落到哪了"的完整答案。
                facts.fact("post_captured" if post.media_complete else "post_capture_incomplete",
                           utcnow(), platform=platform, post_id=post.post_id,
                           scan_id=dcfg.scan_id, images=imgs, videos=vids,
                           source_media_complete=post.source_media_complete,
                           source_media_count=post.source_media_count, media_complete=post.media_complete,
                           requires_manual=not post.media_complete,
                           folder=arc.post_dir(post).relative_to(arc.base).as_posix() if arc.has(post.post_id) else None)
            if arc.has(post.post_id):
                if was_known:
                    res.upgraded += 1
                else:
                    res.new += 1
    finally:
        if lifecycle:
            lifecycle.interrupt(dcfg.scan_id, utcnow(), '已开始的采集被中断，请核对本地结果后人工处理')
        dcfg.diagnostics.update(elapsed_seconds=round(time.monotonic() - started, 3),
                                capture_seconds=round(time.monotonic() - capture_started_at, 3),
                                deferred=res.deferred, outside_baseline=res.outside_baseline)
    return res


def _images_need_fresh_urls(arc: Archive, post: Post) -> bool:
    """还缺图，且它的地址已经**确定**过期——只有这时候再取一次才有意义。

    失效时刻未知（地址里没有 `oe`）时按仍可用处理：宁可白下一次，也不为一个猜测去开详情。
    """
    now = utcnow()
    for media in post.media:
        if media.kind != 'image' or arc.reusable_media(post, media.url) is not None:
            continue
        expiry = signed_url_expiry(media.url)
        if expiry is not None and expiry <= now:
            return True
    return False


async def capture_post(ctx, platform, account, arc, dcfg, post, *, lifecycle=None):
    """一篇一次尝试：只有来源列表不足才打开详情，已失败的下载由人工决定恢复。

    ⚠️ 人工恢复是仅有的例外。归档里存的是 Meta 的签名地址，`oe` 到点之后再请求只会
    拿到 403 URL signature expired，而签名改一个字符就失效，续不了期——所以人明确
    要求恢复某篇时，允许开一次详情换回新地址，否则那篇永远修不好。自动轮次不变，
    仍然「下载失败不触发详情」。
    """
    reason, archived = '', False
    source_visit = False
    stale_urls = dcfg.run_kind == 'recovery' and _images_need_fresh_urls(arc, post)
    if lifecycle:
        lifecycle.started(post, utcnow())
    def guard():
        if dcfg.access:
            dcfg.access.check(platform)
    try:
        guard()
        if (post.source_media_complete is not True or stale_urls) and dcfg.access:
            if not post.permalink:
                reason = ('缺图且没有可用的源帖链接，无法换取新的图片地址' if stale_urls
                          else '来源媒体列表不完整且没有可用的源帖链接')
            else:
                parsed = urlsplit(post.permalink)
                allowed = {'instagram': {'www.instagram.com', 'instagram.com'},
                           'facebook': {'www.facebook.com', 'facebook.com', 'm.facebook.com'}}
                if (parsed.scheme != 'https' or parsed.hostname not in allowed[platform]
                        or parsed.username or parsed.password):
                    raise ValueError('源帖详情链接与平台不匹配')
                dcfg.access.reserve_detail(platform, dcfg.scan_id, post.post_id,
                                           manual=dcfg.run_kind == 'recovery')
                source_visit = True
                dcfg.source_attempts += 1
                detail, final = await scan_page(ctx, post.permalink,
                    replace(dcfg, max_scrolls=0, run_kind='detail', diagnostics={}))
                reason = login_wall_reason(final, detail.blocked_status())
                if reason:
                    raise DeltaBlocked(reason, hard=True)
                matches = extract(detail.payloads, platform, account, route='delta')
                if platform != 'facebook':
                    matches, _ = partition_by_owner(matches, account)
                match = next((p for p in matches if p.platform == post.platform and p.post_id == post.post_id), None)
                if match:
                    # 同帖详情可能只有数字 ID；先结合主页证据，再过滤合并结果。
                    merged = merge_post(replace(post), match)
                    accepted, rejected = partition_by_owner([merged], account)
                    if accepted:
                        post = merged
                        # merge 按完整性择优，两份都完整时留的是旧的那份——过期地址正在这里。
                        if stale_urls and refresh_signed_media_urls(post, match):
                            print('    ~ %s 已用详情里的新图片地址替换过期签名' % post.post_id)
                    else:
                        arc.record_rejected(rejected)
                        dcfg.source_failures.append('详情作者证据与主页不匹配')
                        raise ValueError('详情作者证据与主页不匹配')
                else:
                    dcfg.source_failures.append('详情没有提供身份匹配的帖子结构')
                if post.source_media_complete is not True:
                    reason = '详情没有提供身份匹配且完整的媒体列表'
                source_visit = False
        if dcfg.access:
            await download_media(ctx, arc, post, post.permalink or profile_url(platform, account), check_stop=guard)
        else:
            await download_media(ctx, arc, post, post.permalink or profile_url(platform, account))
    except MediaRateLimited as exc:
        if dcfg.access:
            dcfg.access.outcome(platform, success=False, hard=True, reason=str(exc))
        reason = str(exc)
        raise DeltaBlocked(reason, hard=True) from exc
    except (asyncio.CancelledError, DeltaBlocked):
        reason = '平台阻断或本轮会话预算耗尽，已停止后续请求'
        raise
    except Exception as exc:
        if source_visit and not isinstance(exc, AccessDenied):
            dcfg.source_failures.append(type(exc).__name__)
        reason = str(exc) if isinstance(exc, AccessDenied) else f'本篇采集失败（{type(exc).__name__}），请人工处理'
    finally:
        if reason:
            post.media_complete = False
        try:
            arc.append(post)
            archived = arc.has(post.post_id)
        except (OSError, ValueError):
            reason = '本地归档未成功，请核对磁盘与采集异常'
        if lifecycle:
            lifecycle.finish(post, arc, utcnow(), reason=reason or '', archived=archived)
    return post


def recover_post(key, expected_revision, reason):
    """人工明确指定的一次采集；与自动入口共享锁、停止状态和详情访问额度。"""
    c = cfg()
    lifecycle = CaptureState(c.state_dir)
    access = AccessController(c.state_dir)
    with DeltaRunLock(c.state_dir / 'delta.lock'):
        current = lifecycle.status()
        if type(expected_revision) is not int or current['revision'] != expected_revision:
            raise ValueError('采集状态已变化，请刷新后核对')
        lifecycle.recover_interrupted(utcnow())
        current = lifecycle.status()
        item = current['items'].get(key)
        if not item:
            raise ValueError('找不到该采集异常')
        access.check(item['source']['platform'])
        item = lifecycle.recover(key, current['revision'], reason)
        row = item['source']
        post = Post(**{k: v for k, v in row.items() if k != 'media'},
                    media=[Media(**m) for m in row.get('media', [])])
        dcfg = replace(DeltaConfig.load(c), access=access, scan_id=item['scan_id'], run_kind='recovery')
        async def attempt():
            browser = pw = None
            try:
                pw, browser, ctx = await attach(port=c.detect_debug_port, profile=c.detect_profile_dir)
                arc = Archive(c.archive_dir, post.platform[:2] + '_' + post.account)
                await asyncio.wait_for(capture_post(ctx, post.platform, post.account, arc, dcfg, post,
                                                   lifecycle=lifecycle), dcfg.max_session_seconds)
                if dcfg.source_attempts:
                    access.outcome(post.platform, success=not dcfg.source_failures,
                                   reason=', '.join(dcfg.source_failures))
            except AccessDenied:
                raise
            except (Exception, SystemExit) as exc:
                access.outcome(post.platform, success=False, reason=type(exc).__name__,
                               hard=isinstance(exc, DeltaBlocked) and exc.hard)
                raise
            finally:
                lifecycle.interrupt(dcfg.scan_id, utcnow(), '人工尝试未完成；保留已有内容，请再次人工核对')
                if browser:
                    await browser.close()
                if pw:
                    await pw.stop()
        asyncio.run(attempt())
        return lifecycle.status()


def run_integrity(arc: Archive, entry: dict, platform: str,
                  now: datetime | None = None,
                  suspect: list[dict] | None = None) -> list[dict]:
    """返回并通知完整性问题；suspect 来自本次扫描，不做重复告警节流。"""
    gap_days, alert_after = integrity.params(platform)
    findings = integrity.run_checks(
        arc.rows(), arc.needs_media(), entry, platform,
        gap_days=gap_days, alert_after=alert_after, now=now or utcnow())
    if suspect:
        # 自家已授权账号和第三方合作方的处置完全不同，分开报，否则这条红线哨兵会被当噪音。
        authorized, third_party = integrity.split_suspect_sources(suspect, platform)
        if authorized:
            findings.append({
                "kind": "dropped_authorized_source",
                "message": ("%s 丢弃的节点里有 %d 篇来自**已授权来源**（%s）——"
                            "多半是本品牌另一个账号把同一批文案各发了一次。"
                            "核对 post_id 与正文是否和本账号已收的帖重复；"
                            "重复即正常，不重复才按漏判查。"
                            % (platform, len(authorized),
                               integrity.name_suspect_owners(authorized))),
            })
        if third_party:
            findings.append({
                "kind": "dropped_partner",
                "message": ("%s 丢弃的节点里有 %d 篇来自**已知合作方**（%s）——"
                            "合作帖归属判定可能又漏判了。去 _rejected.jsonl 和"
                            "最新的 _capture_delta_*.json 里离线查这几篇为什么"
                            "没带上 coauthor 信息，**不要直接放行**。"
                            % (platform, len(third_party),
                               integrity.name_suspect_owners(third_party))),
            })
    for f in findings:
        print("[!] %s" % f["message"])
    if findings:
        # 一个平台一条通知，不是一项一条：四条 toast 连弹的结果是全被划掉。
        notify("归档完整性告警 · %s" % platform,
               "\n".join(f["message"] for f in findings))
    return findings


async def _run_due(platforms: list[str], dcfg: DeltaConfig, state: dict,
                   path: Path, dry_run: bool) -> int:
    """Attach once; every outcome persists safety even when content writes are disabled."""
    access = dcfg.access or AccessController(path.parent, schedule=dcfg.schedule)
    try:
        c = cfg()
        pw, browser, ctx = await attach(port=c.detect_debug_port, profile=c.detect_profile_dir)
    except (Exception, SystemExit) as exc:
        msg = "附着专用 Chrome 失败（%s）：%s" % (type(exc).__name__, exc)
        for platform in platforms:
            access.outcome(platform, success=False, reason=msg)
            record_failure(state.setdefault(platform, blank_entry()), utcnow(), msg)
        save_state(path, state)
        print("[!] " + msg)
        notify("增量没能启动", msg)
        return 1
    rc = 0
    try:
        for platform in platforms:
            entry = state.setdefault(platform, blank_entry())
            scan_config = None
            try:
                access.check(platform)
                c = cfg()
                account = c["targets"][platform]
                arc = Archive(c.archive_dir, "%s_%s" % (platform[:2], account))
                facts = MonitoringJournal(c.state_dir, now=utcnow(), inspect_running=False)
                scan_config = replace(dcfg, access=access, scan_id=uuid4().hex, source_failures=[], source_attempts=0,
                                      deadline=0, diagnostics={})
                res = await asyncio.wait_for(
                    delta_once(ctx, platform, account, arc, scan_config, dry_run=dry_run, facts=facts),
                    timeout=dcfg.max_session_seconds)
            except AccessDenied as exc:
                print("[!] " + str(exc))
                rc = 2
                continue
            except (Exception, SystemExit) as exc:
                hard = isinstance(exc, DeltaBlocked) and exc.hard
                diagnostic = scan_config.diagnostics if scan_config is not None else {}
                entry['last_scan_diagnostics'] = diagnostic
                msg = ("整轮 %.0f 秒预算耗尽；阶段 %s，已开始 %d 篇、已完成 %d 篇，当前帖 %s" %
                       (dcfg.max_session_seconds, diagnostic.get('stage', 'unknown'),
                        diagnostic.get('capture_started', 0), diagnostic.get('capture_finished', 0),
                        diagnostic.get('current_post', '—')) if isinstance(exc, asyncio.TimeoutError)
                       else "%s: %s" % (type(exc).__name__, exc))
                access.outcome(platform, success=False, reason=msg, hard=hard)
                record_failure(entry, utcnow(), msg)
                if hard:
                    state["detect_hard_blocked"] = {"reason": msg, "platform": platform,
                                                       "recorded_at": iso(utcnow())}
                    for rest in platforms[platforms.index(platform) + 1:]:
                        record_failure(state.setdefault(rest, blank_entry()), utcnow(),
                                       "同批次的 %s 被拦，本次未执行" % platform)
                save_state(path, state)
                print("[!] " + msg)
                notify(("增量抓取中止" if isinstance(exc, DeltaBlocked) else "增量抓取异常")
                       + " · " + platform, msg)
                rc = 1
                if hard:
                    break
                continue
            if scan_config.source_failures:
                entry['last_scan_diagnostics'] = scan_config.diagnostics
                msg = '详情来源请求或解析失败：' + ', '.join(scan_config.source_failures)
                access.outcome(platform, success=False, reason=msg)
                record_failure(entry, utcnow(), msg)
                save_state(path, state)
                rc = 1
                continue
            access.outcome(platform, success=True)
            entry['last_scan_diagnostics'] = scan_config.diagnostics
            print(res.summary() + ("（dry-run：仅安全与扫描事实已持久化）" if dry_run else ""))
            record_success(entry, utcnow(), res.new)
            entry.update(account=account, last_run_kind=dcfg.run_kind,
                         last_observed_skipped=res.skipped)
            if dcfg.run_kind == "reconcile":
                entry["last_reconcile_at"] = entry["last_success"]
                entry["last_reconcile_new_count"] = res.new
                entry["reconcile_new_total"] = int(entry.get("reconcile_new_total", 0)) + res.new
            if not dry_run:
                run_integrity(arc, entry, platform, suspect=res.suspect)
            save_state(path, state)
        return rc
    finally:
        try:
            await browser.close()
        finally:
            await pw.stop()


def _print_status(state: dict) -> None:
    now = utcnow()
    for platform in PLATFORMS:
        entry = state.get(platform) or blank_entry()
        last = entry.get("last_success") or "从未成功"
        print("%-10s 上次成功 %s · 上次新增 %s 篇 · 零新增 %d 天 · "
              "连续失败 %d 次%s"
              % (platform, last, entry.get("last_new_count", 0),
                 quiet_days(entry, now), int(entry.get("consecutive_failures") or 0),
                 "" if not entry.get("last_error") else
                 "\n           最后一次错误：%s" % entry["last_error"]))
        if entry.get('last_scan_diagnostics'):
            print(json.dumps({'platform': platform, 'last_scan_diagnostics': entry['last_scan_diagnostics']},
                             ensure_ascii=False))


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="python -m routes.delta",
        description="每日增量（登录态 + CDP 附着，方案 B）")
    p.add_argument("--platform", choices=(*PLATFORMS, "all"), default="all")
    p.add_argument("--dry-run", action="store_true", help="访问真实浏览器；不写内容，仍持久化配额、停机与扫描事实")
    p.add_argument("--if-stale", action="store_true",
                   help="兼容旧调用；所有入口均遵守持久 next_due，不另设 stale 阈值")
    p.add_argument("--no-jitter", action="store_true",
                   help="兼容旧调用；随机时刻已经持久化，不在入口二次抽取")
    p.add_argument("--reset-failures", action="store_true",
                   help="兼容恢复别名；必须带 expected-revision 与 reason，不发起请求")
    p.add_argument("--status", action="store_true", help="只打印状态，不抓取")
    p.add_argument("--preview", action="store_true", help="离线查看窗口和配置，不接触浏览器")
    p.add_argument("--initialize-access", action="store_true")
    p.add_argument("--initialize-baseline", action="store_true")
    p.add_argument("--recover-access", action="store_true")
    p.add_argument("--recover-post", metavar="PLATFORM:ACCOUNT:POST_ID", help="人工明确执行该异常项一次采集")
    p.add_argument("--retire-stale-manual", action="store_true",
                   help="把当轮从未开始尝试的旧人工项退回 deferred；不触发任何平台请求")
    p.add_argument("--expected-revision", type=int)
    p.add_argument("--reason", default="")
    return p.parse_args(argv)


def _run_locked(args, dcfg: DeltaConfig, path: Path,
                state: dict, platforms: list[str]) -> int:
    """持有 :class:`DeltaRunLock` 后执行一次计划/手工增量。"""
    access = AccessController(path.parent, schedule=dcfg.schedule)
    dcfg = replace(dcfg, access=access)
    if not args.dry_run:
        _reconcile_archive(platforms)
    due = []
    blocked_by_budget = []
    now = utcnow()
    for platform in platforms:
        try:
            status = access.check(platform)
            next_due = parse_ts(status["platforms"][platform]["next_due_at"])
            if now < next_due:
                print("[i] %s 下次访问 %s" % (platform, next_due.isoformat()))
                continue
            due.append(platform)
        except AccessDenied as exc:
            print("[!] " + str(exc))
            blocked_by_budget.append(platform)
    if blocked_by_budget:
        notify("增量已停止自动运行", "、".join(blocked_by_budget) + "：复核访问状态后人工恢复")
    if not due:
        return 2 if blocked_by_budget else 0

    c = cfg()
    c.assert_chrome_profiles_isolated()
    port = c.detect_debug_port
    profile = c.detect_profile_dir
    if not cdp_ready(port, profile=profile):
        if dcfg.autostart_chrome:
            print("专用 Chrome 没在跑，正在拉起...")
            # 只重启人工登录过的 profile，不代替登录。
            launch_error: Exception | SystemExit | None = None
            try:
                launched = launch(port=port, profile=profile)
            except (Exception, SystemExit) as e:
                # 启动错误并入失败状态，保留 KeyboardInterrupt。
                launched = False
                launch_error = e
            if not launched:
                msg = ("专用 Chrome 拉不起来（端口 %d 未就绪）。"
                       "请检查 config.toml 的 [detect].port 与探测专用 profile"
                       % port)
                if launch_error is not None:
                    msg += "；%s：%s" % (
                        type(launch_error).__name__,
                        str(launch_error) or "无错误详情")
                print("[!] %s" % msg)
                for platform in due:
                    access.outcome(platform, success=False, reason=msg)
                    record_failure(state[platform], utcnow(), msg)
                save_state(path, state)
                notify("增量没能启动", msg)
                return 1
        else:
            msg = "专用 Chrome 没在跑（调试端口 %d 未就绪），本次跳过" % port
            print("[!] %s" % msg)
            now = utcnow()
            for platform in due:
                access.outcome(platform, success=False, reason=msg)
                record_failure(state[platform], now, msg)
            save_state(path, state)
            notify("增量没能启动", msg)
            return 1

    run_rc = asyncio.run(_run_due(due, dcfg, state, path, args.dry_run))
    # 部分平台停摆返回 2；实际运行失败的 1 优先。
    return run_rc if run_rc else (2 if blocked_by_budget else 0)


def _reconcile_archive(platforms):
    c = cfg()
    account_dir = c.archive_dir / ('in_' + c['targets']['instagram'])
    if 'instagram' not in platforms or not account_dir.exists():
        return
    report = reconcile_instagram(Archive(c.archive_dir, account_dir.name))
    if report['checked']:
        print('[i] Instagram 历史归档本地核验：检查 %d 篇 · 修复 %d 篇 · 保留待核验 %d 篇'
              % (report['checked'], len(report['repaired']), len(report['unresolved'])))
        print(json.dumps(report, ensure_ascii=False, indent=2))


@maintenance.guarded('capture')
def main(argv=None, *, config: DeltaConfig | None = None) -> int:
    args = _parse_args(argv)
    # 由 Python 写 UTF-8 运行分隔线，避免 cmd 向日志混入 GBK。
    print("\n===== %s · %s =====" % (iso(utcnow()), " ".join(argv or sys.argv[1:])
                                     or "(无参数)"))
    blocked = account_roles.frozen_target_message(cfg())
    if blocked:
        print("[!] " + blocked)
        return 1
    dcfg = config or DeltaConfig.load()
    if args.preview:
        c = cfg()
        c.assert_chrome_profiles_isolated()
        print(f"目标：{', '.join(c.active_accounts())}；detect CDP={c.detect_debug_port}")
        print(f"上海窗口 {dcfg.schedule.on_duty_window}；当前最小间隔 "
              f"{dcfg.schedule.minimum_interval_minutes(utcnow()):g} 分钟；滚动 {dcfg.max_scrolls} 屏")
        print(f"兜底截止余量 {dcfg.schedule.reconcile_deadline_margin_minutes():g} 分钟")
        return 0
    path = state_path()
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]

    if args.status:
        try:
            print(json.dumps(AccessController(path.parent).status(), ensure_ascii=False, indent=2))
            status = {'items': {}}
            if (path.parent / 'capture_state.json').exists():
                status = CaptureState(path.parent).status()
                print(json.dumps({'capture_revision': status['revision'], 'baselines': status['baselines'],
                    'manual_items': [{'key': key, 'reason': item.get('reason')} for key, item in status['items'].items()
                                     if item['status'] == 'manual'],
                    'deferred_items': [{'key': key, 'reason': item.get('reason')} for key, item in status['items'].items()
                                       if item['status'] == 'deferred']}, ensure_ascii=False, indent=2))
            c = cfg()
            for platform in platforms:
                account_dir = c.archive_dir / (platform[:2] + '_' + c['targets'][platform])
                if account_dir.exists():
                    print(json.dumps(archive_report(Archive(c.archive_dir, account_dir.name), status['items']),
                                     ensure_ascii=False, indent=2))
            _print_status(load_state(path))
            return 0
        except AccessDenied as exc:
            print("[!] " + str(exc))
            return 2

    lock_path = path.with_name("delta.lock")
    try:
        if args.recover_post:
            result = recover_post(args.recover_post, args.expected_revision, args.reason)
            item = result['items'][args.recover_post]
            print(json.dumps({'key': item['key'], 'status': item['status'], 'reason': item.get('reason'),
                              'capture_revision': result['revision']}, ensure_ascii=False, indent=2))
            return 0 if item['status'] == 'complete' else 2
        with DeltaRunLock(lock_path):
            # 持锁后重读状态，避免用旧值覆盖另一任务的进度。
            state = load_state(path)
            bind_target_state(state, cfg())
            access = AccessController(path.parent)
            if args.initialize_baseline:
                c = cfg()
                print(json.dumps(CaptureState(path.parent).initialize(c.archive_dir, c["targets"], args.reason),
                                 ensure_ascii=False, indent=2))
                return 0
            if args.initialize_access:
                print(json.dumps(access.initialize(args.reason), ensure_ascii=False, indent=2))
                return 0
            if args.retire_stale_manual:
                if args.expected_revision is None:
                    raise AccessDenied("退役旧人工项需 --expected-revision 与 --reason；先 --status 复核")
                result = CaptureState(path.parent).retire_stale_manual(args.expected_revision, args.reason)
                print('[i] 退役从未开始的旧人工项 %d 条 · 保留真实失败 %d 条'
                      % (len(result['retired']), len(result['kept_manual'])))
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if args.recover_access or args.reset_failures:
                if args.expected_revision is None:
                    raise AccessDenied("恢复需 --expected-revision 与 --reason；先 --status 复核")
                print(json.dumps(access.recover(args.expected_revision, args.reason,
                    platform=None if args.platform == "all" else args.platform), ensure_ascii=False, indent=2))
                return 0
            return _run_locked(args, dcfg, path, state, platforms)
    except DeltaRunAlreadyActive as e:
        print("[i] %s" % e)
        # 计划任务撞上另一个实例属于成功去重；人工 reset 没执行则必须报失败。
        return 75
    except (ValueError, OSError) as exc:
        print("[!] " + str(exc))
        return 2


if __name__ == "__main__":
    from core.console import force_utf8, note_stop

    force_utf8()
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        note_stop()
        raise SystemExit(0)

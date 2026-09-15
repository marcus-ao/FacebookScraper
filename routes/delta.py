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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.capture import (Collector, atomic_write_json, download_media,
                          prune_captures_days)
from core.chrome import attach, cdp_ready, launch
from core.config import MonitorSchedule, cfg, per_platform
from core import integrity
from core.integrity import parse_ts
from core.monitoring import MonitoringJournal
from core.notify import notify
from core.parse import extract, partition_by_owner
from core.store import Archive
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
    max_session_seconds: float = 300.0
    failure_budget: int = 3
    autostart_chrome: bool = True
    keep_captures_days: int = 7
    min_own_posts: int = 3
    _quiet_slowdown: object = None
    schedule: MonitorSchedule = field(default_factory=MonitorSchedule)
    run_kind: str = "delta"

    @classmethod
    def load(cls, c=None) -> "DeltaConfig":
        c = c if c is not None else cfg()

        def g(key, default):
            return c.get("delta", key, default)

        return cls(
            request_gap_seconds=float(g("request_gap_seconds", 8.0)),
            max_scrolls=int(g("max_scrolls", 0)),
            first_screen_seconds=float(g("first_screen_seconds", 6.0)),
            max_session_seconds=float(g("max_session_seconds", 300.0)),
            failure_budget=int(g("failure_budget", 3)),
            autostart_chrome=bool(g("autostart_chrome", True)),
            keep_captures_days=int(g("keep_captures_days", 7)),
            min_own_posts=int(g("min_own_posts", 3)),
            _quiet_slowdown=g("quiet_days_before_slowdown", 7),
            schedule=MonitorSchedule.load(c),
        )

    def quiet_days_before_slowdown(self, platform: str) -> int:
        return int(per_platform(self._quiet_slowdown, platform, 7))

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
    """读状态文件。坏文件不得让整条链路停摆——重建一个空的继续跑。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # 平台状态也须为对象，防止后续 .get() 在抓取前失败。
    for platform in PLATFORMS:
        if platform in data and not isinstance(data[platform], dict):
            data[platform] = blank_entry()
    return data


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
    """按当前上海窗口的最短随机间隔去重；安静账号白天也按离岗频率。"""
    threshold = dcfg.quiet_days_before_slowdown(platform)
    quiet = threshold > 0 and int(entry.get("consecutive_quiet_days") or 0) >= threshold
    return dcfg.schedule.minimum_interval_minutes(now or utcnow(), quiet=quiet) / 60


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
            return "页面被重定向到 %s —— 会话可能已失效" % final_url
    if blocked:
        status, endpoint = blocked
        if status == 429:
            return "接口返回 429（限流）：%s" % endpoint
        return "接口返回 %d（会话失效或权限不足）：%s" % (status, endpoint)
    return None


async def _pause(window: tuple[float, float]) -> None:
    """异步随机等待，保持响应体读取的事件循环运行。"""
    await asyncio.sleep(random.uniform(*window))


async def _eval(page, expr: str, default):
    """页面求值，失败返回默认值。这些都是辅助信息，不该让抓取失败。"""
    try:
        value = await page.evaluate(expr)
    except Exception:
        return default
    return default if value is None else value


async def human_scroll(page, dcfg: DeltaConfig) -> tuple[int, float]:
    """有限滚动并返回 (屏数, 实际位移)；滚轮先定位到主视口。"""
    screens = max(0, dcfg.max_scrolls)
    if not screens:
        return 0, 0.0
    size = await _eval(page, "() => [window.innerWidth, window.innerHeight]",
                       [1280, 800])
    await page.mouse.move(int(size[0]) // 2, int(size[1]) // 2)
    before = float(await _eval(page, "() => window.scrollY", 0) or 0)
    for _ in range(screens):
        await page.mouse.wheel(0, random.randint(*WHEEL_PX))
        await _pause(dcfg.scroll_pause())
    after = float(await _eval(page, "() => window.scrollY", 0) or 0)
    return screens, after - before


async def scan_page(ctx, url: str, dcfg: DeltaConfig) -> tuple[Collector, str]:
    """打开主页、滚有限几屏、把接口响应捞下来。返回 (collector, 落地 URL)。"""
    col = Collector()
    page = await ctx.new_page()
    handler = col.submit
    try:
        page.on("response", handler)
        await page.goto(url, wait_until="domcontentloaded")
        # 首屏的接口响应是异步来的，goto 返回时通常还没到齐
        await asyncio.sleep(dcfg.first_screen_seconds)
        screens, moved = await human_scroll(page, dcfg)
        if screens and moved <= 0:
            print("[!] 滚了 %d 屏但页面没有移动（scrollY 未变）——"
                  "滚轮事件可能没落在可滚动区域" % screens)
        final_url = page.url
        page.remove_listener("response", handler)
        # 停止监听后 drain，避免页面关闭时取消未读响应。
        await col.drain()
        return col, final_url
    finally:
        try:
            page.remove_listener("response", handler)
            await col.drain()
        finally:
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

    def summary(self) -> str:
        span = ("%s ~ %s" % (self.oldest_seen[:10], self.newest_seen[:10])
                if self.newest_seen else "—")
        result = ("新增 %d 篇 · 修复旧帖 %d 篇 · 本账号 %d 篇（原创 %d · 合作 %d，%s）"
                "· 丢弃 %d · 归档最新 %s"
                % (self.new, self.upgraded, self.own, self.authored, self.collab, span,
                   self.rejected, self.newest_known[:10] or "—"))
        return result + " · 发布范围外 " + " / ".join(
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

    # 按目标帖数判断覆盖；空归档仍保留最低门槛，最新帖日期倒退不作失败依据。
    floor = (0 if dcfg.min_own_posts == 0 else
             min(max(1, dcfg.min_own_posts), max(1, len(known))))
    if len(posts) < floor:
        # 中止原因补充疑似合作误丢，便于区分归属错误与访问受阻。
        hint = ("；其中 %d 篇来自**已知合作方**（%s）—— 优先怀疑合作帖判定失效，"
                "而不是被拦" % (len(suspect),
                                "、".join(sorted({(s.get("owner") or "?")
                                                  for s in suspect})[:4]))
                if suspect else "")
        raise DeltaBlocked(
            "只看到 %d 篇属于 %s 的帖子（丢弃 %d 篇他人内容，期望至少 %d 篇）"
            " —— 大概率没有拿到时间线，而不是没有新帖%s"
            % (len(posts), account, len(rejected), floor, hint))

    for post in sorted(posts, key=lambda p: p.created_at or "", reverse=True):
        if not arc.should_append(post):
            continue
        was_known = arc.has(post.post_id)
        head = (post.text or "").replace("\n", " ")[:38]
        if dry_run:
            print("  ~ %s  %s  %s" % (post.post_id, post.created_at or "(无日期)", head))
            if was_known:
                res.upgraded += 1
            else:
                res.new += 1
            continue
        if facts is not None:
            # 先记"发现"再下载媒体：漏帖和抓不下来在群播报里必须分得开。
            facts.fact("post_discovered", utcnow(), platform=platform, post_id=post.post_id,
                       created_at=post.created_at, permalink=post.permalink, head=head,
                       images=sum(1 for m in post.media if m.kind == "image"),
                       videos=sum(1 for m in post.media if m.kind == "video"), known=was_known)
        await download_media(ctx, arc, post, url)
        if not arc.append(post):
            print("    ! %s 媒体仍未补全，保留原归档并留待下次重试" % post.post_id)
            if facts is not None:
                facts.fact("post_capture_incomplete", utcnow(), platform=platform,
                           post_id=post.post_id)
            continue
        imgs = sum(1 for m in post.media if m.kind == "image")
        vids = sum(1 for m in post.media if m.kind == "video")
        print("  + %s  %d图/%d视频  %s" % (post.post_id, imgs, vids, head))
        if facts is not None:
            facts.fact("post_captured", utcnow(), platform=platform, post_id=post.post_id,
                       images=imgs, videos=vids, folder=arc.post_dir(post).name)
        if was_known:
            res.upgraded += 1
        else:
            res.new += 1
    return res


def run_integrity(arc: Archive, entry: dict, platform: str,
                  now: datetime | None = None,
                  suspect: list[dict] | None = None) -> list[dict]:
    """返回并通知完整性问题；suspect 来自本次扫描，不做重复告警节流。"""
    gap_days, alert_after = integrity.params(platform)
    findings = integrity.run_checks(
        arc.rows(), arc.needs_media(), entry, platform,
        gap_days=gap_days, alert_after=alert_after, now=now or utcnow())
    if suspect:
        who = sorted({(s.get("owner") or "?") for s in suspect})
        findings.append({
            "kind": "dropped_partner",
            "message": ("%s 丢弃的节点里有 %d 篇来自**已知合作方**（%s%s）——"
                        "合作帖归属判定可能又漏判了。去 _rejected.jsonl 和"
                        "最新的 _capture_delta_*.json 里离线查这几篇为什么"
                        "没带上 coauthor 信息，**不要直接放行**。"
                        % (platform, len(suspect), "、".join(who[:4]),
                           " 等" if len(who) > 4 else "")),
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
    """附着一次 Chrome，把到期的平台依次跑完。返回退出码。"""
    try:
        c = cfg()
        pw, browser, ctx = await attach(port=c.detect_debug_port, profile=c.detect_profile_dir)
    except (Exception, SystemExit) as e:
        # 附着失败也更新两平台状态；捕获 SystemExit，保留 KeyboardInterrupt。
        msg = "附着专用 Chrome 失败（%s）：%s" % (
            type(e).__name__, str(e) or "无错误详情")
        print("[!] %s" % msg)
        if not dry_run:
            now = utcnow()
            for platform in platforms:
                record_failure(state.setdefault(platform, blank_entry()), now, msg)
            save_state(path, state)
            notify("增量没能启动", msg)
        return 1
    rc = 0
    try:
        for i, platform in enumerate(platforms):
            entry = state.setdefault(platform, blank_entry())
            # 配置和归档初始化也属于平台运行，失败须进入同一状态闭环。
            account = "账号配置未读取"
            try:
                c = cfg()
                account = c["targets"][platform]
                arc = Archive(c.archive_dir,
                              "%s_%s" % (platform[:2], account))
                print("\n=== %s / %s ===" % (platform, account))
                # inspect_running=False：只追加事实，不去动内容处理批次的状态。
                facts = None if dry_run else MonitoringJournal(
                    c.state_dir, now=utcnow(), inspect_running=False)
                res = await asyncio.wait_for(
                    delta_once(ctx, platform, account, arc, dcfg, dry_run=dry_run, facts=facts),
                    timeout=dcfg.max_session_seconds)
            except DeltaBlocked as e:
                # 访问受阻立即停止，不重试或更换身份。
                print("[!] 本次中止：%s" % e)
                rc = 1
                if dry_run:
                    continue
                record_failure(entry, utcnow(), str(e))
                left = dcfg.failure_budget - int(entry["consecutive_failures"])
                notify("增量抓取中止 · %s" % platform,
                       "%s：%s（连续失败 %d 次，再失败 %d 次将停止自动运行）"
                       % (account, e, entry["consecutive_failures"], max(0, left)))
                if e.hard:
                    state["detect_hard_blocked"] = {
                        "reason": str(e), "platform": platform, "recorded_at": iso(utcnow())}
                    # 硬阻塞同步计入未运行平台的失败预算。
                    for rest in platforms[i + 1:]:
                        record_failure(state.setdefault(rest, blank_entry()),
                                       utcnow(), "同批次的 %s 被拦，本次未执行" % platform)
                        print("[i] %s 本次不再尝试（同一会话、同一指纹）" % rest)
                    save_state(path, state)
                    break
                save_state(path, state)
                continue
            except (Exception, SystemExit) as e:
                # 编程与初始化错误同样记录失败；不吞掉 KeyboardInterrupt。
                msg = "未预期异常（%s）：%s" % (
                    type(e).__name__, str(e) or "无错误详情")
                print("[!] %s" % msg)
                rc = 1
                if dry_run:
                    continue
                record_failure(entry, utcnow(), msg)
                save_state(path, state)
                notify("增量抓取异常 · %s" % platform,
                       "%s：%s（连续失败 %d 次）"
                       % (account, msg, entry["consecutive_failures"]))
                continue

            print(res.summary() + ("（--dry-run，未写盘）" if dry_run else ""))
            if res.suspect:
                # dry-run 也要打：--dry-run 正是排查这类问题时会跑的那一次
                who = sorted({(s.get("owner") or "?") for s in res.suspect})
                print("[!] 丢弃的 %d 篇里有 %d 篇来自已知合作方（%s）——"
                      "合作帖判定可能漏判了，去 _rejected.jsonl 离线查"
                      % (res.rejected, len(res.suspect), "、".join(who[:4])))
            if res.stale_view():
                # 最新帖倒退可能是删帖，仅提示，不消耗失败预算。
                print("[!] 看到的最新一篇（%s）比归档里最新的（%s）还旧 —— "
                      "确认一下是不是没拿到时间线"
                      % (res.newest_seen[:10], res.newest_known[:10]))
            if dry_run:
                continue
            record_success(entry, utcnow(), res.new)
            entry["account"] = account
            entry["last_run_kind"] = dcfg.run_kind
            entry["last_observed_skipped"] = res.skipped
            if dcfg.run_kind == "reconcile":
                entry["last_reconcile_at"] = entry["last_success"]
                entry["last_reconcile_new_count"] = res.new
                entry["reconcile_new_total"] = int(entry.get("reconcile_new_total", 0)) + res.new
            # 检查使用刚更新的状态，告警去重标记一并落盘。
            run_integrity(arc, entry, platform, suspect=res.suspect)
            save_state(path, state)
            quiet = entry["consecutive_quiet_days"]
            if quiet >= dcfg.quiet_days_before_slowdown(platform):
                print("[i] 已连续 %d 天零新增，下次起按降频节奏（%.0f 小时一次）"
                      % (quiet, dcfg.schedule.off_duty_interval_min / 60))
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


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="python -m routes.delta",
        description="每日增量（登录态 + CDP 附着，方案 B）")
    p.add_argument("--platform", choices=(*PLATFORMS, "all"), default="all")
    p.add_argument("--dry-run", action="store_true", help="只抓不写盘")
    p.add_argument("--if-stale", action="store_true",
                   help="距上次成功不足阈值就直接退出（供计划任务的补跑触发器用）")
    p.add_argument("--no-jitter", action="store_true",
                   help="跳过入口延迟；手工运行或已持久随机排期的 scheduler 使用")
    p.add_argument("--reset-failures", action="store_true",
                   help="失败预算用尽后，人工确认已处理，用它清零")
    p.add_argument("--status", action="store_true", help="只打印状态，不抓取")
    p.add_argument("--preview", action="store_true", help="离线查看窗口和配置，不接触浏览器")
    return p.parse_args(argv)


def _run_locked(args, dcfg: DeltaConfig, path: Path,
                state: dict, platforms: list[str]) -> int:
    """持有 :class:`DeltaRunLock` 后执行一次计划/手工增量。"""
    now = utcnow()
    if state.get("detect_hard_blocked"):
        print("[!] detect 会话已被登录墙/限流硬停；请人工检查后 --reset-failures。")
        return 2
    due: list[str] = []
    blocked_by_budget: list[str] = []
    for platform in platforms:
        entry = state.setdefault(platform, blank_entry())
        if budget_exhausted(entry, dcfg.failure_budget):
            blocked_by_budget.append(platform)
            print("[!] %s 已连续失败 %d 次（预算 %d），**停止自动运行**。"
                  % (platform, entry["consecutive_failures"], dcfg.failure_budget))
            print("    最后一次错误：%s" % entry.get("last_error"))
            print("    请先确认专用 Chrome 里的小号还是登录态，处理后跑：")
            print("    .venv\\Scripts\\python.exe -m routes.delta --reset-failures")
            continue
        if args.if_stale:
            hours = effective_stale_hours(entry, dcfg, platform, now=now)
            run, why = stale_enough(entry, now, hours)
            if not run:
                print("[i] %s 跳过：%s" % (platform, why))
                continue
        due.append(platform)

    if blocked_by_budget:
        notify("增量已停止自动运行",
               "%s 连续失败达到预算，需要人工确认会话是否还有效"
               % "、".join(blocked_by_budget))
    if not due:
        print("没有到期的平台，本次不抓取。")
        return 2 if blocked_by_budget else 0

    # 先判断 stale 再抖动；scheduler 已持久化时间时用 --no-jitter。
    if not args.no_jitter and not args.dry_run:
        delay = random.uniform(0, dcfg.schedule.interval_minutes(now)
                               * dcfg.schedule.jitter_ratio * 60)
        print("随机延迟 %.1f 分钟后开始（避免每天固定整点发起请求）..."
              % (delay / 60))
        time.sleep(delay)

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
                if not args.dry_run:
                    for platform in due:
                        record_failure(state[platform], utcnow(), msg)
                    save_state(path, state)
                notify("增量没能启动", msg)
                return 1
        else:
            msg = "专用 Chrome 没在跑（调试端口 %d 未就绪），本次跳过" % port
            print("[!] %s" % msg)
            if not args.dry_run:
                now = utcnow()
                for platform in due:
                    record_failure(state[platform], now, msg)
                save_state(path, state)
            notify("增量没能启动", msg)
            return 1

    run_rc = asyncio.run(_run_due(due, dcfg, state, path, args.dry_run))
    # 部分平台停摆返回 2；实际运行失败的 1 优先。
    return run_rc if run_rc else (2 if blocked_by_budget else 0)


def main(argv=None, *, config: DeltaConfig | None = None) -> int:
    args = _parse_args(argv)
    # 由 Python 写 UTF-8 运行分隔线，避免 cmd 向日志混入 GBK。
    print("\n===== %s · %s =====" % (iso(utcnow()), " ".join(argv or sys.argv[1:])
                                     or "(无参数)"))
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
        _print_status(load_state(path))
        return 0

    lock_path = path.with_name("delta.lock")
    try:
        with DeltaRunLock(lock_path):
            # 持锁后重读状态，避免用旧值覆盖另一任务的进度。
            state = load_state(path)
            bind_target_state(state, cfg())
            if args.reset_failures:
                for platform in platforms:
                    entry = state.setdefault(platform, blank_entry())
                    entry["consecutive_failures"] = 0
                    entry["last_error"] = None
                state.pop("detect_hard_blocked", None)
                save_state(path, state)
                print("已清零 %s 的失败计数。" % "、".join(platforms))
                return 0
            return _run_locked(args, dcfg, path, state, platforms)
    except DeltaRunAlreadyActive as e:
        print("[i] %s" % e)
        # 计划任务撞上另一个实例属于成功去重；人工 reset 没执行则必须报失败。
        return 1 if args.reset_failures else 0


if __name__ == "__main__":
    from core.console import force_utf8

    force_utf8()
    raise SystemExit(main())

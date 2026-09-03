r"""每日增量：**登录态 + CDP 附着**。对应实施计划的 C 组。

与 routes\backfill.py 的差别不是"更快"或"更自动"，而是**频次**：
回填带着小号的登录态跑一次，增量每天跑。封号风险因此是「累积性敞口」，
而抓取小号被封是本项目**唯一不可恢复的失败模式**——回填与增量会同时断掉。

> 2026-08-30：原设计是"增量完全登出、没有可封的东西"。实测 + 用户确认，
> FB/IG 在没有登录态时直接报错（对公开账号请求 `web_profile_info` 首次即 429），
> 登出这条腿不存在。该实现已于 2026-09-02 删除（git 历史可查）。

因此下面每一处看着"保守到没必要"的地方——只滚两屏、随机延迟、异常即停、
失败预算、零新增降频——都是在为"每天都要来一次"买保险。
**不要因为"跑得挺好"就把它们优化掉**：这类风险的反馈是延迟的，且只反馈一次。

当前实现范围：
    C2  Instagram 登录态增量  已实现
    C3  Facebook  登录态增量  已实现
    C4  增量的媒体下载        已实现（走浏览器请求栈）
    C5  运行状态记录          已实现（state/delta_state.json）
    C6  主入口                已实现（--platform / --dry-run / --if-stale）
    C7  累积风险缓解          已实现，**是方案的组成部分不是可选项**
"""
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
                          prune_captures)
from core.chrome import attach, cdp_ready, launch
from core.config import cfg, per_platform
from core import integrity
from core.integrity import parse_ts
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
    stale_after_hours: float = 26.0
    start_jitter_minutes: float = 45.0
    max_scrolls: int = 2
    first_screen_seconds: float = 6.0
    max_session_seconds: float = 300.0
    failure_budget: int = 3
    slowdown_stale_after_hours: float = 72.0
    autostart_chrome: bool = True
    keep_captures: int = 7
    min_own_posts: int = 3
    _quiet_slowdown: object = None

    @classmethod
    def load(cls, c=None) -> "DeltaConfig":
        c = c if c is not None else cfg()

        def g(key, default):
            return c.get("delta", key, default)

        return cls(
            request_gap_seconds=float(g("request_gap_seconds", 8.0)),
            stale_after_hours=float(g("stale_after_hours", 26.0)),
            start_jitter_minutes=float(g("start_jitter_minutes", 45.0)),
            max_scrolls=int(g("max_scrolls", 2)),
            first_screen_seconds=float(g("first_screen_seconds", 6.0)),
            max_session_seconds=float(g("max_session_seconds", 300.0)),
            failure_budget=int(g("failure_budget", 3)),
            slowdown_stale_after_hours=float(g("slowdown_stale_after_hours", 72.0)),
            autostart_chrome=bool(g("autostart_chrome", True)),
            keep_captures=int(g("keep_captures", 7)),
            min_own_posts=int(g("min_own_posts", 3)),
            _quiet_slowdown=g("quiet_days_before_slowdown", 7),
        )

    def quiet_days_before_slowdown(self, platform: str) -> int:
        return int(per_platform(self._quiet_slowdown, platform, 7))

    def scroll_pause(self) -> tuple[float, float]:
        """滚动之间的停顿区间。配置给下界，上界取两倍——随机化本身比倍数重要。"""
        return self.request_gap_seconds, self.request_gap_seconds * 2


class DeltaBlocked(RuntimeError):
    """当次抓取被中止的所有原因（登录墙、被拦、解析异常）共用这一个类型。

    它们的处理办法大体相同——**立即停止、记一次失败、告警、不重试**——
    区别只在给人看的那句话，所以不给每种原因单独造一个异常类型。

    唯一需要程序区分的是 ``hard``：

    * ``hard=True``（登录墙 / 401 / 403 / 429）—— **本次全部平台一起停**。
      两个平台共用同一个会话、同一份浏览器指纹，IG 刚被限流就转头去敲 FB，
      是把"可能被注意到"变成"确定被注意到"。
    * ``hard=False``（解析不出来、页面没加载、超时）—— 只停这个平台。
      这类问题出在我们这边，不是对面在拦，另一个平台照跑。
    """

    def __init__(self, reason: str, hard: bool = False):
        super().__init__(reason)
        self.hard = hard


class DeltaRunAlreadyActive(RuntimeError):
    """另一个每日/补跑实例已经持有全局增量锁。"""


def DeltaRunLock(path: Path) -> FileLock:      # noqa: N802（保留原名，调用点不变）
    """跨两个 Task Scheduler 任务的进程锁。

    ``MultipleInstancesPolicy=IgnoreNew`` 只约束同一个计划任务；每日任务与
    登录/解锁补跑是两个不同任务，醒机时仍可能同时进入这里。锁覆盖 stale
    判定、随机延迟、浏览器和状态写入，保证整个增量主干并发恒为 1。
    """
    return FileLock(path, error_type=DeltaRunAlreadyActive,
                    busy_message="另一个增量实例正在运行；本次不再附着浏览器或写状态。")


# ---- C5：运行状态 -------------------------------------------------------

def state_path() -> Path:
    return cfg().state_dir / "delta_state.json"


def blank_entry() -> dict:
    return {"first_success": None, "last_run": None, "last_success": None,
            "last_new_at": None, "last_new_count": 0,
            "consecutive_quiet_days": 0, "consecutive_failures": 0,
            "last_error": None}


def load_state(path: Path) -> dict:
    """读状态文件。坏文件不得让整条链路停摆——重建一个空的继续跑。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # 最外层是对象还不够：手工修状态、旧版本半写入或文件损坏，都可能留下
    # {"facebook": null} / {"instagram": "..."}。main() 随后会对平台值
    # 调 .get()，若不在读取边界修复，计划任务会在真正抓取前直接崩掉。
    for platform in PLATFORMS:
        if platform in data and not isinstance(data[platform], dict):
            data[platform] = blank_entry()
    return data


def save_state(path: Path, state: dict) -> None:
    """原子保存运行状态，失败时保留上一份完整 JSON。

    计划任务可能在任何时刻被系统结束；直接覆盖目标文件会先截断旧内容，
    随后的中止便同时丢掉失败预算与 ``last_success``。临时文件必须和目标
    位于同一目录，完整落盘后再用 ``Path.replace`` 原子提交。
    """
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
    """距上一次真的抓到新帖过了几天。

    ⚠️ **不是"连续几次跑出 0 新增"**。按次数算的话，`runs_per_day` 一改
    这个数的含义就变了（跑两次 = 一天算两天），而它下游要喂给
    "连续 N 天零新增就告警/降频"——那两处说的都是**天**。
    从没抓到过新帖时，从第一次成功之日起算。
    """
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
    """失败也要写。

    否则"连续三天失败"和"连续三天没新帖"在状态文件里长得**一模一样**，
    而这两件事一个要人去重新登录、一个什么都不用做。
    ⚠️ `last_success` **不刷新** —— 刷新了 `--if-stale` 就会以为跑过了。
    """
    entry["last_run"] = iso(now)
    entry["last_error"] = error
    entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
    return entry


def budget_exhausted(entry: dict, budget: int) -> bool:
    """连续失败达阈值 → 停止自动运行，要人来看一眼（C7「失败预算」）。

    会话失效之后每天硬撞一个已经失效的会话，是这条路径上最坏的行为：
    它把"可能被注意到"变成"每天主动提醒对方注意"。
    """
    if budget <= 0:
        return False
    return int(entry.get("consecutive_failures") or 0) >= budget


def effective_stale_hours(entry: dict, dcfg: DeltaConfig, platform: str) -> float:
    """长期零新增就自动降频（C7「频率可降级」）。

    实测 Instagram 已经连续 45 天没发新帖，天天全速跑没有任何收益，
    却每天都在累积敞口。降频是这条路径上**唯一能直接降低风险**的旋钮。
    """
    threshold = dcfg.quiet_days_before_slowdown(platform)
    if threshold > 0 and int(entry.get("consecutive_quiet_days") or 0) >= threshold:
        return dcfg.slowdown_stale_after_hours
    return dcfg.stale_after_hours


def stale_enough(entry: dict, now: datetime, hours: float) -> tuple[bool, str]:
    """`--if-stale` 的判定。返回 (要不要跑, 给人看的理由)。"""
    last = parse_ts(entry.get("last_success"))
    if last is None:
        return True, "还没有成功跑过"
    elapsed = (now - last).total_seconds() / 3600.0
    if elapsed < hours:
        return False, "距上次成功 %.1f 小时，不足 %.0f 小时" % (elapsed, hours)
    return True, "距上次成功 %.1f 小时" % elapsed


# ---- C2/C3：页面扫描 ----------------------------------------------------

def login_wall_reason(final_url: str, blocked: tuple[int, str] | None = None) -> str | None:
    """这次访问是不是被登录墙 / 限流拦了？是就返回人话理由。

    两个独立证据，都要看：
      1. **落地 URL** —— 会话失效时页面会被重定向到登录页或 checkpoint；
      2. **接口状态码** —— 页面壳可能照常渲染，但接口全在 401/403/429，
         这时 URL 完全正常，只看 URL 会把"被拦"读成"这个号没发帖"。

    单拆成纯函数是为了能离线测：这几种情况的最终表现都是"抓到 0 篇"，
    只断言结果分不清走的哪条分支，而它们给用户的提示完全不同。
    """
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
    """在区间内随机睡一会儿。**必须 await，不能 time.sleep。**

    ⚠️ `time.sleep` 会把事件循环整个冻住，而**响应体正是在那个循环上异步读的**
    ——睡 8 秒等于这 8 秒里到达的响应一段都读不到，
    最后表现为"抓到 0 篇"，且看不出原因。
    """
    await asyncio.sleep(random.uniform(*window))


async def _eval(page, expr: str, default):
    """页面求值，失败返回默认值。这些都是辅助信息，不该让抓取失败。"""
    try:
        value = await page.evaluate(expr)
    except Exception:
        return default
    return default if value is None else value


async def human_scroll(page, dcfg: DeltaConfig) -> tuple[int, float]:
    """按 C7 的深度上限往下滚几屏，每屏之间随机停顿。

    返回 `(滚了几屏, 页面实际移动了多少像素)`。

    ⚠️ **必须先把鼠标移到视口中间。** `mouse.wheel` 是在**当前鼠标位置**派发
    滚轮事件，而默认位置是 (0, 0) —— 视口左上角通常是导航栏/侧边栏，
    滚轮打在那里对主区域没有任何作用。2026-08-30 首次实测时就是这样：
    程序以为自己滚了两屏，页面一动没动，而**日志里看不出任何异常**。

    返回实际位移正是为了让这种失败可见：滚了却没动，就该说出来。

    ❌ **不滚到底。** 增量只需要看到最新几条；每天滚到底既无收益，
    又是这条路径上最明显的机器行为特征。
    """
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
            # 滚了却没动。这不是小事：增量看不到新内容时，
            # "页面没滚动"和"确实没新帖"在输出里必须长得不一样。
            print("[!] 滚了 %d 屏但页面没有移动（scrollY 未变）——"
                  "滚轮事件可能没落在可滚动区域" % screens)
        final_url = page.url
        page.remove_listener("response", handler)
        # 停止接收新事件后再等已到达的响应体读完，否则最后几段 JSON
        # 会在页面关闭时被取消，形成无提示的数据缺口（CR-05）
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
    """一次增量的结果与**诊断信息**。

    诊断字段不是可有可无的日志装饰。2026-08-30 首次实测时，Instagram 那边
    只打了一句"新增 0 篇"——而真相是它**根本没看到本账号的时间线**
    （39 个候选里 1 篇是自己的，还比归档里最新的更旧）。
    "真的没新帖"和"没看到时间线"当时在输出里完全一样。
    下面每个字段都是为了让这两件事长得不一样。
    """
    new: int = 0
    upgraded: int = 0
    own: int = 0
    rejected: int = 0
    newest_seen: str = ""
    oldest_seen: str = ""
    newest_known: str = ""
    payloads: int = 0
    # own 拆成"本账号自己发的"和"合作帖"两半。**这不是装饰**：
    # 实测增量看到的 36 篇里 35 篇是合作帖、自己发的只有 1 篇，
    # 所以 own 这个数字几乎完全由合作帖判定撑着。合作判定一旦漂移，
    # own 会从 36 掉到 1 —— 拆开写，这件事在日志里一眼可见；
    # 不拆的话，它和"今天真的只发了一篇"长得一模一样（CR-18 那类二义）。
    authored: int = 0
    collab: int = 0
    # 被丢弃、但作者是已知合作方的那些（core.integrity.check_dropped_partners）
    suspect: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        span = ("%s ~ %s" % (self.oldest_seen[:10], self.newest_seen[:10])
                if self.newest_seen else "—")
        return ("新增 %d 篇 · 修复旧帖 %d 篇 · 本账号 %d 篇（原创 %d · 合作 %d，%s）"
                "· 丢弃 %d · 归档最新 %s"
                % (self.new, self.upgraded, self.own, self.authored, self.collab, span,
                   self.rejected, self.newest_known[:10] or "—"))

    def stale_view(self) -> bool:
        """看到的最新一篇比归档里最新的还旧 —— 强烈提示"没看到时间线"。"""
        return bool(self.newest_seen and self.newest_known
                    and self.newest_seen < self.newest_known)


async def delta_once(ctx, platform: str, account: str, arc: Archive,
                     dcfg: DeltaConfig, *, dry_run: bool = False) -> ScanResult:
    """跑一个平台的一次增量。被拦时抛 :class:`DeltaBlocked`。

    ⚠️ 判断"这条要不要写"用 `arc.should_append()` 而**不是 `has()`**：
    否则先前留下的残缺帖永远补不全（CR-03），而且下载会白跑一遍 CDN。
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

    # 解析之前无条件转储。回填那边这条兜底救过一次 40 分钟的人工滚动；
    # 增量重跑虽然便宜，但"解析器悄悄失效"与"这几天确实没发帖"在日志里
    # 长得一模一样，没有原始响应就无从分辨。份数由 keep_captures 裁。
    if not dry_run:
        dump = arc.base / ("_capture_delta_%d.json" % int(time.time()))
        atomic_write_json(dump, col.payloads)
        prune_captures(arc.base, dcfg.keep_captures)

    posts = extract(col.payloads, platform, account, route="delta")
    if not posts:
        raise DeltaBlocked("捕获到 %d 段响应但一篇都没解析出来 —— "
                           "解析器可能已经与真实结构不符" % len(col.payloads))

    # 首屏同样会混进推荐内容与被 @ 的 UGC（回填时实测 IG 混进 266 条、
    # FB 混进 1 条；增量实测 IG 一次混进 38 条）。
    # "增量只看几条"不是省掉这一步的理由。
    posts, rejected = partition_by_owner(posts, account)
    if rejected and not dry_run:
        arc.record_rejected(rejected)

    known = arc.rows()
    dates = sorted(p.created_at for p in posts if p.created_at)
    target = (account or "").strip().lower()
    n_authored = sum(1 for p in posts if p.owner == target)
    # 被丢弃的那批里，作者是已知合作方的 —— 强烈提示"合作判定又漏了"。
    # 归档里的 210 个合作方 vs 历史上丢弃过的 6 个账号，交集为空，
    # 所以这条命中一次就值得看（core/integrity.py 的第四项检查有完整理由）。
    # 合作方名单同时取自归档和**本次留下的这批**：归档为空的新账号
    # （第一次跑增量、还没回填过）光看归档会得到空名单，这一项就白搭了。
    suspect = integrity.check_dropped_partners(
        rejected,
        integrity.known_partners(known + [p.to_row() for p in posts], account))
    res = ScanResult(
        own=len(posts), rejected=len(rejected),
        authored=n_authored, collab=len(posts) - n_authored, suspect=suspect,
        newest_seen=dates[-1] if dates else "", oldest_seen=dates[0] if dates else "",
        newest_known=max((r.get("created_at") or "" for r in known), default=""),
        payloads=len(col.payloads))

    # 「看到的自家帖子太少」是"没看到时间线"最可靠的信号。
    # ⚠️ 用篇数而不是"最新一篇的日期倒退"来判：后者在账号删掉最新一帖时会
    # 每天误报、把失败预算耗光，而删帖是会真实发生的。
    # 空归档不能把门槛一起降成 0：首次接入新账号时，推荐位/UGC 全被丢弃
    # 仍会得到“成功、0 篇自家内容”，随后刷新 last_success 并掩盖错误目标。
    # 归档很小时继续按已有规模降门槛，但只要配置没有显式设 0，至少要看到 1 篇。
    floor = (0 if dcfg.min_own_posts == 0 else
             min(max(1, dcfg.min_own_posts), max(1, len(known))))
    if len(posts) < floor:
        # 命中"丢弃的里面有合作方"时，把它写进中止理由里。**这一句是给下一个
        # 会话省几个小时的**：同样是"只看到 1 篇"，"归属判定漏了合作帖"
        # 和"真的被登录墙拦了"处理办法完全不同，而 2026-08-30 那次
        # 恰恰是先猜了后者、写了一整条新代码路径，才发现是前者。
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
        await download_media(ctx, arc, post, url)
        if not arc.append(post):
            print("    ! %s 媒体仍未补全，保留原归档并留待下次重试" % post.post_id)
            continue
        imgs = sum(1 for m in post.media if m.kind == "image")
        vids = sum(1 for m in post.media if m.kind == "video")
        print("  + %s  %d图/%d视频  %s" % (post.post_id, imgs, vids, head))
        if was_known:
            res.upgraded += 1
        else:
            res.new += 1
    return res


# ---- D3：把完整性检查接进增量 -------------------------------------------

def run_integrity(arc: Archive, entry: dict, platform: str,
                  now: datetime | None = None,
                  suspect: list[dict] | None = None) -> list[dict]:
    """跑完整性检查并把命中项告警出去。返回命中项，便于测试与打印。

    **为什么这一步在方案 B 之下比原来更重要**：登出增量最坏只是抓不到；
    登录态增量最坏是**会话失效后每天硬撞，直到账号被处理**。
    爬取路径没有 ground truth——"滚到这里就没了"和"被限流截断了"在响应上
    长得一样——这一层是唯一能让"悄悄坏掉"变成"看得见地坏掉"的东西。

    告警走 `core.notify.notify()`，它**无条件先写 `state/alerts.log`**，
    所以这里不需要再自己写一遍日志（D2 已确立的职责划分）。

    ``suspect`` 是本次扫描里"被丢弃、但作者是已知合作方"的那些
    （见 `core.integrity.check_dropped_partners`）。它和其它三项不同，
    是**本次扫描的**结论而不是归档的结论，所以由调用方传进来。
    **这一条不做去重节流**：其它检查会天天成立（账号真停更时"零新增"
    每天都真），而这一条在全部真实数据上从未成立过——它响一次就意味着
    归属判定可能又开始漏判自家内容了，漏报的代价远大于重复提醒。
    """
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


# ---- C6：主入口 ---------------------------------------------------------

async def _run_due(platforms: list[str], dcfg: DeltaConfig, state: dict,
                   path: Path, dry_run: bool) -> int:
    """附着一次 Chrome，把到期的平台依次跑完。返回退出码。"""
    try:
        pw, browser, ctx = await attach()
    except (Exception, SystemExit) as e:
        # attach 在进入平台循环前失败；若让异常直接穿透，两个平台都会表现成
        # “从未运行”，失败预算和 --if-stale 也就全部失真。core.chrome.attach
        # 的端口竞态/无 context 契约会抛 SystemExit，必须显式列出；不要捕获
        # KeyboardInterrupt 等其它 BaseException。
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
            # 配置读取和 Archive 构造也是这个平台的一次运行步骤，必须和
            # delta_once 落在同一个异常闭环里。否则目录权限、路径校验或坏配置
            # 会在真正抓取前直接穿透：既不累计失败预算，也没有状态/通知，且会
            # 无端阻止另一个平台继续（这些都是我们本地的问题，不是对面在拦）。
            account = "账号配置未读取"
            try:
                c = cfg()
                account = c["targets"][platform]
                arc = Archive(c.archive_dir,
                              "%s_%s" % (platform[:2], account))
                print("\n=== %s / %s ===" % (platform, account))
                res = await delta_once(ctx, platform, account, arc, dcfg,
                                       dry_run=dry_run)
            except DeltaBlocked as e:
                # C7「异常即停」：当次立即停止并告警，不重试、不换 UA、不绕。
                # 继续试探是把"可能被注意到"变成"确定被注意到"。
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
                    # 剩下的平台也一起记一次失败：它们没跑，但"今天被拦了"
                    # 这件事必须体现在预算里，否则连续被拦时预算永远攒不满，
                    # 自动运行就永远停不下来。
                    for rest in platforms[i + 1:]:
                        record_failure(state.setdefault(rest, blank_entry()),
                                       utcnow(), "同批次的 %s 被拦，本次未执行" % platform)
                        print("[i] %s 本次不再尝试（同一会话、同一指纹）" % rest)
                    save_state(path, state)
                    break
                save_state(path, state)
                continue
            except (Exception, SystemExit) as e:
                # 解析器/Playwright/存储之外的编程异常也必须进入同一套状态闭环。
                # 否则无人值守任务只留下一个非 0 退出码，却不累计失败预算、
                # 不更新 last_error，也不会通过通知把问题暴露给业务人员。
                # Archive 的安全校验和部分配置入口会用 SystemExit 表达硬错误；
                # 显式接住它，但仍不捕获 KeyboardInterrupt 等其它 BaseException。
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
                # 不当失败处理：账号删掉最新一帖时也会这样，天天误报会把
                # 失败预算耗光。但必须说出来——它是"没看到时间线"的强提示。
                print("[!] 看到的最新一篇（%s）比归档里最新的（%s）还旧 —— "
                      "确认一下是不是没拿到时间线"
                      % (res.newest_seen[:10], res.newest_known[:10]))
            if dry_run:
                continue
            record_success(entry, utcnow(), res.new)
            # D3：检查放在写完状态之后、存盘之前——它要读刚更新的
            # consecutive_quiet_days，而它自己的"报过了"标记也要一起落盘。
            run_integrity(arc, entry, platform, suspect=res.suspect)
            save_state(path, state)
            quiet = entry["consecutive_quiet_days"]
            if quiet >= dcfg.quiet_days_before_slowdown(platform):
                print("[i] 已连续 %d 天零新增，下次起按降频节奏（%.0f 小时一次）"
                      % (quiet, dcfg.slowdown_stale_after_hours))
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
                   help="跳过随机延迟。手工跑用，计划任务不要加")
    p.add_argument("--reset-failures", action="store_true",
                   help="失败预算用尽后，人工确认已处理，用它清零")
    p.add_argument("--status", action="store_true", help="只打印状态，不抓取")
    return p.parse_args(argv)


def _run_locked(args, dcfg: DeltaConfig, path: Path,
                state: dict, platforms: list[str]) -> int:
    """持有 :class:`DeltaRunLock` 后执行一次计划/手工增量。"""
    now = utcnow()
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
            hours = effective_stale_hours(entry, dcfg, platform)
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
        # ❌ 不得静默跳过：什么都没做也要说清楚是为什么，
        # 否则"每天都在跑"会悄悄变成"一年没跑过"而无人察觉。
        print("没有到期的平台，本次不抓取。")
        return 2 if blocked_by_budget else 0

    # C7「随机化触发时刻」：计划任务只能定固定时刻，抖动必须在这里做。
    # ⚠️ 顺序是**先判 stale 再抖动**——反过来的话每次唤醒都要先睡半小时
    # 才发现"其实不用跑"。
    if not args.no_jitter and not args.dry_run and dcfg.start_jitter_minutes > 0:
        delay = random.uniform(0, dcfg.start_jitter_minutes * 60)
        print("随机延迟 %.1f 分钟后开始（避免每天固定整点发起请求）..."
              % (delay / 60))
        time.sleep(delay)

    port = cfg().debug_port
    if not cdp_ready(port):
        if dcfg.autostart_chrome:
            print("专用 Chrome 没在跑，正在拉起...")
            # 这不是自动登录：会话是人留在 profile 目录里的，这里只是把
            # 那个浏览器重新用起来（全项目红线 1 没有松动）。
            launch_error: Exception | SystemExit | None = None
            try:
                launched = launch()
            except (Exception, SystemExit) as e:
                # launch 的 profile.mkdir / cfg.chrome_exe / Popen 都可能失败；
                # core.chrome 也用 SystemExit 表达部分配置错误。两种形态必须
                # 汇入下面同一套失败状态闭环，但不要吞 KeyboardInterrupt。
                launched = False
                launch_error = e
            if not launched:
                msg = ("专用 Chrome 拉不起来（端口 %d 未就绪）。"
                       "若端口被其它程序占用，改 config.toml 的 [chrome].debug_port"
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
    # 一个平台预算耗尽、另一个仍可运行时，后者成功不能把“部分停摆”洗成 rc=0。
    # 运行本身失败（rc=1）优先；否则用 2 让 Task Scheduler 看见需要人工介入。
    return run_rc if run_rc else (2 if blocked_by_budget else 0)


def main(argv=None) -> int:
    args = _parse_args(argv)
    # 运行分隔线由 Python 打，不由 .bat 的 echo %DATE% 打：
    # cmd 按控制台代码页（本机 936）写，会在一份 UTF-8 日志里插进 GBK 字节。
    # 输出被 run_delta.bat 追加进 state/delta.log，没有这行就分不清哪段是哪次跑的。
    print("\n===== %s · %s =====" % (iso(utcnow()), " ".join(argv or sys.argv[1:])
                                     or "(无参数)"))
    dcfg = DeltaConfig.load()
    path = state_path()
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]

    if args.status:
        _print_status(load_state(path))
        return 0

    lock_path = path.with_name("delta.lock")
    try:
        with DeltaRunLock(lock_path):
            # 锁前读出的 state 可能已经被另一个计划任务改过；拿到锁后才读，
            # stale 判定和随后的原子写才能基于同一份最新状态。
            state = load_state(path)
            if args.reset_failures:
                for platform in platforms:
                    entry = state.setdefault(platform, blank_entry())
                    entry["consecutive_failures"] = 0
                    entry["last_error"] = None
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

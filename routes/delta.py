r"""每日增量：**完全登出**，不带任何 cookie。对应实施计划的 C 组。

与 routes\backfill.py 的根本差别不是"更快"或"更自动"，而是**身份**：
回填带着小号的登录态跑一次，增量每天跑，但**没有账号可封**——
没有 session、没有 cookie，最坏情况只是 IP 被临时限流，换个时间重试即可。

每日定时会把封号风险从"一次性"变成"累积性"（单次会不会被封，和 365 次里
会不会被封一次，是两个量级的问题）。这条路径上的每一个设计
——core/http.py 拒收 Set-Cookie、并发恒为 1、Pacer 的随机间隔——
都是为了让"累积"这件事无处发生。**不要为了省事在这里加登录态。**

⛔ **2026-08-30：上面这套「完全登出」的前提已经不成立了。**

实测 + 用户确认，Facebook / Instagram 在**没有登录态时直接报错**
（对公开账号请求 `web_profile_info`，首次请求即 429）。登出这条腿不存在了。

**用户拍板走方案 B：增量改走登录态**，复用 `routes/backfill.py` 那条 CDP 通道。
代价是封号风险从"一次性敞口"变成"累积性敞口"——
详见 `docs/IMPLEMENTATION_PLAN.md` 第 0 节的方案变更说明与 C7 的缓解措施。

当前实现范围：
    C2  Instagram 登录态增量  已实现（方案 B）
    C3  Facebook  登录态增量  已实现
    C4  增量的媒体下载        已实现（走浏览器请求栈，不用 core/http.py）
    C5  运行状态记录          已实现（state/delta_state.json）
    C6  主入口                已实现（--platform / --dry-run / --if-stale）
    C7  累积风险缓解          已实现，**是方案的组成部分不是可选项**

⚠️ **本文件里的登出实现（下半部分之前的那一段）保留，不要删。**
它是对的（55 项离线断言全过，含归属过滤、登录墙三形态、429 退避上限），
只是端点关了。若哪天该端点重新对登出开放，切回去是**风险更低**的路径。
登录态实现是**并行新增**的，没有覆盖它；`python -m routes.delta --logged-out-probe`
仍可跑那条老路径做探针。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.capture import (Collector, download_media, harvest_embedded_json,
                          prune_captures)
from core.chrome import attach, cdp_ready, launch
from core.config import cfg, per_platform
from core.http import ig_headers, logged_out_client
from core import integrity
from core.integrity import parse_ts
from core.notify import notify
from core.parse import extract, partition_by_owner
from core.session import Pacer
from core.store import Archive, Post

# 登出可用的公开端点。**不需要 doc_id**，这正是选它的理由：
# IG 的 web GraphQL 端点要硬编码 doc_id，而那个值每 2-4 周轮换，
# 轮换后返回 401 且文案是"稍后再试"——那句提示是误导的，等多久都不会恢复。
IG_PROFILE_API = "https://www.instagram.com/api/v1/users/web_profile_info/"

# 429 的重试上限。登出路径没有会话可丢，但重试会加重 IP 侧的触发，
# 所以退避两次仍失败就收手，换个时间再来，别在这儿死磕。
MAX_RATE_LIMIT_RETRIES = 2


def _pacer() -> Pacer:
    """按 config.toml 的 [delta].request_gap_seconds 造限速器。

    配置只给一个数，而 Pacer 要的是区间：把它当**下界**，上界取两倍。
    随机化本身比具体倍数重要——固定间隔是最容易被识别的行为特征之一，
    而这条路径每天都会跑。
    """
    gap = float(cfg().get("delta", "request_gap_seconds", 8))
    return Pacer(lo=gap, hi=gap * 2)


def _body_hint(resp: httpx.Response, limit: int = 200) -> str:
    """失败响应的正文摘要，压成一行。

    诊断价值很高，别丢：IG 的失败正文能区分几种长得一样的失败——
    "Please wait a few minutes before you try again" 是**误导性文案**
    （计划第 1 节：它不是限流，等多久都不会恢复），
    而真限流、登录墙、端点下线给的是别的话。
    只有状态码的话，这三种在日志里完全一样。
    """
    try:
        text = resp.text
    except Exception:
        return ""
    text = " ".join(text.split())
    if not text:
        return ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _print_body_hint(resp: httpx.Response) -> None:
    hint = _body_hint(resp)
    if hint:
        print("    响应正文：%s" % hint)


def _login_wall_reason(resp: httpx.Response) -> str | None:
    """这次响应是不是被登录墙拦了？是就返回人话理由，不是就返回 None。

    单拆成纯函数是为了能离线测：登录墙有好几种长相（302 到 /accounts/login、
    200 但返回 HTML、200 但返回的 JSON 里根本没有 user），
    只断言"最后返回了空列表"分不清它到底走了哪条分支——
    而这几条分支的处理办法完全不同。
    """
    if "/accounts/login" in str(resp.url):
        return "请求被重定向到登录页"
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" not in ctype:
        return "返回的是 %s 而不是 JSON" % (ctype or "未知类型")
    return None


def _timeline_nodes(payload: dict) -> list[dict]:
    """从 web_profile_info 的响应里取出时间线帖子节点。

    只取 data.user.edge_owner_to_timeline_media.edges[].node 这一处，
    不把整个 payload 丢给 walk()：响应里还有精选、标签页等其它节点集合，
    全树搜索会把它们一起捞进来，变成凭空多出来的"帖子"。
    """
    user = (payload.get("data") or {}).get("user")
    if not isinstance(user, dict):
        return []
    edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges") or []
    return [e["node"] for e in edges
            if isinstance(e, dict) and isinstance(e.get("node"), dict)]


def _get_profile(client: httpx.Client, account: str, pacer: Pacer) -> dict | None:
    """拿 web_profile_info 的 JSON。失败一律打印原因并返回 None，不静默吞掉。"""
    headers = ig_headers(account)

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        pacer.wait()
        try:
            resp = client.get(IG_PROFILE_API, params={"username": account},
                              headers=headers)
        except httpx.RequestError as e:
            print("[!] 连不上 Instagram：%s: %s" % (type(e).__name__, e))
            return None

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            if attempt < MAX_RATE_LIMIT_RETRIES:
                print("[i] Instagram 返回 429（第 %d/%d 次），退避后重试%s"
                      % (attempt + 1, MAX_RATE_LIMIT_RETRIES,
                         "，Retry-After: %s" % retry_after if retry_after else ""))
                pacer.backoff(attempt)
                continue
            print("[!] Instagram 持续返回 429。")
            _print_body_hint(resp)
            print("    不要继续重试，隔几小时再跑。登出路径没有账号会被封，")
            print("    但硬顶着限流打会让这个 IP 更长时间不可用。")
            print("    若换时间、换网络后仍然每次都是 429，那就不是限流，")
            print("    而是该端点已对登出访客关闭 —— 按 C3 的模式记录结论并改走人工触发回填。")
            return None

        if resp.status_code in (401, 403):
            # 登出访问拿 401/403 基本等于"这条路今天被关了"。
            # 注意别把它当限流理解：等待对这种 401 无效。
            print("[!] Instagram 返回 %d —— 登出访问被拒。" % resp.status_code)
            _print_body_hint(resp)
            print("    这不是限流，等多久都不一定恢复。隔段时间再试；")
            print("    若持续如此，说明该端点对登出访客关闭了，需要改走人工触发回填。")
            return None

        if resp.status_code != 200:
            print("[!] Instagram 返回 HTTP %d（账号 %s）" % (resp.status_code, account))
            _print_body_hint(resp)
            return None

        reason = _login_wall_reason(resp)
        if reason:
            print("[!] 登录墙触发：%s" % reason)
            print("    **不要立即重试** —— 重试会加重触发。降低频率，隔几小时再来。")
            return None

        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            print("[!] 登录墙触发：响应声称是 JSON 但解析不了")
            print("    **不要立即重试** —— 重试会加重触发。降低频率，隔几小时再来。")
            return None

        if not isinstance(payload, dict):
            print("[!] Instagram 返回的 JSON 不是对象（%s）" % type(payload).__name__)
            return None
        return payload

    return None


def fetch_instagram(account: str, client: httpx.Client | None = None,
                    pacer: Pacer | None = None) -> list[Post]:
    """登出抓取一个 Instagram 账号的时间线。对应实施计划的 C2。

    拿不到就返回空列表——但**绝不静默返回空**，每条失败路径都会打印
    它自己的原因。"抓到 0 条"和"被墙了"在响应上长得不一样，
    在输出里也必须长得不一样，否则每天的定时任务会安静地空跑一年。

    ⚠️ 该端点的 timeline media **不含轮播子项**，只有封面（display_url）。
    轮播帖因此会被 core/parse.py 标成 media_complete=False，
    留给完整性检查汇总、由登录态回填补齐。这是端点的限制，不是 bug。
    """
    own_client = client is None
    client = client if client is not None else logged_out_client()
    pacer = pacer if pacer is not None else _pacer()
    try:
        payload = _get_profile(client, account, pacer)
    finally:
        if own_client:
            client.close()

    if payload is None:
        return []

    user = (payload.get("data") or {}).get("user")
    if not isinstance(user, dict):
        print("[!] 响应里没有 %s 的资料 —— 账号名写错、账号不存在，或已被删除" % account)
        return []
    if user.get("is_private"):
        print("[!] %s 是私密账号，登出访问看不到任何帖子。" % account)
        return []

    nodes = _timeline_nodes(payload)
    if not nodes:
        # 「抓到 0 条」必须显式告警，不能当成正常结束（计划第 2 节的错误处理约定）
        print("[!] %s 的时间线返回了 0 条 —— 可能是账号没有帖子，"
              "也可能是端点开始对登出访客做裁剪。" % account)
        return []

    posts = extract(nodes, "instagram", account, route="delta")
    # 这个端点只返回目标账号的时间线，理论上不会混进别人的帖子。
    # 仍然过一遍：回填那边同样"理论上"不该混，实测混进了 266 条。
    # 一次多余的过滤是免费的，一次漏网要下游用法务风险来买单。
    posts, rejected = partition_by_owner(posts, account)
    if rejected:
        print("[!] %d 个节点的归属不是 %s，已丢弃（owner: %s）"
              % (len(rejected), account,
                 ", ".join(sorted({str(r["owner"]) for r in rejected})[:5])))
    if len(posts) > len(nodes):
        # extract 内部走 walk() 全树搜索。如果它比时间线节点数还多，
        # 说明有嵌套节点也匹配上了帖子判定 —— 那是凭空多出来的帖子，必须看见。
        print("[!] 解析出 %d 篇，但时间线只有 %d 个节点 —— "
              "有嵌套节点被误判成帖子，解析器需要收紧。" % (len(posts), len(nodes)))
    return posts


def _probe(account: str) -> int:
    """C2 的验收探针：跑一次登出抓取，只打印不写盘。

    写盘要等 C4（媒体下载，URL 有时效必须同批下完）和 C5（状态记录）。
    在那之前落 manifest 会写进一批没有本地图片的记录，
    反而要靠 media_complete 的升级路径去补——没必要给自己制造这个坑。
    """
    print("登出抓取 instagram/%s（无 cookie、无登录态）" % account)
    posts = fetch_instagram(account)
    print("\n解析出 %d 篇" % len(posts))
    for p in sorted(posts, key=lambda x: x.created_at, reverse=True):
        imgs = sum(1 for m in p.media if m.kind == "image")
        vids = sum(1 for m in p.media if m.kind == "video")
        flag = "" if p.media_complete else "  [媒体不全]"
        head = (p.text or "").replace("\n", " ")[:42]
        print("  %s  %s  %d图/%d视频  %s%s"
              % (p.post_id, p.created_at or "(无日期)", imgs, vids, head, flag))

    with_text = sum(1 for p in posts if p.text.strip())
    incomplete = sum(1 for p in posts if not p.media_complete)
    print("\n其中 %d 篇正文非空，%d 篇媒体不全（轮播帖只拿得到封面，端点限制）"
          % (with_text, incomplete))
    print("本次只抓不写盘 —— 写归档要等 C4/C5/C6。")
    return 0 if posts and with_text else 1


# ==========================================================================
# 方案 B：登录态 + CDP 附着（C2 / C3 / C4 / C5 / C6 / C7）
#
# 与上面那条登出路径的差别不是"更自动"，而是**身份**：这条路每天都带着
# 小号的登录态去露一次面。封号风险因此从「一次性敞口」变成「累积性敞口」，
# 而抓取小号被封是本项目**唯一不可恢复的失败模式**（回填与增量同时断掉）。
#
# 因此下面每一处看着"保守到没必要"的地方——只滚两屏、随机延迟、异常即停、
# 失败预算、零新增降频——都是在为"每天都要来一次"买保险。
# **不要因为"跑得挺好"就把它们优化掉**：这类风险的反馈是延迟的，且只反馈一次。
# ==========================================================================

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
    harvest_embedded: bool = True
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
            harvest_embedded=bool(g("harvest_embedded", True)),
            min_own_posts=int(g("min_own_posts", 3)),
            _quiet_slowdown=g("quiet_days_before_slowdown", 7),
        )

    def quiet_days_before_slowdown(self, platform: str) -> int:
        return int(per_platform(self._quiet_slowdown, platform, 7))

    def pacer(self) -> Pacer:
        """滚动之间的停顿。配置给下界，上界取两倍——随机化本身比倍数重要。"""
        return Pacer(lo=self.request_gap_seconds, hi=self.request_gap_seconds * 2)


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
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                    encoding="utf-8")


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


async def _pause(pacer: Pacer) -> None:
    """Pacer 的间隔策略，但用 await 睡。

    ⚠️ 不能直接调 `pacer.wait()`：那是 `time.sleep`，会把事件循环整个冻住，
    而**响应体正是在那个循环上异步读的**——睡 8 秒等于这 8 秒里到达的响应
    一段都读不到，最后表现为"抓到 0 篇"，且看不出原因。
    """
    await asyncio.sleep(random.uniform(pacer.lo, pacer.hi))


async def _eval(page, expr: str, default):
    """页面求值，失败返回默认值。这些都是辅助信息，不该让抓取失败。"""
    try:
        value = await page.evaluate(expr)
    except Exception:
        return default
    return default if value is None else value


async def human_scroll(page, dcfg: DeltaConfig, pacer: Pacer) -> tuple[int, float]:
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
        await _pause(pacer)
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
        screens, moved = await human_scroll(page, dcfg, dcfg.pacer())
        if screens and moved <= 0:
            # 滚了却没动。这不是小事：增量看不到新内容时，
            # "页面没滚动"和"确实没新帖"在输出里必须长得不一样。
            print("[!] 滚了 %d 屏但页面没有移动（scrollY 未变）——"
                  "滚轮事件可能没落在可滚动区域" % screens)
        final_url = page.url
        # IG 的首屏时间线随 HTML 下发、不走 XHR，只拦响应会永远看不到最新几篇。
        # 放在滚动之后取：这时页面已经把该渲染的都渲染了。
        if dcfg.harvest_embedded:
            embedded = await harvest_embedded_json(page)
            col.embedded = len(embedded)
            col.payloads.extend(embedded)
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
    own: int = 0
    rejected: int = 0
    newest_seen: str = ""
    oldest_seen: str = ""
    newest_known: str = ""
    payloads: int = 0
    embedded: int = 0

    def summary(self) -> str:
        span = ("%s ~ %s" % (self.oldest_seen[:10], self.newest_seen[:10])
                if self.newest_seen else "—")
        return ("新增 %d 篇 · 本账号 %d 篇（%s）· 丢弃 %d · 归档最新 %s"
                % (self.new, self.own, span, self.rejected,
                   self.newest_known[:10] or "—"))

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
        dump.write_text(json.dumps(col.payloads, ensure_ascii=False),
                        encoding="utf-8")
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
    res = ScanResult(
        own=len(posts), rejected=len(rejected),
        newest_seen=dates[-1] if dates else "", oldest_seen=dates[0] if dates else "",
        newest_known=max((r.get("created_at") or "" for r in known), default=""),
        payloads=len(col.payloads), embedded=col.embedded)

    # 「看到的自家帖子太少」是"没看到时间线"最可靠的信号。
    # ⚠️ 用篇数而不是"最新一篇的日期倒退"来判：后者在账号删掉最新一帖时会
    # 每天误报、把失败预算耗光，而删帖是会真实发生的。
    floor = min(dcfg.min_own_posts, len(known))
    if len(posts) < floor:
        raise DeltaBlocked(
            "只看到 %d 篇属于 %s 的帖子（丢弃 %d 篇他人内容，期望至少 %d 篇）"
            " —— 大概率没有拿到时间线，而不是没有新帖"
            % (len(posts), account, len(rejected), floor))

    for post in sorted(posts, key=lambda p: p.created_at or "", reverse=True):
        if not arc.should_append(post):
            continue
        head = (post.text or "").replace("\n", " ")[:38]
        if dry_run:
            print("  ~ %s  %s  %s" % (post.post_id, post.created_at or "(无日期)", head))
            res.new += 1
            continue
        await download_media(ctx, arc, post, url)
        if not arc.append(post):
            print("    ! %s 媒体仍未补全，保留原归档并留待下次重试" % post.post_id)
            continue
        imgs = sum(1 for m in post.media if m.kind == "image")
        vids = sum(1 for m in post.media if m.kind == "video")
        print("  + %s  %d图/%d视频  %s" % (post.post_id, imgs, vids, head))
        res.new += 1
    return res


# ---- D3：把完整性检查接进增量 -------------------------------------------

def run_integrity(arc: Archive, entry: dict, platform: str,
                  now: datetime | None = None) -> list[dict]:
    """跑完整性检查并把命中项告警出去。返回命中项，便于测试与打印。

    **为什么这一步在方案 B 之下比原来更重要**：登出增量最坏只是抓不到；
    登录态增量最坏是**会话失效后每天硬撞，直到账号被处理**。
    爬取路径没有 ground truth——"滚到这里就没了"和"被限流截断了"在响应上
    长得一样——这一层是唯一能让"悄悄坏掉"变成"看得见地坏掉"的东西。

    告警走 `core.notify.notify()`，它**无条件先写 `state/alerts.log`**，
    所以这里不需要再自己写一遍日志（D2 已确立的职责划分）。
    """
    gap_days, alert_after = integrity.params(platform)
    findings = integrity.run_checks(
        arc.rows(), arc.needs_media(), entry, platform,
        gap_days=gap_days, alert_after=alert_after, now=now or utcnow())
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
    pw, browser, ctx = await attach()
    rc = 0
    try:
        for i, platform in enumerate(platforms):
            account = cfg()["targets"][platform]
            arc = Archive(cfg().archive_dir, "%s_%s" % (platform[:2], account))
            entry = state.setdefault(platform, blank_entry())
            print("\n=== %s / %s ===" % (platform, account))
            try:
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

            print(res.summary() + ("（--dry-run，未写盘）" if dry_run else ""))
            if res.embedded:
                print("    （其中 %d 段来自页面内嵌 JSON —— IG 首屏时间线走这条）"
                      % res.embedded)
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
            run_integrity(arc, entry, platform)
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
    p.add_argument("--logged-out-probe", action="store_true",
                   help="跑保留的登出实现做探针（端点已关闭，预期失败）")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    # 运行分隔线由 Python 打，不由 .bat 的 echo %DATE% 打：
    # cmd 按控制台代码页（本机 936）写，会在一份 UTF-8 日志里插进 GBK 字节。
    # 输出被 run_delta.bat 追加进 state/delta.log，没有这行就分不清哪段是哪次跑的。
    print("\n===== %s · %s =====" % (iso(utcnow()), " ".join(argv or sys.argv[1:])
                                     or "(无参数)"))
    if args.logged_out_probe:
        return _probe(cfg()["targets"]["instagram"])

    dcfg = DeltaConfig.load()
    path = state_path()
    state = load_state(path)
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]

    if args.status:
        _print_status(state)
        return 0

    if args.reset_failures:
        for platform in platforms:
            entry = state.setdefault(platform, blank_entry())
            entry["consecutive_failures"] = 0
            entry["last_error"] = None
        save_state(path, state)
        print("已清零 %s 的失败计数。" % "、".join(platforms))
        return 0

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
            if not launch():
                msg = ("专用 Chrome 拉不起来（端口 %d 未就绪）。"
                       "若端口被其它程序占用，改 config.toml 的 [chrome].debug_port"
                       % port)
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
            notify("增量没能启动", msg)
            return 1

    return asyncio.run(_run_due(due, dcfg, state, path, args.dry_run))


if __name__ == "__main__":
    from core.console import force_utf8

    force_utf8()
    raise SystemExit(main())

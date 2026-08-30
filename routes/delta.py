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
    C2  Instagram 登录态增量  **待建**（方案 B）
    C3  Facebook  登录态增量  待建
    C4  增量的媒体下载        待建（走浏览器请求栈，不要用 core/http.py）
    C5  运行状态记录          待建
    C6  主入口（--platform / --dry-run / --if-stale）待建
    C7  累积风险缓解          待建，**是方案的组成部分不是可选项**

⚠️ **本文件里现有的这份登出实现保留，不要删。**
它是对的（55 项离线断言全过，含归属过滤、登录墙三形态、429 退避上限），
只是端点关了。若哪天该端点重新对登出开放，切回去是**风险更低**的路径。
C2 的新实现请**并行新增**，不要覆盖它。

因此本模块现在**只抓不写盘**：__main__ 是旧登出实现的探针，不是最终入口。
"""
from __future__ import annotations

import json
import sys

import httpx

from core.config import cfg
from core.http import ig_headers, logged_out_client
from core.parse import extract, partition_by_owner
from core.session import Pacer
from core.store import Post

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


if __name__ == "__main__":
    from core.console import force_utf8

    force_utf8()
    # C6 会把这里换成完整入口（--platform / --dry-run / --if-stale）。
    # 当前只是 C2 的验收探针：不带参数用 config.toml 里的目标账号，
    # 带一个参数就抓那个账号（验收要求的"已知公开账号"走这条）。
    raise SystemExit(_probe(sys.argv[1] if len(sys.argv) > 1
                            else cfg()["targets"]["instagram"]))

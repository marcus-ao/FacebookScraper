"""完整性检查。对应实施计划的 D1。

爬取相对官方 API 最本质的劣势是**没有 ground truth**：
"滚到这里就没了"和"被限流截断了"在响应上长得一模一样。
本模块的目的不是修复什么，而是**让静默失败变成可见失败**——
它只负责把可疑之处找出来，处理交给人。

四项检查各自盯一种失败模式：
  check_continuity        时间序列里的洞  → 某段历史根本没被抓到
  check_quiet             长期零新增      → 增量路径已经被登录墙拦住了
  check_incomplete        媒体不全的帖子  → 源响应只给了封面，或图片没下全
  check_dropped_partners  丢弃了合作方的帖子 → 归属判定又开始漏判自家内容了
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse_ts(value) -> datetime | None:
    """把 manifest 里的 created_at 解析成 datetime，解析不了就返回 None。

    正常路径产出的是 `core.parse.iso()` 的 "%Y-%m-%dT%H:%M:%SZ"，
    但 `from_fb_story` 在时间戳不是数字时会原样透传，所以这里必须宽容。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# 时间戳解析是增量的状态文件（routes/delta.py）也要用的东西，
# 公开一个不带下划线的名字，免得项目里出现第三份 fromisoformat 包装。
parse_ts = _parse_ts


def check_undated(rows: list[dict]) -> list[dict]:
    """created_at 缺失或无法解析的记录。

    计划里没有这一项，是实现时补的：这些记录**无法参与连续性检查**，
    如果只是在 check_continuity 里默默跳过，它们就成了检查不到的盲区——
    而"检查不到"恰恰是本模块要消灭的东西。
    """
    return [r for r in rows if _parse_ts(r.get("created_at")) is None]


def check_continuity(rows: list[dict], gap_days: int) -> list[dict]:
    """按时间排序后，找出相邻两帖间隔超过 gap_days 的位置。

    返回 `[{"after": <较早那篇>, "before": <较晚那篇>, "gap_days": 12.4}]`——
    缺口在 after 之后、before 之前。

    created_at 不可用的记录会被排除（它们由 check_undated 单独汇报）。
    """
    dated = []
    for r in rows:
        ts = _parse_ts(r.get("created_at"))
        if ts is not None:
            dated.append((ts, r))
    dated.sort(key=lambda pair: pair[0])

    gaps: list[dict] = []
    for (t_early, r_early), (t_late, r_late) in zip(dated, dated[1:]):
        delta = (t_late - t_early).total_seconds() / 86400.0
        if delta > gap_days:
            gaps.append({
                "after": r_early.get("post_id"),
                "before": r_late.get("post_id"),
                "gap_days": round(delta, 1),
            })
    return gaps


# --------------------------------------------------------------------------
# 第四项：归属判定漏判（CR-19 那一类失败的"下次能被发现"版本）
# --------------------------------------------------------------------------

def known_partners(rows: list[dict], account: str) -> set[str]:
    """从归档里推出"本账号合作过的账号"名单。

    两个来源，都要：

    1. 合作帖的 `owner` —— 别人发布、本账号是 coauthor；
    2. 任何一篇帖子的 `coauthors` —— 本账号发布、别人是 coauthor。

    实测 Instagram 归档里有 **210 个**这样的账号
    （neakasa.global 40 篇、neakasa.de 18 篇、aria_neakasa 14 篇……）。
    """
    who = (account or "").strip().lower()
    out: set[str] = set()
    for r in rows:
        owner = (r.get("owner") or "").strip().lower()
        if owner and owner != who:
            out.add(owner)
        for c in r.get("coauthors") or []:
            name = (c or "").strip().lower() if isinstance(c, str) else ""
            if name and name != who:
                out.add(name)
    return out


def check_dropped_partners(rejected: list[dict],
                           partners: set[str]) -> list[dict]:
    """被丢弃的节点里，作者是**已知合作方**的那些。

    ### 这一项为什么存在

    2026-08-30 之前，Instagram 有 **263 篇自己主页上的帖子**被归属判定当成
    他人帖丢掉，持续到用户自己问"会不会我们抓的这个只是转发角色"才被发现。
    根因（`coauthor_producers` 没被看）已经修了；**但让它一直没被发现的
    那个原因没修**——丢弃是完全静默的：`_rejected.jsonl` 只写不读，
    没有任何东西会说一句"你刚丢掉的这批里，有一半来自你的合作方"。

    合作机制会变（IG 换字段名、换形态、出新的联合发布方式），
    修好的是这一次，这一项盯的是下一次。

    ### 为什么用"作者是已知合作方"作信号

    它在现有全部真实数据上**零误报**：Instagram 归档里有 210 个合作方账号，
    四份 capture 一共丢弃过 6 个账号的帖子
    （chicagofire / shaq / cars_luxury_accessories / erykatravel /
    diycraftsofficial1 / craftypanda），**与合作方名单交集为空**。
    推荐位来自完全陌生的账号，而合作方的帖子本来就该留下——
    两者天然不重叠，所以这个信号既灵敏又安静。

    ⚠️ 它**不是判定**，是提示。真出现交集时正确的动作是去
    `_rejected.jsonl` 和 `_capture_*.json` 里离线查那一篇为什么没带上
    coauthor 信息，**不是**把它无条件收进来——"宁可漏一篇自家的，
    不可混进一篇别人的"这条没有变。
    """
    if not partners:
        return []
    return [r for r in rejected
            if (r.get("owner") or "").strip().lower() in partners]


def params(platform: str | None = None) -> tuple[int, int]:
    """从 config.toml 的 [integrity] 读阈值，返回 (gap_flag_days, alert_after_quiet_days)。

    检查函数本身收显式参数（好测），阈值来源集中在这里，代码里不写死数字。

    ⚠️ **两个阈值都支持按平台配置**（内联表写法）。给了 ``platform`` 就按平台取，
    不给就退回通用值。实测两个账号的节奏差一个量级，共用阈值必然一边误报、
    一边漏报——详见计划 D 组头部那张表。
    """
    from core.config import cfg, per_platform
    c = cfg()
    gap = per_platform(c.get("integrity", "gap_flag_days", None), platform or "", 5)
    quiet = per_platform(c.get("integrity", "alert_after_quiet_days", None),
                         platform or "", 4)
    return int(gap), int(quiet)


# --------------------------------------------------------------------------
# D3：把上面几项检查接进每日增量
# --------------------------------------------------------------------------

# 同一个问题隔多久才允许再报一次。账号真的停更时，"连续 N 天零新增"会天天成立，
# 天天弹一次的结果是用户把通知关掉——**那时真正的故障也就没人看得见了**。
ALERT_REPEAT_DAYS = 7

# 连续性只看最近这些天。归档跨 6 年，Instagram 里有 102 个 >5 天的历史间隔，
# 全量检查会每天把它们重报一遍。**几年前的缺口现在也补不回来，不是可行动信息。**
CONTINUITY_WINDOW_DAYS = 60

# state 里记住已经报过的缺口，避免同一个缺口天天报。留个上限，别无限长。
MAX_REMEMBERED_GAPS = 50


def _marks(entry: dict) -> dict:
    marks = entry.get("alerts")
    if not isinstance(marks, dict):
        marks = {}
        entry["alerts"] = marks
    return marks


def _due(marks: dict, key: str, now: datetime, repeat_days: int) -> bool:
    """这个问题上次报是什么时候？超过 repeat_days 才允许再报。"""
    last = _parse_ts(marks.get(key))
    if last is None:
        return True
    return (now - last).total_seconds() >= repeat_days * 86400


def run_checks(rows: list[dict], incomplete: list[dict], entry: dict,
               platform: str, *, gap_days: int, alert_after: int,
               now: datetime, window_days: int = CONTINUITY_WINDOW_DAYS,
               repeat_days: int = ALERT_REPEAT_DAYS) -> list[dict]:
    """跑完整性检查，返回**这次需要告警的**项。会更新 ``entry["alerts"]``。

    返回的每一项是 ``{"kind": ..., "message": ...}``，message 是可以直接
    发给人看的具体文案（含平台、检查项、数值）——
    反例"发现问题"，正例"instagram 连续 25 天零新增（阈值 21 天）"。

    ### 为什么不是"检查到就报"

    这一层的价值全部取决于**用户还会不会看它**。爬取路径没有 ground truth，
    告警是唯一能把"悄悄坏掉"变成"看得见地坏掉"的东西；而一旦它开始每天
    重复同一条不可行动的消息，用户会在第三天关掉通知，那之后真正的故障
    就再也没人知道了。**误报的代价不是打扰，是让整条告警通道失效。**

    所以这里的规则是"只报**新出现**或**变严重**的问题"：

    * 连续性缺口 —— 只看最近 ``window_days`` 天，且**同一个缺口只报一次**；
    * 媒体不全 / 无日期记录 —— 只在**数量比上次多**时报；
    * 长期零新增 —— 达阈值时报，之后每 ``repeat_days`` 天最多再报一次。
    """
    marks = _marks(entry)
    findings: list[dict] = []

    quiet = entry.get("consecutive_quiet_days")
    if (isinstance(quiet, (int, float)) and not isinstance(quiet, bool)
            and alert_after > 0 and quiet >= alert_after
            and _due(marks, "quiet_at", now, repeat_days)):
        marks["quiet_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        findings.append({
            "kind": "quiet",
            "message": ("%s 连续 %d 天零新增（阈值 %d 天）——"
                        "可能是增量被登录墙拦住了，也可能该账号确实停更了。"
                        "跑一次 --dry-run 看看能不能拿到时间线。"
                        % (platform, int(quiet), alert_after)),
        })

    # 只看最近这些天：几年前的缺口不可行动，天天重报只会淹掉今天的问题。
    #
    # ⚠️ **按"缺口的结束时间"筛，不是按"帖子的时间"筛。**
    # 旧写法先把窗口外的帖子整条剔掉再算相邻间隔，于是**跨越窗口边界的缺口
    # 会整个消失**：起点在窗口外的那一篇被删掉后，窗口内最早的一篇就没有
    # 前邻居了，缺口无从产生。2026-08-31 实测撞上这一条——Instagram
    # 2026-07-01 → 07-09 那个 8.4 天的缺口（阈值 6 天）**一次都没报过**，
    # 只因为起点比 60 天前早了一天。
    #
    # 这个盲区正好落在本模块要消灭的那类失败上：一段"根本没抓到"的历史，
    # 它的起点必然在更早的时间，越是大洞越容易被这样剔掉。
    # 改成先算全量相邻间隔、再按**较晚那篇**是否落在窗口内来筛，
    # "不重报陈年缺口"的初衷不变，盲区没了。
    cutoff = now - timedelta(days=window_days)
    seen = [g for g in (marks.get("gaps") or []) if isinstance(g, str)]
    by_id = {r.get("post_id"): r for r in rows if isinstance(r, dict)}

    def ends_in_window(gap: dict) -> bool:
        later = by_id.get(gap.get("before")) or {}
        ts = _parse_ts(later.get("created_at"))
        return ts is not None and ts >= cutoff

    fresh_gaps = []
    for gap in (g for g in check_continuity(rows, gap_days) if ends_in_window(g)):
        key = "%s->%s" % (gap.get("after"), gap.get("before"))
        if key in seen:
            continue
        seen.append(key)
        fresh_gaps.append(gap)
    if fresh_gaps:
        marks["gaps"] = seen[-MAX_REMEMBERED_GAPS:]
        worst = max(fresh_gaps, key=lambda g: g.get("gap_days") or 0)
        findings.append({
            "kind": "gap",
            "message": ("%s 最近 %d 天内新出现 %d 处时间缺口（阈值 %d 天），"
                        "最大一处 %.1f 天：%s 之后、%s 之前 —— "
                        "那段时间的帖子可能没抓到。"
                        % (platform, window_days, len(fresh_gaps), gap_days,
                           worst.get("gap_days") or 0, worst.get("after"),
                           worst.get("before"))),
        })

    for kind, items, label, hint in (
        ("incomplete", incomplete, "媒体不全",
         "这些帖子的图片没下全，下次抓取会自动重试；一直不降就要人看了"),
        ("undated", check_undated(rows), "没有可用日期",
         "它们无法参与连续性检查，是检查不到的盲区"),
    ):
        prev = marks.get(kind)
        prev = prev if isinstance(prev, int) else 0
        if len(items) > prev:
            marks[kind] = len(items)
            findings.append({
                "kind": kind,
                "message": ("%s %s的帖子从 %d 条增加到 %d 条 —— %s"
                            % (platform, label, prev, len(items), hint)),
            })
        elif len(items) != prev:
            marks[kind] = len(items)          # 变少了：静默更新，不打扰

    return findings

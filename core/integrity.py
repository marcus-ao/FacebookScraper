"""检查归档缺口、零新增、不完整媒体和疑似误丢合作帖。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.config import cfg, per_platform


def _parse_ts(value) -> datetime | None:
    """解析 created_at；缺失或非法时返回 None。"""
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


parse_ts = _parse_ts


def check_undated(rows: list[dict]) -> list[dict]:
    """单独报告无法参与连续性检查的日期缺失记录。"""
    return [r for r in rows if _parse_ts(r.get("created_at")) is None]


def check_continuity(rows: list[dict], gap_days: int) -> list[dict]:
    """返回相邻帖的超阈值缺口；after 为较早帖，before 为较晚帖。"""
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


def known_partners(rows: list[dict], account: str) -> set[str]:
    """从 owner 和 coauthors 汇总已知合作账号。"""
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
    """提示被丢弃节点中的已知合作方，供人工核查；不据此自动收录。"""
    if not partners:
        return []
    return [r for r in rejected
            if (r.get("owner") or "").strip().lower() in partners]


def params(platform: str | None = None) -> tuple[int, int]:
    """返回 (gap_flag_days, alert_after_quiet_days)，支持按平台取值。"""
    c = cfg()
    gap = per_platform(c.get("integrity", "gap_flag_days", None), platform or "", 5)
    quiet = per_platform(c.get("integrity", "alert_after_quiet_days", None),
                         platform or "", 4)
    return int(gap), int(quiet)


# 零新增告警的最短重报间隔。
ALERT_REPEAT_DAYS = 7

# 只报告近期结束的缺口。
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
    """返回新增或加重的告警并更新 entry['alerts']；缺口去重，零新增按 repeat_days 重报。"""
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

    # 先算全部相邻间隔，再按结束时间筛选，避免漏掉跨窗口缺口。
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

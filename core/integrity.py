"""完整性检查。对应实施计划的 D1。

爬取相对官方 API 最本质的劣势是**没有 ground truth**：
"滚到这里就没了"和"被限流截断了"在响应上长得一模一样。
本模块的目的不是修复什么，而是**让静默失败变成可见失败**——
它只负责把可疑之处找出来，处理交给人。

三项检查各自盯一种失败模式：
  check_continuity  时间序列里的洞      → 某段历史根本没被抓到
  check_quiet       长期零新增          → 增量路径已经被登录墙拦住了
  check_incomplete  媒体不全的帖子      → 源响应只给了封面，或图片没下全
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 仅为类型标注，避免运行时多一次 import
    from core.store import Archive


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


def check_quiet(state: dict, platform: str, alert_after: int) -> bool:
    """连续 alert_after 天零新增即返回 True。

    目标账号日均约 1 帖，长期零新增本身就是异常信号——
    最可能的解释不是"他们没发"，而是增量路径已经被登录墙挡住了。

    ⚠️ 阈值需要按真实发帖节奏重设：2026-08-30 实测该账号**连续一个多月
    没发新帖**，而 config 里的 4 天是按"日均约 1 帖"定的。不改会天天误报，
    误报多了真报警就没人看了。

    state 里没有该平台的记录时返回 False：那说明增量还没跑过，
    属于"没数据"而不是"安静"，该由调用方按 last_success 另行判断。
    """
    entry = state.get(platform)
    if not isinstance(entry, dict):
        return False
    quiet = entry.get("consecutive_quiet_days")
    if not isinstance(quiet, (int, float)) or isinstance(quiet, bool):
        return False
    return quiet >= alert_after


def check_incomplete(arc: "Archive") -> list[dict]:
    """媒体不全的帖子（源响应只给封面、或图片没下全的那些）。"""
    return arc.needs_media()


def params() -> tuple[int, int]:
    """从 config.toml 的 [integrity] 读阈值，返回 (gap_flag_days, alert_after_quiet_days)。

    检查函数本身收显式参数（好测），阈值来源集中在这里，代码里不写死数字。
    """
    from core.config import cfg
    c = cfg()
    return (int(c.get("integrity", "gap_flag_days", 5)),
            int(c.get("integrity", "alert_after_quiet_days", 4)))

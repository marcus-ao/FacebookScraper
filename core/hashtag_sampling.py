"""Google Trends CSV、Instagram 累计量级与德国同类账号的离线采样适配。"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from core.monitor_access import AccessController, AccessDenied
import calendar
import csv
import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import quote


TRENDS_EXPORT_HELP = "https://support.google.com/trends/answer/4365538?hl=en"
TRENDS_API_INFO = "https://developers.google.com/search/apis/trends"


class SamplingUnavailable(RuntimeError):
    """A public sample could not be verified; semantic choice remains usable."""


@dataclass(frozen=True)
class SamplingConfig:
    enabled: bool = True
    peer_accounts: tuple[str, ...] = ()
    refresh_days: int = 7
    max_candidate_tags: int = 20
    max_peer_accounts: int = 10

    @classmethod
    def load(cls, c=None) -> "SamplingConfig":
        if c is None:
            from core.config import cfg  # 延迟导入：允许纯解析调用注入隔离配置。
            c = cfg()

        def value(key, default):
            return c.get("hashtags", key, default)

        raw_peers = value("peer_accounts", [])
        if not isinstance(raw_peers, list):
            raise ValueError("hashtags.peer_accounts 必须是账号数组")
        peers = tuple(dict.fromkeys(str(item).strip().lower() for item in raw_peers))
        if any(not re.fullmatch(r"[a-z0-9_.]+", item) or ".." in item for item in peers):
            raise ValueError("hashtags.peer_accounts 含无效 Instagram 账号")
        refresh_days = int(value("refresh_days", 7))
        max_candidate_tags = int(value("max_candidate_tags", 20))
        max_peer_accounts = int(value("max_peer_accounts", 10))
        if not 1 <= refresh_days <= 31:
            raise ValueError("hashtags.refresh_days 必须在 1..31")
        if not 1 <= max_candidate_tags <= 30 or not 1 <= max_peer_accounts <= 20:
            raise ValueError("hashtags 单批数量超出安全范围")
        if len(peers) > max_peer_accounts:
            raise ValueError("hashtags.peer_accounts 超过单批账号上限")
        return cls(bool(value("enabled", True)), peers, refresh_days,
                   max_candidate_tags, max_peer_accounts)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("采样时刻必须带时区")
    return value.astimezone(timezone.utc)


def _number(value: str) -> float | None:
    text = value.strip().replace(",", "")
    if not text or text.lower() in {"ispartial", "true", "false"}:
        return None
    if text.startswith("<"):
        text = text[1:]
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _declared_range(value: str) -> tuple[datetime, datetime]:
    match = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s*", value)
    if not match:
        raise ValueError("Trends 时间范围须为 YYYY-MM-DD YYYY-MM-DD")
    start, end = (datetime.fromisoformat(part) for part in match.groups())
    if end < start:
        raise ValueError("Trends 时间范围起止无效")
    return start, end


def _trend_column(value: str) -> tuple[str, str | None]:
    text = value.strip()
    match = re.fullmatch(r"(.+?):\s*\(([^()]*)\)", text)
    if match:
        return match.group(1).strip().removeprefix("#").casefold(), match.group(2).strip()
    return text.removeprefix("#").casefold(), None


def _period_bounds(value: str, grain: str) -> tuple[date, date]:
    text = value.strip()
    try:
        if grain in {"day", "date"}:
            point = date.fromisoformat(text)
            return point, point
        if grain == "week":
            parts = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\s+-\s+(\d{4}-\d{2}-\d{2}))?", text)
            if not parts:
                raise ValueError
            start = date.fromisoformat(parts.group(1))
            end = date.fromisoformat(parts.group(2)) if parts.group(2) else start + timedelta(days=6)
            if end < start or (end - start).days > 7:
                raise ValueError
            return start, end
        if grain == "month" and re.fullmatch(r"\d{4}-\d{2}", text):
            year, month = (int(part) for part in text.split("-"))
            start = date(year, month, 1)
            return start, date(year, month, calendar.monthrange(year, month)[1])
    except ValueError as exc:
        raise ValueError("Trends CSV 时间索引无效") from exc
    raise ValueError("Trends CSV 采样粒度暂不支持，不能猜测时间窗口")


def _verified_export_context(context: Mapping | None, *, candidate_group: str,
                             names: tuple[str, ...], time_range: str) -> bool:
    if context is None:
        return False
    expected = {name.removeprefix("#").casefold() for name in names}
    actual = context.get("tags")
    if (context.get("verified") is not True or context.get("geo") != "DE"
            or context.get("candidate_group") != candidate_group
            or context.get("time_range") != time_range or not isinstance(actual, list)
            or {str(name).removeprefix("#").casefold() for name in actual} != expected
            or any(not isinstance(context.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', context[key])
                   for key in ('source_sha256', 'text_sha256', 'proof_sha256'))):
        raise ValueError("Trends 导出上下文与候选、地域或时间范围不一致")
    return True


def import_trends_csv(value: str, *, candidate_group: str, tags: Iterable[str],
                      geo: str, time_range: str, sampled_at: datetime,
                      export_context: Mapping | None = None) -> list[dict]:
    """导入网页同一张比较图的 CSV；每批只属于一个英文候选组。"""
    names = tuple(dict.fromkeys(str(tag) for tag in tags))
    if geo != "DE" or not candidate_group or len(names) < 2:
        raise ValueError("Trends 导入需要 DE、统一时间范围及至少两个同组候选")
    declared_start, declared_end = _declared_range(time_range)
    rows = list(csv.reader(io.StringIO(value.lstrip("\ufeff"))))
    header_index = next((i for i, row in enumerate(rows)
                         if row and row[0].strip().casefold() in
                         {"day", "week", "month", "date", "hour"}), None)
    if header_index is None:
        raise ValueError("找不到 Google Trends 时间序列表头")
    header = [cell.strip() for cell in rows[header_index]]
    trusted_context = _verified_export_context(
        export_context, candidate_group=candidate_group,
        names=names, time_range=time_range)
    columns = [_trend_column(cell) for cell in header[1:]]
    normalized_columns = [name for name, _ in columns]
    wanted = {tag: tag.removeprefix("#").casefold() for tag in names}
    missing = [tag for tag, key in wanted.items() if key not in normalized_columns]
    if missing:
        raise ValueError("Trends CSV 缺少同组候选列：%s" % "、".join(missing))
    if len(set(normalized_columns)) != len(normalized_columns):
        raise ValueError("Trends CSV 表头包含重复列")
    indexes = {tag: normalized_columns.index(key) + 1 for tag, key in wanted.items()}
    regions = [columns[index - 1][1] for index in indexes.values()]
    if any(region is not None and region.casefold() != "germany" for region in regions):
        raise ValueError("Trends CSV 候选列地域不是 Germany，不能标为 DE")
    if not trusted_context and any(region is None for region in regions):
        raise ValueError("Trends CSV 缺少可验证的 Germany 地域列或导出上下文")
    values: dict[str, list[float]] = {tag: [] for tag in names}
    periods: list[tuple[date, date]] = []
    grain = header[0].strip().casefold()
    for row in rows[header_index + 1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        period = _period_bounds(row[0], grain)
        samples = {tag: (_number(row[index]) if index < len(row) else None)
                   for tag, index in indexes.items()}
        if any(number is None for number in samples.values()):
            raise ValueError("Trends CSV 每个日期必须含同组候选的完整数值")
        periods.append(period)
        for tag in names:
            values[tag].append(samples[tag])
    if not periods or len(set(periods)) != len(periods) or periods != sorted(periods):
        raise ValueError("Trends CSV 时间索引必须完整、唯一且递增")
    if any(current[0] != previous[1] + timedelta(days=1)
           for previous, current in zip(periods, periods[1:])):
        raise ValueError("Trends CSV 时间采样存在缺口或重叠")
    requested_start, requested_end = declared_start.date(), declared_end.date()
    if not (periods[0][0] <= requested_start <= periods[0][1]
            and periods[-1][0] <= requested_end <= periods[-1][1]):
        raise ValueError("Trends CSV 数据日期与声明时间范围不一致")
    averages = {tag: sum(items) / len(items) for tag, items in values.items()}
    maximum = max(averages.values())
    scores = {tag: (0.0 if maximum == 0 else round(100 * value / maximum, 4))
              for tag, value in averages.items()}
    moment = _aware(sampled_at)
    text_sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()
    if export_context is not None and export_context.get("text_sha256") != text_sha256:
        raise ValueError("Trends CSV 原文哈希与导出上下文不一致")
    source_sha256 = (export_context["source_sha256"] if export_context is not None
                     else text_sha256)
    batch = hashlib.sha256((candidate_group + "\0" + geo + "\0" + time_range
                            + "\0" + value).encode("utf-8")).hexdigest()
    return [{
        "tag": tag, "trend_score": scores[tag], "geo": "DE",
        "comparison_group": candidate_group, "sample_batch": batch,
        "time_range": time_range, "sampled_at": moment.isoformat(),
        "stale_after": (moment + timedelta(days=7)).isoformat(),
        "source": "Google Trends public CSV export",
        "source_url": (export_context.get("source_url") if export_context else TRENDS_EXPORT_HELP),
        "source_sha256": source_sha256,
        "metric_scope": "DE_search_interest_normalized_within_candidate_group",
    } for tag in names]


def verified_instagram_count(payloads: Iterable[Mapping], tag: str) -> int | None:
    """Read counts only from objects naming the exact hashtag; unknown shapes return None."""
    normalized = tag.removeprefix("#").casefold()

    def hashtag_nodes(value):
        if isinstance(value, Mapping):
            candidate = value.get("hashtag")
            if isinstance(candidate, Mapping):
                yield candidate
            for child in value.values():
                yield from hashtag_nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from hashtag_nodes(child)

    for payload in payloads:
        for node in hashtag_nodes(payload):
            name = node.get("name")
            if not isinstance(name, str) or name.removeprefix("#").casefold() != normalized:
                continue
            edge = node.get("edge_hashtag_to_media")
            count = edge.get("count") if isinstance(edge, Mapping) else None
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                return count
    return None


async def close_browser_session(playwright, browser) -> None:
    """Close an attached CDP session and always stop its Playwright driver."""
    if browser is None:
        if playwright is not None:
            await playwright.stop()
        return
    try:
        await browser.close()
    finally:
        if playwright is not None:
            await playwright.stop()


async def _browser_observations(*, tags: tuple[str, ...], peers: tuple[str, ...], c,
                                dcfg) -> dict:
    """Use the isolated detect browser and the repository's capture/parser path."""
    from core.chrome import attach  # 延迟导入：CSV 离线导入不应加载 Playwright。
    from core.parse import extract, partition_by_owner  # 延迟导入：仅浏览器 peer 路径需要帖子解析器。
    from core.translated import extract_hashtags  # 延迟导入：仅 peer 帖子转换需要正文标签解析。
    from routes import delta  # 延迟导入：仅实际浏览器路径依赖增量状态和节奏。

    c.assert_chrome_profiles_isolated()
    pw = browser = None
    result = {"instagram": {}, "peer_posts": {}, "errors": []}
    def record_failure(reason: str, *, hard: bool = False) -> None:
        dcfg.access.outcome("instagram", success=False, reason=reason, hard=hard)

    try:
        try:
            pw, browser, context = await attach(
                port=c.detect_debug_port, profile=c.detect_profile_dir,
                start_script=r"scripts\start_chrome_detect.bat", login_hint="探测小号")
        except (Exception, SystemExit) as exc:
            reason = "标签采样附着 detect 浏览器失败（%s）：%s" % (
                type(exc).__name__, str(exc) or "无错误详情")
            record_failure(reason)
            result["errors"].append(reason)
            return result
        requests = [("tag", tag) for tag in tags] + [("peer", peer) for peer in peers]
        for index, (kind, value) in enumerate(requests):
            try:
                dcfg.access.check("instagram")
            except AccessDenied as exc:
                result["errors"].append(str(exc))
                break
            url = ("https://www.instagram.com/explore/tags/%s/"
                   % quote(value.removeprefix("#"), safe="")
                   if kind == "tag" else delta.profile_url("instagram", value))
            try:
                if index:
                    dcfg = replace(dcfg, scan_id=uuid4().hex)
                    dcfg.access.reserve_homepage("instagram", dcfg.scan_id, run_kind="sampling")
                collector, final_url = await asyncio.wait_for(
                    delta.scan_page(context, url, dcfg), timeout=dcfg.max_session_seconds)
                reason = delta.login_wall_reason(final_url, collector.blocked_status())
                if reason:
                    record_failure(reason, hard=True)
                    result["errors"].append(reason)
                    break
                if kind == "tag":
                    count = verified_instagram_count(collector.payloads, value)
                    if count is None:
                        raise SamplingUnavailable("公开响应结构未核验，未生成标签量级")
                    result["instagram"][value] = {"count": count, "url": url}
                else:
                    posts = extract(collector.payloads, "instagram", value, route="delta")
                    posts, _ = partition_by_owner(posts, value)
                    if not posts:
                        raise SamplingUnavailable("同类账号响应未解析出可归属帖子")
                    result["peer_posts"][value] = [{
                        "created_at": post.created_at,
                        "tags": extract_hashtags(post.text or ""),
                        "url": post.permalink or url,
                    } for post in posts]
                dcfg.access.outcome("instagram", success=True)
            except AccessDenied as exc:
                result["errors"].append(str(exc))
                break
            except (Exception, SystemExit) as exc:
                hard = isinstance(exc, delta.DeltaBlocked) and exc.hard
                reason = "%s:%s:%s:%s" % (
                    kind, value, type(exc).__name__, str(exc) or "无错误详情")
                record_failure(reason, hard=hard)
                result["errors"].append(reason)
                if hard:
                    break
            if index + 1 < len(requests):
                await delta._pause(dcfg.scroll_pause())
    finally:
        await close_browser_session(pw, browser)
    return result


def collect_browser_observations(*, tags: Iterable[str] = (), peers: Iterable[str] = (),
                                 c=None, settings: SamplingConfig | None = None) -> dict:
    """Blocking production adapter; callers run it in a dedicated worker thread."""
    if c is None:
        from core.config import cfg  # 延迟导入：离线调用可注入临时配置且不初始化全局路径。
        c = cfg()
    settings = settings or SamplingConfig.load(c)
    tag_names = tuple(dict.fromkeys(str(tag) for tag in tags))
    peer_names = tuple(dict.fromkeys(str(peer) for peer in peers))
    if len(tag_names) > settings.max_candidate_tags or len(peer_names) > settings.max_peer_accounts:
        raise ValueError("标签采样请求超过配置的单批上限")
    if not tag_names and not peer_names:
        return {"instagram": {}, "peer_posts": {}, "errors": []}
    from routes import delta  # 延迟导入：只有实际浏览器采样才争用增量锁和状态。
    access = AccessController(Path(c.state_dir))
    dcfg = replace(delta.DeltaConfig.load(c), max_scrolls=0, access=access,
                   scan_id=uuid4().hex, run_kind="sampling")
    with delta.DeltaRunLock(Path(c.state_dir) / "delta.lock"):
        try:
            access.check("instagram")
            access.reserve_homepage("instagram", dcfg.scan_id, run_kind="sampling")
        except AccessDenied as exc:
            raise SamplingUnavailable(str(exc)) from exc
        return asyncio.run(_browser_observations(tags=tag_names, peers=peer_names, c=c, dcfg=dcfg))



def sample_instagram_tags(tags: Iterable[str], *, sampled_at: datetime | None = None,
                          c=None, browser_runner=None) -> list[dict]:
    settings = SamplingConfig.load(c)
    if not settings.enabled:
        raise SamplingUnavailable("标签公开采样未启用")
    names = tuple(dict.fromkeys(str(tag) for tag in tags))
    runner = browser_runner or collect_browser_observations
    result = runner(tags=names, peers=(), c=c, settings=settings)
    sampled = instagram_global_counts(
        result.get("instagram") or {}, sampled_at=sampled_at or datetime.now(timezone.utc))
    if not sampled["rows"]:
        raise SamplingUnavailable("Instagram 标签公开量级不可用；仍可按语义手选")
    return sampled["rows"]


def sample_german_peers(accounts: Iterable[str], *, sampled_at: datetime | None = None,
                        c=None, browser_runner=None) -> dict:
    moment = sampled_at or datetime.now(timezone.utc)
    settings = SamplingConfig.load(c)
    peers = tuple(dict.fromkeys(str(account).strip() for account in accounts if str(account).strip()))
    if not peers:
        return {"status": "skipped", "count": 0, "rows": [],
                "reason": "同类德语账号名单为空"}
    if not settings.enabled:
        return {"status": "unavailable", "count": 0, "rows": [],
                "reason": "标签公开采样未启用"}
    runner = browser_runner or collect_browser_observations
    try:
        result = runner(tags=(), peers=peers, c=c, settings=settings)
        posts = result.get("peer_posts") or {}
        return collect_peer_usage(peers, fetcher=lambda account: posts[account], sampled_at=moment)
    except (Exception, SystemExit):
        return {"status": "unavailable", "count": 0, "rows": [],
                "reason": "同类账号采集失败；仍可按语义手选"}


def instagram_global_counts(observations: Mapping[str, Mapping], *,
                            sampled_at: datetime) -> dict:
    moment = _aware(sampled_at)
    rows = []
    try:
        for tag, observation in observations.items():
            count, url = observation["count"], observation["url"]
            if (not isinstance(tag, str) or not tag.startswith("#")
                    or not isinstance(count, int) or isinstance(count, bool) or count < 0
                    or not isinstance(url, str) or not url.startswith("https://www.instagram.com/")):
                raise ValueError("Instagram 标签量级观测格式无效")
            rows.append({
                "tag": tag, "media_count": count, "geo": "global",
                "sampled_at": moment.isoformat(),
                "stale_after": (moment + timedelta(days=7)).isoformat(),
                "source": "Instagram public hashtag page",
                "source_url": url, "metric_scope": "global_cumulative_posts",
            })
    except (KeyError, TypeError) as exc:
        raise ValueError("Instagram 标签量级观测格式无效") from exc
    return {"status": "sampled", "count": len(rows), "rows": rows}


def collect_peer_usage(accounts: Iterable[str], *, fetcher: Callable[[str], Iterable[Mapping]],
                       sampled_at: datetime, window_days: int = 14) -> dict:
    peers = tuple(dict.fromkeys(str(account).strip() for account in accounts if str(account).strip()))
    if not peers:
        return {"status": "skipped", "count": 0, "rows": [],
                "reason": "同类德语账号名单为空"}
    moment = _aware(sampled_at)
    start = moment - timedelta(days=window_days)
    counts: Counter[str] = Counter()
    try:
        for account in peers:
            for post in fetcher(account):
                when = datetime.fromisoformat(str(post["created_at"]).replace("Z", "+00:00"))
                if when.tzinfo is None or not start <= when.astimezone(timezone.utc) <= moment:
                    continue
                tags = post.get("tags") or []
                if not isinstance(tags, list):
                    raise ValueError("同类账号帖子 tags 不是列表")
                counts.update(set(tag for tag in tags
                                  if isinstance(tag, str) and tag.startswith("#")))
    except Exception:
        return {"status": "unavailable", "count": 0, "rows": [],
                "reason": "同类账号采集失败；仍可按语义手选"}
    rows = [{
        "tag": tag, "peer_uses_14d": count, "geo": "DE",
        "sampled_at": moment.isoformat(),
        "stale_after": (moment + timedelta(days=7)).isoformat(),
        "source": "configured German peer accounts weekly collection",
        "source_accounts": list(peers),
        "time_range": "%s/%s" % (start.isoformat(), moment.isoformat()),
        "metric_scope": "configured_DE_peer_accounts_recent_posts",
    } for tag, count in sorted(counts.items())]
    return {"status": "sampled", "count": len(rows), "rows": rows}

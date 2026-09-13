"""Google Trends CSV、Instagram 累计量级与德国同类账号的离线采样适配。"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
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
            from core.config import cfg
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


def _date(value: str) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Trends CSV 时间索引无效") from exc
    return parsed


def _declared_range(value: str) -> tuple[datetime, datetime]:
    match = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s*", value)
    if not match:
        raise ValueError("Trends 时间范围须为 YYYY-MM-DD YYYY-MM-DD")
    start, end = (datetime.fromisoformat(part) for part in match.groups())
    if end < start:
        raise ValueError("Trends 时间范围起止无效")
    return start, end


def import_trends_csv(value: str, *, candidate_group: str, tags: Iterable[str],
                      geo: str, time_range: str, sampled_at: datetime) -> list[dict]:
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
    missing = [tag for tag in names if tag not in header]
    if missing:
        raise ValueError("Trends CSV 缺少同组候选列：%s" % "、".join(missing))
    if len(set(header)) != len(header):
        raise ValueError("Trends CSV 表头包含重复列")
    indexes = {tag: header.index(tag) for tag in names}
    values: dict[str, list[float]] = {tag: [] for tag in names}
    dates: list[datetime] = []
    for row in rows[header_index + 1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        when = _date(row[0])
        samples = {tag: (_number(row[index]) if index < len(row) else None)
                   for tag, index in indexes.items()}
        if any(number is None for number in samples.values()):
            raise ValueError("Trends CSV 每个日期必须含同组候选的完整数值")
        dates.append(when)
        for tag in names:
            values[tag].append(samples[tag])
    if not dates or len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("Trends CSV 时间索引必须完整、唯一且递增")
    actual_start = dates[0].replace(tzinfo=None)
    actual_end = dates[-1].replace(tzinfo=None)
    if actual_start != declared_start or actual_end != declared_end:
        raise ValueError("Trends CSV 数据日期与声明时间范围不一致")
    averages = {tag: sum(items) / len(items) for tag, items in values.items()}
    maximum = max(averages.values())
    scores = {tag: (0.0 if maximum == 0 else round(100 * value / maximum, 4))
              for tag, value in averages.items()}
    moment = _aware(sampled_at)
    batch = hashlib.sha256((candidate_group + "\0" + geo + "\0" + time_range
                            + "\0" + value).encode("utf-8")).hexdigest()
    return [{
        "tag": tag, "trend_score": scores[tag], "geo": "DE",
        "comparison_group": candidate_group, "sample_batch": batch,
        "time_range": time_range, "sampled_at": moment.isoformat(),
        "stale_after": (moment + timedelta(days=7)).isoformat(),
        "source": "Google Trends CSV export", "source_url": TRENDS_EXPORT_HELP,
        "metric_scope": "DE_search_interest_normalized_within_candidate_group",
    } for tag in names]


def verified_instagram_count(payloads: Iterable[Mapping], tag: str) -> int | None:
    """Read only a count whose enclosing object names the exact hashtag.

    This deliberately does not inspect page text or guess selectors.  Unknown
    response shapes return ``None`` so a UI can degrade to semantic ordering.
    """
    normalized = tag.removeprefix("#").casefold()

    def nodes(value):
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from nodes(child)

    for payload in payloads:
        for node in nodes(payload):
            name = node.get("name")
            if not isinstance(name, str) or name.removeprefix("#").casefold() != normalized:
                continue
            candidates = []
            edge = node.get("edge_hashtag_to_media")
            if isinstance(edge, Mapping):
                candidates.append(edge.get("count"))
            candidates.extend((node.get("media_count"), node.get("count")))
            for count in candidates:
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    return count
    return None


async def _browser_observations(*, tags: tuple[str, ...], peers: tuple[str, ...], c,
                                settings: SamplingConfig) -> dict:
    """Use the isolated detect browser and the repository's capture/parser path."""
    from core.chrome import attach
    from core.parse import extract, partition_by_owner
    from core.translated import extract_hashtags
    from routes import delta

    c.assert_chrome_profiles_isolated()
    dcfg = replace(delta.DeltaConfig.load(c), max_scrolls=0)
    pw = browser = None
    result = {"instagram": {}, "peer_posts": {}, "errors": []}
    failures = 0
    try:
        pw, browser, context = await attach(
            port=c.detect_debug_port, profile=c.detect_profile_dir,
            start_script=r"scripts\start_chrome_detect.bat", login_hint="探测小号")
        requests = [("tag", tag) for tag in tags] + [("peer", peer) for peer in peers]
        for index, (kind, value) in enumerate(requests):
            if failures >= dcfg.failure_budget:
                result["errors"].append("连续失败达到抓取预算，已停止本批")
                break
            url = ("https://www.instagram.com/explore/tags/%s/"
                   % quote(value.removeprefix("#"), safe="")
                   if kind == "tag" else delta.profile_url("instagram", value))
            try:
                collector, final_url = await asyncio.wait_for(
                    delta.scan_page(context, url, dcfg), timeout=dcfg.max_session_seconds)
                reason = delta.login_wall_reason(final_url, collector.blocked_status())
                if reason:
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
                failures = 0
            except (Exception, SystemExit) as exc:
                failures += 1
                result["errors"].append("%s:%s:%s" % (kind, value, type(exc).__name__))
            if index + 1 < len(requests):
                await delta._pause(dcfg.scroll_pause())
    finally:
        if browser is not None:
            await browser.close()
        if pw is not None:
            await pw.stop()
    return result


def collect_browser_observations(*, tags: Iterable[str] = (), peers: Iterable[str] = (),
                                 c=None, settings: SamplingConfig | None = None) -> dict:
    """Blocking production adapter; callers run it in a dedicated worker thread."""
    if c is None:
        from core.config import cfg
        c = cfg()
    settings = settings or SamplingConfig.load(c)
    tag_names = tuple(dict.fromkeys(str(tag) for tag in tags))
    peer_names = tuple(dict.fromkeys(str(peer) for peer in peers))
    if len(tag_names) > settings.max_candidate_tags or len(peer_names) > settings.max_peer_accounts:
        raise ValueError("标签采样请求超过配置的单批上限")
    if not tag_names and not peer_names:
        return {"instagram": {}, "peer_posts": {}, "errors": []}
    from routes.delta import DeltaRunLock
    with DeltaRunLock(Path(c.state_dir) / "delta.lock"):
        return asyncio.run(_browser_observations(
            tags=tag_names, peers=peer_names, c=c, settings=settings))


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


def rows_from_json(value: str) -> list[dict]:
    data = json.loads(value)
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError("采样 JSON 必须是对象数组")
    return data

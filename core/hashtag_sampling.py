"""Google Trends CSV、Instagram 累计量级与德国同类账号的离线采样适配。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping


TRENDS_EXPORT_HELP = "https://support.google.com/trends/answer/4365538?hl=en"
TRENDS_API_INFO = "https://developers.google.com/search/apis/trends"


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


def import_trends_csv(value: str, *, candidate_group: str, tags: Iterable[str],
                      geo: str, time_range: str, sampled_at: datetime) -> list[dict]:
    """导入网页同一张比较图的 CSV；每批只属于一个英文候选组。"""
    names = tuple(dict.fromkeys(str(tag) for tag in tags))
    if geo != "DE" or not candidate_group or len(names) < 2 or not time_range.strip():
        raise ValueError("Trends 导入需要 DE、统一时间范围及至少两个同组候选")
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
    values: dict[str, list[float]] = {tag: [] for tag in names}
    for row in rows[header_index + 1:]:
        for tag in names:
            index = header.index(tag)
            number = _number(row[index]) if index < len(row) else None
            if number is not None:
                values[tag].append(number)
    if any(not values[tag] for tag in names):
        raise ValueError("Trends CSV 至少一个候选没有数值")
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

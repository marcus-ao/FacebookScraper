"""导入人工从公开页面导出的标签观测；本工具本身不登录、不抓取。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from core import hashtag_rank, hashtag_sampling
from core.config import cfg
from core.console import force_utf8


def _moment(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--sampled-at 必须带时区")
    return parsed


def _output(value: str | None) -> Path:
    return Path(value) if value else cfg().state_dir / "de_hashtags.jsonl"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="导入可追溯的公开标签采样；Google Trends 官方 API 仍需申请 alpha 访问。")
    sub = parser.add_subparsers(dest="command", required=True)
    trends = sub.add_parser("trends-csv", help="导入 Google Trends 网页导出的同组 CSV")
    trends.add_argument("--input", required=True)
    trends.add_argument("--group", required=True, help="英文原标签，例如 #Cats")
    trends.add_argument("--tag", action="append", required=True, help="同一次图表中的德语候选，可重复")
    trends.add_argument("--time-range", required=True)
    trends.add_argument("--geo", default="DE", choices=("DE",))
    trends.add_argument("--sampled-at")
    trends.add_argument("--output")
    instagram = sub.add_parser("instagram-json", help="导入公开标签页的全球累计量级 JSON")
    instagram.add_argument("--input", required=True,
                           help='对象：{"#Tag":{"count":123,"url":"https://www.instagram.com/..."}}')
    instagram.add_argument("--sampled-at")
    instagram.add_argument("--output")
    peers = sub.add_parser("peers-json", help="导入已配置德国同类账号的公开帖子 JSON")
    peers.add_argument("--input", required=True, help="账号名到帖子数组的 JSON 对象")
    peers.add_argument("--account", action="append", default=[])
    peers.add_argument("--sampled-at")
    peers.add_argument("--output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    force_utf8()
    args = _parse_args(argv)
    sampled_at = _moment(args.sampled_at)
    raw = Path(args.input).read_text(encoding="utf-8-sig")
    if args.command == "trends-csv":
        rows = hashtag_sampling.import_trends_csv(
            raw, candidate_group=args.group, tags=args.tag, geo=args.geo,
            time_range=args.time_range, sampled_at=sampled_at)
    elif args.command == "instagram-json":
        result = hashtag_sampling.instagram_global_counts(
            json.loads(raw), sampled_at=sampled_at)
        rows = result["rows"]
    else:
        posts = json.loads(raw)
        if not isinstance(posts, dict):
            raise ValueError("同类账号 JSON 顶层必须是对象")
        result = hashtag_sampling.collect_peer_usage(
            args.account, fetcher=lambda account: posts[account], sampled_at=sampled_at)
        if result["status"] == "unavailable":
            raise ValueError(result["reason"])
        rows = result["rows"]
    count = hashtag_rank.append_samples(_output(args.output), rows)
    print("已导入 %d 条标签观测。" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

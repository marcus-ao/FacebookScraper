"""导出或导入带来源证据的公开标签观测。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from core import maintenance
from core import hashtag_rank, hashtag_sampling, paid_model, trends_export
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
    def request_args(command):
        command.add_argument("--group", required=True, help="英文原标签，例如 #Cats")
        command.add_argument("--tag", action="append", required=True,
                             help="同一次比较中的 2..5 个候选，可重复")
        command.add_argument("--start", required=True, help="统一窗口开始日 YYYY-MM-DD")
        command.add_argument("--end", required=True, help="统一窗口结束日 YYYY-MM-DD")
        command.add_argument("--geo", default="DE", choices=("DE",))

    trends = sub.add_parser("trends-csv", help="导入 Google Trends 网页导出的同组 CSV")
    trends.add_argument("--input", required=True)
    trends.add_argument("--group", required=True, help="英文原标签，例如 #Cats")
    trends.add_argument("--tag", action="append", required=True, help="同一次图表中的德语候选，可重复")
    trends.add_argument("--time-range", required=True)
    trends.add_argument("--geo", default="DE", choices=("DE",))
    trends.add_argument("--context", help="可选：浏览器导出生成的 .json 元数据；否则列名必须明确 (Germany)")
    trends.add_argument("--sampled-at")
    trends.add_argument("--output")
    public = sub.add_parser("trends-public", help="经已录证的唯一可访问控件下载并导入公开 CSV")
    request_args(public)
    public.add_argument("--proof", required=True, help="被动可访问树录到的精确控件 proof JSON")
    public.add_argument("--sampled-at")
    public.add_argument("--output")
    proof = sub.add_parser("trends-proof", help="把人工被动录到的唯一控件观察固化为请求专属 proof")
    request_args(proof)
    proof.add_argument("--observation", required=True,
                       help="含 method/observed_url/control 的被动录证 JSON")
    proof.add_argument("--recorded-at")
    proof.add_argument("--output", help="默认 state/trends_export_proof.json")
    reset = sub.add_parser("trends-reset", help="人工确认 429/challenge 已解除后清除持久硬停")
    reset.add_argument("--reason", required=True, help="人工核对说明")
    reset.add_argument('--version', required=True, help='trends-status 返回的当前记录版本')
    sub.add_parser('trends-status', help='只读停止/导出状态及恢复所需版本')
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


@maintenance.guarded('sampling_cli')
def main(argv=None) -> int:
    force_utf8()
    args = _parse_args(argv)
    c = cfg()
    if args.command == 'trends-status':
        state = trends_export.load_state(c)
        print(json.dumps(dict(state, revision=trends_export.state_revision(state)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "trends-reset":
        state = trends_export.reset_block(c, reason=args.reason, expected_revision=args.version)
        print("已人工恢复 Trends 导出硬停：%s" % state["recovered_at"])
        return 0
    if args.command == "trends-proof":
        request = trends_export.ExportRequest.create(
            candidate_group=args.group, tags=args.tag, geo=args.geo,
            start=args.start, end=args.end)
        observation = json.loads(Path(args.observation).read_text(encoding="utf-8"))
        if not isinstance(observation, dict):
            raise ValueError("控件观察必须是 JSON 对象")
        proof = trends_export.create_control_proof(
            request, observation, recorded_at=_moment(args.recorded_at))
        target = Path(args.output) if args.output else c.state_dir / "trends_export_proof.json"
        paid_model.atomic_write_json(target, proof, indent=2, sort_keys=True)
        print("已固化请求专属控件 proof：%s" % target)
        return 0

    sampled_at = _moment(args.sampled_at)
    if args.command == "trends-public":
        request = trends_export.ExportRequest.create(
            candidate_group=args.group, tags=args.tag, geo=args.geo,
            start=args.start, end=args.end)
        proof = json.loads(Path(args.proof).read_text(encoding="utf-8"))
        artifact = trends_export.export_public_csv(
            request, proof=proof, c=c, now=sampled_at)
        raw = Path(artifact["raw_path"]).read_bytes().decode('utf-8-sig')
        rows = hashtag_sampling.import_trends_csv(
            raw, candidate_group=args.group, tags=args.tag, geo=args.geo,
            time_range=request.time_range, sampled_at=sampled_at,
            export_context=artifact["export_context"])
        print("原始 CSV：%s（sha256=%s）" % (artifact["raw_path"], artifact["sha256"]))
    elif args.command == "trends-csv":
        raw = Path(args.input).read_bytes().decode('utf-8-sig')
        context = None
        if args.context:
            metadata = json.loads(Path(args.context).read_text(encoding="utf-8"))
            context = metadata.get("export_context") if isinstance(metadata, dict) else None
        rows = hashtag_sampling.import_trends_csv(
            raw, candidate_group=args.group, tags=args.tag, geo=args.geo,
            time_range=args.time_range, sampled_at=sampled_at, export_context=context)
    elif args.command == "instagram-json":
        raw = Path(args.input).read_text(encoding="utf-8-sig")
        result = hashtag_sampling.instagram_global_counts(
            json.loads(raw), sampled_at=sampled_at)
        rows = result["rows"]
    else:
        raw = Path(args.input).read_text(encoding="utf-8-sig")
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

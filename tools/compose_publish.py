"""离线组装待发内容并打印文案、图片和告警；不访问浏览器或写文件。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import cfg                                # noqa: E402
from core import maintenance
from core.console import force_utf8                        # noqa: E402
from core.store import Archive                             # noqa: E402
from publish.compose import ComposeError, compose_post     # noqa: E402
from localize.text import account_dirs                         # noqa: E402


def _schedule_timezone() -> ZoneInfo:
    name = (cfg().get("publish", "timezone", "") or "").strip()
    if not name:
        raise SystemExit("[publish].timezone 为空；排期必须显式带时区，不用本机时区")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        # Windows 需要 tzdata 提供 IANA 时区。
        raise SystemExit(
            f"找不到时区 {name!r}。\n\n"
            "  Windows 不自带 IANA 时区数据库，需要纯数据包 tzdata：\n"
            "      uv pip install --python .venv\\Scripts\\python.exe tzdata\n"
            "  或者直接重跑 scripts\\setup.bat（requirements.txt 里已经列了它）。\n\n"
            "  ❌ 不要改成写死 UTC 偏移绕过去：美西 UI 时区每年切两次夏令时，"
            "写死偏移会在切换日把帖子发到错误的时刻，而且没人会立刻发现"
            "（见 docs/HANDOFF.md 第 6 节）。") from exc
    except ValueError as exc:
        raise SystemExit(f"[publish].timezone 不是有效时区名：{name!r}") from exc


def _parse_when(raw: str | None) -> datetime:
    """解析 --at；省略时用明天 10:00 作离线校验输入，不代表排期决定。"""
    zone = _schedule_timezone()
    if raw is None:
        base = datetime.now(zone) + timedelta(days=1)
        return base.replace(hour=10, minute=0, second=0, microsecond=0)
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(
            f"--at 不是 ISO 时间：{raw!r}（例：2026-09-05T10:00）") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=zone)


def _latest_post_ids(count: int) -> list[str]:
    """跨账号取最新 N 篇的 post_id，只读 manifest。"""
    entries: list[tuple[str, str, str]] = []
    for account_dir in account_dirs(cfg().archive_dir):
        arc = Archive(account_dir.parent, account_dir.name)
        for row in arc.rows():
            post_id = row.get("post_id")
            if isinstance(post_id, str) and post_id.strip():
                entries.append((str(row.get("created_at") or ""),
                                account_dir.name, post_id.strip()))
    entries.sort(reverse=True)
    return [post_id for _, _, post_id in entries[:count]]


def _print_post(post) -> None:
    print("-" * 72)
    for line in post.review_lines():
        print("  " + line)
    print("  图片：")
    for path, kind in zip(post.image_paths, post.image_sources):
        label = "德语图" if kind == "media_de" else "⚠ 原图（图内可能仍有英文）"
        print(f"    {path.name:<12} {label}")
    print("  德语正文：")
    for line in post.text_de.splitlines() or [""]:
        print("    " + line)


@maintenance.guarded('compose_cli')
def main(argv=None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description="离线组装待发德语帖并打印待确认清单（不碰浏览器、不写盘）")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--post-id", action="append", default=None,
                       help="要组装的 post_id，可重复传入")
    scope.add_argument("--latest", type=int, default=None,
                       help="跨账号取最新 N 篇")
    parser.add_argument("--at", default=None,
                        help="排期时刻（ISO，例 2026-09-05T10:00）；"
                             "不给则按 [publish].timezone 取明天 10:00 作占位")
    parser.add_argument("--account", default=None,
                        help="只在这一个账号归档目录里找，如 in_neakasa.tech")
    parser.add_argument("--strict", action="store_true",
                        help="按真实发布的严格模式组装：控件录制须可回查"
                             "（[publish].ui_constraints_verified = true）；无需人工测量 UI 边界")
    parser.add_argument("--json", action="store_true",
                        help="额外输出机器可读摘要")
    args = parser.parse_args(argv)
    if args.latest is not None and args.latest < 1:
        parser.error("--latest 必须是正整数")

    when = _parse_when(args.at)
    post_ids = args.post_id or _latest_post_ids(args.latest)
    if not post_ids:
        print("[!] 没有可组装的 post_id：归档为空，或 --latest 选不到东西。")
        return 1

    archive_root = cfg().archive_dir
    print("=== 待发德语帖离线组装（零浏览器、零网络、零写盘）===")
    print(f"  归档根目录：{archive_root}")
    print(f"  排期时刻：{when.isoformat()}"
          + ("" if args.at else "（占位值，--at 可显式指定）"))
    print(f"  严格模式：{'开' if args.strict else '关'}"
          + ("" if args.strict else "（G1 未完成时的离线预演）"))

    ok: list = []
    bad: list[tuple[str, str]] = []
    for post_id in post_ids:
        try:
            post = compose_post(
                post_id, when, archive_root=archive_root,
                account=args.account,
                require_verified_ui_constraints=args.strict,
                warning_sink=None)
        except ComposeError as exc:
            bad.append((post_id, str(exc)))
            print("-" * 72)
            print(f"  ✗ {post_id}\n    {exc}")
            continue
        ok.append(post)
        _print_post(post)

    print("-" * 72)
    print(f"可组装 {len(ok)} 篇 / 被硬闸拦下 {len(bad)} 篇。")
    if ok:
        print("⚠️ 这只是**离线组装**。真正的发布还需要 G1 探查完成、"
              "selectors.py 回填、以及你在 require_confirmation 那一步点头。")
    if args.json:
        print(json.dumps({
            "scheduled_at": when.isoformat(),
            "strict": args.strict,
            "composed": [{
                "post_id": post.post_id,
                "platform": post.platform,
                "images": [path.name for path in post.image_paths],
                "image_sources": list(post.image_sources),
                "source_author": post.source_author,
                "warnings": list(post.warnings),
            } for post in ok],
            "rejected": [{"post_id": pid, "reason": reason}
                         for pid, reason in bad],
        }, ensure_ascii=False, indent=2))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

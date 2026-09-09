r"""离线组装待发德语帖并打印待确认清单（G0b 的用户入口）。

**这个工具不碰浏览器，不发任何请求，不写任何文件。** 它只把
`publish/compose.py` 的全部离线硬闸跑一遍，把结果按人能读的形式打出来：
译文、用了哪几张图（德语图还是回退的原图）、合作帖原作者、以及所有告警。

为什么需要它（CR-58）：`compose_post()` 此前唯一的调用方是
`tests/tests_publish.py`，于是 `PUBLISH_PLAN.md` 第 5 节那条【验收】
——「对最新 3 篇真实帖组装成功；人为改过期/删图各自被正确拒绝」——
**没有任何命令可以让用户自己复跑**。项目工作协议要求需要用户操作的功能
同步进 `MANUAL_STEPS.md`，而没有命令就没有可交接的验收。

它同时就是 `[publish].require_confirmation = true` 那道人工闸要看的那张清单。

用法::

    scripts\run_publish.bat --post-id 122123185335379375 --at 2026-09-05T10:00
    scripts\run_publish.bat --latest 3

`--at` 不给时按 `[publish].timezone` 取「明天 10:00」，只是为了让排期校验有个
具体值；本工具**不会**因此排任何东西。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import cfg                                # noqa: E402
from core.console import force_utf8                        # noqa: E402
from core.store import Archive                             # noqa: E402
from publish.compose import ComposeError, compose_post     # noqa: E402
from translate import account_dirs                         # noqa: E402


def _schedule_timezone() -> ZoneInfo:
    name = (cfg().get("publish", "timezone", "") or "").strip()
    if not name:
        raise SystemExit("[publish].timezone 为空；排期必须显式带时区，不用本机时区")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        # ⚠️ Windows 不自带 IANA 时区数据库，`zoneinfo` 只读系统数据库（CR-60）。
        # 本机实测 TZPATH 为空，所以缺 tzdata 时这里必然失败。
        raise SystemExit(
            f"找不到时区 {name!r}。\n\n"
            "  Windows 不自带 IANA 时区数据库，需要纯数据包 tzdata：\n"
            "      uv pip install --python .venv\\Scripts\\python.exe tzdata\n"
            "  或者直接重跑 scripts\\setup.bat（requirements.txt 里已经列了它）。\n\n"
            "  ❌ 不要改成写死 UTC 偏移绕过去：Europe/Berlin 每年切两次夏令时，"
            "写死偏移会在切换日把帖子发到错误的时刻，而且没人会立刻发现"
            "（见 docs/HANDOFF.md 第 5 节）。") from exc
    except ValueError as exc:
        raise SystemExit(f"[publish].timezone 不是有效时区名：{name!r}") from exc


def _parse_when(raw: str | None) -> datetime:
    """解析 --at；不给时给一个明天 10:00 的占位值。

    ⚠️ 占位值只是为了让排期校验有个具体输入，**它不代表任何真实排期决定**。
    真正的排期窗口上下限要等 G1 实测（见 PUBLISH_PLAN 第 3.3 节）。
    """
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
                        help="按真实发布的严格模式组装：要求 G1 已完成并"
                             "人工复核（[publish].ui_constraints_verified = true）")
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
            # 显式传 archive_root：入口要说清自己在读哪份归档，
            # 而不是让它隐式落到某个模块级默认值上。
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

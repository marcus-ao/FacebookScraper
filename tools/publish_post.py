"""单帖发布入口：默认在编辑器准备草稿，--submit 才提交并回读；纯离线使用 run_publish.bat。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from publish import business_suite as bs                     # noqa: E402
from publish import journal                                  # noqa: E402
from publish import records                                  # noqa: E402
from publish import workflow                                 # noqa: E402
from publish.compose import (ComposeError,                   # noqa: E402
                             _validated_probe_dump, compose_post)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _parse_when(raw: str) -> datetime:
    name = (cfg().get("publish", "timezone", "") or "").strip()
    if not name:
        raise SystemExit("[publish].timezone 为空；排期必须显式带时区")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SystemExit(
            "[publish].timezone 不可用：%r（%s）。\n安装时区数据：uv pip install --python .venv\\Scripts\\python.exe tzdata\n固定 UTC 偏移无法处理夏令时。"
            % (name, exc)) from exc
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(
            "--at 不是 ISO 时间：%r（例：2026-09-08T10:00）" % raw) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=zone)


def print_checklist(post, when: datetime, ui_timezone: str) -> None:
    print("=" * 72)
    print("待发德语帖（这是 require_confirmation 那道人工闸要看的清单）")
    print("=" * 72)
    for line in post.review_lines():
        print("  " + line)
    print("  图片：")
    for path, kind in zip(post.image_paths, post.image_sources):
        label = "德语图" if kind == "media_de" else "⚠ 原图（图内可能仍有英文）"
        print("    %-14s %s" % (path.name, label))
    # 同时显示业务时刻与 UI 时刻，便于核对。
    print("  目标时刻：%s" % when.isoformat())
    try:
        shown = when.astimezone(ZoneInfo(ui_timezone))
    except (ZoneInfoNotFoundError, ValueError):
        print("  UI 时区：%s" % (ui_timezone or "(未实测)"))
    else:
        # 按 dump 里录到的渲染形状打（`12 : 30 AM`），方便逐字符对屏幕
        print("  UI 会显示：%s %d:%02d %s（%s，本机时区）"
              % (shown.strftime("%m/%d/%Y"), shown.hour % 12 or 12,
                 shown.minute, "AM" if shown.hour < 12 else "PM", ui_timezone))
    print("  德语正文：")
    for line in post.text_de.splitlines() or [""]:
        print("    " + line)
    print("-" * 72)


# 兼容已有测试/内部调用；新代码使用不带下划线的公开入口。
_print_checklist = print_checklist


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _pending_blocks_force(row: dict | None) -> bool:
    """提交可能已发生或自动 pre-submit 失败时，``--force`` 也不能绕过。"""
    return bool(row and row.get("status") in {
        journal.STATUS_SUBMIT_AMBIGUOUS,
        journal.STATUS_SUBMITTED_UNVERIFIED,
        journal.STATUS_FAILED_PRE_SUBMIT,
    })


def _resolve_ui_timezone(strict: bool) -> str:
    """读取 UI 时区；严格模式须与人工记录一致。"""
    name = (cfg().get("publish", "ui_timezone", "") or "").strip()
    if not name:
        raise SystemExit(bs.describe_gap("ui_timezone"))
    if not strict:
        return name
    data = _validated_probe_dump(())
    observed = str((data.get("observations") or {}).get("ui_timezone") or "")
    if name not in observed:
        raise SystemExit(
            "[publish].ui_timezone = %r 与 G1 dump 里那条人工观察对不上：%r。\n"
            "  这两处必须一致——UI 时区是这一步唯一的真相来源。"
            % (name, observed))
    return name


async def prepare(post, when: datetime, *, ui_timezone: str,
                  timeout: float, stamp: str, submit_enabled: bool = False,
                  source_refs: tuple[str, ...] = (),
                  force: bool = False) -> tuple[int, dict]:
    """兼容包装；真正的追加式状态机在 :mod:`publish.workflow`。"""
    outcome = await workflow.execute(
        post, when, ui_timezone=ui_timezone, timeout=timeout, stamp=stamp,
        submit_enabled=submit_enabled, source_refs=source_refs, force=force)
    return outcome.code, asdict(outcome.attempt)


def _manual_resolve(state_dir: Path, post_id: str, *, scheduled: bool) -> int:
    """以明确的人工证据闭合准备/模糊/未回读尝试。"""
    # 人工结转与提交共用发布锁，避免并发时错误放开未决记录。
    try:
        with journal.PublishOperationLock(Path(state_dir) / "publish.lock"):
            row = journal.last_resolvable(state_dir, post_id)
            if row is None:
                print("没有找到 %s 的可人工结转记录。" % post_id)
                return 3
            previous = journal.attempt_from_row(row)
            if scheduled and previous.status == journal.STATUS_FAILED_PRE_SUBMIT:
                print("failed_pre_submit 表示提交点击前失败，不能人工冒充为已排期；"
                      "请用 --mark-not-scheduled 闭合。")
                return 3
            status = (journal.STATUS_SCHEDULED if scheduled
                      else journal.STATUS_FAILED_PRE_SUBMIT)
            record = journal.transition(
                previous, status,
                recorded_at=datetime.now().astimezone().isoformat(),
                step="人工结转",
                manual_evidence=True,
                verification=("人工在 Business Suite 内容日历确认已排期"
                              if scheduled else
                              "人工确认远端未排期，可重新开始一次新尝试"),
                note=("人工证据：已排期；不能冒充自动成功信号/自动回读"
                      if scheduled else
                      "人工证据：未排期；关闭此前模糊/未回读状态"))
            path = journal.append(state_dir, record)
            records.project(record)
    except RuntimeError as exc:
        print("人工结转未开始：%s" % exc)
        return 3
    print("人工结转完成：%s / %s → %s（%s）"
          % (record.post_id, record.platform, path, record.status))
    return 0


def main(argv=None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description="默认填到提交前；显式 --submit 才单击提交并回读内容日历")
    parser.add_argument("--post-id", required=True, help="要发的 post_id")
    parser.add_argument("--at", default=None,
                        help="排期时刻（ISO，例 2026-09-08T10:00）。"
                             "**准备模式下必填**：真的要往 UI 里写时刻了，"
                             "不给占位值")
    parser.add_argument("--mark-scheduled", action="store_true",
                        help="你已经在浏览器里点了提交 —— 把上一次的"
                             "「已准备」结转成「已排期」，幂等从此生效。"
                             "不碰浏览器、不重新组装")
    parser.add_argument("--mark-not-scheduled", action="store_true",
                        help="人工确认模糊/未回读尝试没有排上；追加人工证据后允许新尝试")
    parser.add_argument("--submit", action="store_true",
                        help="显式开放 G6：只点一次提交，并且只有内容日历回读成功"
                             "才记 scheduled。自动隐含 --strict；缺任一 v2/G1"
                             "证据则不碰浏览器")
    parser.add_argument("--source-ref", action="append", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--apply-price-map", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--account", default=None,
                        help="只在这一个账号归档目录里找，如 in_neakasa.tech")
    parser.add_argument("--strict", action="store_true",
                        help="要求 G1 已完成并人工复核"
                             "（[publish].ui_constraints_verified = true）")
    parser.add_argument("--force", action="store_true",
                        help="允许越过 prepared 草稿提示继续；不能越过"
                             " submit_ambiguous、submitted_unverified 或"
                             "自动 failed_pre_submit，后三者必须先人工结转")
    parser.add_argument("--assume-yes", action="store_true",
                        help="跳过那句交互确认。**只跳过提问**，"
                             "所有硬闸一条都不跳")
    args = parser.parse_args(argv)

    c = cfg()
    if args.mark_scheduled and args.mark_not_scheduled:
        parser.error("--mark-scheduled 与 --mark-not-scheduled 只能选一个")
    if args.mark_scheduled or args.mark_not_scheduled:
        if args.submit:
            parser.error("人工结转不能与 --submit 同时使用")
        return _manual_resolve(
            c.state_dir, args.post_id, scheduled=args.mark_scheduled)
    if not args.at:
        parser.error("--at 是必填的（准备模式要往 UI 里写一个真实时刻）")
    when = _parse_when(args.at)
    strict = args.strict or args.submit
    try:
        ui_timezone = _resolve_ui_timezone(strict)
    except ComposeError as exc:
        print("✗ 严格 G1/UI 证据门未通过，浏览器没有被触碰：\n  %s" % exc)
        return 2
    try:
        bs.assert_ui_time_unambiguous(when, ui_timezone)
    except bs.PublishStepError as exc:
        print("✗ 排期时刻在 UI 时区不可无歧义表达，没有碰浏览器：\n  %s" % exc)
        return 2
    timeout = float(c.get("publish", "ui_timeout_seconds",
                          bs.DEFAULT_UI_TIMEOUT))

    try:
        post = compose_post(
            args.post_id, when, archive_root=c.archive_dir,
            account=args.account,
            price_map=(c.get("publish", "price_map", {})
                       if args.apply_price_map else None),
            require_verified_ui_constraints=strict,
            warning_sink=None)
    except ComposeError as exc:
        print("✗ 离线硬闸拦下了这一篇，没有碰浏览器：\n  %s" % exc)
        return 2

    state_dir = c.state_dir
    source_refs = journal.effective_source_refs(
        post.platform, post.post_id, tuple(args.source_ref or ()))
    done = journal.scheduled_record_for_refs(state_dir, source_refs)
    if done is not None:
        print("至少一个来源已经排过了（%s，%s），永久跳过；"
              "--force 也不能制造重复发布。"
              % (done.get("scheduled_at"), done.get("recorded_at")))
        return 0
    pending = journal.pending_record_for_refs(state_dir, source_refs)
    hard_block = _pending_blocks_force(pending)
    if pending is not None and (hard_block or not args.force):
        print("⚠️ 上一次跑到「%s」就停了（%s）。"
              % (pending.get("status"), pending.get("recorded_at")))
        print("  Business Suite 里**可能留着一份草稿**，再跑一次会再留一份。")
        if hard_block:
            print("  该状态不允许 --force 绕过；必须人工查清后用"
                  " --mark-scheduled 或 --mark-not-scheduled 结转。")
        else:
            print("  先去看一眼；确认没问题之后加 --force 继续。")
        return 3

    if args.submit:
        try:
            bs.require_submission_evidence()
            bs.require_readback_evidence()
        except bs.ProbeRequired as exc:
            print("✗ --submit 仍由证据门禁关闭，浏览器没有被触碰：\n%s" % exc)
            return 6

    print_checklist(post, when, ui_timezone)
    if not strict:
        print("⚠️ 非严格模式：IG 的画幅/图片数/正文长度/标签数上限**还没实测过**，"
              "composer 可能会自己拒。")
    if args.submit:
        print("⚠️ 已显式启用 --submit：提交只点一次；信号或回读不明确时绝不重试。")
    else:
        print("⚠️ 默认准备模式：填完就停，最后那一下仍由你在浏览器里点。")
    require = c.get("publish", "require_confirmation", True) is not False
    if require and not args.assume_yes:
        if not _confirm("确认要把上面这些填进 Business Suite 吗？(yes/no) "):
            print("已取消，浏览器一个字都没碰。")
            return 0

    stamp = _now_stamp()
    try:
        code, trace = asyncio.run(prepare(
            post, when, ui_timezone=ui_timezone, timeout=timeout, stamp=stamp,
            submit_enabled=args.submit,
            source_refs=source_refs, force=args.force))
    except (RuntimeError, bs.PublishStepError) as exc:
        print("✗ 发布互斥/锁内 journal 重查拦下了本次尝试；浏览器没有被触碰：\n  %s"
              % exc)
        return 3

    print("\n留痕已追加：%s（attempt_id=%s，status=%s）"
          % (journal.journal_path(state_dir), trace.get("attempt_id"),
             trace.get("status")))
    if code == 0 and not args.submit:
        print("\n" + "=" * 72)
        print("内容已经填好，**现在轮到你**：")
        print("  1. 回到那个 Chrome 窗口，逐项看一眼（图几张、正文、时刻、渠道）；")
        print("  2. 确认无误后由**你**点提交；")
        print("  3. 排上之后回来把幂等闭上：")
        print("       scripts\\run_publish_post.bat --post-id %s --mark-scheduled"
              % post.post_id)
        print("=" * 72)
    elif code == 0:
        print("✓ 自动提交成功，且内容日历回读为 scheduled。")
    elif trace.get("status") in {
            journal.STATUS_SUBMIT_AMBIGUOUS,
            journal.STATUS_SUBMITTED_UNVERIFIED}:
        print("⚠️ 禁止自动重试。人工检查后任选一个结转：")
        print("   scripts\\run_publish_post.bat --post-id %s --mark-scheduled"
              % post.post_id)
        print("   scripts\\run_publish_post.bat --post-id %s --mark-not-scheduled"
              % post.post_id)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

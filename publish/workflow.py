r"""G6/G6c 浏览器状态机；把每次状态转换耐久追加到 journal。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.chrome import attach
from core.config import cfg
from publish import business_suite as bs
from publish import journal, channels
from publish import planning


@dataclass(frozen=True)
class AttemptOutcome:
    code: int
    attempt: journal.PublishAttempt
    message: str


def _independent_refs(post, source_refs: tuple[str, ...],
                       target_channels: tuple[str, ...]) -> tuple[str, ...]:
    canonical = journal.source_ref(post.platform, post.post_id)
    refs = journal.effective_source_refs(post.platform, post.post_id, source_refs)
    if refs != (canonical,) or target_channels != (post.platform,):
        raise bs.PublishStepError('新发布尝试只能覆盖本篇来源及其对应渠道；旧跨平台覆盖记录只作历史保留。')
    return refs


def new_attempt(post, when: datetime, *, ui_timezone: str,
                source_refs: tuple[str, ...] = (),
                target_channels: tuple[str, ...] | None = None
                ) -> journal.PublishAttempt:
    """为一次浏览器尝试冻结所有内容指纹。"""
    target_channels = target_channels or (post.platform,)
    source_refs = _independent_refs(post, source_refs, target_channels)
    return journal.PublishAttempt(
        post_id=post.post_id,
        platform=post.platform,
        status=journal.STATUS_PREPARED,
        scheduled_at=when.isoformat(),
        recorded_at=datetime.now().astimezone().isoformat(),
        text_de_sha256=journal.text_sha256(post.text_de),
        images=tuple(path.name for path in post.image_paths),
        image_sources=tuple(post.image_sources),
        ui_timezone=ui_timezone,
        warnings=tuple(post.warnings),
        source_refs=journal.effective_source_refs(
            post.platform, post.post_id, source_refs),
        target_channels=target_channels,
        source_text_sha256=journal.text_sha256(post.source_text),
        original_text_sha256=journal.text_sha256(post.original_text_de),
        final_text_sha256=journal.text_sha256(post.text_de),
        image_sha256=tuple(journal.file_sha256(path) for path in post.image_paths),
        ui_scheduled_at=when.astimezone(ZoneInfo(ui_timezone)).isoformat(),
    )


async def _screenshot(page, attempt_id: str, phase: str, *,
                      state_dir: Path, timeout: float) -> str:
    folder = Path(state_dir) / "publish_attempts"
    folder.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in attempt_id if ch.isalnum() or ch in "-_")
    target = folder / ("%s_%s.png" % (safe_id, phase))
    try:
        await page.screenshot(
            path=str(target), full_page=False,
            mask=[page.locator(bs.SENSITIVE_INPUT_SELECTOR)],
            timeout=timeout * 1000.0)
    except Exception:                             # noqa: BLE001
        return ""
    return str(target)


async def check_live_slot(page, post, when: datetime, *, ui_timezone: str, timeout: float):
    """在单次提交意图落盘前再读远端；缓存从不参与最终裁决。"""
    inventory = await bs.read_remote_slot_inventory(page, ui_timezone=ui_timezone,
        business_timezone='Europe/Berlin', timeout=timeout, include_cards=True)
    decision = planning.evaluate_slot(when, post.platform, inventory,
        now=datetime.now().astimezone(), window=planning.configured_window(post.platform))
    if not decision.allowed:
        alternatives = '、'.join(value.isoformat() for value in decision.suggestions)
        raise bs.PublishStepError('提交前时刻复核未通过（%s）；未自动顺延。可选时刻：%s'
                                  % (decision.reason, alternatives or '当前可见范围内暂无可用时刻'))


async def _execute_unlocked(
        post, when: datetime, *, ui_timezone: str, timeout: float,
        stamp: str, submit_enabled: bool,
        source_refs: tuple[str, ...] = (),
        target_channels: tuple[str, ...] | None = None
        ) -> AttemptOutcome:
    """执行 G2–G6c；不登录、不清草稿、不取消排期、不关闭用户 Chrome。"""
    c = cfg()
    target_channels = target_channels or (post.platform,)
    c.assert_publish_chrome_isolated()
    channels.require_independent_channel_evidence(target_channels)
    bs.assert_ui_time_unambiguous(when, ui_timezone)
    if submit_enabled:
        # 必须在附着浏览器前证明三类证据都已回填。
        bs.require_submission_evidence()
        bs.require_readback_evidence()

    base = new_attempt(
        post, when, ui_timezone=ui_timezone, source_refs=source_refs,
        target_channels=target_channels)
    pw = page = planner_page = None
    pre_submit_baseline: bs.ScheduledBaseline | None = None
    account_spec = None
    step = "附着发布 Chrome"
    notes: list[str] = []
    prepared: journal.PublishAttempt | None = None
    current = base
    try:
        if submit_enabled:
            account_spec = bs.require_account_context_evidence()
        pw, _browser, context = await attach(
            port=c.publish_debug_port,
            profile=c.publish_profile_dir,
            start_script=r"scripts\start_chrome_publish.bat",
            login_hint="DE 发布账号")

        if submit_enabled:
            step = "G6 提交前 Planner 基线"
            print("[0/7] 提交前确认远端没有同槽/同文案/同素材卡片 …")
            planner_page = await context.new_page()
            pre_submit_baseline = await bs.snapshot_scheduled_matches(
                planner_page, when, post.text_de, ui_timezone=ui_timezone,
                target_channels=target_channels,
                expected_image_count=len(post.image_paths), timeout=timeout)
            if pre_submit_baseline.match_count:
                raise bs.PublishStepError(
                    "提交前已经存在 %d 张同条件排期卡片；为防旧卡冒充本次结果，"
                    "本次没有打开 composer、没有点击提交"
                    % pre_submit_baseline.match_count)

        step = "G1-1 打开 composer"
        print("[1/7] 新开标签页进 composer …")
        page = await bs.open_composer(context, timeout=timeout)

        step = "G2 登录态与目标主页"
        print("[2/7] 核对登录态与目标主页 …")
        # ⚠️ **这里故意不传 account_spec。** 见下面 [3/7] 之后那一段：
        # 已录证的那条 heading 要等 FB 预览渲染出内容才存在，空 composer 上没有。
        account = await bs.ensure_logged_in(
            page,
            page_name=str(c.get("publish", "facebook_page_name", "") or ""),
            instagram_account=str(c.get("publish", "instagram_account", "") or ""),
            account_spec=None,
            timeout=timeout)
        notes.extend(account.notes)
        for note in account.notes:
            print("    " + note)

        step = "G3 图片上传"
        print("[3/7] 交图（%d 张）…" % len(post.image_paths))
        upload_notes = await bs.upload_images(page, list(post.image_paths), timeout=timeout)
        notes.extend(upload_notes)
        for note in upload_notes:
            print("    " + note)

        if account_spec is not None:
            # ⚠️⚠️ **严格账号核对必须放在上传之后，这不是随手挪的。**
            #
            # 已录证的 `composer_account_context` 是 FB 预览里那条
            # `heading 'Neakasa Deutschland'`，而**预览面板要有内容才渲染它**：
            # dump 里它第一次出现在 evidence_order=31，紧跟在
            # `Add photo/video`（交互 #12，ord=29）之后。空 composer 上
            # 等 30 秒也等不到 —— 2026-09-01 第一次 G8 真机跑就是这样失败的。
            #
            # 挪到上传之后**没有放松这道闸**：
            #   - 上传素材到 composer **不会发布任何东西**；
            #   - 闸仍然在**点提交之前**，发错主页依旧发不出去；
            #   - 而且此刻读到的就是"即将提交的这一屏"，比空屏时更有说服力。
            # ⛔ 不要为了"更早拦住"把它挪回上传前 —— 那里没有证据可读。
            step = "G2b 严格核对目标主页（预览渲染后）"
            print("    核对目标主页（FB 预览渲染后才读得到）…")
            strict = await bs.ensure_logged_in(
                page,
                page_name=str(c.get("publish", "facebook_page_name", "") or ""),
                instagram_account=str(
                    c.get("publish", "instagram_account", "") or ""),
                account_spec=account_spec,
                timeout=timeout)
            account = strict
            for note in strict.notes:
                if note not in notes:
                    notes.append(note)
                    print("    " + note)

        step = "G4 文案填写"
        print("[4/7] 填正文并逐字符回读 …")
        await bs.fill_caption(page, post.text_de, timeout=timeout)

        step = "G5 定时设置"
        print("[5/7] 设排期并回读 …")
        ui_readback = await bs.set_schedule(
            page, when, ui_timezone=ui_timezone, timeout=timeout)
        prepared_shot = await _screenshot(
            page, base.attempt_id, "prepared", state_dir=c.state_dir,
            timeout=timeout)
        prepared = journal.transition(
            base, journal.STATUS_PREPARED,
            recorded_at=datetime.now().astimezone().isoformat(),
            ui_readback=ui_readback, step=step, screenshot=prepared_shot,
            note=("已填到提交前，等待人工点击提交" if not submit_enabled
                  else "离线硬闸和 G2–G5 回读通过，准备自动提交"),
            warnings=tuple(base.warnings) + tuple(notes))
        journal.append(c.state_dir, prepared)
        current = prepared

        if not submit_enabled:
            return AttemptOutcome(0, prepared, "已准备，停在提交前")

        step = "提交前实时复核同渠道间隔"
        await check_live_slot(planner_page or page, post, when,
                              ui_timezone=ui_timezone, timeout=timeout)
        step = "G6 单次提交"
        # 先把“即将允许一次点击”的意图耐久化为禁止自动重试态，再调用 click。
        # 否则机器恰好在 click 已送达、SubmitResult/下一行 journal 尚未落盘时
        # 崩溃，只剩 prepared；--force 会再次点击并造成重复发布。
        armed = journal.transition(
            prepared, journal.STATUS_SUBMIT_AMBIGUOUS,
            recorded_at=datetime.now().astimezone().isoformat(),
            step=step, note=("自动提交意图已耐久；从此刻起即使进程中断也必须"
                             "人工确认远端状态，禁止自动重试"))
        journal.append(c.state_dir, armed)
        current = armed
        print("[6/7] 点击一次提交并等待已录证的成功信号 …")
        result = await bs.submit(page, timeout=timeout)
        submitted_shot = await _screenshot(
            page, base.attempt_id, "submitted", state_dir=c.state_dir,
            timeout=timeout)
        if not result.confirmed:
            ambiguous = journal.transition(
                armed, journal.STATUS_SUBMIT_AMBIGUOUS,
                recorded_at=datetime.now().astimezone().isoformat(),
                step=step, screenshot=submitted_shot,
                success_signal=result.success_signal,
                remote_id=result.remote_id,
                note=result.error or "提交结果不明确；禁止自动重试")
            current = ambiguous
            journal.append(c.state_dir, ambiguous)
            return AttemptOutcome(
                4, ambiguous,
                "提交结果不明确；禁止自动重试，必须人工结转")

        unverified = journal.transition(
            armed, journal.STATUS_SUBMITTED_UNVERIFIED,
            recorded_at=datetime.now().astimezone().isoformat(),
            step=step, screenshot=submitted_shot,
            success_signal=result.success_signal,
            remote_id=result.remote_id,
            note="已看到提交成功信号，等待内容日历回读")
        current = unverified
        journal.append(c.state_dir, unverified)

        step = "G6c 内容日历回读"
        print("[7/7] 重新进入内容日历并回读排期卡片 …")
        readback_path = (Path(c.state_dir) / "publish_attempts" /
                         ("%s_scheduled.png" % base.attempt_id))
        readback = await bs.verify_scheduled(
            planner_page or page, when, post.text_de, ui_timezone=ui_timezone,
            target_channels=target_channels, timeout=timeout,
            expected_image_count=len(post.image_paths),
            pre_submit_baseline=pre_submit_baseline,
            expected_remote_id=result.remote_id,
            screenshot_path=readback_path)
        if not readback.found:
            unresolved = journal.transition(
                unverified, journal.STATUS_SUBMITTED_UNVERIFIED,
                recorded_at=datetime.now().astimezone().isoformat(),
                step=step, screenshot=readback.screenshot,
                success_signal=unverified.success_signal,
                readback_signal="",
                remote_id=readback.remote_id or unverified.remote_id,
                verification=readback.error,
                readback_diagnostics=readback.diagnostics,
                channels_verified=readback.channels,
                note=(readback.error
                      + "；禁止自动重试，必须人工确认远端是否已经排期"))
            journal.append(c.state_dir, unresolved)
            return AttemptOutcome(
                5, unresolved,
                "提交信号已出现，但内容日历未回读成功；禁止自动重试")

        scheduled = journal.transition(
            unverified, journal.STATUS_SCHEDULED,
            recorded_at=datetime.now().astimezone().isoformat(),
            step=step, screenshot=readback.screenshot,
            success_signal=unverified.success_signal,
            readback_signal=readback.success_signal,
            readback_diagnostics=readback.diagnostics,
            remote_id=(readback.remote_id or unverified.remote_id),
            remote_ids=tuple(
                part for part in str(readback.remote_id or "").split(";")
                if part),
            verification=("目标时刻、完整最终正文、%d 张图与目标渠道"
                          "均已从内容日历回读" % len(post.image_paths)),
            channels_verified=readback.channels,
            note="自动回读确认已排期")
        journal.append(c.state_dir, scheduled)
        return AttemptOutcome(0, scheduled, "自动提交并回读为 scheduled")
    except Exception as exc:                      # noqa: BLE001
        message = str(exc)
        screenshot = ""
        if page is not None:
            failure = await bs.capture_failure(
                page, step, message, state_dir=c.state_dir, stamp=stamp)
            screenshot = str(failure.screenshot or "")
            for line in failure.lines():
                print("  " + line)
        terminal_status = (current.status if current.status in {
            journal.STATUS_SUBMIT_AMBIGUOUS,
            journal.STATUS_SUBMITTED_UNVERIFIED,
        } else journal.STATUS_FAILED_PRE_SUBMIT)
        failed = journal.transition(
            current, terminal_status,
            recorded_at=datetime.now().astimezone().isoformat(),
            step=step, screenshot=screenshot, note=message,
            warnings=tuple(base.warnings) + tuple(notes))
        journal.append(c.state_dir, failed)
        return AttemptOutcome(1, failed, message)
    finally:
        # 只断开自动化侧；不关闭标签页，不关闭用户 Chrome。
        if pw is not None:
            try:
                await pw.stop()
            except Exception:                     # noqa: BLE001
                pass


async def execute(post, when: datetime, *, ui_timezone: str, timeout: float,
                  stamp: str, submit_enabled: bool,
                  source_refs: tuple[str, ...] = (),
                  target_channels: tuple[str, ...] | None = None,
                  force: bool = False) -> AttemptOutcome:
    """在全局发布锁内重查 journal，再执行一次浏览器尝试。"""
    c = cfg()
    target_channels = target_channels or (post.platform,)
    refs = _independent_refs(post, source_refs, target_channels)
    with journal.PublishOperationLock(
            Path(c.state_dir) / "publish.lock", allow_reentrant=True):
        done = journal.scheduled_record_for_refs(c.state_dir, refs)
        if done is not None:
            raise bs.PublishStepError(
                "至少一个 source_ref 已在锁内重查为 scheduled；"
                "--force 也不能重复发布，本次零浏览器操作")
        pending = journal.pending_record_for_refs(c.state_dir, refs)
        if pending is not None:
            status = str(pending.get("status") or "")
            if status != journal.STATUS_PREPARED or not force:
                raise bs.PublishStepError(
                    "锁内重查发现未闭合状态 %s；本次零浏览器操作" % status)
        return await _execute_unlocked(
            post, when, ui_timezone=ui_timezone, timeout=timeout, stamp=stamp,
            submit_enabled=submit_enabled, source_refs=refs,
            target_channels=target_channels)

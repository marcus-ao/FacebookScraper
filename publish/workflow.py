r"""浏览器发布状态机；每次转换追加到 journal。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import maintenance
from core.chrome import attach, close_owned_page
from core.config import cfg
from core.translated import source_text_sha256
from core import notify
from publish import business_suite as bs
from publish import journal, channels, snapshots, records, capabilities
from publish import planning
from publish import channel_evidence, month_inventory, month_readback, media


@dataclass(frozen=True)
class AttemptOutcome:
    code: int
    attempt: journal.PublishAttempt
    message: str
    suggestions: tuple = ()


def _independent_refs(post, source_refs: tuple[str, ...],
                       target_channels: tuple[str, ...]) -> tuple[str, ...]:
    canonical = journal.source_ref(post.platform, post.post_id)
    refs = journal.effective_source_refs(post.platform, post.post_id, source_refs)
    if refs != (canonical,) or target_channels != (post.platform,):
        raise bs.PublishStepError('新发布尝试只能覆盖本篇来源及其对应渠道；旧跨平台覆盖记录只作历史保留。')
    return refs


def new_attempt(post, when: datetime, *, ui_timezone: str,
                source_refs: tuple[str, ...] = (),
                target_channels: tuple[str, ...] | None = None,
                origin: str = "",
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
        source_text_sha256=source_text_sha256(post.source_text),
        original_text_sha256=journal.text_sha256(post.original_text_de),
        final_text_sha256=journal.text_sha256(post.text_de),
        image_sha256=tuple(journal.file_sha256(path) for path in post.image_paths),
        ui_scheduled_at=when.astimezone(ZoneInfo(ui_timezone)).isoformat(),
        snapshot_id=getattr(post, 'snapshot_id', ''),
        source_fingerprint=getattr(post, 'source_fingerprint', ''),
        source_fingerprint_version=getattr(post, 'source_fingerprint_version', 1),
        origin=origin,
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


async def check_live_slot(page, post, when: datetime, *, ui_timezone: str, timeout: float,
                           run=None, now=None):
    """在单次提交意图落盘前再读远端；缓存从不参与最终裁决。"""
    inventory = await month_inventory.read(page, ui_timezone=ui_timezone,
        business_timezone=bs.business_timezone(), timeout=timeout, run=run,
        detail_range=planning.slot_range(when))
    window = planning.config_window() if run is not None else planning.configured_window(post.platform)
    decision = planning.evaluate_slot(when, post.platform, inventory,
        now=now or datetime.now().astimezone(), window=window)
    if not decision.allowed:
        raise bs.PublishStepError(planning.slot_refusal(decision) + "；未自动顺延。",
                                  suggestions=decision.suggestions)


def print_progress(index: int, text: str) -> None:
    print("[%d/7] %s" % (index, text))


async def _execute_unlocked(
        post, when: datetime, *, ui_timezone: str, timeout: float,
        stamp: str, submit_enabled: bool,
        source_refs: tuple[str, ...] = (),
        target_channels: tuple[str, ...] | None = None,
        report=print_progress, run=None, opening_inventory=None,
        ) -> AttemptOutcome:
    """核验目标、填写内容并提交；保留人工会话和其他远端内容。"""
    c = cfg()
    target_channels = target_channels or (post.platform,)
    try:
        c.assert_publish_chrome_isolated()
    except SystemExit as exc:
        if run is None:
            raise
        raise bs.PublishStepError(str(exc)) from exc
    bs.assert_ui_time_unambiguous(when, ui_timezone)
    base = new_attempt(
        post, when, ui_timezone=ui_timezone, source_refs=source_refs,
        target_channels=target_channels, origin="review_desk" if run is not None else "")
    pw = page = planner_page = None
    pre_submit_baseline: bs.ScheduledBaseline | None = None
    step = "附着发布 Chrome"
    notes: list[str] = []
    prepared: journal.PublishAttempt | None = None
    current = base
    try:
        try:
            pw, _browser, context = await attach(
                port=c.publish_debug_port,
                profile=c.publish_profile_dir,
                start_script=r"scripts\start_chrome_publish.bat",
                login_hint="DE 发布账号")
        except SystemExit as exc:
            if run is None:
                raise
            raise bs.PublishStepError("无法连接发布浏览器：" + str(exc)) from exc

        if submit_enabled:
            step = "G6 提交前 Planner 基线"
            report(0, "提交前确认远端没有同槽/同文案/同素材卡片 …")
            if opening_inventory is not None:
                pre_submit_baseline = month_readback.baseline_from_inventory(
                    opening_inventory, when, post.text_de, target_channels)
            else:
                planner_page = await context.new_page()
                pre_submit_baseline = await month_readback.baseline(
                    planner_page, when, post.text_de, ui_timezone=ui_timezone,
                    target_channels=target_channels,
                    timeout=timeout, run=run)
            if pre_submit_baseline.match_count:
                raise bs.PublishStepError(
                    "提交前已经存在 %d 张同条件排期卡片；为防旧卡冒充本次结果，"
                    "本次没有打开 composer、没有点击提交"
                    % pre_submit_baseline.match_count)

        step = "G1-1 打开 composer"
        report(1, "新开标签页进 composer …")
        asset_context = (run.asset_context if run is not None
                         else channel_evidence.require(post.platform)['context_ids'])
        page = await bs.open_composer(context, asset_context=asset_context, timeout=timeout)

        step = "G2 登录态与目标主页"
        report(2, "核对登录态与目标主页 …")
        selection = await channels.select(page, target_channels, timeout=timeout, run=run)
        notes.append('已核对单渠道目标：' + selection['channel'] + ' / ' + selection['account'])

        step = "G3 图片上传"
        report(3, "交图（%d 张）…" % len(post.image_paths))
        upload_notes = await bs.upload_images(page, list(post.image_paths), timeout=timeout)
        media_check = await media.verify_upload(page, post.image_paths, timeout=timeout)
        notes.extend(upload_notes[:1])
        notes.append('已按冻结清单交图，编辑器附件数量一致，未见上传进行中提示。')
        for note in notes[-2:]:
            print("    " + note)

        step = "G4 文案填写"
        report(4, "填正文并逐字符回读 …")
        await bs.fill_caption(page, post.text_de, timeout=timeout)

        step = "G5 定时设置"
        report(5, "设排期并回读 …")
        ui_readback = await bs.set_schedule(
            page, when, ui_timezone=ui_timezone, timeout=timeout, target_channels=target_channels)
        await channels.verify_before_submit(page, target_channels, run=run)
        prepared_shot = await _screenshot(
            page, base.attempt_id, "prepared", state_dir=c.state_dir,
            timeout=timeout)
        prepared = journal.transition(
            base, journal.STATUS_PREPARED,
            recorded_at=datetime.now().astimezone().isoformat(),
            ui_readback=ui_readback, step=step, screenshot=prepared_shot,
            readback_diagnostics={'composer_media': media_check},
            note=("已填到提交前，等待人工点击提交" if not submit_enabled
                  else "离线硬闸和 G2–G5 回读通过，准备自动提交"),
            warnings=tuple(base.warnings) + tuple(notes))
        journal.append(c.state_dir, prepared)
        current = prepared

        if not submit_enabled:
            return AttemptOutcome(0, prepared, "已准备，停在提交前")

        step = "提交前实时复核同渠道间隔"
        if planner_page is None:
            planner_page = await context.new_page()
        await check_live_slot(planner_page, post, when,
                              ui_timezone=ui_timezone, timeout=timeout, run=run)
        step = '提交前最终表单复核'
        await bs.wait_submit_ready(page, timeout=timeout, button_spec=None if run is None else run.submit_button)
        media_check = await media.verify_upload(page, post.image_paths, timeout=timeout, previous=media_check)
        await bs.verify_form(page, post.text_de, when, ui_timezone=ui_timezone, timeout=timeout)
        await channels.verify_before_submit(page, target_channels, run=run)
        step = "G6 单次提交"
        # 点击前先耐久记录未决意图，避免点击后崩溃又被当作可安全重试。
        armed = journal.transition(
            prepared, journal.STATUS_SUBMIT_AMBIGUOUS,
            recorded_at=datetime.now().astimezone().isoformat(),
            readback_diagnostics={'composer_media': media_check},
            step=step, note=("自动提交意图已耐久；从此刻起即使进程中断也必须"
                             "人工确认远端状态，禁止自动重试"))
        journal.append(c.state_dir, armed)
        current = armed
        report(6, "点击一次提交并等待已录证的成功信号 …")
        result = await bs.submit(page, timeout=timeout,
                                 button_spec=None if run is None else run.submit_button,
                                 success_spec=None if run is None else run.success_signal)
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
        report(7, "重新进入内容日历并回读排期卡片 …")
        readback_path = (Path(c.state_dir) / "publish_attempts" /
                         ("%s_scheduled.png" % base.attempt_id))
        readback = await month_readback.verify(
            planner_page or page, when, post.text_de, ui_timezone=ui_timezone,
            target_channels=target_channels, timeout=timeout,
            expected_image_count=len(post.image_paths),
            frozen_attempt=asdict(unverified),
            pre_submit_baseline=pre_submit_baseline,
            expected_remote_id=result.remote_id,
            screenshot_path=readback_path, run=run)
        if not readback.found:
            unresolved = journal.transition(
                unverified, journal.STATUS_SUBMITTED_UNVERIFIED,
                recorded_at=datetime.now().astimezone().isoformat(),
                step=step, screenshot=readback.screenshot,
                success_signal=unverified.success_signal,
                readback_signal="",
                remote_id=readback.remote_id or unverified.remote_id,
                verification=readback.error,
                readback_diagnostics=dict(readback.diagnostics, composer_media=media_check),
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
            readback_diagnostics=dict(readback.diagnostics, composer_media=media_check),
            remote_id=(readback.remote_id or unverified.remote_id),
            remote_ids=tuple(
                part for part in str(readback.remote_id or "").split(";")
                if part),
            verification=month_readback.verification_text(readback),
            channels_verified=readback.channels,
            note="自动回读确认已排期")
        journal.append(c.state_dir, scheduled)
        current = scheduled
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
        current = failed
        return AttemptOutcome(1, failed, message, tuple(getattr(exc, "suggestions", ()) or ()))
    finally:
        # Own Planner tabs are disposable. Keep only a manual preparation or
        # uncertain composer for inspection; failed pre-click content is frozen on disk.
        if planner_page is not None:
            await close_owned_page(planner_page)
        if page is not None and submit_enabled and current.status in {
                journal.STATUS_FAILED_PRE_SUBMIT, journal.STATUS_SCHEDULED}:
            await close_owned_page(page)
        if pw is not None:
            try:
                await pw.stop()
            except Exception:                     # noqa: BLE001
                pass


@maintenance.guarded('publication')
async def execute(post, when: datetime, *, ui_timezone: str, timeout: float,
                  stamp: str, submit_enabled: bool,
                  source_refs: tuple[str, ...] = (),
                  target_channels: tuple[str, ...] | None = None,
                  force: bool = False, report=print_progress,
                  run=None, opening_inventory=None) -> AttemptOutcome:
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
        if run is not None:
            channels.require_independent_channel_evidence(target_channels, run=run)
        elif submit_enabled:
            capabilities.require(post.platform)
        else:
            channels.require_independent_channel_evidence(target_channels)
        post = snapshots.ensure(post)
        records.queue_approved(post.snapshot_id)
        outcome = await _execute_unlocked(
            post, when, ui_timezone=ui_timezone, timeout=timeout, stamp=stamp,
            submit_enabled=submit_enabled, source_refs=refs,
            target_channels=target_channels, report=report, run=run,
            opening_inventory=opening_inventory)
        try:
            await records.project_async(outcome.attempt)
        except Exception:
            notify.notify('发布回执留档待补齐', '发布账本保留结果；请核对快照后恢复本地状态，勿重复提交。', popup=False)
        return outcome

"""Project confirmed publication facts into review, snapshots, mirror and notifications."""
from __future__ import annotations

import asyncio
import json
import re
from contextvars import copy_context
from dataclasses import asdict
from datetime import datetime, timezone

from core import maintenance, notify, review, store, translated
from core.chrome import attach, close_owned_page
from core.config import cfg
from core.feishu import FeishuSettings, Outbox, WebhookBot
from core.mirror import MirrorSettings, MirrorService
from core.paid_model import atomic_write_json
from core.process_identity import worker_alive
from publish import business_suite as bs, journal, month_readback, operations, planner_cache, snapshots
from publish.manual_run import load as load_manual_run


def queue_mirror(source, stage, files, evidence, now):
    settings = MirrorSettings.load()
    if not settings.enabled:
        return False
    account = cfg().archive_dir / (source['platform'][:2] + '_' + source['account'])
    MirrorService(cfg().state_dir, settings).queue_stage(account, source, stage, files, evidence=evidence, now=now)
    return True


def queue_notification(account, source, attempt, now):
    settings = FeishuSettings.load()
    if not settings.enabled:
        return False
    success = attempt['status'] == journal.STATUS_SCHEDULED
    kind = 'scheduled' if success else 'schedule_failed'
    Outbox(cfg().state_dir / 'feishu_outbox.json', settings).enqueue(kind + ':' + attempt['attempt_id'], kind,
        {'task_id': account.name + '/' + source['post_id'], 'platform': source['platform'],
         'account': source.get('account'), 'published_at': source.get('created_at'),
         'scheduled_at': attempt.get('scheduled_at'), 'occurred_at': attempt.get('recorded_at'),
         'module_name': '发布排期',
         'text': '远端已确认排期，尚不代表已经公开。' if success else '本次排期尚未确认完成。',
         'next_step': '请核对回执。' if success else '请核对远端结果，避免重复提交。'}, now)
    return True


def queue_approved(snapshot_id, *, now=None):
    try:
        _, source, files, _ = snapshots.load(snapshot_id)
        return queue_mirror(source, 'approved', files, dict(source, status='approved', snapshot_id=snapshot_id),
                            now or datetime.now(timezone.utc))
    except Exception:
        notify.notify('批准版镜像待补送', '冻结快照已保留，请从发布恢复入口补送。', popup=False)
        return False


def deliver_notification(attempt, now):
    """Deliver only this confirmed receipt using the existing durable outbox."""
    settings = FeishuSettings.load()
    if not settings.enabled:
        return {'status': 'disabled'}
    outbox = Outbox(cfg().state_dir / 'feishu_outbox.json', settings)
    event_id = 'scheduled:' + attempt['attempt_id']
    status = outbox.event_status(event_id)
    if status['status'] in {'sent', 'uncertain', 'cancelled', 'missing'}:
        return status
    # Validate configuration before Outbox writes send intent. Missing credentials
    # are retryable setup failures, not evidence of a possibly delivered request.
    client = WebhookBot.from_environment()
    try:
        outbox.dispatch(now, client.send, allowed_kinds={'scheduled'}, event_ids={event_id})
    finally:
        client.close()
    return outbox.event_status(event_id)


def retry_notification_enqueues(now):
    """Repair explicit failed enqueue intents, not historical or disabled notices."""
    c = cfg()
    settings = FeishuSettings.load()
    if not settings.enabled:
        return
    with journal.PublishOperationLock(c.state_dir / 'publish.lock', allow_reentrant=True):
        data = Outbox(c.state_dir / 'feishu_outbox.json', settings)._load()
        known = set(data['events']) | set(data['archived_events'])
        latest = {row['attempt_id']: row for row in journal.load(c.state_dir)}
        for row in latest.values():
            if (row['status'] != journal.STATUS_SCHEDULED or not row.get('snapshot_id')
                    or 'scheduled:' + row['attempt_id'] in known):
                continue
            path = snapshots.folder(row['snapshot_id']) / 'projection.json'
            progress = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            done = progress.get(row['attempt_id'] + ':scheduled', {})
            if not (done.get('errors') or {}).get('notification'):
                continue
            metadata, source, _, _ = snapshots.load_for_attempt(row)
            queue_notification(c.archive_dir / metadata['account'], source, row, now)


async def project_async(attempt) -> dict:
    # The worker inherits the caller's reentrant publish lock. Cancellation must
    # wait for it to finish before the caller releases the actual OS lock.
    worker = asyncio.get_running_loop().run_in_executor(None, copy_context().run, project, attempt)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


def project(attempt, *, now=None) -> dict:
    """Retry only missing local projections; never visit or write the remote calendar."""
    row = asdict(attempt) if isinstance(attempt, journal.PublishAttempt) else dict(attempt)
    if not row.get('snapshot_id'):
        return {'status': 'legacy', 'message': '旧发布记录保留防重，缺少冻结素材，不补造历史版本'}
    moment = now or datetime.now(timezone.utc)
    with journal.PublishOperationLock(cfg().state_dir / 'publish.lock', allow_reentrant=True):
        latest = {item['attempt_id']: item for item in journal.load(cfg().state_dir)}.get(row['attempt_id'])
        if latest is None or latest['status'] != row['status']:
            raise review.ReviewConflict('发布账本尚未确认这个回执版本')
        # Project the durable row, never fields supplied by a stale caller.
        row = latest
        metadata, source, files, directory = snapshots.load_for_attempt(row)
        account = cfg().archive_dir / metadata['account']
        progress_path = directory / 'projection.json'
        progress = json.loads(progress_path.read_text(encoding='utf-8')) if progress_path.exists() else {}
        key = row['attempt_id'] + ':' + row['status']
        done = progress.setdefault(key, {})
        # Receipt updates are local facts. Side effects get their own durable completion bits.
        atomic_write_json(directory / 'receipt.json', row)
        if row['status'] in {journal.STATUS_SCHEDULED, journal.STATUS_FAILED_PRE_SUBMIT} and not done.get('review'):
            with review.transaction(account) as session:
                current, _ = store.read_post_truth(account, source)
                state = review.latest(account).get(source['post_id'])
                target = 'scheduled' if row['status'] == journal.STATUS_SCHEDULED else 'submit_failed'
                if target == 'scheduled' and (not state or state['status'] != 'scheduled'):
                    session.change(current, target, expected_revision=(state or {}).get('revision'),
                        expected_source_sha256=translated.source_text_sha256(current['text']), scheduled=True,
                        snapshot_id=row['snapshot_id'], now=moment)
                elif target == 'submit_failed' and state and state['status'] == 'approved':
                    session.change(current, target, expected_revision=state['revision'],
                        expected_source_sha256=translated.source_text_sha256(current['text']), now=moment)
            done['review'] = True
            atomic_write_json(progress_path, progress)
        errors = {}
        approved = dict(source, status='approved', snapshot_id=row['snapshot_id'])
        for stage, evidence in [('approved', approved), ('scheduled', row)]:
            if stage == 'scheduled' and row['status'] != journal.STATUS_SCHEDULED:
                continue
            if done.get(stage):
                continue
            try:
                done[stage] = queue_mirror(source, stage, files, evidence, moment) is not False
            except Exception as exc:
                errors[stage] = type(exc).__name__
        if row['status'] != journal.STATUS_PREPARED and not done.get('notification'):
            try:
                done['notification'] = queue_notification(account, source, row, moment) is not False
            except Exception as exc:
                errors['notification'] = type(exc).__name__
        if row['status'] == journal.STATUS_SCHEDULED and done.get('notification'):
            try:
                done['notification_delivery'] = deliver_notification(row, moment)
            except Exception as exc:
                done['notification_delivery'] = {'status': 'pending'}
                errors['notification_delivery'] = type(exc).__name__
        elif row['status'] == journal.STATUS_SCHEDULED:
            done['notification_delivery'] = {'status': 'pending' if 'notification' in errors else 'disabled'}
        done['errors'] = errors
        atomic_write_json(progress_path, progress)
        delivery_status = (done.get('notification_delivery') or {}).get('status')
        notice = {
            'pending': '排期已确认，飞书通知仍待发送；请查看运行状态中的机器人配置和发件箱。',
            'missing': '排期已确认，但未找到对应飞书通知事件；请保留发件箱并核对。',
            'uncertain': '排期已确认，飞书通知的送达结果不明确；请先到群里核对，再在运行状态中登记，系统不会自动重发。',
            'retry': '排期已确认，飞书明确拒绝了通知；已保留原消息等待退避重试，请查看运行状态中的原因。',
            'disabled': '排期已确认，飞书通知当前未启用。',
            'cancelled': '排期已确认，对应飞书通知已取消，请查看发件箱记录。',
        }.get(delivery_status, '')
        return {'status': row['status'], 'snapshot_id': row['snapshot_id'], 'projection': done,
                'notification_notice': notice}


async def unschedule(account, indexed, *, reason: str, inventory_reader=None, now=None) -> dict:
    """登记「我已在 Business Suite 手删了这条排期」。

    系统不去自动删远端卡片：删错不可逆，而且当前没有删除控件的录证。它只负责核实——
    实时读整月，确认这条的 remote ID 确实不在了，才解除防重。
    ⛔ `published.jsonl` 的原始行保留不删，只追加一条撤销转移。
    """
    moment = now or datetime.now(timezone.utc)
    if not isinstance(reason, str) or not reason.strip():
        raise review.ReviewValidationError('请说明这条排期是怎么处理的')
    with journal.PublishOperationLock(cfg().state_dir / 'publish.lock', allow_reentrant=True):
        source, _ = store.read_post_truth(account, indexed)
        ref = journal.source_ref(source['platform'], source['post_id'])
        row = journal.scheduled_record_for_refs(cfg().state_dir, (ref,))
        if row is None:
            raise review.ReviewConflict('这篇没有可撤销的排期记录')
        async def _live_month():
            return await planner_cache.read_live_inventory(run=load_manual_run(source['platform']))
        inventory = await (inventory_reader or _live_month)()
        # 撤销核实要整月详情。网格对齐不够：未读卡片里的 remote ID 不能被当成已经删除。
        if not isinstance(inventory, bs.RemoteSlotInventory) or not inventory.decision_complete:
            raise review.ReviewConflict('这次月历没有读完整，不能据此判定卡片已被删除')
        at = datetime.fromisoformat(row['scheduled_at'])
        if not inventory.covers((at,)):
            raise review.ReviewConflict('这次月历没有覆盖该排期时刻，不能据此判定卡片已被删除')
        wanted = {value for _, value in re.findall(r'(facebook|instagram)=(\d{6,})',
                                                   row.get('remote_id') or '')}
        if not wanted:
            raise review.ReviewConflict('这条排期没有远端 ID，无法核实是否已删除；请人工核对发布账本')
        present = {remote for card in inventory.cards for _, remote in card.remote_ids}
        if wanted & present:
            raise review.ReviewConflict('后台仍能读到这条排期卡片；请确认已在 Business Suite 删除后再登记')
        cancelled = journal.transition(journal.attempt_from_row(row), journal.STATUS_CANCELLED_REMOTE,
            recorded_at=moment.isoformat(), manual_evidence=True,
            note='人工已在 Business Suite 删除；实时整月读取未见该 remote ID。' + reason.strip())
        journal.append(cfg().state_dir, cancelled)
        review.transition(account, source, 'unscheduled',
            expected_revision=review.latest(account).get(source['post_id'], {}).get('revision'),
            expected_source_sha256=translated.source_text_sha256(source['text']),
            reason=reason.strip(), now=moment)
        return {'status': 'pending_review', 'attempt_id': row['attempt_id'],
                'remote_ids': sorted(wanted)}


async def _read_frozen_attempt(row, *, timeout, verify_images=False):
    """Attach for one existing object's readback; own only the temporary reader tab."""
    c = cfg()
    metadata, _, files, _ = snapshots.load_for_attempt(row)
    run = load_manual_run(row['platform'])
    if (row.get('target_channels') != [row['platform']]
            or metadata.get('publish_target') != run.target() or row['ui_timezone'] != run.ui_timezone):
        raise review.ReviewConflict('冻结时的发布目标或 UI 时区缺失或已变化，不能推断原账号绑定')
    when = snapshots.require_bound(metadata)
    pw = page = None
    cleanup_errors = {}
    try:
        pw, _browser, context = await attach(port=c.publish_debug_port, profile=c.publish_profile_dir,
            start_script=r'scripts\start_chrome_publish.bat', login_hint='DE 发布账号')
        page = await context.new_page()
        shot = c.state_dir / 'publish_attempts' / (row['attempt_id'] + '_recheck_'
            + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.png')
        # Revisit an existing submission. Never fabricate an empty pre-submit baseline.
        readback = await month_readback.verify(page, when, files['text_de.txt'].decode('utf-8'),
            ui_timezone=run.ui_timezone, target_channels=(row['platform'],), expected_remote_id=row['remote_id'],
            timeout=timeout, run=run, frozen_attempt=row, verify_images=verify_images, screenshot_path=shot)
    except (Exception, SystemExit) as exc:
        readback = bs.ScheduledReadback(False, datetime.now(timezone.utc).isoformat(),
            when.isoformat(), row['ui_scheduled_at'], row['final_text_sha256'],
            error='原排期读取失败：' + (str(exc) if isinstance(exc, SystemExit) else type(exc).__name__),
            diagnostics={'remote_images_verified': False, 'failure_stage': 'receipt_recheck_read'})
    finally:
        if page is not None:
            cleanup_error = await close_owned_page(page)
            if cleanup_error:
                cleanup_errors['page'] = cleanup_error
        if pw is not None:
            try:
                await pw.stop()
            except Exception as exc:
                cleanup_errors['connection'] = type(exc).__name__
    return readback, cleanup_errors


@maintenance.guarded('planner_read')
async def reconcile(account, indexed, *, timeout=30) -> dict:
    """Read an unresolved submission, then append its receipt; never submit again."""
    c = cfg()
    with journal.PublishOperationLock(c.state_dir / 'publish.lock', allow_reentrant=True):
        source, _ = store.read_post_truth(account, indexed)
        ref = journal.source_ref(source['platform'], source['post_id'])
        row = journal.pending_record_for_refs(c.state_dir, (ref,))
        if row and row['status'] in journal.NO_AUTO_RETRY_STATUSES:
            unresolved = {item['attempt_id']: item for item in journal.load(c.state_dir)
                          if ref in item.get('source_refs', ())}
            if sum(item['status'] in journal.NO_AUTO_RETRY_STATUSES for item in unresolved.values()) != 1:
                raise review.ReviewConflict('存在多条未决提交，不能将同一个远端对象绑定到不同尝试；请保留账本核对')
            readback, cleanup = await _read_frozen_attempt(row, timeout=timeout)
            diagnostics = dict(row.get('readback_diagnostics') or {})
            diagnostics.update(readback.diagnostics)
            diagnostics['receipt_recheck'] = {'observed_at': readback.observed_at,
                'target_found': readback.found, 'error': readback.error}
            if cleanup:
                diagnostics['browser_cleanup_errors'] = cleanup
            updates = dict(recorded_at=datetime.now(timezone.utc).isoformat(),
                step='已有提交只读回执核对', readback_diagnostics=diagnostics,
                screenshot=readback.screenshot or row.get('screenshot', ''))
            if readback.found:
                updates.update(remote_id=readback.remote_id, remote_ids=tuple(readback.remote_id.split(';')),
                    channels_verified=readback.channels, readback_signal=readback.success_signal,
                    verification=month_readback.verification_text(readback),
                    note='人工触发只读核对：唯一同账号、渠道、时刻和完整冻结正文的排期已确认；未重新提交')
            else:
                updates.update(verification=readback.error, note='只读核对未完成；保留未决状态和防重')
            updated = journal.transition(journal.attempt_from_row(row),
                journal.STATUS_SCHEDULED if readback.found else row['status'], **updates)
            journal.append(c.state_dir, updated)
            row = asdict(updated)
            if not readback.found:
                return {'status': row['status'], 'publication': row,
                        'message': '尚未补齐远端回执：' + readback.error + '；请勿重新提交。'}
        else:
            result = recover(account, indexed)
            row = journal.scheduled_record_for_refs(c.state_dir, (ref,))
            if row is None:
                return result
        result = {'status': row['status'], 'publication': row, 'message': '已核实已有排期并补齐回执。'}
        try:
            result['projection'] = project(row)
            if result['projection'].get('notification_notice'):
                result['projection_error'] = result['projection']['notification_notice']
        except Exception:
            result['projection_error'] = '远端排期已记录，本地显示待补齐；可再次核对，不要重新提交。'
        operation = operations.for_task(account.name + '/' + source['post_id'], row['snapshot_id'])
        previous = ((operation or {}).get('result') or {}).get('publication') or {}
        if operation and previous.get('attempt_id') == row['attempt_id']:
            operations.finish(operation['operation_id'], status=operations.SUCCEEDED,
                message=result['message'], result=dict(result, ok=True))
        return result


@maintenance.guarded('planner_read')
async def reverify_media(attempt_id: str, *, timeout=30) -> dict:
    """Revisit an exact scheduled attempt without uploading, submitting or deleting."""
    c = cfg()
    with journal.PublishOperationLock(c.state_dir / 'publish.lock', allow_reentrant=True):
        row = {item['attempt_id']: item for item in journal.load(c.state_dir)}.get(attempt_id)
        if row is None or row['status'] != journal.STATUS_SCHEDULED:
            raise review.ReviewConflict('只读图片复验仅接受当前仍为 scheduled 的原 attempt')
        channel = row['platform']
        if (row.get('target_channels') != [channel] or row.get('channels_verified') != [channel]
                or not row.get('readback_signal') or not row.get('ui_readback')
                or not re.fullmatch(re.escape(channel) + r'=\d{6,}', row.get('remote_id') or '')):
            raise review.ReviewConflict('原排期缺少确切的单渠道回读或 remote ID 绑定')
        readback, cleanup_errors = await _read_frozen_attempt(row, timeout=timeout, verify_images=True)
        diagnostics = dict(row.get('readback_diagnostics') or {})
        diagnostics.update(readback.diagnostics)
        diagnostics.pop('browser_cleanup_errors', None)
        if cleanup_errors:
            diagnostics['browser_cleanup_errors'] = cleanup_errors
        diagnostics['full_caption_equal'] = readback.found and readback.diagnostics.get('full_caption_equal') is True
        diagnostics['remote_images_verified'] = readback.found and readback.diagnostics.get('remote_images_verified') is True
        diagnostics['media_recheck'] = {'observed_at': readback.observed_at,
                                      'target_found': readback.found, 'error': readback.error}
        updated = journal.transition(journal.attempt_from_row(row), journal.STATUS_SCHEDULED,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            step='原排期只读图片复验', readback_diagnostics=diagnostics,
            screenshot=readback.screenshot or row.get('screenshot', ''),
            verification=month_readback.verification_text(readback),
            note='原 attempt 只读复验；没有上传、提交或远端修改')
        journal.append(c.state_dir, updated)
        result = {'status': updated.status, 'attempt_id': attempt_id,
                  'remote_images_verified': diagnostics['remote_images_verified'],
                  'target_found': readback.found, 'readback_diagnostics': diagnostics,
                  'verification': updated.verification}
        try:
            result['projection'] = project(updated)
        except Exception:
            result['projection_error'] = '发布账本已追加；本地投影待 recover 补齐，勿重新提交'
        return result


def recover(account, indexed) -> dict:
    with journal.PublishOperationLock(cfg().state_dir / 'publish.lock', allow_reentrant=True):
        source, _ = store.read_post_truth(account, indexed)
        ref = journal.source_ref(source['platform'], source['post_id'])
        pending = journal.pending_record_for_refs(cfg().state_dir, (ref,))
        if pending:
            raise review.ReviewConflict('远端结果尚未确认，请先核验发布账本，不能重新提交')
        done = journal.scheduled_record_for_refs(cfg().state_dir, (ref,))
        if done:
            return project(done)
        state = review.latest(account).get(source['post_id'])
        if not state or state['status'] != 'approved':
            return {'status': (state or {}).get('status', 'pending_review')}
        snapshot_id = state.get('snapshot_id')
        metadata, _, _, _ = snapshots.load(snapshot_id)
        attempts = [row for row in journal.load(cfg().state_dir) if row.get('snapshot_id') == snapshot_id]
        if attempts:
            return project(attempts[-1])
        if worker_alive(metadata.get('worker')) is not False:
            raise review.ReviewConflict('不能确认原批准进程已退出，请等待或核对后再恢复')
        review.transition(account, source, 'submit_failed', expected_revision=state['revision'],
            expected_source_sha256=translated.source_text_sha256(source['text']),
            reason='批准进程中断；发布账本没有提交尝试，恢复审校，冻结内容保留')
        return {'status': 'pending_review', 'snapshot_id': snapshot_id}

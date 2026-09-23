"""Project confirmed publication facts into review, snapshots, mirror and notifications."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

from core import notify, review, store, translated
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
from core.mirror import MirrorSettings, MirrorService
from core.paid_model import atomic_write_json
from core.process_identity import worker_alive
from publish import business_suite as bs, journal, planner_cache, snapshots


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
        metadata, source, files, directory = snapshots.load(row['snapshot_id'])
        if (metadata['fingerprint'] != row['final_text_sha256'] + ':' + ','.join(row['image_sha256'])
                or metadata['post_id'] != row['post_id'] or metadata['platform'] != row['platform']
                or metadata['account'] != source['platform'][:2] + '_' + source['account']
                or metadata['source_fingerprint'] != row.get('source_fingerprint')
                or snapshots.require_bound(metadata) != datetime.fromisoformat(row['scheduled_at'])):
            raise review.ReviewConflict('发布回执与批准快照不一致')
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
        done['errors'] = errors
        atomic_write_json(progress_path, progress)
        return {'status': row['status'], 'snapshot_id': row['snapshot_id'], 'projection': done}


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
        inventory = await (inventory_reader or planner_cache.read_live_inventory)()
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

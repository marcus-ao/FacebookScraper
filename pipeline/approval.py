"""审校台指定时刻的真实批准入口：同一把锁内复核、冻结、提交、记录回执。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core import notify, review, translated
from core.config import cfg
from core.feishu import FeishuSettings, Outbox
from core.mirror import MirrorService, MirrorSettings
from core.paid_model import atomic_write_json
from core.store import read_post_truth
from pipeline import engine
from publish import business_suite as bs, channels, compose, journal, planner_cache, planning, workflow


class ApprovalConflict(review.ReviewConflict):
    def __init__(self, message: str, *, suggestions=()):
        super().__init__(message)
        self.suggestions = tuple(suggestions)


def _moment(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise review.ReviewValidationError('发布时间需要包含时区')
    return value


def options(account_dir: Path, indexed: dict, *, now=None) -> dict:
    moment = now or datetime.now(timezone.utc)
    source, _ = read_post_truth(account_dir, indexed)
    result = {'available': False, 'reason': '', 'fingerprint': None,
              'platform': source['platform'], 'business_timezone': 'Europe/Berlin',
              'default_times': cfg().get('publish', 'schedule_rule', {}).get('times', ['10:00', '17:00'])}
    try:
        window = planning.configured_window(source['platform'])
        bounds = planning.calendar_bounds(moment, window=window)
        result.update(earliest=bounds.earliest.isoformat(), latest=bounds.latest.isoformat(),
                      ui_timezone=bounds.ui_timezone)
        post = compose.compose_post(source['post_id'], bounds.earliest,
            archive_root=account_dir.parent, account=account_dir.name,
            price_map=engine.publish_rules().price_map, now=moment,
            require_verified_ui_constraints=True, warning_sink=None)
        result['fingerprint'] = engine._publish_fingerprint(post)
        channels.require_independent_channel_evidence((source['platform'],))
        result['available'] = True
    except (ValueError, compose.ComposeError, bs.PublishStepError, bs.ProbeRequired, engine.PipelineRunError) as exc:
        result['reason'] = str(exc)
    return result


def _freeze(post, source, expected_fingerprint: str):
    directory = cfg().state_dir / 'publish_snapshots' / uuid4().hex
    directory.mkdir(parents=True)
    files = {'text_de.txt': post.text_de.encode('utf-8'),
             '元信息.json': json.dumps(source, ensure_ascii=False, indent=2).encode('utf-8')}
    images = []
    for index, path in enumerate(post.image_paths, 1):
        name = f'{index:02d}{path.suffix}'
        files[name] = path.read_bytes()
        images.append(directory / name)
    fingerprint = journal.text_sha256(post.text_de) + ':' + ','.join(
        hashlib.sha256(files[path.name]).hexdigest() for path in images)
    if fingerprint != expected_fingerprint:
        raise ApprovalConflict('图片或文案在批准前已有变化，请刷新后重新确认')
    for name, content in files.items():
        with (directory / name).open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    atomic_write_json(directory / 'snapshot.json', {'fingerprint': fingerprint,
        'source_text_sha256': journal.text_sha256(source['text']), 'status': 'frozen',
        'scheduled_at': post.scheduled_at.isoformat()})
    return replace(post, image_paths=tuple(images)), files, directory


def _side_effects(account_dir, source, stage, files, evidence, now):
    """网络派发留给调度器；通知/云盘失败不能改写发布结果。"""
    try:
        settings = MirrorSettings.load()
        if settings.enabled:
            mirror = MirrorService(cfg().state_dir, settings)
            mirror.queue_source(account_dir, source, now=now)
            mirror.queue_stage(account_dir, source, stage, files, evidence=evidence, now=now)
    except Exception:
        notify.notify('留档镜像待处理', '发布事实已保留，请检查本地快照和云盘队列。', popup=False)


def _notification(account_dir, source, attempt, now):
    try:
        settings = FeishuSettings.load()
        if settings.enabled:
            success = attempt['status'] == journal.STATUS_SCHEDULED
            kind = 'scheduled' if success else 'schedule_failed'
            Outbox(cfg().state_dir / 'feishu_outbox.json', settings).enqueue(
                f'{kind}:{attempt["attempt_id"]}', kind, {
                    'task_id': account_dir.name + '/' + source['post_id'], 'platform': source['platform'],
                    'text': ('排期已确认：' if success else '排期尚未确认：') + attempt.get('scheduled_at', ''),
                    'next_step': '请核对回执。' if success else '请先检查远端结果，避免重复提交。'}, now)
    except Exception:
        notify.notify('发布回执待推送', '请在审校台查看已保存的发布记录。', popup=False)


async def approve(account_dir: Path, indexed: dict, *, scheduled_at, source_text_sha256: str,
                  human_revision: str | None, review_revision: str | None, content_fingerprint: str,
                  now=None, inventory_reader=None, executor=None) -> dict:
    c = cfg()
    moment, target = now or datetime.now(timezone.utc), _moment(scheduled_at)
    if account_dir.name not in c.active_accounts():
        raise ApprovalConflict('此来源账号已冻结或未配置')
    if engine.activation_time(c.state_dir) is None:
        raise ApprovalConflict('流水线尚未激活，请先完成本机发布核验')
    c.state_dir.mkdir(parents=True, exist_ok=True)
    with journal.PublishOperationLock(c.state_dir / 'publish.lock', allow_reentrant=True):
        source, _ = read_post_truth(account_dir, indexed)
        ref = journal.source_ref(source['platform'], source['post_id'])
        if journal.scheduled_record_for_refs(c.state_dir, (ref,)) or journal.pending_record_for_refs(c.state_dir, (ref,)):
            raise ApprovalConflict('此帖已有排期或待核对的提交，请先查看发布回执')
        with review.transaction(account_dir) as session:
            source, state = session.validate(source, expected_revision=review_revision,
                                             expected_source_sha256=source_text_sha256)
            if state['status'] not in {'pending_review', 'edited'}:
                raise ApprovalConflict('请先恢复这篇的审校再批准发布')
        channels.require_independent_channel_evidence((source['platform'],))
        bs.require_submission_evidence()
        bs.require_readback_evidence()
        window = planning.configured_window(source['platform'])
        inventory = (await inventory_reader(now=moment) if inventory_reader is not None
                     else await planner_cache.read_live_inventory())
        decision = planning.evaluate_slot(target, source['platform'], inventory, now=moment, window=window)
        if not decision.allowed:
            raise ApprovalConflict('这个时刻暂不能排期（%s）；请重新选择，系统不会自动顺延。' % decision.reason,
                                   suggestions=decision.suggestions)
        with review.transaction(account_dir) as session:
            source, state = session.validate(source, expected_revision=review_revision,
                                             expected_source_sha256=source_text_sha256)
            if state['status'] not in {'pending_review', 'edited'}:
                raise ApprovalConflict('请先恢复这篇的审校再批准发布')
            human = translated.load_human_translated(account_dir / 'translated_human.jsonl').get(source['post_id'])
            if (human or {}).get('revision') != human_revision:
                raise ApprovalConflict('人工文案已有更新，请刷新后重新确认')
            candidate = engine.SourcePost(source['platform'], account_dir, source, moment, ref)
            issue = engine._prepaid_issue(engine.Candidate(candidate, (candidate,), 'independent'), engine.publish_rules())
            if issue:
                raise ApprovalConflict(issue.summary)
            post = compose.compose_post(source['post_id'], target, archive_root=account_dir.parent,
                account=account_dir.name, price_map=engine.publish_rules().price_map, now=moment,
                require_verified_ui_constraints=True, warning_sink=None)
            frozen, files, snapshot = _freeze(post, source, content_fingerprint)
            approved = session.change(source, 'approved', expected_revision=review_revision,
                                      expected_source_sha256=source_text_sha256, now=moment)
        _side_effects(account_dir, source, 'approved', files, approved, moment)
        try:
            outcome = await (executor or workflow.execute)(frozen, target, ui_timezone=window.ui_timezone,
                timeout=float(c.get('publish', 'ui_timeout_seconds', bs.DEFAULT_UI_TIMEOUT)),
                stamp=approved['revision'], submit_enabled=True, source_refs=(ref,), target_channels=(source['platform'],))
            attempt = asdict(outcome.attempt)
        except Exception:
            # 无回执时保留快照；下面只恢复审校状态，绝不声称远端已经排期。
            try:
                with review.transaction(account_dir) as session:
                    current, _ = read_post_truth(account_dir, source)
                    session.change(current, 'submit_failed', expected_revision=approved['revision'],
                        expected_source_sha256=translated.source_text_sha256(current['text']))
            except Exception:
                notify.notify('审校状态回填待处理', '提交未完成；请核对本地发布账本后恢复审校。', popup=False)
            raise
        success = attempt['status'] == journal.STATUS_SCHEDULED
        try:
            atomic_write_json(snapshot / 'receipt.json', attempt)
        except OSError:
            notify.notify('快照回执待补存', '发布账本已经记录结果；请检查本地磁盘，勿重复提交。', popup=False)
        try:
            with review.transaction(account_dir) as session:
                current, _ = read_post_truth(account_dir, source)
                session.change(current, 'scheduled' if success else 'submit_failed',
                    expected_revision=approved['revision'], expected_source_sha256=translated.source_text_sha256(current['text']),
                    scheduled=success)
        except Exception:
            notify.notify('审校状态回填待处理', '发布回执已留存；请根据发布记录核对，勿重复提交。', popup=False)
        if success:
            files['排期回执.json'] = json.dumps(attempt, ensure_ascii=False, indent=2).encode('utf-8')
            _side_effects(account_dir, source, 'scheduled', files, attempt, datetime.now(timezone.utc))
        _notification(account_dir, source, attempt, datetime.now(timezone.utc))
        return {'ok': success, 'status': 'scheduled' if success else 'pending_review',
                'publication': attempt, 'message': outcome.message}

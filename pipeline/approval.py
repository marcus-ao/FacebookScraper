"""审校台指定时刻的真实批准入口：同一把锁内复核、冻结、提交、记录回执。"""
from __future__ import annotations

from publish import capabilities
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from core import notify, paid_consent, review, translated
from core.config import cfg
from core.store import read_post_truth
from pipeline import engine
from publish import business_suite as bs, compose, journal, planner_cache, planning, workflow, records, snapshots


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


def composed_post(account_dir: Path, source: dict, *, now=None, strict: bool = False):
    """组装待发内容。`scheduled_at` 只参与窗口校验，不进正文与图片指纹。"""
    moment = now or datetime.now(timezone.utc)
    return compose.compose_post(source['post_id'], moment,
        archive_root=account_dir.parent, account=account_dir.name,
        price_map=engine.publish_rules().price_map, now=moment,
        require_verified_ui_constraints=strict, warning_sink=None)


def options(account_dir: Path, indexed: dict, *, now=None) -> dict:
    moment = now or datetime.now(timezone.utc)
    source, _ = read_post_truth(account_dir, indexed)
    result = {'available': False, 'reason': '', 'fingerprint': None,
              'lockable': False, 'lock_reason': '',
              'platform': source['platform'], 'business_timezone': bs.business_timezone(),
              'audience_timezone': planning.AUDIENCE_TIMEZONE,
              'audience_quiet_hours': [planning.AUDIENCE_QUIET_HOURS.start, planning.AUDIENCE_QUIET_HOURS.stop],
              'default_times': cfg().get('publish', 'schedule_rule', {}).get('times', ['10:00', '17:00'])}
    # 冻结只认内容本身：录证缺失让排期不可用，但不该挡住人确认文案和图片。
    try:
        result['fingerprint'] = engine._publish_fingerprint(
            composed_post(account_dir, source, now=moment))
        result['lockable'] = True
    except (ValueError, compose.ComposeError, engine.PipelineRunError) as exc:
        result['lock_reason'] = str(exc)
    try:
        window = planning.configured_window(source['platform'])
        bounds = planning.calendar_bounds(moment, window=window)
        result.update(earliest=bounds.earliest.isoformat(), latest=bounds.latest.isoformat(),
                      ui_timezone=bounds.ui_timezone)
        composed_post(account_dir, source, now=moment, strict=True)
        capabilities.require(source['platform'])
        result['available'] = True
    except (ValueError, compose.ComposeError, bs.PublishStepError, bs.ProbeRequired, engine.PipelineRunError) as exc:
        result['reason'] = str(exc)
    return result


def lock(account_dir: Path, indexed: dict, *, source_text_sha256: str,
         review_revision: str | None, content_fingerprint: str, now=None) -> dict:
    """「编辑确认无误」：冻住正文与图片字节，时刻留到排期时再绑。"""
    moment = now or datetime.now(timezone.utc)
    with review.transaction(account_dir) as session:
        source, _ = session.validate(indexed, expected_revision=review_revision,
                                     expected_source_sha256=source_text_sha256)
        post = composed_post(account_dir, source, now=moment)
        try:
            _, _, snapshot = snapshots.freeze(post, source, scheduled_at=None,
                                              expected_fingerprint=content_fingerprint)
        except review.ReviewConflict as exc:
            raise ApprovalConflict(str(exc)) from exc
        return session.change(source, 'content_locked', expected_revision=review_revision,
                              expected_source_sha256=source_text_sha256, now=moment,
                              snapshot_id=snapshot.name)


def unlock(account_dir: Path, indexed: dict, *, source_text_sha256: str,
           review_revision: str | None, now=None) -> dict:
    """解除冻结退回可编辑。⛔ 快照只标作废、不删字节——它是当时确认过什么的证据。"""
    with review.transaction(account_dir) as session:
        source, state = session.validate(indexed, expected_revision=review_revision,
                                         expected_source_sha256=source_text_sha256)
        if state['status'] != 'content_locked':
            raise ApprovalConflict('这篇当前不是已冻结状态，请刷新后重试')
        snapshots.discard(state['snapshot_id'])
        return session.change(source, 'unlocked', expected_revision=review_revision,
                              expected_source_sha256=source_text_sha256,
                              now=now or datetime.now(timezone.utc))


def _bind(post, snapshot_id, source, account_dir):
    """把已冻结内容接到本次提交上：先核内容，再绑时刻。"""
    account = cfg().archive_dir / (post.platform[:2] + '_' + post.account)
    candidate = replace(post, snapshot_id=snapshot_id,
                        source_fingerprint=paid_consent.fingerprint(source, account))
    try:
        return snapshots.ensure(candidate, bind=True)
    except review.ReviewConflict as exc:
        raise ApprovalConflict(str(exc)) from exc


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
            if state['status'] != 'content_locked':
                raise ApprovalConflict('请先确认内容无误并冻结，再选择发布时间')
            snapshot_id = state['snapshot_id']
        capabilities.require(source['platform'])
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
            if state['status'] != 'content_locked':
                raise ApprovalConflict('请先确认内容无误并冻结，再选择发布时间')
            snapshot_id = state['snapshot_id']
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
            if engine._publish_fingerprint(post) != content_fingerprint:
                raise ApprovalConflict('内容在确认之后已有变化，请重新核对并冻结')
            frozen = _bind(post, snapshot_id, source, account_dir)
            approved = session.change(source, 'approved', expected_revision=review_revision,
                                      expected_source_sha256=source_text_sha256, now=moment, snapshot_id=snapshot_id)
        records.queue_approved(snapshot_id, now=moment)
        try:
            outcome = await (executor or workflow.execute)(frozen, target, ui_timezone=window.ui_timezone,
                timeout=float(c.get('publish', 'ui_timeout_seconds', bs.DEFAULT_UI_TIMEOUT)),
                stamp=approved['revision'], submit_enabled=True, source_refs=(ref,), target_channels=(source['platform'],))
            attempt = asdict(outcome.attempt)
        except Exception:
            # 无回执时保留快照；下面只恢复审校状态，绝不声称远端已经排期。
            try:
                if journal.pending_record_for_refs(c.state_dir, (ref,)):
                    raise ApprovalConflict('发布结果不确定，保留批准状态等待核验')
                with review.transaction(account_dir) as session:
                    current, _ = read_post_truth(account_dir, source)
                    session.change(current, 'submit_failed', expected_revision=approved['revision'],
                        expected_source_sha256=translated.source_text_sha256(current['text']))
            except Exception:
                notify.notify('审校状态回填待处理', '提交未完成；请核对本地发布账本后恢复审校。', popup=False)
            raise
        success = attempt['status'] == journal.STATUS_SCHEDULED
        try:
            projection = records.project(attempt)
        except Exception as exc:
            projection = {'status': 'pending', 'error': type(exc).__name__}
            notify.notify('发布回执留档待补齐', '发布账本已保留结果，请在恢复入口补齐记录。', popup=False)
        return {'ok': success, 'status': 'scheduled' if success else 'pending_review',
                'publication': attempt, 'projection': projection, 'message': outcome.message}

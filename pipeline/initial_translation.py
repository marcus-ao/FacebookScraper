"""运营确认第三方单篇进入翻译；复用优化任务记录与现有模型执行器。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from localize import images as image_de
from localize import text as translation
from core import account_roles, paid_consent, paid_model, paid_requests, review
from core import maintenance
from core.config import cfg
from core.store import read_post_truth
from pipeline import engine, refinement, risk_scan
from publish import compose, journal


def _check(account_dir, indexed, *, allow_consent=False):
    if Path(account_dir).name not in cfg().active_accounts():
        raise review.ReviewConflict('此来源账号已冻结或未配置')
    source, _ = read_post_truth(account_dir, indexed)
    ref = journal.source_ref(source['platform'], source['post_id'])
    if journal.scheduled_record_for_refs(cfg().state_dir, (ref,)) or journal.pending_record_for_refs(cfg().state_dir, (ref,)):
        raise review.ReviewConflict('已有排期或未核对的发布记录，请先核对结果')
    if review.state_for(account_dir, source)['status'] not in {'pending_review', 'edited'}:
        raise review.ReviewConflict('请先恢复这篇的审校，再发起翻译')
    origin = engine.SourcePost(source['platform'], account_dir, source,
        datetime.fromisoformat(source['created_at'].replace('Z', '+00:00')), ref)
    candidate = engine.Candidate(origin, (origin,), 'independent')
    rules = engine.publish_rules()
    issue = engine.prepaid_issue(candidate, rules)
    if allow_consent and issue and issue.kind == 'unknown_collaborator' and issue.details.get('collaborators'):
        # 仅为判断其余硬闸模拟本篇许可；不写名单，也不把此临时规则传给执行器。
        trusted = dict(rules.trusted_owners)
        trusted[source['platform']] = trusted[source['platform']] | frozenset(issue.details['collaborators'])
        issue = engine.prepaid_issue(candidate, replace(rules, trusted_owners=trusted))
    if issue:
        raise review.ReviewConflict(issue.summary)
    return source, candidate


def capabilities(account_dir: Path, indexed: dict) -> dict:
    result = {'available': False, 'reason': '', 'source_fingerprint': None, 'needs_consent': False,
              'third_party': False, 'job': None}
    try:
        source, _ = read_post_truth(account_dir, indexed)
        rules = engine.publish_rules()
        owners = {str(value).strip().lower()
                  for value in [source.get('owner'), *(source.get('coauthors') or [])]
                  if value and str(value).strip()}
        result['third_party'] = bool(owners - account_roles.collaborator_trust(
            rules.trusted_owners, rules.brand_accounts, source['platform']))
        jobs = [row for row in refinement.latest().values() if row['kind'] == 'initial'
                and row['account'] == account_dir.name and row['post_id'] == source['post_id']]
        result['job'] = refinement.public_job(jobs[-1]) if jobs else None
        result['needs_consent'] = result['third_party'] and not paid_consent.is_current(account_dir, source)
        source, candidate = _check(account_dir, source, allow_consent=True)
        result['source_fingerprint'] = paid_consent.fingerprint(source, account_dir)
        if not result['third_party']:
            result['reason'] = '品牌账号来源沿用自动处理流程'
        elif any(row['status'] in {'pending', 'running'} for row in jobs):
            result['reason'] = '本篇已在处理，重启遗留任务需先核对付费账本'
        elif not engine.translation_needed(candidate.canonical) and not engine.pending_image_indices(candidate.canonical):
            result['reason'] = '本篇已经完成生成，请直接审校或使用单篇优化'
        else:
            result['available'] = True
    except (Exception, SystemExit) as exc:
        result['reason'] = str(exc)
    return result


@maintenance.guarded('initial_translation')
def submit(account_dir: Path, indexed: dict, *, source_fingerprint: str, source_text_sha256: str,
           review_revision: str | None, human_revision: str | None, executor=None) -> dict:
    account_dir = Path(account_dir)
    with paid_model.FileLock(cfg().state_dir / 'refinement.lock', busy_message='有内容请求正在受理，请稍后重试'):
        source, _ = _check(account_dir, indexed, allow_consent=True)
        if any(row['account'] == account_dir.name and row['post_id'] == source['post_id']
               and row['status'] in {'pending', 'running'} for row in refinement.latest().values()):
            raise review.ReviewConflict('这篇已有内容任务在处理，请等待结果')
        engine.budget_preflight()
        consent = paid_consent.grant(account_dir, source, source_fingerprint=source_fingerprint,
            source_text_sha256=source_text_sha256, review_revision=review_revision, human_revision=human_revision)
        _check(account_dir, source)
        row = {'job_id': uuid4().hex, 'kind': 'initial', 'account': account_dir.name, 'post_id': source['post_id'],
               'source_fingerprint': source_fingerprint, 'source_text_sha256': source_text_sha256,
               'source_fingerprint_version': paid_consent.FINGERPRINT_VERSION,
               'consent_revision': consent['revision'], 'status': 'pending', 'actor': None,
               'recorded_at': datetime.now(timezone.utc).isoformat()}
        row.update(worker=refinement.current_worker(), operation_tracked=True)
        refinement._append(row)
    try:
        maintenance.submit(executor or refinement._executor, 'initial_translation', execute, row, source)
    except Exception:
        refinement._append(dict(row, status='failed', error='executor_unavailable', message='处理线程未启动，请重新受理'))
        raise review.ReviewConflict('处理线程未启动，本次未调用模型')
    return row


def job_result(job_id: str) -> dict | None:
    row = refinement.job_result(job_id)
    return row if row and row.get('kind') == 'initial' else None


def execute(row: dict, indexed: dict, *, translator=None, editor=None,
            risk_scanner=None) -> dict:
    account_dir = cfg().archive_dir / row['account']
    with refinement._wait_lock('refinement.lock'):
        current = job_result(row['job_id'])
        if current is None or current['status'] != 'pending':
            return current or row
        event = dict(row, status='running', recorded_at=datetime.now(timezone.utc).isoformat())
        refinement._append(event)
    try:
        def preflight():
            source, _ = _check(account_dir, indexed)
            if paid_consent.fingerprint(source, account_dir, version=paid_consent.fingerprint_version(row)) != row['source_fingerprint']:
                raise review.ReviewConflict('原文、作者或原图已经改变，请重新确认')
            engine.budget_preflight()
        preflight()
        source, candidate = _check(account_dir, indexed)
        controller = paid_requests.RequestController(cfg().state_dir, preflight=preflight, operation_id=row['job_id'])
        if engine.translation_needed(candidate.canonical):
            scan = risk_scanner or risk_scan.scan_source
            scan_result = scan(
                state_dir=cfg().state_dir,
                task_id='%s/%s' % (account_dir.name, source['post_id']),
                source_ref=source['platform'] + ':' + source['post_id'],
                source_text=source['text'], controller=controller)
            event['risk_scan_status'] = scan_result.get('status', 'failed')
            settings = translation.Settings()
            translator = translator or translation.Translator(settings, paid_controller=controller)
            with translation.TranslationRunLock(cfg().state_dir / 'translation.lock'):
                preflight()
                _, failed = translation.run_translate(settings, translator, account_dir, 1, False, False,
                    scope=frozenset([source['post_id']]), source_rows=[source])
            if failed:
                raise ValueError('文案生成未通过产出检查')
        settings = image_de.Settings()
        for index in engine.pending_image_indices(candidate.canonical):
            preflight()
            editor = editor or image_de.ImageEditor(settings, paid_controller=controller)
            with image_de.ImageRunLock(cfg().state_dir / 'images.lock'):
                stats = image_de.run_localize(settings, editor, account_dir, [source], 1, False, False, index)
            if stats.failed:
                raise ValueError('图片生成未通过产出检查')
        preflight()
        now = datetime.now(timezone.utc)
        try:
            post = compose.compose_post(source['post_id'], now, archive_root=account_dir.parent,
                account=account_dir.name, price_map=engine.publish_rules().price_map, now=now, warning_sink=None)
            item = engine._ready_item(candidate, post)
        except compose.ComposeError as exc:
            item = engine.HumanItem('initial-review-' + row['job_id'], 'offline_gate', candidate.source_refs,
                str(exc), {'source_text_sha256': translation.source_text_sha256(source['text'])})
        engine.append_human_item(cfg().state_dir, item, now)
        event.update(status='succeeded', message='本轮处理结束，请在本篇核对素材并继续审校')
    except (Exception, SystemExit) as exc:
        event.update(status='failed', error=type(exc).__name__,
                     message='本篇处理未完成，已生成的内容保留；请核对提示和付费记录后再决定是否重试')
    event['recorded_at'] = datetime.now(timezone.utc).isoformat()
    refinement._append(event)
    return event

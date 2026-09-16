"""运营发起的单篇优化。复用付费执行器，生成结果不替换人工稿。"""
from __future__ import annotations

import re
import statistics
import time
from contextlib import contextmanager
from threading import RLock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from localize import images as image_de
from localize import text as translation
from core import paid_consent, paid_model, paid_requests, review, translated
from core.process_identity import current_worker, worker_alive
from core.config import cfg
from core.store import ArchivePathError, account_dirs, read_post_truth
from pipeline import engine
from publish import journal

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='content-refine')
_event_threads = RLock()


def _rows() -> list[dict]:
    if not (cfg().state_dir / 'refinements.jsonl').exists():
        return []
    with _event_threads, _events_lock():
        return paid_model.read_jsonl(cfg().state_dir / 'refinements.jsonl',
                                    on_corrupt=lambda *args: ValueError('优化记录损坏，请先核对'))


def _events_lock():
    return _wait_lock('refinement_events.lock')


@contextmanager
def _wait_lock(name):
    # Locks cover local transactions only; queued workers have not started paid requests.
    lock = paid_model.FileLock(cfg().state_dir / name, busy_message='内容任务正在领取')
    while True:
        try:
            lock.__enter__()
            break
        except paid_model.FileLockBusy:
            time.sleep(.02)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _append(row):
    with _event_threads, _events_lock():
        paid_model.append_jsonl(cfg().state_dir / 'refinements.jsonl', row)


def latest() -> dict[str, dict]:
    return {row['job_id']: row for row in _rows()}


def job_result(job_id: str) -> dict | None:
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise review.ReviewValidationError('优化任务编号无效')
    row = latest().get(job_id)
    return public_job(row) if row else None


def public_job(row: dict) -> dict:
    result = dict(row)
    events = [event for event in paid_requests.load_events(cfg().state_dir)
              if event.get('operation_id') == row['job_id']]
    result['paid_request_ids'] = list(dict.fromkeys(event['request_id'] for event in events))
    result['cost_usd'] = sum(float(event.get('cost_usd') or 0) for event in events
                             if event['event'] == paid_requests.EVENT_USAGE)
    if row['status'] in {'pending', 'running'}:
        alive = worker_alive(row.get('worker'))
        result['worker_state'] = 'alive' if alive else ('exited' if alive is False else 'unknown')
        if alive is False:
            result.update(status='interrupted', message='处理进程已退出，请先核对费用和已保存的产物')
    return result


def recover(job_id: str, *, expected_updated_at: str) -> dict:
    """Only reconcile durable results. This endpoint never sends a model request."""
    job_result(job_id)  # validate identifier before taking the mutation lock
    with paid_model.FileLock(cfg().state_dir / 'refinement.lock', busy_message='内容任务正在更新'):
        row = latest().get(job_id)
        if row is None:
            raise review.ReviewValidationError('未找到内容任务')
        if row['recorded_at'] != expected_updated_at:
            raise review.ReviewConflict('任务状态已变化，请刷新后核对')
        if row['status'] not in {'pending', 'running'}:
            return public_job(row)
        if worker_alive(row.get('worker')) is not False:
            raise review.ReviewConflict('尚不能确认原处理进程已退出，不能关闭任务')
        if not row.get('operation_tracked'):
            raise review.ReviewConflict('旧任务缺少付费请求关联，请先人工核对旧账本和产物')
        events = [event for event in paid_requests.load_events(cfg().state_dir)
                  if event.get('operation_id') == job_id]
        requests = {event['request_id']: event for event in events}
        if any(event['event'] in paid_requests._GLOBAL_BLOCKING for event in requests.values()):
            raise review.ReviewConflict('存在结果或费用不确定的付费请求，请先核账，不能重新调用模型')
        usage_ids = {event['request_id'] for event in events if event['event'] == paid_requests.EVENT_USAGE}
        if any(event['event'] == paid_requests.EVENT_ACCEPTED and event['request_id'] not in usage_ids for event in requests.values()):
            raise review.ReviewConflict('付费请求缺少用量凭据，请先核对实际费用')
        event = dict(row, status='failed', error='interrupted_after_request' if requests else 'interrupted_before_request',
                     message='中断任务已关闭；已保存的内容和费用保留，可另行受理未完成的工作',
                     recorded_at=datetime.now(timezone.utc).isoformat())
        directory = cfg().archive_dir / row['account']
        if row['kind'] == 'text':
            result = translated.load_translated(directory / 'translated.jsonl').get(row['post_id'])
            if result and result.get('refine_id') == job_id and result.get('text_de'):
                event.update(status='succeeded', error=None, text_de=result['text_de'], message='已找回本次保存的文案')
        elif row['kind'] == 'image':
            result = image_de.load_image_state(directory / 'images_de.jsonl').latest.get(
                (row['post_id'], row['media_index']))
            if result and result.get('refine_id') == job_id:
                path = (directory / result.get('out_path', '')).resolve()
                if path.is_relative_to(directory.resolve()) and path.is_file():
                    if journal.file_sha256(path) == result.get('output_sha256'):
                        event.update(status='succeeded', error=None, out_path=result['out_path'], message='已找回本次保存的图片')
        _append(event)
        return public_job(event)


def capabilities(account_dir: Path, post_id: str, indexed: dict | None = None) -> dict:
    """费用为当前有效样本中位数；无样本时明确使用业务测量参考，绝非预算上界。"""
    settings = image_de.Settings()
    costs = []
    for directory in account_dirs(cfg().archive_dir):
        for row in image_de.load_image_state(directory / 'images_de.jsonl').latest.values():
            usage = row.get('usage')
            if isinstance(usage, dict):
                cost = image_de.image_usage_cost(settings, usage)
                if cost is not None:
                    costs.append(cost)
    jobs = [row for row in latest().values()
            if row['account'] == Path(account_dir).name and row['post_id'] == post_id and row['kind'] in {'text', 'image'}]
    counts = {}
    for row in jobs:
        if row['kind'] == 'image':
            key = str(row['media_index'])
            counts[key] = counts.get(key, 0) + 1
    return {'max_refine_per_media': settings.max_refine_per_media, 'image_attempts': counts,
            'estimated_image_usd': round(statistics.median(costs), 4) if costs else 0.211,
            'estimate_basis': '本地 usage 样本中位数' if costs else '业务测量参考，暂无本地 usage 样本',
            'estimate_samples': len(costs), 'jobs': [public_job(row) for row in jobs],
            'image_versions': image_versions(account_dir, indexed) if indexed else {}}


def _current_basis(account_dir: Path, source: dict) -> str:
    """当前有效德语正文的哈希；历史版本要按它判断还能不能采用。"""
    entry = translated.image_translation(
        source,
        translated.load_translated(account_dir / 'translated.jsonl').get(source['post_id']),
        translated.load_human_translated(account_dir / 'translated_human.jsonl').get(source['post_id']))
    return image_de.text_de_sha256(entry['text_de']) if entry else ''


def image_versions(account_dir: Path, indexed: dict) -> dict[str, list]:
    """每张图生成过的历史版本，按媒体序号分组；只读，不产生费用。"""
    source, _ = read_post_truth(account_dir, indexed)
    basis = _current_basis(account_dir, source)
    grouped: dict[str, list] = {}
    for index, media in enumerate(source.get('media') or []):
        if not isinstance(media, dict) or media.get('kind') != 'image':
            continue
        try:
            found = image_de.image_versions(account_dir, source, index, basis)
        except (OSError, ValueError, ArchivePathError):
            found = []
        if found:
            grouped[str(index)] = found
    return grouped


def select_version(account_dir: Path, indexed: dict, *, media_index: int, out_path: str,
                   source_text_sha256: str, review_revision: str | None) -> dict:
    """采用某个历史版本。**不调用模型、不产生费用**，只改"当前是哪一版"。"""
    account_dir = Path(account_dir)
    with review.transaction(account_dir) as session:
        source, state = session.validate(
            dict(indexed), expected_revision=review_revision,
            expected_source_sha256=source_text_sha256,
            scheduled=journal.source_ref(
                indexed.get('platform'), indexed.get('post_id')) in journal.scheduled_source_refs(cfg().state_dir))
        if state['status'] in review.TERMINAL | {'approved'}:
            raise review.ReviewConflict('这篇已结束审校或正在提交，不能更换图片版本')
        try:
            chosen = image_de.select_image_version(
                account_dir, source, media_index, out_path, _current_basis(account_dir, source))
        except (OSError, ValueError) as exc:
            raise review.ReviewValidationError('未能采用这一版：%s' % exc) from exc
        session.change(source, 'edited', expected_revision=review_revision,
                       expected_source_sha256=source_text_sha256)
    return chosen


def _eligible(account_dir, indexed, *, source_hash, review_revision=None, human_revision=None,
              initial=False):
    source, _ = read_post_truth(account_dir, indexed)
    scheduled = journal.source_ref(source['platform'], source['post_id']) in journal.scheduled_source_refs(cfg().state_dir)
    state = review.state_for(account_dir, source, scheduled=scheduled)
    if state['status'] not in {'pending_review', 'edited'}:
        raise review.ReviewConflict('请先恢复这篇的审校，再发起优化')
    if translated.source_text_sha256(source['text']) != source_hash:
        raise review.ReviewConflict('原文已有更新，请重新核对后发起优化')
    human = translated.load_human_translated(account_dir / 'translated_human.jsonl').get(source['post_id'])
    if initial and (state.get('revision') != review_revision or (human or {}).get('revision') != human_revision):
        raise review.ReviewConflict('审校内容已有更新，请刷新后重试')
    effective = translated.effective_translation(source,
        translated.load_translated(account_dir / 'translated.jsonl').get(source['post_id']), human)
    if effective and effective.get('is_human') and effective.get('stale'):
        raise review.ReviewConflict('请先复核并保存原文变更后的人工稿')
    if effective and effective.get('stale'):
        effective = None
    # 复用与自动处理相同的归属、素材和价格规则，Web 不能绕过付费闸。
    created = datetime.fromisoformat(source['created_at'].replace('Z', '+00:00'))
    candidate = engine.SourcePost(source['platform'], account_dir, source, created,
                                  journal.source_ref(source['platform'], source['post_id']))
    issue = engine._prepaid_issue(engine.Candidate(candidate, (candidate,), 'independent'), engine.publish_rules())
    if issue:
        raise review.ReviewConflict(issue.summary)
    return source, effective


def submit(account_dir: Path, indexed: dict, *, kind: str, instruction: str,
           source_text_sha256: str, human_revision: str | None,
           review_revision: str | None, media_index: int | None = None,
           executor=None) -> dict:
    if kind not in {'text', 'image'} or not isinstance(instruction, str) or not instruction.strip():
        raise review.ReviewValidationError('请选择文案或图片，并填写本次优化要求')
    if len(instruction) > 4000:
        raise review.ReviewValidationError('优化要求请控制在 4000 字内')
    account_dir = Path(account_dir)
    if account_dir.name not in cfg().active_accounts():
        raise review.ReviewValidationError('此账号已冻结或未配置，不产生新费用')
    cfg().state_dir.mkdir(parents=True, exist_ok=True)
    with paid_model.FileLock(cfg().state_dir / 'refinement.lock',
                             busy_message='有优化请求正在受理，请稍后重试'), review.transaction(account_dir):
        source, effective = _eligible(account_dir, indexed, source_hash=source_text_sha256,
            review_revision=review_revision, human_revision=human_revision, initial=True)
        if kind == 'image' and (not isinstance(media_index, int) or isinstance(media_index, bool)
                or not 0 <= media_index < len(source.get('media', [])) or effective is None):
            raise review.ReviewValidationError('请指定已有译文的有效图片序号')
        previous = [row for row in latest().values()
                    if row['account'] == account_dir.name and row['post_id'] == source['post_id']]
        if any(row['status'] in {'pending', 'running'} for row in previous):
            raise review.ReviewConflict('这篇已有优化任务在处理，请等待结果；重启遗留任务需先核对付费账本')
        if kind == 'image':
            count = sum(row['kind'] == 'image' and row.get('media_index') == media_index for row in previous)
            if count >= image_de.Settings().max_refine_per_media:
                raise review.ReviewConflict('这张图片已达到优化次数上限，请下载后交人工处理')
        engine.budget_preflight()
        now = datetime.now(timezone.utc).isoformat()
        row = {'job_id': uuid4().hex, 'account': account_dir.name, 'post_id': source['post_id'],
               'source_text_sha256': source_text_sha256, 'kind': kind, 'instruction': instruction.strip(),
               'media_index': media_index, 'status': 'pending', 'recorded_at': now, 'actor': None}
        row.update(worker=current_worker(), operation_tracked=True,
                   source_fingerprint=paid_consent.fingerprint(source, account_dir))
        _append(row)
    try:
        (executor or _executor).submit(execute, row, source)
    except Exception as exc:
        _append(dict(row, status='failed', error='executor_unavailable',
                     message='处理线程未启动，本次未调用模型，请重新受理'))
        raise review.ReviewConflict('处理线程未启动，本次未调用模型') from exc
    return row


def execute(row: dict, indexed: dict, *, translator=None, editor=None) -> dict:
    """工作线程入口；重启后不会自动重放未闭合请求。测试注入执行器，零真实费用。"""
    account_dir = cfg().archive_dir / row['account']
    with _wait_lock('refinement.lock'):
        current = latest().get(row['job_id'])
        if current is None or current['status'] != 'pending':
            return current or row
        event = dict(row, status='running', recorded_at=datetime.now(timezone.utc).isoformat())
        _append(event)
    try:
        def preflight():
            source, _ = _eligible(account_dir, indexed, source_hash=row['source_text_sha256'])
            if row.get('source_fingerprint') and paid_consent.fingerprint(source, account_dir) != row['source_fingerprint']:
                raise review.ReviewConflict('源文、作者或原图已经改变，请重新核对')
            engine.budget_preflight()
        preflight()
        source, effective = _eligible(account_dir, indexed, source_hash=row['source_text_sha256'])
        controller = paid_requests.RequestController(cfg().state_dir, preflight=preflight, operation_id=row['job_id'])
        if row['kind'] == 'text':
            settings = translation.Settings()
            translator = translator or translation.Translator(settings, paid_controller=controller)
            with translation.TranslationRunLock(cfg().state_dir / 'translation.lock'):
                ok, failed = translation.run_translate(settings, translator, account_dir, 1, True, False,
                    scope=frozenset([source['post_id']]), source_rows=[source],
                    refine_instruction=row['instruction'], refine_id=row['job_id'],
                    current_body=(effective or {}).get('text_de', ''))
            if ok != 1 or failed:
                raise ValueError('文案优化未通过产出检查；已有人工稿保持原样')
            result = translated.load_translated(account_dir / 'translated.jsonl')[source['post_id']]
            event['text_de'] = result['text_de']
        else:
            settings = image_de.Settings()
            editor = editor or image_de.ImageEditor(settings, paid_controller=controller)
            with image_de.ImageRunLock(cfg().state_dir / 'images.lock'):
                stats = image_de.run_localize(settings, editor, account_dir, [source], 1, True, False,
                    row['media_index'], refine_instruction=row['instruction'], refine_id=row['job_id'])
            if stats.succeeded != 1:
                raise ValueError('图片优化未通过产出检查；已有图片保持原样')
            result = image_de.load_image_state(account_dir / 'images_de.jsonl').latest[
                (source['post_id'], row['media_index'])]
            event['out_path'] = result['out_path']
        event['status'] = 'succeeded'
    except (Exception, SystemExit) as exc:
        # 不把 SDK 异常原文（可能含凭据/响应）放进业务界面。
        event.update(status='failed', error=type(exc).__name__,
                     message='优化未完成，请检查模型配置、素材和付费记录后再决定是否重试。')
    event['recorded_at'] = datetime.now(timezone.utc).isoformat()
    _append(event)
    return event

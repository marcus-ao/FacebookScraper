"""带预算检查的德语标签推荐；业务仍需在分区编辑器确认选择。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import translate
from core import hashtag_rank, hashtag_sampling, paid_model, paid_requests
from core.config import cfg
from pipeline import engine, refinement
from routes import hashtags


def suggest(account_dir, source, *, source_text_sha256, human_revision, review_revision,
            translator=None, sampler=None) -> dict:
    truth, _ = refinement._eligible(account_dir, source, source_hash=source_text_sha256,
                                    review_revision=review_revision, human_revision=human_revision, initial=True)
    c = cfg()
    keep = c.get('image', 'keep_verbatim', {})
    system, semantic = hashtag_rank.candidate_prompt(truth['text'], keep)
    request_id = uuid4().hex
    candidates = {}
    if semantic:
        def preflight():
            refinement._eligible(account_dir, source, source_hash=source_text_sha256)
            engine.budget_preflight()
        settings = translate.Settings()
        caller = translator or translate.Translator(settings,
            paid_controller=paid_requests.RequestController(
                c.state_dir, preflight=preflight, operation_id=request_id))
        c.state_dir.mkdir(parents=True, exist_ok=True)
        with translate.TranslationRunLock(c.state_dir / 'translate.lock'):
            preflight()
            caller.set_paid_context('hashtags:' + request_id, truth['platform'] + ':' + truth['post_id'])
            try:
                response = caller.translate(json.dumps(semantic, ensure_ascii=False), system)
                candidates = hashtag_rank.parse_candidates(response, semantic)
                paid_model.append_jsonl(c.state_dir / 'hashtag_suggestions.jsonl', {
                    'request_id': request_id, 'post_id': truth['post_id'], 'account': account_dir.name,
                    'source_text_sha256': source_text_sha256, 'candidates': candidates,
                    'recorded_at': datetime.now(timezone.utc).isoformat(), 'actor': None,
                    'paid_request_id': caller.paid_request_id})
                caller.finalize_paid(True, 'hashtag candidates fsynced')
            except Exception:
                caller.finalize_paid(False, 'hashtag candidate contract failed')
                raise
    signal_path = c.state_dir / 'de_hashtags.jsonl'
    if sampler is None:
        def sampler(tags):
            return hashtag_sampling.sample_instagram_tags(tags, c=c)
    sample = hashtags.refresh_candidates([tag for items in candidates.values() for tag in items],
                                          signal_path, sampler=sampler)
    result = hashtag_rank.recommend(truth['text'], candidates, keep_verbatim=keep,
                                   signals=hashtag_rank.load_signals(signal_path))
    result['sampling'] = sample
    result['source_text_sha256'] = source_text_sha256
    return result


WEEKLY_STATE = 'hashtag_sampling_state.json'


def _weekly_state_path(c) -> Path:
    return Path(c.state_dir) / WEEKLY_STATE


def _peer_config_digest(settings: hashtag_sampling.SamplingConfig) -> str:
    import hashlib
    raw = json.dumps({"enabled": settings.enabled,
                      "peer_accounts": settings.peer_accounts,
                      "refresh_days": settings.refresh_days},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _read_weekly_state(c) -> dict:
    try:
        data = json.loads(_weekly_state_path(c).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def weekly_refresh_due(*, now: datetime | None = None, c=None) -> bool:
    """Cheap read-only scheduler predicate; it never opens a browser."""
    c = c or cfg()
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = hashtag_sampling.SamplingConfig.load(c)
    state = _read_weekly_state(c)
    if state.get('peer_config_sha256') != _peer_config_digest(settings):
        return True
    try:
        next_attempt = datetime.fromisoformat(str(state['next_attempt_at']).replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError):
        return True
    return next_attempt.tzinfo is None or moment >= next_attempt.astimezone(timezone.utc)


def weekly_refresh(*, now: datetime | None = None, c=None, browser_sampler=None) -> dict:
    """Blocking weekly hook. Runtime must submit it to a dedicated executor."""
    c = c or cfg()
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = hashtag_sampling.SamplingConfig.load(c)
    if not weekly_refresh_due(now=moment, c=c):
        return {'status': 'not_due', 'count': 0}
    result = hashtag_sampling.sample_german_peers(
        settings.peer_accounts, sampled_at=moment, c=c, browser_runner=browser_sampler)
    count = 0
    if result['status'] == 'sampled':
        count = hashtag_rank.append_samples(Path(c.state_dir) / 'de_hashtags.jsonl', result['rows'])
    retry_days = settings.refresh_days if result['status'] in {'sampled', 'skipped'} else 1
    state = {
        'schema_version': 1,
        'status': result['status'],
        'count': count,
        'attempted_at': moment.isoformat(),
        'next_attempt_at': (moment + timedelta(days=retry_days)).isoformat(),
        'peer_config_sha256': _peer_config_digest(settings),
        'message': result.get('reason'),
    }
    paid_model.atomic_write_json(_weekly_state_path(c), state)
    return dict(result, count=count, next_attempt_at=state['next_attempt_at'])

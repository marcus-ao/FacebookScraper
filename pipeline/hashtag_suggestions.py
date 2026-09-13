"""带预算检查的德语标签推荐；业务仍需在分区编辑器确认选择。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import translate
from core import hashtag_rank, paid_model, paid_requests
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
    sample = hashtags.refresh_candidates([tag for items in candidates.values() for tag in items],
                                          signal_path, sampler=sampler)
    result = hashtag_rank.recommend(truth['text'], candidates, keep_verbatim=keep,
                                   signals=hashtag_rank.load_signals(signal_path))
    result['sampling'] = sample
    result['source_text_sha256'] = source_text_sha256
    return result

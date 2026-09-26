"""当前正文与逐图版本的审校确认判据，供 Web 与真实提交入口共用。"""
from __future__ import annotations

from pathlib import Path

from core import content_confirmation as decisions, localization, review, store, translated
from localize import images


def current_tokens(account_dir: Path, source: dict) -> dict:
    account_dir = Path(account_dir)
    post_id = source['post_id']
    machine = translated.load_translated(account_dir / 'translated.jsonl').get(post_id)
    human = translated.load_human_translated(account_dir / 'translated_human.jsonl').get(post_id)
    effective = translated.effective_translation(source, machine, human)
    draft = localization.effective_draft(account_dir, source, effective)
    body = draft['body_de']
    source_digest = translated.source_text_sha256(source['text'])
    body_token = decisions.token('body-v1', source_digest, body) if body.strip() else None

    media = source.get('media') or []
    image_entry = translated.image_translation(source, machine, human)
    try:
        pairs = images.review_image_pairs(account_dir, source, image_entry)
    except (OSError, ValueError, store.ArchivePathError):
        pairs = []
    by_index = {pair.media_index: pair for pair in pairs}
    image_tokens = []
    for media_index, item in enumerate(media):
        if not isinstance(item, dict) or item.get('kind') != 'image':
            continue
        pair = by_index.get(media_index)
        current = None
        if pair and pair.selected_rel:
            try:
                selected = account_dir / pair.selected_rel
                store.assert_physical_direct_path(selected.parent, selected, kind='file', label='当前审校图片')
                byte_digest = images.sha256_file(selected)
                record = pair.record or {}
                manual = images.manual_upload_record(selected) if pair.manual else None
                current = decisions.token(
                    'image-v1', media_index, str(item.get('source_media_id') or ''),
                    pair.source_sha256, pair.selection, pair.selected_rel, byte_digest,
                    record.get('created_at'), record.get('selected_from'), record.get('refine_id'),
                    (manual or {}).get('replaced_at'))
            except (OSError, ValueError, store.ArchivePathError):
                current = None
        image_tokens.append({'index': len(image_tokens), 'media_index': media_index, 'token': current})
    return {'body': body_token, 'images': image_tokens}


def current_state(account_dir: Path, source: dict, *, events: list[dict] | None = None) -> dict:
    if events is None:
        events = review.history(account_dir, source['post_id'])
    return decisions.project(events, current_tokens(account_dir, source))


def require_current(account_dir: Path, source: dict, post: object = None) -> dict:
    state = current_state(account_dir, source)
    if not state['body']['confirmed']:
        raise review.ReviewConflict('请先核对并确认当前德语正文')
    missing = [item['index'] + 1 for item in state['images'] if not item['confirmed']]
    if missing:
        raise review.ReviewConflict('请先核对并确认第 %s 张图片' % '、'.join(map(str, missing)))
    return state

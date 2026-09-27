"""当前正文与逐图版本的审校确认判据，供 Web 与真实提交入口共用。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from core import content_confirmation as decisions, localization, review, store, translated
from localize import images


def _changes(values: list[object]) -> list[str]:
    changes = []
    for value in values:
        digest = decisions.token(value)
        if not changes or changes[-1] != digest:
            changes.append(digest)
    return changes


def _source_versions(account_dir: Path, source: dict) -> list[dict]:
    directory = store.post_directory(account_dir, source)
    path = store.assert_physical_direct_path(directory, directory / 'source_history.jsonl',
                                             kind='file', label='来源版本记录')
    previous = []
    if path.exists():
        with path.open('rb') as handle:
            for line in handle:
                try:
                    if not line.endswith(b'\n'):
                        raise ValueError('incomplete source history')
                    row = json.loads(line)
                    old = row['source']
                    if not isinstance(old, dict) or old.get('post_id') != source['post_id']:
                        raise ValueError('invalid source history')
                    previous.append(old)
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    raise review.ReviewConflict('来源版本记录不完整，请先核对') from exc
    return [*previous, source]


def _image_source(item: object) -> object:
    if not isinstance(item, dict):
        return None
    return (item.get('kind'), item.get('source_media_id'), item.get('sha256'),
            urlsplit(str(item.get('url') or '')).path if not item.get('source_media_id')
            and not item.get('sha256') else None)


def _body_versions(account_dir: Path, post_id: str) -> list[str]:
    rows = translated.load_image_translation_history(account_dir).get(post_id, [])
    human = [row for row in rows if row['is_human']]
    machine = [row for row in rows if not row['is_human']]
    if human:
        # 人工稿出现前的机器变化仍是确认依据；之后机器重译不改变生效正文。
        try:
            first_human = datetime.fromisoformat(human[0]['recorded_at'].replace('Z', '+00:00'))
            machine = [row for row in machine if datetime.fromisoformat(
                row['translated_at'].replace('Z', '+00:00')) <= first_human]
        except (ValueError, KeyError, TypeError) as exc:
            raise review.ReviewConflict('译文版本记录不完整，请先核对') from exc
    return _changes([localization.split_content(row['text_de'])['body'] for row in [*machine, *human]])


def current_tokens(account_dir: Path, source: dict, *, events: list[dict] | None = None) -> dict:
    account_dir = Path(account_dir)
    post_id = source['post_id']
    events = review.history(account_dir, post_id) if events is None else events
    source_versions = _source_versions(account_dir, source)
    source_text_versions = _changes([row.get('text') for row in source_versions])
    selections = {row['image_selection']['media_index']: row['revision'] for row in events
                  if row['action'] == 'image_selected'}
    machine = translated.load_translated(account_dir / 'translated.jsonl').get(post_id)
    human = translated.load_human_translated(account_dir / 'translated_human.jsonl').get(post_id)
    effective = translated.effective_translation(source, machine, human)
    draft = localization.effective_draft(account_dir, source, effective)
    body = draft['body_de']
    source_digest = translated.source_text_sha256(source['text'])
    body_token = decisions.token('body-v2', source_digest, body, source_text_versions,
                                 _body_versions(account_dir, post_id)) if body.strip() else None

    media = source.get('media') or []
    image_entry = translated.image_translation(source, machine, human)
    try:
        pairs = images.review_image_pairs(account_dir, source, image_entry)
    except (OSError, ValueError, store.ArchivePathError):
        pairs = []
    by_index = {pair.media_index: pair for pair in pairs}
    directory = store.post_directory(account_dir, source)
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
                # 只绑帖子目录内的位置：改商品分类会整体移动帖子目录，不能让图片确认失效。
                location = (selected.relative_to(directory).as_posix()
                            if directory in selected.parents else pair.selected_rel)
                current = decisions.token(
                    'image-v3', media_index, str(item.get('source_media_id') or ''),
                    pair.source_sha256, pair.selection, location, byte_digest,
                    record.get('created_at'), record.get('selected_from'), record.get('refine_id'),
                    (manual or {}).get('replaced_at'), selected.stat().st_mtime_ns,
                    selections.get(media_index),
                    _changes([_image_source((row.get('media') or [])[media_index]
                                            if len(row.get('media') or []) > media_index else None)
                              for row in source_versions]))
            except (OSError, ValueError, store.ArchivePathError):
                current = None
        image_tokens.append({'index': len(image_tokens), 'media_index': media_index, 'token': current})
    return {'body': body_token, 'images': image_tokens}


def current_state(account_dir: Path, source: dict, *, events: list[dict] | None = None) -> dict:
    if events is None:
        events = review.history(account_dir, source['post_id'])
    return decisions.project(events, current_tokens(account_dir, source, events=events))


def require_current(account_dir: Path, source: dict, post: object = None) -> dict:
    state = current_state(account_dir, source)
    if not state['body']['confirmed']:
        raise review.ReviewConflict('请先核对并确认当前德语正文')
    missing = [item['index'] + 1 for item in state['images'] if not item['confirmed']]
    if missing:
        raise review.ReviewConflict('请先核对并确认第 %s 张图片' % '、'.join(map(str, missing)))
    return state

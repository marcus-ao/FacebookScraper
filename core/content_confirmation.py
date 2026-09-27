"""审校确认的内容指纹与只读投影；确认记录本身只追加到账本。"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence


def token(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def validate(action: str, payload: object) -> None:
    image = action == 'image_reviewed'
    keys = {'confirmed', 'content_token', 'media_index'} if image else {'confirmed', 'content_token'}
    if (not isinstance(payload, dict) or set(payload) != keys
            or type(payload.get('confirmed')) is not bool
            or not isinstance(payload.get('content_token'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', payload['content_token'])
            or (image and (type(payload.get('media_index')) is not int
                           or payload['media_index'] < 0))):
        raise ValueError('内容确认记录不完整')


def project(events: Sequence[Mapping[str, object]], tokens: Mapping[str, object]) -> dict:
    body_saved: Mapping[str, object] | None = None
    image_saved: dict[int, Mapping[str, object]] = {}
    for event in events:
        action = event.get('action')
        payload = event.get('content_confirmation')
        if action == 'body_reviewed' and isinstance(payload, Mapping):
            body_saved = event
        elif action == 'image_reviewed' and isinstance(payload, Mapping):
            image_saved[payload['media_index']] = event

    def decision(saved: Mapping[str, object] | None, current: object) -> dict:
        payload = saved.get('content_confirmation') if saved else None
        confirmed = bool(isinstance(current, str) and isinstance(payload, Mapping)
                         and payload.get('confirmed') is True
                         and payload.get('content_token') == current)
        # version 是页面所见内容的不透明版本；确认时原样带回，内容已变则拒绝。
        return {'confirmed': confirmed, 'confirmed_at': saved.get('recorded_at') if confirmed else None,
                'version': current if isinstance(current, str) else None}

    return {
        'body': decision(body_saved, tokens['body']),
        'images': [dict(index=item['index'], **decision(
            image_saved.get(item['media_index']), item['token']))
            for item in tokens['images']],
    }

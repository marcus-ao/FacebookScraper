"""Public delivery facts obtained from remote detail views, never from elapsed time."""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.paid_model import atomic_write_json
from core.store import assert_physical_direct_path

NAME = 'publication_observations.json'


def _load(state_dir):
    root = Path(state_dir)
    path = assert_physical_direct_path(root, root / NAME, kind='file', label='公开发布观测')
    if not path.exists():
        return {'version': 1, 'items': {}}
    data = json.loads(path.read_text('utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('items'), dict):
        raise ValueError('公开发布观测记录无法读取')
    return data


def record(state_dir, inventory, observed_at):
    """Caller holds publish.lock and has just performed a live Planner read."""
    data = _load(state_dir)
    for card in inventory.cards:
        if card.delivery != 'published':
            continue
        for channel, remote in card.remote_ids:
            if channel not in card.channels or not re.fullmatch(r'\d{6,}', remote):
                raise ValueError('公开发布记录缺少渠道和远端身份')
            key = channel + ':' + remote
            previous = data['items'].get(key, {})
            data['items'][key] = {'channel': channel, 'remote_id': remote, 'status': 'published',
                'first_observed_at': previous.get('first_observed_at', observed_at.isoformat()),
                'observed_at': observed_at.isoformat(), 'published_at': card.at.isoformat(),
                'card_sha256': card.card_sha256, 'source': 'planner_live_detail'}
    if data['items']:
        atomic_write_json(Path(state_dir) / NAME, data)


def status(state_dir, attempt):
    unknown = {'status': 'unknown', 'observed_at': None, 'message': '尚无与本次排期匹配的远端公开发布观测'}
    if not attempt or attempt.get('status') != 'scheduled':
        return unknown
    try:
        data = _load(state_dir)
        remote_ids = dict(re.findall(r'(facebook|instagram)=(\d{6,})', attempt.get('remote_id', '')))
        targets = attempt.get('target_channels') or ['facebook', 'instagram']
        values = [data['items'].get(channel + ':' + remote_ids.get(channel, '')) for channel in targets]
        if not values or not all(row and row.get('status') == 'published'
                                 and row.get('source') == 'planner_live_detail' for row in values):
            return unknown
        return {'status': 'published', 'observed_at': max(row['observed_at'] for row in values),
                'message': '远端详情已观测到公开发布', 'channels': values}
    except (OSError, ValueError, KeyError, TypeError):
        return dict(unknown, message='公开发布观测记录需要核对，排期回执仍保留')

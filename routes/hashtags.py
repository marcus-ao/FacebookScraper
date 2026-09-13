"""已授权采样器的容错边界。未配置接口时只提供候选，不伪造热度。"""
from __future__ import annotations

from core.hashtag_rank import append_samples


def refresh_candidates(tags, path, *, sampler=None) -> dict:
    if sampler is None:
        return {'status': 'unavailable', 'count': 0, 'reason': '尚无已核验的标签查询接口'}
    try:
        rows = sampler(tuple(dict.fromkeys(tags)))
        return {'status': 'sampled', 'count': append_samples(path, rows)}
    except (Exception, SystemExit):
        return {'status': 'unavailable', 'count': 0, 'reason': '采样未完成，仍可按语义手选'}


def refresh_peers(accounts, path, *, sampler=None) -> dict:
    if not accounts:
        return {'status': 'skipped', 'count': 0, 'reason': '同类德语账号名单为空'}
    return refresh_candidates(accounts, path, sampler=sampler)

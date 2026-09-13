"""德语标签候选与可追溯的采样信号。排序缺失不阻断人工选词。"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.localization import protected_tags, split_content
from core.paid_model import FileLock, append_jsonl
from core.translated import extract_hashtags


def candidate_prompt(source: str, keep_verbatim: dict) -> tuple[str, list[str]]:
    preserved = protected_tags(source, keep_verbatim)
    semantic = list(dict.fromkeys(tag for tag in split_content(source)['tags'] if tag not in preserved))
    prompt = ('为德国宠物、家居清洁品牌的社媒帖子推荐自然的德语话题标签。'
              '每个给定英文标签返回3至5个语义接近的德语候选，不增加品牌、产品型号或优惠信息。'
              '不要声称知道热度，不排序热度，不返回URL。只输出JSON对象，键为原英文标签，'
              '值为含#前缀的候选词数组。每个标签不得有空格。')
    return prompt, semantic


def parse_candidates(value: str, expected: list[str]) -> dict[str, list[str]]:
    data = json.loads(value)
    if not isinstance(data, dict) or set(data) != set(expected):
        raise ValueError('模型返回的标签与原帖不对应，请手工选择')
    for tag, candidates in data.items():
        if (not isinstance(candidates, list) or not 3 <= len(candidates) <= 5
                or any(not isinstance(item, str) or extract_hashtags(item) != [item] for item in candidates)
                or len(set(candidates)) != len(candidates)):
            raise ValueError('每个语义标签需要3至5个不同的完整德语候选')
    return data


def append_samples(path: Path, rows: list[dict]) -> int:
    accepted = []
    for row in rows:
        if not isinstance(row, dict) or extract_hashtags(str(row.get('tag', ''))) != [row.get('tag')]:
            raise ValueError('采样标签无效')
        moment = datetime.fromisoformat(row['sampled_at'].replace('Z', '+00:00'))
        if moment.tzinfo is None or not isinstance(row.get('source'), str) or not row['source']:
            raise ValueError('采样需要来源和带时区的时刻')
        for key in ('media_count', 'peer_uses_14d', 'trend_score'):
            value = row.get(key)
            if value is not None and (not isinstance(value, (float, int)) or isinstance(value, bool)
                    or not math.isfinite(value) or (key != 'trend_score' and value < 0)):
                raise ValueError('采样值无效')
        if any(row.get(key) is not None for key in ('peer_uses_14d', 'trend_score')) and row.get('geo') != 'DE':
            raise ValueError('同类账号和趋势信号须明确为德国地区')
        if row.get('media_count') is not None and row.get('geo', 'global') != 'global':
            raise ValueError('Instagram 累计帖子数只能标为全球量级')
        if row.get('trend_score') is not None and any(
                not isinstance(row.get(key), str) or not row[key].strip()
                for key in ('comparison_group', 'sample_batch', 'time_range')):
            raise ValueError('Trends 信号必须绑定同组、同批次和统一时间范围')
        stale_after = row.get('stale_after')
        if stale_after is not None:
            expiry = datetime.fromisoformat(str(stale_after).replace('Z', '+00:00'))
            if expiry.tzinfo is None or expiry <= moment:
                raise ValueError('采样过期时刻必须晚于采样时刻')
        accepted.append(dict(row, sampled_at=moment.astimezone(timezone.utc).isoformat()))
    if not accepted:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix('.lock'), busy_message='标签采样正在更新'):
        for row in accepted:
            append_jsonl(path, row)
    return len(accepted)


def load_signals(path: Path) -> dict[str, dict]:
    signals = {}
    if not path.exists():
        return signals
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
            when = datetime.fromisoformat(row['sampled_at'].replace('Z', '+00:00'))
            if when.tzinfo is None:
                continue
            tag = row['tag']
            for metric in ('media_count', 'peer_uses_14d', 'trend_score'):
                value = row.get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    current = signals.setdefault(tag, {}).get(metric)
                    if current is None or when > datetime.fromisoformat(current['sampled_at']):
                        signals[tag][metric] = {'value': value, 'source': row['source'],
                            'sampled_at': when.isoformat(), 'geo': row.get('geo', 'global'),
                            **{key: row[key] for key in ('source_url', 'metric_scope', 'stale_after',
                               'comparison_group', 'sample_batch', 'time_range') if row.get(key) is not None}}
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return signals


def recommend(source: str, candidates: dict[str, list[str]], *, keep_verbatim: dict,
              signals: dict | None = None, now: datetime | None = None, peer_accounts=()) -> dict:
    moment = now or datetime.now(timezone.utc)
    signals = signals or {}
    original = split_content(source)['tags']
    protected = protected_tags(source, keep_verbatim)
    groups, selected = [], []
    for original_tag in original:
        names = [original_tag] if original_tag in protected else candidates[original_tag]
        trend_observations = [signals.get(name, {}).get('trend_score') for name in names]
        trend_keys = {(item.get('comparison_group'), item.get('sample_batch'), item.get('time_range'))
                      for item in trend_observations if item}
        comparable_trends = (len(trend_observations) > 1
                             and all(trend_observations)
                             and len(trend_keys) == 1
                             and next(iter(trend_keys))[0] == original_tag)
        items = []
        for name in names:
            signal = signals.get(name, {})
            current = {}
            for key, observation in signal.items():
                age = moment - datetime.fromisoformat(observation['sampled_at'])
                expiry = observation.get('stale_after')
                fresh = (datetime.fromisoformat(expiry) >= moment if expiry
                         else timedelta(0) <= age <= timedelta(days=14))
                if timedelta(0) <= age and fresh and (key != 'trend_score' or comparable_trends):
                    current[key] = observation
            items.append({'tag': name, 'signals': signal, 'current_signals': current})
        # 同类账号近期使用为主，其次趋势，最后才是累计量级；相同信号保持模型顺序。
        items.sort(key=lambda item: tuple(-item['current_signals'].get(key, {}).get('value', 0)
                    for key in ('peer_uses_14d', 'trend_score', 'media_count')))
        groups.append({'source_tag': original_tag, 'protected': original_tag in protected,
                       'candidates': items})
        selected.append(items[0]['tag'])
    has_peer_signal = any('peer_uses_14d' in item['current_signals']
                          for group in groups for item in group['candidates'])
    return {'groups': groups, 'selected': selected, 'sampled': any(item['current_signals']
                for group in groups for item in group['candidates']),
            'notice': ('缺少同类账号采样，排序依据不完整。'
                       if not peer_accounts and not has_peer_signal else '')
                      + '未采样、失败或过期信号不参与排序；Trends 只在同一候选组同一批次内比较，不可跨批。'
                      + 'Instagram 累计帖子数是全球量级，不代表德国当前热度。',
            'generated_at': moment.astimezone(timezone.utc).isoformat()}

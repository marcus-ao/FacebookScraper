"""Read-only timing evidence. Missing discovery facts never become zero latency."""
import json
import math
from datetime import datetime, timedelta

from core.config import MonitorSchedule
from core.integrity import parse_ts


def _percentile(values, proportion):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * proportion) - 1)], 2) if ordered else None


def published_latency(rows, now, days):
    """Each source post contributes its first observed ready event, never a rewrite."""
    seen, elapsed = set(), []
    for row in sorted(rows, key=lambda item: parse_ts(item['recorded_at'])):
        if row.get('event') != 'post_content_ready' or not isinstance(row.get('source_ref'), str):
            continue
        source_ref = row['source_ref']
        published, ready = parse_ts(row.get('source_created_at')), parse_ts(row['recorded_at'])
        if not source_ref or source_ref in seen or published is None or ready < published:
            continue
        seen.add(source_ref)
        if now - timedelta(days=days) <= ready <= now:
            elapsed.append((ready - published).total_seconds() / 60)
    return {'status': 'available' if elapsed else 'missing', 'basis': 'first_ready_per_source_post',
            'sample_count': len(elapsed), 'p50_minutes': _percentile(elapsed, .5),
            'p95_minutes': _percentile(elapsed, .95)}


def timings(state_dir, now, *, days=30, schedule=None):
    schedule = schedule or MonitorSchedule.load()
    path = state_dir / 'monitoring_facts.jsonl'
    empty = {'status': 'missing', 'window_days': days, 'basis': 'completed_batches_with_ready_content',
             'sample_count': 0, 'p50_minutes': None, 'p95_minutes': None,
             'morning_sample_count': 0, 'morning_deadline_rate': None,
             'published_to_ready': published_latency([], now, days)}
    try:
        rows = [json.loads(line) for line in path.read_text('utf-8').splitlines() if line.strip()]
        if any(not isinstance(row, dict) or parse_ts(row.get('recorded_at')) is None for row in rows):
            raise ValueError('Invalid timing evidence')
        for row in rows:
            if row.get('event') in {'processing_started', 'content_ready'}:
                if not isinstance(row.get('batch_id'), str) or not row['batch_id']:
                    raise ValueError('Invalid batch identity')
            if row.get('event') == 'content_ready' and (
                    type(row.get('ready_count')) is not int or row['ready_count'] < 0):
                raise ValueError('Invalid ready count')
    except FileNotFoundError:
        return empty
    except (OSError, ValueError):
        return dict(empty, status='state_unreadable')
    empty['published_to_ready'] = published_latency(rows, now, days)
    starts = {row['batch_id']: row for row in rows if row.get('event') == 'processing_started' and row.get('batch_id')}
    elapsed, morning = [], []
    seen = set()
    for row in rows:
        key = row.get('batch_id')
        if row.get('event') != 'content_ready' or not row.get('ready_count') or key in seen:
            continue
        seen.add(key)
        requested = parse_ts(starts.get(key, {}).get('requested_at'))
        ready = parse_ts(row['recorded_at'])
        if requested is None or ready < requested or not now - timedelta(days=days) <= ready <= now:
            continue
        elapsed.append((ready - requested).total_seconds() / 60)
        local = MonitorSchedule.local(requested)
        start, end = [schedule.parse_time(v) for v in schedule.on_duty_window]
        if local.time() < start or local.time() >= end:
            day = local.date() + (timedelta(days=1) if local.time() >= end else timedelta())
            deadline = datetime.combine(day, start, local.tzinfo)
            morning.append(ready <= deadline)
    if not elapsed:
        return empty
    return dict(empty, status='available', sample_count=len(elapsed), p50_minutes=_percentile(elapsed, .5),
                p95_minutes=_percentile(elapsed, .95), morning_sample_count=len(morning),
                morning_deadline_rate=round(sum(morning) / len(morning), 4) if morning else None)

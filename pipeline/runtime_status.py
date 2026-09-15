"""Read-only five-stage state; process liveness never stands in for business success."""
from __future__ import annotations

from core import paid_requests
from pipeline.metrics import timings
import json
from datetime import datetime, timezone

from core.config import ROOT, cfg
from core.feishu import FeishuSettings, Outbox, WEBHOOK_ROLES
from core.heartbeat import HeartbeatSettings, heartbeat_status
from core.mirror import MirrorService, MirrorSettings
from core.network_evidence import NetworkEvidenceSettings, network_evidence_status
from core.paid_model import atomic_write_json, ModelCredentials
from core.monitoring import MonitoringJournal
from core.process_identity import current_worker, worker_alive
from publish import capabilities, journal, planner_cache


def read_json(path):
    try:
        data = json.loads(path.read_text('utf-8'))
        return data if isinstance(data, dict) else {'status': 'state_unreadable'}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {'status': 'state_unreadable'}


def record_process_tick(now):
    atomic_write_json(cfg().state_dir / 'scheduler_heartbeat.json',
                      {'worker': current_worker(), 'tick_at': now.isoformat()})


def _mirror_status(state):
    """Read the durable mirror summary without creating a client or changing the queue."""
    try:
        return MirrorService(state, MirrorSettings.load()).status()
    except Exception:
        # Keep the queue intact: a malformed record needs a human to inspect it before
        # any recovery action.  Do not reveal the raw record or provider diagnostics here.
        return {'enabled': True, 'status': 'blocked',
                'counts': {'pending': 0, 'completed': 0, 'uncertain': 0, 'blocked': 0},
                'last_success_at': None, 'last_error': '镜像记录暂时无法读取，请由维护人员核对。',
                'operations': [], 'posts': {}}


def snapshot(now=None):
    now = now or datetime.now(timezone.utc)
    c = cfg()
    state = ROOT / c.get('paths', 'state', 'state')
    process = read_json(state / 'scheduler_heartbeat.json')
    alive = worker_alive(process.get('worker')) if process.get('worker') else None
    process['alive'] = alive
    business = read_json(state / 'pipeline_state.json')
    processing = read_json(state / 'processing_state.json')
    processing_revision = MonitoringJournal.revision(processing)
    if processing.get('status') == 'running' and worker_alive(processing.get('owner')) is False:
        processing = dict(processing, status='interrupted', requires_manual_recovery=True)
    processing['state_revision'] = processing_revision
    try:
        events = [row for row in paid_requests.load_events(state) if row.get('operation_id') == processing.get('batch_id')]
        processing['paid_request_ids'] = list(dict.fromkeys(row['request_id'] for row in events)) if processing.get('batch_id') else []
        processing['cost_usd'] = sum(row.get('cost_usd', 0) for row in events if row['event'] == paid_requests.EVENT_USAGE) if processing.get('batch_id') else 0
    except Exception:
        processing['ledger_unreadable'] = True
    checks = capabilities.checks()
    try:
        accepted = capabilities.acceptance(state)
        attempts = {row['attempt_id']: row for row in journal.load(state)}
        unconfirmed = sum(row['status'] in journal.BLOCKING_STATUSES for row in attempts.values())
    except Exception:
        accepted, unconfirmed = {}, None
    try:
        feishu = Outbox(state / 'feishu_outbox.json', FeishuSettings.load()).status()
    except ValueError as exc:
        feishu = {'status': 'configuration_invalid', 'message': str(exc)}
    hooks = {role: ModelCredentials(name).optional_value() for role, name in WEBHOOK_ROLES.items()}
    feishu['credentials_present'] = all(hooks.values())
    # 两个群填成同一个地址要说出来。F4-2 的"两组不串"靠人配对，静默合并会让那条红线失效。
    feishu['groups_merged'] = feishu['credentials_present'] and len(set(hooks.values())) == 1
    mirror = _mirror_status(state)
    detection = read_json(state / 'delta_state.json')
    return {'observed_at': now.isoformat(), 'read_only': True,
            'activation': business.get('activated_at'),
            'process': {'alive': alive, 'last_tick_at': process.get('tick_at')},
            'business': {'last_successful_run': business.get('last_successful_run'),
                         'processing': processing, 'timings': timings(state, now)},
            'stages': [
                {'number': 1, 'name': '监测与调度',
                 'status': 'blocked' if detection.get('detect_hard_blocked') else 'active' if alive else 'not_observed',
                 'detection': detection},
                {'number': 2, 'name': '源内容与归档', 'status': 'available' if c.archive_dir.is_dir() else 'missing',
                 # mirror_status is retained for existing API consumers.  `mirror` is the
                 # durable queue summary and deliberately does not mean cloud content exists.
                 'mirror_status': mirror['status'], 'mirror': mirror},
                {'number': 3, 'name': '德语本地化', 'status': processing.get('status', 'not_observed'),
                 'tag_sampling': read_json(state / 'hashtag_sampling_state.json'),
                 'trends_export': read_json(state / 'trends_export_state.json')},
                {'number': 4, 'name': '飞书提醒', 'status': feishu['status'], 'outbox': feishu},
                {'number': 5, 'name': '审校与单渠道排期', 'status': 'available' if all(item['available'] for item in checks) else 'blocked',
                 'checks': checks, 'acceptance': accepted, 'unconfirmed_attempts': unconfirmed,
                 'calendar': planner_cache.read_cache(state / 'planner_cache.json', now=now)}],
            'heartbeat': heartbeat_status(state / 'heartbeat.json', HeartbeatSettings.load(c), now),
            'network': network_evidence_status(state / 'network_evidence.json', NetworkEvidenceSettings.load(c), now)}

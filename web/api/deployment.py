"""Local deployment status and admission; never performs a real business probe."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core import maintenance, paid_requests, review, translated
from core.config import ROOT, cfg
from core.process_identity import current_worker
from core.runtime_identity import read_release, validate_binding
from core.web_access import load_web_access
from publish import journal

router = APIRouter()
CONTROL_PATHS = {'/api/health', '/api/deployment/status', '/api/deployment/session', '/api/deployment/defer'}


def _gate():
    gate = maintenance.managed_gate()
    if gate is None:
        raise HTTPException(409, '当前为开发模式，未启用服务机部署')
    return gate


@router.get('/api/health')
def health():
    result = {'managed': bool(os.environ.get('FBSCRAPER_CONTROL_DIR')), 'deployment_ready': False,
              'sha': None, 'runtime_id': None, 'frontend_runtime_id': None, 'instance_id': None,
              'worker': current_worker(), 'launch_id': os.environ.get('FBSCRAPER_LAUNCH_ID'),
              'role': 'web', 'maintenance': None, 'error': None}
    try:
        access = load_web_access()
        result.update(web_host=access.web_host, web_port=access.web_port, public_base_url=access.public_base_url)
        manifest = read_release(ROOT)
        if manifest:
            result.update(sha=manifest['sha'], runtime_id=manifest['runtime_id'])
        if result['managed']:
            if manifest is None:
                raise ValueError('受管进程缺少发布清单')
            c = cfg()
            result['instance_id'] = validate_binding(c, ROOT)['instance_id']
            view = _gate().status()
            result['maintenance'] = view['phase']
            worker = view['workers'].get('web', {})
            if any(worker.get(key) != result[key] for key in ('worker', 'launch_id', 'sha', 'runtime_id', 'role')):
                raise ValueError('部署心跳不能证明当前 Web 进程')
            actual = {key: worker.get(key) for key in ('web_host', 'web_port', 'public_base_url')}
            result.update(actual)
            if any(actual[key] != getattr(access, key) for key in actual):
                raise ValueError('访问配置与正在监听的进程不一致，请按维护流程重启')
            if Path(str(c.get('paths', 'web_dist', 'web/ui/dist'))) != Path('web/ui/dist'):
                raise ValueError('发布包必须提供版本内的固定前端目录')
            dist = ROOT / 'web/ui/dist'
            frontend = json.loads((dist / 'runtime.json').read_text(encoding='utf-8'))
            result['frontend_runtime_id'] = frontend['runtime_id']
            if frontend['runtime_id'] != manifest['runtime_id'] or not (dist / 'index.html').is_file():
                raise ValueError('前后端运行内容不一致')
            # Readability is distinct from unresolved fees/publication and browser acceptance.
            paid_requests.load_events(c.state_dir)
            journal.load(c.state_dir)
            for account in c.archive_dir.iterdir():
                if account.is_dir():
                    review.history(account)
                    translated.load_human_translated(account / 'translated_human.jsonl')
            result['deployment_ready'] = True
        elif manifest is not None:
            raise ValueError('发布版本未使用受管数据绑定')
    except (OSError, ValueError, KeyError, maintenance.MaintenanceBlocked, paid_requests.PaidRequestBlocked) as exc:
        # Keep credentials, absolute data paths and arbitrary provider payloads out of HTTP.
        result['error'] = type(exc).__name__
    return JSONResponse(result, status_code=200 if not result['managed'] or result['deployment_ready'] else 503)


@router.get('/api/deployment/status')
def status():
    gate = maintenance.managed_gate()
    manifest = read_release(ROOT)
    result = {'managed': gate is not None, 'sha': manifest['sha'] if manifest else None,
              'runtime_id': manifest['runtime_id'] if manifest else None, 'maintenance': None,
              'deployment': {}, 'public_base_url': load_web_access().public_base_url}
    if gate:
        view = gate.status()
        result['maintenance'] = {key: view.get(key) for key in ('phase', 'epoch', 'deferred_until')}
        result['maintenance'].update(blockers=[{'reason': row['reason']} for row in view['blockers']],
                                     operations=[{'kind': row['kind']} for row in view['operations']])
        path = gate.control / 'deployment.json'
        if path.exists():
            value = json.loads(path.read_text(encoding='utf-8'))
            result['deployment'] = {key: value.get(key) for key in (
                'phase', 'current_sha', 'last_good_sha', 'candidate_sha', 'observed_sha', 'paused',
                'blocked_sha', 'error_code', 'waiting_since', 'updated_at', 'scheduler_enabled', 'process_enabled',
                'notification_error')}
    return result


@router.post('/api/deployment/session')
async def session(request: Request):
    try:
        body = await request.json()
        if (not isinstance(body, dict) or set(body) - {'session_id', 'runtime_id', 'dirty', 'busy', 'ack_epoch', 'closed', 'sequence'}
                or type(body.get('sequence')) is not int or body['sequence'] < 1):
            raise ValueError('会话参数无效')
        _gate().session(**body)
        return {'accepted': True}
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, '编辑会话参数无效') from exc


@router.post('/api/deployment/defer')
async def defer(request: Request):
    _gate().defer()
    return {'deferred_minutes': 30}


class AdmissionMiddleware:
    """Pure ASGI scope keeps the same operation context through queued admission."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/') or scope['path'] in CONTROL_PATHS:
            return await self.app(scope, receive, send)
        try:
            with maintenance.operation('http:' + scope['method']):
                manifest = read_release(ROOT)
                if manifest and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
                    headers = dict(scope['headers'])
                    if headers.get(b'x-fbscraper-runtime', b'').decode('ascii', errors='replace') != manifest['runtime_id']:
                        response = JSONResponse({'code': 'runtime_changed', 'detail': '系统版本已更新，请保留当前草稿并在安全时刷新'}, status_code=409)
                        return await response(scope, receive, send)
                return await self.app(scope, receive, send)
        except maintenance.MaintenanceBlocked:
            response = JSONResponse({'code': 'maintenance', 'detail': '系统正在更新，请保留当前内容并稍后重试'},
                                    status_code=503, headers={'Retry-After': '5'})
            return await response(scope, receive, send)

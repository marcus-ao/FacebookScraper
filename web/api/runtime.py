"""Runtime status uses the CLI preflight contract, with no refresh or remote write."""
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from core.config import cfg
from core.feishu import FeishuSettings, FeishuError, Outbox
from core.monitoring import MonitoringJournal
from core.monitor_access import AccessController
from pipeline.runtime_status import snapshot

router = APIRouter()


@router.get('/api/runtime')
def runtime():
    return snapshot()


@router.post('/api/runtime/monitor/recover')
async def recover_monitor(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求应为对象')
        AccessController(cfg().state_dir).recover(body.get('version'), body.get('reason') or '', body.get('platform'))
        return snapshot()
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({'detail': str(exc)}, status_code=409)


@router.post('/api/runtime/capture/recover')
async def recover_capture(request: Request):
    from routes.delta import recover_post
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('key'), str):
            raise ValueError('请指定采集异常项')
        await run_in_threadpool(recover_post, body['key'], body.get('version'), body.get('reason') or '')
        return snapshot()
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({'detail': str(exc)}, status_code=409)


@router.post('/api/runtime/notifications/{delivery_id}/resolve')
async def resolve_notification(delivery_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求应为对象')
        outbox = Outbox(cfg().state_dir / 'feishu_outbox.json', FeishuSettings.load())
        return outbox.resolve(delivery_id, action=body.get('action'), expected_version=body.get('version'),
                              message_id=body.get('message_id', ''), now=datetime.now(timezone.utc))
    except ValueError as exc:
        return JSONResponse({'detail': str(exc)}, status_code=400)
    except FeishuError as exc:
        return JSONResponse({'detail': str(exc)}, status_code=409)


@router.post('/api/runtime/processing/recover')
async def recover_processing(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求应为对象')
        # Construction must not rewrite running->interrupted before the CAS check.
        journal = MonitoringJournal(cfg().state_dir, now=datetime.now(timezone.utc), inspect_running=False)
        return journal.recover(batch_id=body.get('batch_id'), expected_revision=body.get('version'),
            outputs_reviewed=body.get('outputs_reviewed'), now=datetime.now(timezone.utc))
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({'detail': str(exc)}, status_code=409)

"""人工指定排期的HTTP适配；业务逻辑只调用pipeline.approval。"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core import review
from pipeline import approval
from publish import business_suite as bs, compose
from web.api import reader

router = APIRouter()


def _source(task_id):
    source = reader.source_post(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    return source


def berlin_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError('请填写柏林发布时间')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is not None:
        return moment
    zone = ZoneInfo('Europe/Berlin')
    candidate = moment.replace(tzinfo=zone)
    if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != moment:
        raise ValueError('这个柏林时刻因夏令时切换不存在，请重新选择')
    if candidate.utcoffset() != candidate.replace(fold=1).utcoffset():
        raise ValueError('这个柏林时刻在夏令时切换中出现两次，请选择其他时刻或提供明确UTC偏移')
    return candidate


@router.get('/api/tasks/{task_id:path}/approval-options')
def get_options(task_id: str):
    source = _source(task_id)
    return approval.options(source.account_dir, dict(source.row))


@router.post('/api/tasks/{task_id:path}/approve')
async def post_approve(task_id: str, request: Request):
    source = _source(task_id)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求需要是对象')
        fingerprint = body.get('content_fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError('请重新载入本篇的排期信息后确认')
        result = await approval.approve(source.account_dir, dict(source.row),
            scheduled_at=berlin_time(body.get('scheduled_at')),
            source_text_sha256=body.get('source_text_sha256'),
            human_revision=body.get('human_revision'), review_revision=body.get('review_revision'),
            content_fingerprint=fingerprint)
        return JSONResponse(result, status_code=200 if result['ok'] else 409)
    except approval.ApprovalConflict as exc:
        return JSONResponse({'detail': str(exc), 'suggestions': [value.isoformat() for value in exc.suggestions]}, status_code=409)
    except (bs.PublishStepError, bs.ProbeRequired, compose.ComposeError, review.ReviewConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

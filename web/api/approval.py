"""人工指定排期的HTTP适配；业务逻辑只调用pipeline.approval。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core import maintenance, review
from pipeline import approval
from publish import business_suite as bs, compose, manual_run, operations, records
from starlette.concurrency import run_in_threadpool
from web.api import reader

router = APIRouter()


@router.post('/api/tasks/{task_id:path}/publication/reconcile')
async def reconcile_publication(task_id: str):
    source = _source(task_id)
    return await run_in_threadpool(records.recover, source.account_dir, dict(source.row))


@router.post('/api/tasks/{task_id:path}/publication/unschedule')
async def unschedule_publication(task_id: str, request: Request):
    """人已在 Business Suite 手删之后来登记一次；系统实时读整月核实，不自己去删。"""
    source = _source(task_id)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求需要是对象')
        if body.get('confirmed_deleted') is not True:
            raise ValueError('请先在 Business Suite 删除这条排期，再回来登记')
        return JSONResponse(await records.unschedule(
            source.account_dir, dict(source.row), reason=body.get('reason', '')))
    except (bs.PublishStepError, bs.ProbeRequired, review.ReviewConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _source(task_id):
    source = reader.source_post(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    return source


def business_time(value: str) -> datetime:
    """把页面填的业务时区墙上时刻变成绝对时刻。⚠️ 两条夏令时分支要保留：当前业务时区
    （上海）没有夏令时，不等于这个函数以后不会换到有夏令时的时区。"""
    if not isinstance(value, str):
        raise ValueError('请填写发布时间')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is not None:
        return moment
    zone = bs.resolve_business_timezone()
    candidate = moment.replace(tzinfo=zone)
    if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != moment:
        raise ValueError('这个时刻因夏令时切换不存在，请重新选择')
    if candidate.utcoffset() != candidate.replace(fold=1).utcoffset():
        raise ValueError('这个时刻在夏令时切换中出现两次，请选择其他时刻或提供明确UTC偏移')
    return candidate


@router.get('/api/tasks/{task_id:path}/approval-options')
def get_options(task_id: str):
    source = _source(task_id)
    return approval.options(source.account_dir, dict(source.row))


def _conflict(exc) -> JSONResponse:
    suggestions = [value.isoformat() for value in getattr(exc, 'suggestions', ())]
    return JSONResponse({'detail': str(exc), 'suggestions': suggestions}, status_code=409)


async def _run(operation_id: str, account_dir, row, body, fingerprint):
    """浏览器那几分钟跑在请求之外：页面关掉、刷新或网络抖动都不影响这一次提交。"""
    def report(index, text):
        operations.progress(operation_id, index, text)
    try:
        result = await approval.approve(account_dir, row,
            scheduled_at=business_time(body.get('scheduled_at')),
            source_text_sha256=body.get('source_text_sha256'),
            human_revision=body.get('human_revision'), review_revision=body.get('review_revision'),
            content_fingerprint=fingerprint, publish_target=body.get('publish_target'), report=report)
    except approval.ApprovalConflict as exc:
        operations.finish(operation_id, status=operations.FAILED, message=str(exc),
                          result={'suggestions': [value.isoformat() for value in exc.suggestions]})
    except asyncio.CancelledError:
        operations.finish(operation_id, status=operations.UNCERTAIN,
                          message='提交任务已中断，结果不明确；请核对发布回执，不要直接重试。')
        raise
    except (bs.PublishStepError, bs.ProbeRequired, compose.ComposeError, review.ReviewConflict,
            ValueError, TypeError) as exc:
        operations.finish(operation_id, status=operations.FAILED, message=str(exc))
    except Exception as exc:                          # noqa: BLE001
        # 点击意图已耐久记在发布账本里；未知结果一律停下等人工核对，不自动重试。
        operations.finish(operation_id, status=operations.UNCERTAIN,
                          message='提交结果不明确（%s）；请核对发布回执，不要直接重试。' % type(exc).__name__)
    else:
        operations.finish(operation_id, status=result.get('operation_status')
                          or (operations.SUCCEEDED if result['ok'] else operations.UNCERTAIN),
                          message=result['message'], result=result)


_running: set = set()


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
        target = business_time(body.get('scheduled_at'))
        state = review.state_for(source.account_dir, dict(source.row))
        if state['status'] != 'content_locked':
            raise review.ReviewConflict('请先确认内容无误并冻结，再选择发布时间')
        # 能当场判定的失败就当场返回：让人轮询几秒才看到「发布环境没录证」没有意义。
        ready = approval.options(source.account_dir, dict(source.row))
        if not ready['available']:
            raise review.ReviewConflict(ready['reason'])
        if not ready.get('preview'):
            raise review.ReviewConflict(ready.get('reason') or '冻结内容无法读取，请重新确认')
        try:
            checked = manual_run.load(source.row['platform'])
            manual_run.confirm_target(body.get('publish_target'), checked)
        except manual_run.ManualRunError as exc:
            raise review.ReviewConflict(str(exc)) from exc
        busy = operations.active()
        if busy is not None:
            # ⛔ 不排队：并发直接拒绝，见 FUNCTIONALITY F5-9。
            return JSONResponse({'detail': '有发布操作正在进行，请等它结束后再试。',
                                 'operation': busy}, status_code=409)
        record = operations.start(task_id, platform=source.row['platform'],
                                  snapshot_id=state['snapshot_id'], scheduled_at=target)
    except approval.ApprovalConflict as exc:
        return _conflict(exc)
    except (bs.PublishStepError, bs.ProbeRequired, compose.ComposeError, review.ReviewConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        task = maintenance.create_task('publish', _run, record['operation_id'],
                                       source.account_dir, dict(source.row), body, fingerprint)
    except maintenance.MaintenanceBlocked:
        operations.finish(record['operation_id'], status=operations.FAILED,
                          message='系统正在准备更新，本次尚未开始提交，请更新后重新确认。')
        raise
    # 留住引用：没有强引用的 task 可能在跑完之前被回收。
    _running.add(task)
    task.add_done_callback(_running.discard)
    return JSONResponse(record, status_code=202)


@router.get('/api/publish-operations/{operation_id}')
def get_operation(operation_id: str):
    try:
        record = operations.read(operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail='这次提交的记录不存在')
    return JSONResponse(record, headers={'Cache-Control': 'no-store'})

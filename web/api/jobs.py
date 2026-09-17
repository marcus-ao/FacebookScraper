"""人工单篇优化与只读模板接口；实际费用由内容执行器统一受理。"""
from __future__ import annotations
import re
import mimetypes
from urllib.parse import quote, urlencode
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from core import review, localization
from core.store import ArchivePathError, read_post_truth
from localize import images
from publish import compose
from pipeline import refinement, hashtag_suggestions, engine, initial_translation
from web.api import reader

router = APIRouter()


@router.post('/api/content-jobs/{job_id}/recover')
async def recover_content_job(job_id: str, request: Request):
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise review.ReviewValidationError('请提供要核对的任务版本')
    if not isinstance(body, dict) or not isinstance(body.get('expected_updated_at'), str):
        raise review.ReviewValidationError('缺少任务版本，请刷新后重试')
    return await run_in_threadpool(refinement.recover, job_id,
                                  expected_updated_at=body['expected_updated_at'])


def _source(task_id):
    source = reader.source_post(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return source


async def _body(request):
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise review.ReviewValidationError("请填写有效的优化请求")
    if not isinstance(body, dict):
        raise review.ReviewValidationError("优化请求须为对象")
    digest = body.get("source_text_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise review.ReviewValidationError("缺少有效源文版本，请刷新后重试")
    for key in ("human_revision", "review_revision"):
        value = body.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise review.ReviewValidationError("文案版本无效，请刷新后重试")
    return body


@router.get('/api/initial-translation/task/{task_id:path}')
def initial_capabilities(task_id: str):
    source = _source(task_id)
    return initial_translation.capabilities(source.account_dir, dict(source.row))


@router.post('/api/initial-translation/task/{task_id:path}')
async def translate_one(task_id: str, request: Request):
    body = await _body(request)
    if body.get('consent') is not True:
        raise review.ReviewValidationError('请确认允许处理这一篇第三方内容')
    fingerprint = body.get('source_fingerprint')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
        raise review.ReviewValidationError('原帖依据不完整，请刷新后重新确认')
    source = _source(task_id)
    job = initial_translation.submit(source.account_dir, dict(source.row),
        source_fingerprint=fingerprint, source_text_sha256=body['source_text_sha256'],
        human_revision=body.get('human_revision'), review_revision=body.get('review_revision'))
    return JSONResponse(job, status_code=202)


@router.get('/api/initial-translation/jobs/{job_id}')
def initial_result(job_id: str):
    job = initial_translation.job_result(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail='本篇处理任务不存在')
    return job


@router.get("/api/refinements/task/{task_id:path}")
def get_capabilities(task_id: str):
    source = _source(task_id)
    result = refinement.capabilities(source.account_dir, source.post_id, dict(source.row))
    result['max_image_count'] = None
    if source.platform == 'instagram':
        try:
            limits, _ = compose.verified_constraints_from_config(platform='instagram')
            result['max_image_count'] = limits.max_images
        except compose.ComposeError:
            pass
    for index, versions in result['image_versions'].items():
        for version in versions:
            version['preview_url'] = (
                '/api/image-versions/task/' + quote(task_id, safe='/') + '/preview?'
                + urlencode({'media_index': index, 'out_path': version['out_path']})) if version['available'] else None
    return result


@router.get("/api/image-versions/task/{task_id:path}/preview")
def preview_image_version(task_id: str, media_index: int, out_path: str):
    source = _source(task_id)
    truth, _ = read_post_truth(source.account_dir, dict(source.row))
    try:
        path = images.image_version_path(source.account_dir, truth, media_index, out_path)
        data = path.read_bytes()
    except (ValueError, OSError, ArchivePathError) as exc:
        raise HTTPException(status_code=404, detail="这一版图片不可用") from exc
    return Response(data, media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                    headers={'Cache-Control': 'no-store'})


@router.post("/api/image-versions/task/{task_id:path}")
async def select_image_version(task_id: str, request: Request):
    """换回某个历史版本。零模型调用、零费用，所以不走付费闸也不占优化次数。"""
    body = await _body(request)
    media_index = body.get("media_index")
    if not isinstance(media_index, int) or isinstance(media_index, bool) or media_index < 0:
        raise review.ReviewValidationError("请指定有效的图片序号")
    if not isinstance(body.get("out_path"), str) or not body["out_path"].strip():
        raise review.ReviewValidationError("请指定要采用的版本")
    source = _source(task_id)
    return await run_in_threadpool(
        refinement.select_version, source.account_dir, dict(source.row),
        media_index=media_index, out_path=body["out_path"].strip(),
        source_text_sha256=body["source_text_sha256"],
        review_revision=body.get("review_revision"))


@router.post("/api/refinements/task/{task_id:path}")
async def create_refinement(task_id: str, request: Request):
    body = await _body(request)
    if not isinstance(body.get("kind"), str) or body["kind"] not in refinement.KINDS:
        raise review.ReviewValidationError("请选择文案优化、图片优化或优化建议")
    source = _source(task_id)
    job = refinement.submit(source.account_dir, dict(source.row), kind=body["kind"],
        instruction=body.get("instruction"), source_text_sha256=body["source_text_sha256"],
        human_revision=body.get("human_revision"), review_revision=body.get("review_revision"),
        media_index=body.get("media_index"), body_de=body.get("body_de"))
    return JSONResponse(job, status_code=202)


@router.get("/api/refinements/jobs/{job_id}")
def get_refinement(job_id: str):
    job = refinement.job_result(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="优化任务不存在")
    if job["status"] == "succeeded" and job["kind"] == "text":
        return dict(job, body_de=localization.split_content(job["text_de"])["body"])
    return job


@router.get("/api/templates/{kind}")
def get_template(kind: str):
    names = {"text": "translate_de.md", "image": "image_de.md"}
    if kind not in names:
        raise HTTPException(status_code=404, detail="模板不存在")
    path = reader.ROOT / "prompts" / names[kind]
    return {"kind": kind, "content": path.read_text(encoding="utf-8"), "read_only": True}


@router.post("/api/hashtags/task/{task_id:path}")
async def suggest_hashtags(task_id: str, request: Request):
    body = await _body(request)
    source = _source(task_id)
    try:
        return await run_in_threadpool(hashtag_suggestions.suggest, source.account_dir, dict(source.row),
            source_text_sha256=body["source_text_sha256"], human_revision=body.get("human_revision"),
            review_revision=body.get("review_revision"))
    except (review.ReviewConflict, review.ReviewValidationError, engine.BudgetStopped):
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="标签建议暂未生成，请稍后重试或继续手工选择") from exc

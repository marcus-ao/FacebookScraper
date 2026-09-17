"""FastAPI 审校接口与 React 静态服务；写操作调用共享业务入口。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
# StaticFiles 抛父类异常，FastAPI 的子类捕获不到。
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import cfg                            # noqa: E402
from core.console import force_utf8                    # noqa: E402
from web.api import reader, writer, jobs, approval, calendar, settings, runtime, deployment         # noqa: E402
from core.maintenance import MaintenanceBlocked        # noqa: E402
from web.api.access import AccessMiddleware             # noqa: E402
from localize import images                            # noqa: E402
from core.store import ArchivePathError                # noqa: E402
from core import review, translated, localization                     # noqa: E402
from core.paid_model import FileLockBusy                 # noqa: E402
from pipeline.engine import BudgetStopped              # noqa: E402
from publish.compose import ComposeError               # noqa: E402

# 确保重定向日志使用 UTF-8。
force_utf8()

app = FastAPI(title="审校台", docs_url="/api/docs", redoc_url=None)
app.add_middleware(deployment.AdmissionMiddleware)
app.add_middleware(AccessMiddleware)
app.include_router(deployment.router)

app.include_router(jobs.router)
app.include_router(approval.router)
app.include_router(calendar.router)
app.include_router(settings.router)
app.include_router(runtime.router)

#: React 前端构建产物目录，相对于项目根目录。
DEFAULT_DIST_REL = "web/ui/dist"


def _dist_dir() -> Path:
    """解析 web_dist，仅允许项目内目录；越界回落默认值。"""
    raw = str(cfg().get("paths", "web_dist", DEFAULT_DIST_REL) or DEFAULT_DIST_REL).strip()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    if resolved == ROOT.resolve() or not resolved.is_relative_to(ROOT.resolve()):
        print("⚠ [paths].web_dist 指向仓库之外或仓库根，已回落 %s" % DEFAULT_DIST_REL)
        return (ROOT / DEFAULT_DIST_REL).resolve()
    return resolved


DIST = _dist_dir()


@app.exception_handler(ComposeError)
@app.exception_handler(ArchivePathError)
async def archive_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "源内容无法安全读取，请检查归档后重试"})


@app.exception_handler(MaintenanceBlocked)
async def maintenance_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={'code': 'maintenance', 'detail': str(exc)})


@app.exception_handler(BudgetStopped)
@app.exception_handler(localization.LocalizationConflict)
@app.exception_handler(review.ReviewConflict)
@app.exception_handler(translated.HumanRevisionConflict)
@app.exception_handler(FileLockBusy)
async def review_conflict(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(localization.LocalizationValidationError)
@app.exception_handler(review.ReviewValidationError)
async def review_validation(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# 查询与预览

@app.get("/api/tasks")
def get_tasks(status: str | None = None, tag: str | None = None, month: str | None = None,
              platform: str | None = Query(None, pattern='^(facebook|instagram)$'),
              scope: str = Query('review', pattern='^(review|history)$'),
              page: int = Query(1, ge=1), limit: int | None = Query(None, ge=1, le=100)) -> JSONResponse:
    """审校队列与历史分页；参数和响应契约见 web/DESIGN.md。"""
    payload = reader.list_tasks(status=status, tag=tag, month=month, platform=platform,
                                scope=scope, page=page, limit=limit)
    return JSONResponse(payload)


# 图片路由须先于贪婪的 task_id:path 注册。
@app.get("/api/tasks/{task_id:path}/image/{index}")
def get_task_image(task_id: str, index: int,
                   variant: str = Query("de", pattern="^(de|original)$")
                   ) -> Response:
    """直接读归档字节。``variant=de`` 缺德语图时回退原图（降级另有明示）。"""
    found = reader.image_bytes(task_id, index, variant)
    if found is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    data, media_type = found
    # 人工图和当前译文会变化，刷新时须重新读取，不能继续展示一天前的图。
    return Response(content=data, media_type=media_type,
                    headers={"Cache-Control": "no-cache"})


@app.get("/api/tasks/{task_id:path}")
def get_task(task_id: str) -> JSONResponse:
    """一条任务的完整详情。"""
    detail = reader.task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse(detail)


# 文案、审校、分类与导出

@app.post("/api/tasks/{task_id:path}/skip")
async def post_skip(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    return JSONResponse(writer.review_action(task_id, "skipped", **_review_input(body)))


@app.post("/api/tasks/{task_id:path}/review")
async def post_review(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    return JSONResponse(writer.review_action(task_id, body.get("action"), **_review_input(body)))


@app.post("/api/tasks/{task_id:path}/content-lock")
async def post_content_lock(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    fingerprint = body.get("content_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise review.ReviewValidationError("请重新载入本篇内容后确认")
    options = _review_input(body)
    return JSONResponse(writer.lock_content(
        task_id, source_text_sha256=options["source_text_sha256"],
        review_revision=options["review_revision"], content_fingerprint=fingerprint))


@app.delete("/api/tasks/{task_id:path}/content-lock")
async def delete_content_lock(task_id: str, request: Request) -> JSONResponse:
    options = _review_input(await _json_body(request))
    return JSONResponse(writer.unlock_content(
        task_id, source_text_sha256=options["source_text_sha256"],
        review_revision=options["review_revision"]))


@app.post("/api/tasks/{task_id:path}/export")
async def post_export(task_id: str, request: Request) -> Response:
    body = await _json_body(request)
    options = _review_input(body)
    # mode=download 只取素材，不转态；mode=handoff 才是"系统不再代发"这个终态决定。
    mode = body.get("mode", "handoff")
    if mode not in {"download", "handoff"}:
        raise HTTPException(status_code=400, detail="导出方式只能是 download 或 handoff")
    data, filename = writer.export_post(task_id,
        source_text_sha256=options["source_text_sha256"], review_revision=options["review_revision"],
        handoff_url=options["handoff_url"], handoff=mode == "handoff")
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "Cache-Control": "no-store",
    })


@app.post("/api/tasks/{task_id:path}/image/{index}/upload")
async def post_image_upload(task_id: str, index: int, request: Request) -> JSONResponse:
    """用业务自己的图片替换第 index 张德语图；记为 edited 后继续走系统发布。

    收 JSON + base64 而不是 multipart：后者要装 python-multipart，而本仓库对新依赖
    有明确约定，且 `decode_image_payload` 已经是经过验证的"不可信 base64 → 图片字节"入口。
    """
    body = await _json_body(request)
    digest = _source_digest(body)
    payload = body.get("image_base64")
    if not isinstance(payload, str) or not payload.strip():
        raise HTTPException(status_code=400, detail="请提供要替换的图片内容")
    # base64 比原字节大约 4/3，先按编码后长度挡住超大请求，再解码。
    if len(payload) > images.MAX_UPLOAD_BYTES // 3 * 4 + 1024:
        raise HTTPException(status_code=413, detail="上传的图片超过大小上限")
    try:
        data = images.decode_image_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="上传内容不是可识别的图片：%s" % exc) from exc
    revision = _state_revision(body)
    return JSONResponse(await run_in_threadpool(writer.replace_image,
        task_id, index, data, str(body.get("filename") or ""),
        source_text_sha256=digest,
        review_revision=revision))


@app.put("/api/tasks/{task_id:path}/tags")
async def put_tags(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    digest = _source_digest(body)
    revision = body.get("tags_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise HTTPException(status_code=400, detail="分类标签版本无效，请刷新后重试")
    return JSONResponse(writer.save_tags(task_id, body.get("tags"),
                         source_text_sha256=digest, tags_revision=revision))


@app.put("/api/tasks/{task_id:path}/localization")
async def put_localization(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    human_revision = body.get("human_revision")
    local_revision = body.get("localization_revision")
    for revision in (human_revision, local_revision):
        if revision is not None and (not isinstance(revision, str) or not revision.strip()):
            raise HTTPException(status_code=400, detail="文案版本无效，请重新打开这篇")
    source_version = _source_digest(body)
    return JSONResponse(writer.save_localization(task_id, body,
        source_text_sha256=source_version, human_revision=human_revision,
        review_revision=_state_revision(body), localization_revision=local_revision))


@app.put("/api/tasks/{task_id:path}/text_de")
async def put_text_de(task_id: str, request: Request) -> JSONResponse:
    """追加人工文案，并核对浏览器看到的源文与人工稿版本。"""
    body = await _json_body(request)
    text = body.get("text_de")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text_de 必须是非空字符串")
    digest = _source_digest(body)
    revision = body.get("human_revision")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise HTTPException(status_code=400, detail="人工文案版本无效，请重新打开这篇后保存")
    return JSONResponse(writer.save_text_de(
        task_id, text, source_text_sha256=digest, human_revision=revision,
        review_revision=_state_revision(body)))


# 即时检查，只算不写

@app.post("/api/tasks/{task_id:path}/check")
async def post_check(task_id: str, request: Request) -> JSONResponse:
    """返回德文即时检查结果，不保存内容；提示性差异不阻止人工保存。"""
    body = await _json_body(request)
    detail = reader.task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    text_de = body.get("text_de")
    if not isinstance(text_de, str):
        raise HTTPException(status_code=400, detail="text_de 必须是字符串")
    result = {"highlights": reader.build_highlights(
        detail["localization"]["source_body"] if body.get("body_only") else detail["text"]["en"], text_de)}
    if 'localization' in body:
        if not isinstance(body['localization'], dict):
            raise HTTPException(status_code=400, detail='localization 必须是完整分区草稿对象')
        draft = dict(detail['localization'], **localization.normalize_fields(body['localization']))
    elif body.get('body_only'):
        draft = dict(detail['localization'], body_de=text_de)
    else:
        return JSONResponse(dict(result, caption_length=len(text_de),
                                 hashtag_count=len(translated.extract_hashtags(text_de)), warnings=[], issues=[]))
    validation = localization.validate(draft)
    return JSONResponse(dict(result, caption=validation['caption'],
                             caption_length=validation['char_count'],
                             hashtag_count=validation['hashtag_count'], warnings=validation['warnings'],
                             issues=validation['issues']))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _source_digest(body: dict) -> str:
    digest = body.get("source_text_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(status_code=400, detail="缺少有效源文版本，请重新打开这篇后保存")
    return digest


def _state_revision(body: dict) -> str | None:
    revision = body.get("review_revision")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise HTTPException(status_code=400, detail="审校状态版本无效，请刷新后重试")
    return revision


def _review_input(body: dict) -> dict:
    source_version = _source_digest(body)
    return {"source_text_sha256": source_version, "review_revision": _state_revision(body),
            "reason": body.get("reason", ""), "wake_at": body.get("wake_at"),
            "handoff_url": body.get("handoff_url", "")}


# 静态文件

@app.get("/")
def index() -> Response:
    if not (DIST / "index.html").is_file():
        try:
            where = DIST.relative_to(ROOT).as_posix()
        except ValueError:                             # 理论上进不来：_dist_dir 已经挡住越界
            where = str(DIST)
        source = where[: -len("/dist")] if where.endswith("/dist") else where
        return Response(
            "前端尚未构建。请在项目根目录执行：\n"
            "  npm.cmd --prefix %s ci\n"
            "  npm.cmd --prefix %s run build\n"
            "构建完成后重新启动审校台。\n"
            "当前挂载点由 config.toml 的 [paths].web_dist 决定，默认 %s。\n"
            % (source, source, DEFAULT_DIST_REL),
            media_type="text/plain; charset=utf-8", status_code=503)
    return FileResponse(DIST / "index.html")


class SinglePageFiles(StaticFiles):
    """为 HTML 深链接返回 index.html；保留 API、资源、方法及路径边界错误。"""

    async def get_response(self, path: str, scope):
        # StaticFiles 以异常报告 404。
        try:
            response = await super().get_response(path, scope)
            if response.status_code != 404:
                return response
            missing: StarletteHTTPException | None = None
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response, missing = None, exc
        # 用请求 URL 判断 /api/，避免 Windows 规范化后的反斜杠。
        request_path = scope.get("path", "")
        if not request_path.startswith("/api/") and not Path(request_path).suffix:
            accept = dict(scope.get("headers") or []).get(b"accept", b"").decode("latin-1")
            if "text/html" in accept:
                return FileResponse(DIST / "index.html")
        if missing is not None:
            raise missing
        return response


if DIST.is_dir():
    # 无构建时保留首页提示，使服务仍可启动。
    app.mount("/", SinglePageFiles(directory=str(DIST), html=True), name="ui")

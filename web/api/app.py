r"""审校台 HTTP 接口：归档读取、人工文案保存与即时检查。

人工文案、本地化选择、审校状态与 ZIP 导出写入真实归档；排期需通过本机核验。
历史原型 _fake_state.json 不参与任何请求。前端静态产物由本应用直接伺服。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8                    # noqa: E402
from web.api import reader, writer, jobs, approval, calendar, settings, runtime                     # noqa: E402
from core.store import ArchivePathError                # noqa: E402
from core import review, translated, localization                     # noqa: E402
from core.paid_model import FileLockBusy                 # noqa: E402
from pipeline.engine import BudgetStopped              # noqa: E402
from publish.compose import ComposeError               # noqa: E402

# 每个可执行入口都要调一次：本机代码页是 936，uvicorn 的日志一旦被重定向到
# 文件就回落到 GBK，而告警文案里的 ⚠ ✅ ❌ ß 一个都编码不出来，print 会直接
# 把进程带走。双击运行时看不到这个故障，它只在真正需要可靠时发作。
force_utf8()

app = FastAPI(title="审校台", docs_url="/api/docs", redoc_url=None)

app.include_router(jobs.router)
app.include_router(approval.router)
app.include_router(calendar.router)
app.include_router(settings.router)
app.include_router(runtime.router)

DIST = ROOT / "web" / "ui" / "dist"


@app.exception_handler(ComposeError)
@app.exception_handler(ArchivePathError)
async def archive_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "源内容无法安全读取，请检查归档后重试"})


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


# ---------------------------------------------------------------------------
# 查询与预览
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def get_tasks(status: str | None = None, tag: str | None = None, month: str | None = None,
              platform: str | None = Query(None, pattern='^(facebook|instagram)$'),
              scope: str = Query('review', pattern='^(review|history)$'),
              page: int = Query(1, ge=1), limit: int | None = Query(None, ge=1, le=100)) -> JSONResponse:
    """审校队列与历史分页；参数和响应契约见 web/DESIGN.md。"""
    payload = reader.list_tasks(status=status, tag=tag, month=month, platform=platform,
                                scope=scope, page=page, limit=limit)
    return JSONResponse(payload)


# ⚠️ 顺序要紧：``{task_id:path}`` 是贪婪的，图片这条必须先注册，
# 否则 /api/tasks/<id>/image/0 会被上一条整个吞掉。
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


# ---------------------------------------------------------------------------
# 文案、审校状态、分类与导出；排期由独立 approval 路由调用生产流程
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id:path}/skip")
async def post_skip(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    return JSONResponse(writer.review_action(task_id, "skipped", **_review_input(body)))


@app.post("/api/tasks/{task_id:path}/review")
async def post_review(task_id: str, request: Request) -> JSONResponse:
    body = await _json_body(request)
    return JSONResponse(writer.review_action(task_id, body.get("action"), **_review_input(body)))


@app.post("/api/tasks/{task_id:path}/export")
async def post_export(task_id: str, request: Request) -> Response:
    body = await _json_body(request)
    options = _review_input(body)
    data, filename = writer.export_post(task_id,
        source_text_sha256=options["source_text_sha256"], review_revision=options["review_revision"],
        handoff_url=options["handoff_url"])
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "Cache-Control": "no-store",
    })


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


# ---------------------------------------------------------------------------
# 编辑时的实时校验（第 11.4 节）——**只算不写**，所以它属于只读那一半
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id:path}/check")
async def post_check(task_id: str, request: Request) -> JSONResponse:
    """编辑德语正文时实时跑 regex 校验，当场标红。

    ⛔ **保存时不拦。** 这几个检查是给人看的辅助，不是硬闸——她可能有正当理由
    改一个金额（比如原文写错了）。硬拦人工修改等于说"机器比人懂"，
    而这个系统的既定原则正好相反（第 11.4 节）。所以本端点只回结果，
    没有任何一条路径会因为校验不过而拒绝保存。
    """
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
    return JSONResponse(dict(result, caption_length=validation['char_count'],
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


# ---------------------------------------------------------------------------
# 前端静态产物
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> Response:
    if not (DIST / "index.html").is_file():
        return Response(
            "前端还没构建。在开发机上跑：\n"
            "  cd web/ui && npm install && npm run build\n"
            "然后把 web/ui/dist/ 拷到生产机即可（生产机不需要 Node）。\n",
            media_type="text/plain; charset=utf-8", status_code=503)
    return FileResponse(DIST / "index.html")


if DIST.is_dir():
    # 构建产物存在才挂载；不存在时上面那条 / 会给出可操作的提示，
    # 而不是启动时就崩掉。
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="ui")

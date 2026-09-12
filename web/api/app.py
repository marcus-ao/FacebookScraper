r"""审校台 HTTP 接口：归档读取、人工文案保存与即时检查。

人工文案追加到 translated_human.jsonl。通过与不发尚未接通，明确返回 501；
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
from web.api import reader, writer                     # noqa: E402
from core.store import ArchivePathError                # noqa: E402
from publish.compose import ComposeError               # noqa: E402

# 每个可执行入口都要调一次：本机代码页是 936，uvicorn 的日志一旦被重定向到
# 文件就回落到 GBK，而告警文案里的 ⚠ ✅ ❌ ß 一个都编码不出来，print 会直接
# 把进程带走。双击运行时看不到这个故障，它只在真正需要可靠时发作。
force_utf8()

app = FastAPI(title="审校台", docs_url="/api/docs", redoc_url=None)

DIST = ROOT / "web" / "ui" / "dist"


@app.exception_handler(ComposeError)
@app.exception_handler(ArchivePathError)
async def archive_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "源内容无法安全读取，请检查归档后重试"})


# ---------------------------------------------------------------------------
# 只读端（真）
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def get_tasks() -> JSONResponse:
    """任务列表。契约见 PROTOTYPE_DESIGN.md 第 6 节。"""
    payload = reader.list_tasks()
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
# 文案保存已接通；审校状态与排期保留明确的未实现响应
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id:path}/approve")
async def post_approve(task_id: str, request: Request) -> JSONResponse:
    """真实排期尚未接通，不能把原型点击冒充发布成功。"""
    raise HTTPException(status_code=501, detail="审校通过与排期尚未接通，当前仅支持保存人工文案")


@app.post("/api/tasks/{task_id:path}/skip")
async def post_skip(task_id: str, request: Request) -> JSONResponse:
    """审校状态流转将由后续 review_items 真相源实现。"""
    raise HTTPException(status_code=501, detail="不发与挂起等审校状态尚未接通，当前仅支持保存人工文案")


@app.put("/api/tasks/{task_id:path}/text_de")
async def put_text_de(task_id: str, request: Request) -> JSONResponse:
    """追加人工文案，并核对浏览器看到的源文与人工稿版本。"""
    body = await _json_body(request)
    text = body.get("text_de")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text_de 必须是非空字符串")
    digest = body.get("source_text_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(status_code=400, detail="缺少有效源文版本，请重新打开这篇后保存")
    revision = body.get("human_revision")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise HTTPException(status_code=400, detail="人工文案版本无效，请重新打开这篇后保存")
    return JSONResponse(writer.save_text_de(
        task_id, text, source_text_sha256=digest, human_revision=revision))


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
    return JSONResponse(
        {"highlights": reader.build_highlights(detail["text"]["en"], text_de)})


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        return {}
    return body if isinstance(body, dict) else {}


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

r"""审校台的 FastAPI 应用与路由注册。

**读全真，写全假**（web/DESIGN.md 第 2 节）。这份文件本身很薄，
它的全部职责是把请求分给两个来源：

    GET  /api/tasks                  ─┐
    GET  /api/tasks/<id>              ├─ reader.py   真 —— 只读归档，生产代码
    GET  /api/tasks/<id>/image/<n>   ─┘

    POST /api/tasks/<id>/approve     ─┐
    POST /api/tasks/<id>/skip         ├─ fake_writer.py  假 —— 只写 web/_fake_state.json
    PUT  /api/tasks/<id>/text_de     ─┘

**切换到生产 = 换掉 fake_writer 那三个 handler，前端一行不用改。**

为什么选 FastAPI 而不是 stdlib ``http.server``：原型只读，``http.server``
够用，但 M2 要多用户并发 + 写入 + 登录，那时必须换掉——等于把切换成本推到
最需要稳定的时刻。现在选定，从原型到 M2 一次不用换（第 3 节）。

### 跑起来

    .venv\Scripts\python.exe -m uvicorn web.api.app:app --port 8765

前端构建产物 ``web/ui/dist/`` 存在时由本应用直接伺服，所以**生产机不需要
Node、不需要 npm install、断网也能跑**。
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8                    # noqa: E402
from web.api import fake_writer, reader                # noqa: E402

# 每个可执行入口都要调一次：本机代码页是 936，uvicorn 的日志一旦被重定向到
# 文件就回落到 GBK，而告警文案里的 ⚠ ✅ ❌ ß 一个都编码不出来，print 会直接
# 把进程带走。双击运行时看不到这个故障，它只在真正需要可靠时发作。
force_utf8()

app = FastAPI(title="审校台原型", docs_url="/api/docs", redoc_url=None)

DIST = ROOT / "web" / "ui" / "dist"


# ---------------------------------------------------------------------------
# 只读端（真）
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def get_tasks() -> JSONResponse:
    """任务列表。契约见 PROTOTYPE_DESIGN.md 第 6 节。"""
    payload = reader.list_tasks()
    fake_writer.overlay_list(payload)
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
    # 归档是不可变内容，缓存一天；德语图换了之后 URL 不变，所以只给一天，
    # 不给 immutable。
    return Response(content=data, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/tasks/{task_id:path}")
def get_task(task_id: str) -> JSONResponse:
    """一条任务的完整详情。"""
    detail = reader.task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    fake_writer.overlay_detail(detail)
    return JSONResponse(detail)


# ---------------------------------------------------------------------------
# 假写入端（假）—— 切换到生产时只换这三个
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id:path}/approve")
async def post_approve(task_id: str, request: Request) -> JSONResponse:
    """原型：只写 web/_fake_state.json。生产：调 pipeline 的批准路径。"""
    body = await _json_body(request)
    return JSONResponse(_guard(task_id, lambda: fake_writer.approve(
        task_id, actor=_actor(body))))


@app.post("/api/tasks/{task_id:path}/skip")
async def post_skip(task_id: str, request: Request) -> JSONResponse:
    """原型：只写 web/_fake_state.json。生产：写 needs_human.jsonl 的终态。"""
    body = await _json_body(request)
    return JSONResponse(_guard(task_id, lambda: fake_writer.skip(
        task_id, actor=_actor(body), reason=str(body.get("reason") or ""))))


@app.put("/api/tasks/{task_id:path}/text_de")
async def put_text_de(task_id: str, request: Request) -> JSONResponse:
    """原型：只写 web/_fake_state.json。生产：追加 translated_human.jsonl。"""
    body = await _json_body(request)
    text = body.get("text_de")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text_de 必须是非空字符串")
    return JSONResponse(_guard(task_id, lambda: fake_writer.save_text_de(
        task_id, text, actor=_actor(body))))


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


def _actor(body: dict) -> str:
    """留痕的 actor（REQUIREMENTS.md 第 4.3 节）。

    原型没有登录（第 14 节：登录与权限是 M2 的事），所以 actor 由前端带上来，
    默认一个占位。**字段位置和生产一致**，M2 接上登录之后换的是取值来源，
    不是 JSON 形状。
    """
    value = body.get("actor")
    return value.strip() if isinstance(value, str) and value.strip() else "原型用户"


def _guard(task_id: str, action):
    """写之前先确认这个任务真实存在，避免假状态里长出归档里没有的 id。"""
    if reader.task_detail(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return action()


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

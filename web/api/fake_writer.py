r"""审校台的**假写入端**。

⛔ **这是一次性代码。** web/DESIGN.md 第 2 节把原型切成两半：
``reader.py`` 那半从第一天起就是生产代码，本文件这半随时可扔。切换到生产时
**只替换本文件**，``reader.py`` 和整个前端一行都不用改。

**本文件只写一个地方：``web/_fake_state.json``。**
它绝不碰 ``archive/`` 与 ``state/`` 下的任何真相源——那三个文件
（``translated.jsonl`` / ``needs_human.jsonl`` / ``published.jsonl``）是追加式
生产数据，而原型会被反复打开、点烂、改十几版。

### 切换到生产 = 换掉这三个函数

=================  ==================================  ==========================
函数                原型                                生产
=================  ==================================  ==========================
:func:`approve`     写 ``web/_fake_state.json``         调 ``pipeline`` 的批准路径
:func:`skip`        同上                                写 ``needs_human.jsonl`` 的终态
:func:`save_text_de` 同上                               追加 ``translated_human.jsonl``
=================  ==================================  ==========================

### ⛔ 一条不许破的规则

> **假实现必须返回和真实现完全相同的 JSON 结构。**

少一个字段、多一层嵌套，切换到生产那天前端就得跟着改——"立马便捷切换"
这个目标就没了。所以三个写入函数**一律返回一份完整的任务详情**，
和 ``GET /api/tasks/<id>`` 逐字段同构：不是"够前端用就行"的子集，
而是同一个形状。真实现照着返回同一份即可，前端不需要分辨。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import paid_model                            # noqa: E402
from web.api import reader                             # noqa: E402

#: 原型自己的状态。**唯一被写的文件**，在 .gitignore 里。
STATE_PATH = ROOT / "web" / "_fake_state.json"

#: 业务视角状态里由"人做过什么"决定的那三个（第 7 节）。
#: ``scheduled`` 不在这里——它是 ``state/published.jsonl`` 的真实事实，
#: 假状态不许盖掉它。
STATUS_EDITED = "edited"
STATUS_APPROVED = "approved"
STATUS_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    """读原型状态；缺文件或坏格式一律当空，不让界面挂掉。"""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": 1, "tasks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        return {"schema_version": 1, "tasks": {}}
    return data


def _save(state: dict) -> None:
    """原子写。复用 core.paid_model 那份（临时文件 → fsync → replace）。

    原型的状态丢了无所谓，但**写坏了**会让下一次打开界面就崩——
    演示当场最不想遇到的事。既然仓库里已经有一份写对了的原子写，就用它。
    """
    paid_model.atomic_write_json(STATE_PATH, state, indent=2, sort_keys=True)


def _entry(state: dict, task_id: str) -> dict:
    return state["tasks"].setdefault(
        task_id, {"status": None, "text_de_human": None, "trail": []})


def _record(task_id: str, *, action: str, actor: str,
            status: str | None = None, text_de: str | None = None,
            note: str = "") -> dict:
    """落一次操作，返回写完之后的完整任务详情。"""
    state = load_state()
    entry = _entry(state, task_id)
    if status is not None:
        entry["status"] = status
    if text_de is not None:
        entry["text_de_human"] = text_de
    trail = entry.setdefault("trail", [])
    row = {"at": _now(), "actor": actor, "action": action}
    if note:
        row["note"] = note
    trail.append(row)
    _save(state)

    detail = reader.task_detail(task_id)
    if detail is None:                                 # 调用方已经 guard 过
        raise ValueError("任务不存在：%s" % task_id)
    overlay_detail(detail)
    return detail


# ---------------------------------------------------------------------------
# 三个写入动作
# ---------------------------------------------------------------------------

def approve(task_id: str, *, actor: str) -> dict:
    """通过。原型只落状态；生产要调 ``pipeline`` 的批准路径。

    ⚠️ **演示前必须说明：原型上点的"通过"不会真的发帖**（第 13 节）。
    否则她会以为已经排了。
    """
    return _record(task_id, action="approved", actor=actor,
                   status=STATUS_APPROVED)


def skip(task_id: str, *, actor: str, reason: str = "") -> dict:
    """这篇不发。**这是决定，不是退回**——不需要谁来接手（第 7 节）。

    这个动作是原型最想套出答案的那个（第 13 节）：「什么情况下我会点这个？」
    直接拿到内容取舍规则，而且是她自己带场景说出来的。所以 ``reason``
    照原样留痕，一个字都不改写。
    """
    return _record(task_id, action="skipped", actor=actor,
                   status=STATUS_SKIPPED, note=reason)


def save_text_de(task_id: str, text_de: str, *, actor: str) -> dict:
    """保存人工版德语正文。生产要追加 ``translated_human.jsonl``。

    ⛔ **保存时不拦。** 实时校验（``POST /api/tasks/<id>/check``）标红只是
    给人看的辅助，不是硬闸——她可能有正当理由改一个金额（比如原文写错了）。
    硬拦人工修改等于说"机器比人懂"，而这个系统的既定原则正好相反（第 11.4 节）。
    所以这里没有任何一条基于校验结果的拒绝路径。
    """
    return _record(task_id, action="text_edited", actor=actor,
                   status=STATUS_EDITED, text_de=text_de)


# ---------------------------------------------------------------------------
# 叠加到只读结果上
# ---------------------------------------------------------------------------

def _apply_status(real_status: str, fake_status: str | None) -> str:
    """假状态盖真状态，但 ``scheduled`` 除外。

    ``scheduled`` 来自 ``state/published.jsonl``——那是系统真的排过期了。
    让一次原型点击把它盖掉，等于用假数据覆盖真事实，正是第 2 节要避免的。
    """
    if real_status == reader.STATUS_SCHEDULED or not fake_status:
        return real_status
    return fake_status


def overlay_list(payload: dict) -> None:
    """就地把假状态叠到列表上。"""
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return
    state = load_state()["tasks"]
    for task in tasks:
        entry = state.get(task.get("id"))
        if not isinstance(entry, dict):
            continue
        task["status"] = _apply_status(task["status"], entry.get("status"))
        human = entry.get("text_de_human")
        if isinstance(human, str) and human.strip():
            # 人工版优先渲染（第 9 节：真相源是 translated_human.jsonl，
            # 机器译文只是它的下位）。
            task["text_de_excerpt"] = reader.excerpt(human)


def overlay_detail(detail: dict) -> None:
    """就地把假状态叠到详情上，并**按人工版重算标记**。

    重算是必须的：她改完一句德语，红标记还停在旧位置的话，
    「下一处 →」就会跳到不存在的地方——比不标还糟。
    """
    entry = load_state()["tasks"].get(detail.get("id"))
    if not isinstance(entry, dict):
        return
    detail["status"] = _apply_status(detail["status"], entry.get("status"))
    trail = entry.get("trail")
    if isinstance(trail, list):
        detail["trail"] = list(trail)
    human = entry.get("text_de_human")
    if isinstance(human, str) and human.strip():
        detail["text"]["de_human"] = human
        detail["highlights"] = reader.build_highlights(
            detail["text"]["en"], human)

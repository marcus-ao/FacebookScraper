"""审校台的真实人工文案保存；付费处理与发布不从这里触发。"""
from __future__ import annotations

from fastapi import HTTPException

from core import translated
from core.paid_model import FileLock, FileLockBusy
from core.store import ArchivePathError, assert_physical_direct_path
from publish.compose import ComposeError
from web.api import reader


def save_text_de(task_id: str, text_de: str, *, source_text_sha256: str,
                 human_revision: str | None) -> dict:
    try:
        source = reader.source_post(task_id)
        if source is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        account_dir = source.account_dir
        lock_path = assert_physical_direct_path(
            account_dir, account_dir / "review_write.lock",
            kind="file", label="人工文案保存锁")
        with FileLock(lock_path, busy_message="文案正在保存，请稍后重试"):
            # 拿锁后再取真相；同一账号的多个浏览器标签页不能互相覆盖。
            source = reader.source_post(task_id)
            if source is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            if translated.source_text_sha256(source.text) != source_text_sha256:
                raise HTTPException(
                    status_code=409, detail="源帖已更新，请载入最新内容后复核；当前修改仍保留在编辑框中")
            path = account_dir / "translated_human.jsonl"
            previous = translated.load_human_translated(path).get(source.post_id)
            current_revision = previous["revision"] if previous else None
            if current_revision != human_revision:
                raise HTTPException(
                    status_code=409, detail="另一页面已保存了新版文案，请载入最新内容后核对；当前修改仍保留")
            translated.append_human_translation(
                path, dict(source.row), text_de, expected_revision=human_revision)
        detail = reader.task_detail(task_id)
        if detail is None:
            raise HTTPException(status_code=409, detail="文案已保存，任务已离开当前列表，请刷新查看")
        return detail
    except FileLockBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except translated.HumanRevisionConflict as exc:
        raise HTTPException(
            status_code=409, detail="人工文案刚刚更新，请载入最新内容后核对；当前修改仍保留") from exc
    except (ArchivePathError, ComposeError, OSError) as exc:
        raise HTTPException(status_code=409, detail="归档暂时无法安全读写，请重试或检查归档") from exc

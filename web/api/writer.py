"""审校台的真实人工文案保存；付费处理与发布不从这里触发。"""
from __future__ import annotations

from fastapi import HTTPException

from core import review, store, translated, localization
from core.config import cfg
from localize import images
from pipeline import approval
from web.api import exporter, reader


def _source(task_id: str):
    source = reader.source_post(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if source.account_dir.name not in cfg().active_accounts():
        raise review.ReviewConflict('此账号已冻结，历史归档只读')
    return source


def _detail(task_id: str) -> dict:
    detail = reader.task_detail(task_id)
    if detail is None:
        raise HTTPException(status_code=409, detail="操作已保存，任务已离开当前列表，请刷新查看")
    return detail


def save_text_de(task_id: str, text_de: str, *, source_text_sha256: str,
                 human_revision: str | None, review_revision: str | None = None) -> dict:
    source = _source(task_id)
    with review.transaction(source.account_dir) as session:
        truth, state = session.validate(
            dict(source.row), expected_revision=review_revision,
            expected_source_sha256=source_text_sha256, scheduled=reader.has_schedule(source))
        if state["status"] in review.TERMINAL | review.LOCKED:
            raise review.ReviewConflict(
                "这篇的内容已冻结；要改动请先解除冻结"
                if state["status"] == "content_locked"
                else "这篇已结束审校或正在提交，不能直接修改文案")
        translated.append_human_translation(
            source.account_dir / "translated_human.jsonl", truth, text_de,
            expected_revision=human_revision)
        session.change(truth, "edited", expected_revision=review_revision,
                       expected_source_sha256=source_text_sha256)
    return _detail(task_id)


def lock_content(task_id: str, *, source_text_sha256: str, review_revision: str | None,
                 content_fingerprint: str) -> dict:
    source = _source(task_id)
    approval.lock(source.account_dir, dict(source.row), source_text_sha256=source_text_sha256,
                  review_revision=review_revision, content_fingerprint=content_fingerprint)
    return _detail(task_id)


def unlock_content(task_id: str, *, source_text_sha256: str, review_revision: str | None) -> dict:
    source = _source(task_id)
    approval.unlock(source.account_dir, dict(source.row),
                    source_text_sha256=source_text_sha256, review_revision=review_revision)
    return _detail(task_id)


def review_action(task_id: str, action: str, *, source_text_sha256: str,
                   review_revision: str | None, reason: str = "", wake_at=None,
                   handoff_url: str = "") -> dict:
    if not isinstance(action, str) or action not in {"snoozed", "woke", "skipped", "handed_off", "handoff_link"}:
        raise review.ReviewValidationError("此操作尚未开放")
    source = _source(task_id)
    with review.transaction(source.account_dir) as session:
        session.change(
            dict(source.row), action, expected_revision=review_revision,
            expected_source_sha256=source_text_sha256, reason=reason, wake_at=wake_at,
            handoff_url=handoff_url, scheduled=reader.has_schedule(source),
            snooze_days=cfg().get("review", "snooze_default_days", 3))
    return _detail(task_id)


def export_post(task_id: str, *, source_text_sha256: str,
                 review_revision: str | None, handoff_url: str = "",
                 handoff: bool = True) -> tuple[bytes, str]:
    """打包本篇素材。

    `handoff=False` 只下载，**不改状态**——业务要把某张图拿去自己改再传回来，
    这篇仍然要留在系统里继续走排期。`handed_off` 是终态，代表"系统不再代发"，
    两件事不是同一个决定，所以不能由同一个按钮顺带完成。
    """
    source = _source(task_id)
    with review.transaction(source.account_dir) as session:
        truth, state = session.validate(
            dict(source.row), expected_revision=review_revision,
            expected_source_sha256=source_text_sha256, scheduled=reader.has_schedule(source))
        if handoff and state["status"] in {"scheduled", "approved", "skipped"}:
            raise review.ReviewConflict("这篇已排期、提交中或决定不发，不能直接转交人工")
        try:
            package = exporter.package_post(source.account_dir, truth, handoff=handoff)
        except (OSError, store.ArchivePathError, ValueError) as exc:
            raise review.ReviewConflict("资源包未准备成功：%s" % exc) from exc
        # 只有完整资源包准备成功才记录接管；已接管帖子允许重新下载，不重复转态。
        if handoff and state["status"] != "handed_off":
            session.change(truth, "handed_off", expected_revision=review_revision,
                           expected_source_sha256=source_text_sha256, handoff_url=handoff_url)
        else:
            session.validate(truth, expected_revision=review_revision,
                             expected_source_sha256=source_text_sha256)
        return package


def replace_image(task_id: str, index: int, data: bytes, filename: str, *,
                  source_text_sha256: str, review_revision: str | None) -> dict:
    """用业务自己的图片替换第 index 张德语图，记为 edited 后继续走系统发布。"""
    source = _source(task_id)
    with images.FileLock(cfg().state_dir / "images.lock",
            busy_message="图片正在生成，请完成后再替换"), review.transaction(source.account_dir) as session:
        truth, state = session.validate(
            dict(source.row), expected_revision=review_revision,
            expected_source_sha256=source_text_sha256, scheduled=reader.has_schedule(source))
        if state["status"] in review.TERMINAL | review.LOCKED:
            raise review.ReviewConflict("这篇已冻结内容、结束审校或正在提交，不能替换图片")
        try:
            images.replace_localized(source.account_dir, truth, index, data, filename)
        except store.ArchivePathError:
            raise
        except (OSError, ValueError) as exc:
            # 上传内容的问题是人能当场改的，给 400 和原因，不要报成服务器故障。
            raise review.ReviewValidationError("替换图片未保存：%s" % exc) from exc
        session.change(truth, "edited", expected_revision=review_revision,
                       expected_source_sha256=source_text_sha256)
    return _detail(task_id)


def save_tags(task_id: str, tags: list[str], *, source_text_sha256: str,
               tags_revision: str) -> dict:
    if (not isinstance(tags, list) or len(tags) > 30
            or any(not isinstance(tag, str) or len(tag.strip()) > 100 for tag in tags)):
        raise review.ReviewValidationError("分类标签须为文字列表，最多 30 个，每个不超过 100 字")
    source = _source(task_id)
    with review.transaction(source.account_dir):
        truth, _ = store.read_post_truth(source.account_dir, dict(source.row))
        current = list(truth.get("tags") or [])
        if reader.tags_revision(current) != tags_revision:
            raise review.ReviewConflict("分类标签已有更新，请刷新后重试")
        store.update_post_tags(source.account_dir, truth, tags, expected_tags=current,
                               expected_source_sha256=source_text_sha256)
    return _detail(task_id)


def save_localization(task_id: str, fields: dict, *, source_text_sha256: str,
                      human_revision: str | None, review_revision: str | None,
                      localization_revision: str | None) -> dict:
    source = _source(task_id)
    clean = localization.normalize_fields(fields)
    with review.transaction(source.account_dir) as session:
        truth, state = session.validate(dict(source.row), expected_revision=review_revision,
            expected_source_sha256=source_text_sha256, scheduled=reader.has_schedule(source))
        if state["status"] in review.TERMINAL | review.LOCKED:
            raise review.ReviewConflict(
                "这篇的内容已冻结；要改动请先解除冻结"
                if state["status"] == "content_locked"
                else "这篇已结束审校或正在提交，不能直接修改文案")
        human = translated.load_human_translated(source.account_dir / "translated_human.jsonl").get(source.post_id)
        machine = translated.load_translated(source.account_dir / "translated.jsonl").get(source.post_id)
        effective = translated.effective_translation(truth, machine, human)
        draft = localization.effective_draft(source.account_dir, truth, effective)
        if draft["revision"] != localization_revision:
            raise localization.LocalizationConflict("本地化选择已有更新，请载入最新内容后再保存")
        if [item["source_url"] for item in clean["links"]] != [item["source_url"] for item in draft["links"]]:
            raise localization.LocalizationValidationError("原帖链接不应删除或替换，请在对应行填写德语落地页")
        draft.update(clean, source_stale=False)
        result = localization.validate(draft)
        invalid = [item["message"] for item in result["issues"] if item["code"] in {
            "body_missing", "body_urls", "body_hashtags", "invalid_link", "invalid_cta", "protected_tags_changed"}]
        if invalid:
            raise localization.LocalizationValidationError("；".join(invalid))
        human = translated.append_human_translation(source.account_dir / "translated_human.jsonl",
            truth, localization.render(draft), expected_revision=human_revision)
        localization.append_localization(source.account_dir, truth, draft,
            human_revision=human["revision"], expected_revision=localization_revision,
            expected_source_sha256=source_text_sha256)
        session.change(truth, "edited", expected_revision=review_revision,
                       expected_source_sha256=source_text_sha256)
    return _detail(task_id)

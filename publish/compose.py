"""发布前离线硬闸（G0b）。

本模块只读归档，且在碰浏览器之前完成译文、金额、媒体与排期契约校验。
Business Suite 没有事务性；越早失败，就越不可能留下半成品草稿。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from PIL import Image, UnidentifiedImageError

from core.config import cfg
from core.store import (Archive, ArchivePathError, assert_physical_direct_path,
                        post_dirname)
from translate import (PROMPT_VERSION, account_dirs, extract_hashtags,
                       load_translated, money_preserved,
                       translation_is_current)

Platform = Literal["facebook", "instagram"]
CaptionLengthMode = Literal["codepoints", "utf16_units", "utf8_bytes"]
WarningSink = Callable[[str], None]


class ComposeError(ValueError):
    """待发帖未通过离线硬闸。"""


@dataclass(frozen=True)
class InstagramConstraints:
    """G1 实测后注入的 IG UI 限制；本类本身不携带任何猜测数字。

    ``probe_dump`` 必须点名产生这些值的真实 dump。字段允许为 ``None``，表示
    那一项尚未测出；发布入口可以用 ``require_verified_ui_constraints=True``
    在任何浏览器操作前失败闭合。
    """

    probe_dump: str
    min_aspect_ratio: float | None = None
    max_aspect_ratio: float | None = None
    max_images: int | None = None
    max_caption_length: int | None = None
    caption_length_mode: CaptionLengthMode | None = None
    max_hashtags: int | None = None

    def __post_init__(self) -> None:
        if not self.probe_dump.strip():
            raise ValueError("IG 约束必须写明来自哪份 G1 probe dump")
        if (self.min_aspect_ratio is not None
                and self.min_aspect_ratio <= 0):
            raise ValueError("min_aspect_ratio 必须大于 0")
        if (self.max_aspect_ratio is not None
                and self.max_aspect_ratio <= 0):
            raise ValueError("max_aspect_ratio 必须大于 0")
        if (self.min_aspect_ratio is not None
                and self.max_aspect_ratio is not None
                and self.min_aspect_ratio > self.max_aspect_ratio):
            raise ValueError("IG 最小画幅比不得大于最大画幅比")
        for label, value in (("max_images", self.max_images),
                             ("max_caption_length", self.max_caption_length),
                             ("max_hashtags", self.max_hashtags)):
            if value is not None and value < 0:
                raise ValueError("%s 不得为负数" % label)
        if ((self.max_caption_length is None)
                != (self.caption_length_mode is None)):
            raise ValueError("正文上限与 UI 的字符计数方式必须一起提供")

    def complete(self) -> bool:
        """G0b 点名的四类约束是否都已有真实值。"""
        return all(value is not None for value in (
            self.min_aspect_ratio,
            self.max_aspect_ratio,
            self.max_images,
            self.max_caption_length,
            self.caption_length_mode,
            self.max_hashtags,
        ))


@dataclass(frozen=True)
class ScheduleWindow:
    """G1 实测的 Business Suite UI 定时窗口。"""

    probe_dump: str
    min_ahead: timedelta
    max_ahead: timedelta

    def __post_init__(self) -> None:
        if not self.probe_dump.strip():
            raise ValueError("定时窗口必须写明来自哪份 G1 probe dump")
        if self.min_ahead < timedelta(0):
            raise ValueError("定时窗口下限不得为负数")
        if self.max_ahead < self.min_ahead:
            raise ValueError("定时窗口上限不得早于下限")


@dataclass(frozen=True)
class DePost:
    """一篇已通过当前可用离线硬闸的德语图文帖。"""

    post_id: str
    platform: Platform
    account: str
    source_text: str
    text_de: str
    scheduled_at: datetime
    image_paths: tuple[Path, ...]
    image_sources: tuple[Literal["media_de", "original"], ...]
    prompt_version: int
    source_author: str | None
    source_author_name: str | None
    warnings: tuple[str, ...]
    post_dir: Path

    @property
    def is_collaboration(self) -> bool:
        return bool(self.source_author
                    and self.source_author.lower() != self.account.lower())

    def review_lines(self) -> tuple[str, ...]:
        """提交前必须展示的信息；合作帖明确点名原作者。"""
        lines = [
            "帖子：%s / %s" % (self.platform, self.post_id),
            "排期：%s" % self.scheduled_at.isoformat(),
            "图片：%d 张（%s）" % (
                len(self.image_paths), "、".join(self.image_sources)),
        ]
        if self.is_collaboration:
            who = self.source_author_name or self.source_author or "未知"
            lines.append("合作帖原作者：%s（提交前确认二次使用授权）" % who)
        lines.extend("警告：%s" % item for item in self.warnings)
        return tuple(lines)


def _fail(post_id: str, message: str) -> ComposeError:
    return ComposeError("帖子 %s：%s" % (post_id, message))


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime):
        raise ComposeError("%s 必须是 datetime" % label)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ComposeError("%s 必须显式带时区；不得依赖本机时区" % label)


def _text_length(text: str, mode: CaptionLengthMode) -> int:
    if mode == "codepoints":
        return len(text)
    if mode == "utf16_units":
        return len(text.encode("utf-16-le")) // 2
    if mode == "utf8_bytes":
        return len(text.encode("utf-8"))
    raise ValueError("未知正文计数方式：%s" % mode)


def _relative_archive_path(account_dir: Path, raw: str, *,
                           post_id: str) -> Path:
    """把归档里的跨平台相对路径还原，并拒绝越出帖子目录的路径。"""
    value = (raw or "").replace("\\", "/")
    rel = PurePosixPath(value)
    if not value or rel.is_absolute() or ".." in rel.parts:
        raise _fail(post_id, "图片 local_path 不是安全的归档相对路径：%r" % raw)
    return account_dir.joinpath(*rel.parts)


def _read_post_truth(arc: Archive, row: dict) -> tuple[dict, Path]:
    post_id = str(row.get("post_id") or "")
    post_dir = arc.posts_dir / post_dirname(post_id, row.get("created_at"))
    try:
        assert_physical_direct_path(
            arc.posts_dir, post_dir, kind="directory", label="待发布帖子目录")
    except ArchivePathError as exc:
        raise _fail(post_id, str(exc)) from exc
    if not post_dir.is_dir():
        raise _fail(post_id, "帖子目录不存在：%s" % post_dir)

    post_json = post_dir / "post.json"
    try:
        assert_physical_direct_path(
            post_dir, post_json, kind="file", label="待发布 post.json")
        source = json.loads(post_json.read_text(encoding="utf-8"))
    except (ArchivePathError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _fail(post_id, "post.json 无法安全读取：%s" % exc) from exc
    if not isinstance(source, dict):
        raise _fail(post_id, "post.json 顶层不是对象")
    if source.get("post_id") != post_id:
        raise _fail(post_id, "post.json 的 post_id 与 manifest 不一致")
    return source, post_dir


def _find_source(archive_root: Path, post_id: str, *,
                 account: str | None, platform: Platform | None
                 ) -> tuple[Archive, dict]:
    if not isinstance(post_id, str) or not post_id.strip():
        raise ComposeError("post_id 必须是非空字符串")
    try:
        assert_physical_direct_path(
            archive_root.parent, archive_root,
            kind="directory", label="archive 根目录")
    except ArchivePathError as exc:
        raise _fail(post_id, "archive 根目录不安全：%s" % exc) from exc
    if not archive_root.is_dir():
        raise _fail(post_id, "archive 根目录不存在：%s" % archive_root)

    candidates = []
    if account:
        account_dir = archive_root / account
        if account_dir.parent != archive_root or account_dir.name != account:
            raise _fail(post_id, "account 必须是 archive/ 下的单个账号目录名")
        dirs = [account_dir]
    else:
        dirs = account_dirs(archive_root)
    for account_dir in dirs:
        try:
            assert_physical_direct_path(
                archive_root, account_dir, kind="directory", label="账号归档目录")
            posts_dir = account_dir / "posts"
            assert_physical_direct_path(
                account_dir, posts_dir, kind="directory", label="posts 根目录")
            if not account_dir.is_dir() or not posts_dir.is_dir():
                raise ArchivePathError("账号归档与 posts/ 必须已存在；compose 只读不创建")
            arc = Archive(archive_root, account_dir.name)
        except (ArchivePathError, OSError) as exc:
            raise _fail(post_id, "账号归档路径不安全：%s" % exc) from exc
        for row in arc.rows():
            if row.get("post_id") != post_id:
                continue
            if platform is not None and row.get("platform") != platform:
                continue
            candidates.append((arc, row))

    if not candidates:
        where = "账号 %s" % account if account else str(archive_root)
        raise _fail(post_id, "%s 中找不到该帖" % where)
    if len(candidates) > 1:
        names = "、".join(sorted(arc.base.name for arc, _ in candidates))
        raise _fail(post_id, "在多个账号归档中出现（%s）；请显式指定 account" % names)
    return candidates[0]


def _load_current_translation(arc: Archive, source: dict) -> str:
    post_id = source.get("post_id") or "?"
    text = source.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _fail(post_id, "英文原文缺失或为空")

    entry = load_translated(arc.base / "translated.jsonl").get(post_id)
    if not isinstance(entry, dict):
        raise _fail(post_id, "translated.jsonl 中没有译文")
    text_de = entry.get("text_de")
    if not isinstance(text_de, str) or not text_de.strip():
        raise _fail(post_id, "德语译文缺失或为空")
    if entry.get("prompt_version") != PROMPT_VERSION:
        raise _fail(
            post_id,
            "译文提示词版本已过期（实得 %r，当前 %d）"
            % (entry.get("prompt_version"), PROMPT_VERSION))
    if not translation_is_current(source, entry):
        raise _fail(post_id, "译文绑定的英文正文指纹已过期；必须先重译")

    violations = money_preserved(text, text_de)
    if violations:
        raise _fail(post_id, "金额硬闸未通过：%s" % "；".join(violations))
    return text_de


def _validate_image(path: Path, post_id: str) -> tuple[int, int]:
    try:
        assert_physical_direct_path(
            path.parent, path, kind="file", label="待发布图片")
        if not path.exists():
            raise _fail(post_id, "图片不存在：%s" % path)
        if path.stat().st_size <= 0:
            raise _fail(post_id, "图片是 0 字节：%s" % path)
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
        if width <= 0 or height <= 0:
            raise _fail(post_id, "图片尺寸无效：%s" % path)
        return int(width), int(height)
    except ComposeError:
        raise
    except (ArchivePathError, OSError, UnidentifiedImageError, ValueError) as exc:
        raise _fail(post_id, "图片无法由 Pillow 完整打开：%s（%s）" % (path, exc)) from exc


def _choose_images(arc: Archive, source: dict, post_dir: Path,
                   warnings: list[str]) -> tuple[
                       tuple[Path, ...],
                       tuple[Literal["media_de", "original"], ...],
                       tuple[tuple[int, int], ...]]:
    post_id = source.get("post_id") or "?"
    if source.get("media_complete", True) is not True:
        raise _fail(post_id, "归档标记 media_complete=False；不得发布残缺轮播")
    media = source.get("media")
    if not isinstance(media, list):
        raise _fail(post_id, "post.json 的 media 不是列表")
    images = [item for item in media
              if isinstance(item, dict) and item.get("kind") == "image"]
    if not images:
        raise _fail(post_id, "至少需要 1 张图片；纯视频/无媒体帖不进入发布")

    media_de = post_dir / "media_de"
    try:
        assert_physical_direct_path(
            post_dir, media_de, kind="directory", label="media_de 目录")
    except ArchivePathError as exc:
        raise _fail(post_id, str(exc)) from exc

    selected: list[Path] = []
    sources: list[Literal["media_de", "original"]] = []
    dimensions: list[tuple[int, int]] = []
    for position, item in enumerate(images, 1):
        raw = item.get("local_path")
        if not isinstance(raw, str) or not raw.strip():
            raise _fail(post_id, "第 %d 张图没有 local_path" % position)
        original = _relative_archive_path(arc.base, raw, post_id=post_id)
        if original.parent != post_dir:
            raise _fail(post_id, "第 %d 张图不在该帖目录内：%s" % (position, original))

        localized: list[Path] = []
        if media_de.is_dir():
            try:
                localized = [candidate for candidate in media_de.iterdir()
                             if candidate.stem == original.stem
                             and candidate.is_file()]
            except OSError as exc:
                raise _fail(post_id, "无法读取 media_de：%s" % exc) from exc
        if len(localized) > 1:
            raise _fail(post_id, "media_de 里第 %d 张图有多个候选：%s"
                        % (position, "、".join(p.name for p in localized)))

        if localized:
            chosen = localized[0]
            source_kind: Literal["media_de", "original"] = "media_de"
        else:
            chosen = original
            source_kind = "original"
            warnings.append(
                "第 %d 张缺少德语图，已回退原图 %s；图内可能仍有英文"
                % (position, original.name))
        dimensions.append(_validate_image(chosen, post_id))
        selected.append(chosen)
        sources.append(source_kind)

    return tuple(selected), tuple(sources), tuple(dimensions)


def _validate_instagram(post_id: str, text: str,
                        dimensions: tuple[tuple[int, int], ...],
                        limits: InstagramConstraints) -> None:
    if limits.max_images is not None and len(dimensions) > limits.max_images:
        raise _fail(post_id, "IG 图片数 %d 超过 G1 实测上限 %d（%s）"
                    % (len(dimensions), limits.max_images, limits.probe_dump))
    if limits.max_caption_length is not None:
        assert limits.caption_length_mode is not None
        actual = _text_length(text, limits.caption_length_mode)
        if actual > limits.max_caption_length:
            raise _fail(post_id, "IG 正文长度 %d 超过 G1 实测上限 %d（%s）"
                        % (actual, limits.max_caption_length, limits.probe_dump))
    if limits.max_hashtags is not None:
        actual = len(extract_hashtags(text))
        if actual > limits.max_hashtags:
            raise _fail(post_id, "IG 标签数 %d 超过 G1 实测上限 %d（%s）"
                        % (actual, limits.max_hashtags, limits.probe_dump))
    for index, (width, height) in enumerate(dimensions, 1):
        ratio = width / height
        if (limits.min_aspect_ratio is not None
                and ratio < limits.min_aspect_ratio):
            raise _fail(post_id, "IG 第 %d 张画幅比 %.6f 低于 G1 实测下限 %.6f（%s）"
                        % (index, ratio, limits.min_aspect_ratio,
                           limits.probe_dump))
        if (limits.max_aspect_ratio is not None
                and ratio > limits.max_aspect_ratio):
            raise _fail(post_id, "IG 第 %d 张画幅比 %.6f 高于 G1 实测上限 %.6f（%s）"
                        % (index, ratio, limits.max_aspect_ratio,
                           limits.probe_dump))


def _validate_schedule(post_id: str, scheduled_at: datetime,
                       window: ScheduleWindow, now: datetime) -> None:
    _aware(now, "now")
    delta = (scheduled_at.astimezone(timezone.utc)
             - now.astimezone(timezone.utc))
    if delta < window.min_ahead:
        raise _fail(post_id, "排期早于 G1 实测 UI 下限（%s）" % window.probe_dump)
    if delta > window.max_ahead:
        raise _fail(post_id, "排期晚于 G1 实测 UI 上限（%s）" % window.probe_dump)


def compose_post(post_id: str, scheduled_at: datetime, *,
                 archive_root: Path | str | None = None,
                 account: str | None = None,
                 platform: Platform | None = None,
                 instagram_constraints: InstagramConstraints | None = None,
                 schedule_window: ScheduleWindow | None = None,
                 now: datetime | None = None,
                 require_verified_ui_constraints: bool = False,
                 warning_sink: WarningSink | None = print) -> DePost:
    """组装一篇待发帖；失败发生在任何浏览器操作之前。

    G1 完成前，UI 数值约束没有真实来源。此时函数会显式告警但允许做离线组装；
    真正的发布入口必须传 ``require_verified_ui_constraints=True``，缺任一份
    probe 来源即失败闭合。
    """
    _aware(scheduled_at, "scheduled_at")
    root = Path(archive_root) if archive_root is not None else cfg().archive_dir
    arc, manifest_row = _find_source(
        root, post_id, account=account, platform=platform)
    source, post_dir = _read_post_truth(arc, manifest_row)

    source_platform = source.get("platform")
    if source_platform not in {"facebook", "instagram"}:
        raise _fail(post_id, "platform 不是 facebook/instagram：%r" % source_platform)
    if platform is not None and source_platform != platform:
        raise _fail(post_id, "post.json 的 platform 与调用方指定值不一致")
    source_account = source.get("account")
    if not isinstance(source_account, str) or not source_account.strip():
        raise _fail(post_id, "post.json 的 account 缺失")
    expected_prefix = "fa_" if source_platform == "facebook" else "in_"
    expected_dir = expected_prefix + source_account
    if arc.base.name.lower() != expected_dir.lower():
        raise _fail(post_id, "post.json 的 platform/account 与账号归档目录不一致")

    text_de = _load_current_translation(arc, source)
    warnings: list[str] = []
    image_paths, image_sources, dimensions = _choose_images(
        arc, source, post_dir, warnings)

    if source_platform == "instagram":
        if instagram_constraints is None:
            message = ("IG 画幅/图片数/正文长度/标签数尚无 G1 实测值；"
                       "当前只完成离线组装，禁止进入浏览器发布")
            if require_verified_ui_constraints:
                raise _fail(post_id, message)
            warnings.append(message)
        else:
            if require_verified_ui_constraints and not instagram_constraints.complete():
                raise _fail(post_id, "IG 约束不完整；必须补齐 G1 的四类实测值")
            _validate_instagram(post_id, text_de, dimensions, instagram_constraints)

    if schedule_window is None:
        message = ("定时窗口尚无 G1 实测值；config.toml 的 10 分钟/75 天只是 API 占位，"
                   "当前禁止把它当 Business Suite UI 事实")
        if require_verified_ui_constraints:
            raise _fail(post_id, message)
        warnings.append(message)
    else:
        _validate_schedule(
            post_id, scheduled_at, schedule_window,
            now or datetime.now(timezone.utc))

    owner = source.get("owner")
    owner_name = source.get("owner_name")
    coauthors = source.get("coauthors") or []
    if not isinstance(owner, str) or not owner.strip():
        raise _fail(post_id, "owner 缺失；无法确认是在发布原创帖还是合作帖")
    owner_differs = owner.lower() != source_account.lower()
    coauthor_names = ({str(item).lower() for item in coauthors}
                      if isinstance(coauthors, list) else set())
    if owner_differs and source_account.lower() not in coauthor_names:
        raise _fail(
            post_id,
            "owner=%s 与目标账号不同，但 coauthors 没有 %s；归属证据不完整，禁止发布"
            % (owner, source_account))
    is_collab = owner_differs
    if is_collab:
        warnings.append(
            "合作帖原作者：%s；提交排期前确认二次使用授权"
            % (owner_name or owner))

    for message in warnings:
        if warning_sink is not None:
            warning_sink("[!] %s" % message)

    return DePost(
        post_id=post_id,
        platform=source_platform,
        account=source_account,
        source_text=source["text"],
        text_de=text_de,
        scheduled_at=scheduled_at,
        image_paths=image_paths,
        image_sources=image_sources,
        prompt_version=PROMPT_VERSION,
        source_author=owner if is_collab else None,
        source_author_name=owner_name if is_collab else None,
        warnings=tuple(warnings),
        post_dir=post_dir,
    )

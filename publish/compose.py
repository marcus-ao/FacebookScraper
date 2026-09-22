"""发布前离线校验译文、金额、媒体与排期契约。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from PIL import Image, UnidentifiedImageError

from core.config import ROOT as PROJECT_ROOT
from core.config import cfg
from core import localization
from localize import images as image_de
from publish import evidence
from publish.business_suite import resolve_ui_timezone
from core.store import (Archive, ArchivePathError, account_dirs,
                        assert_physical_direct_path, read_post_truth)
from core.translated import (PROMPT_VERSION, apply_money_mapping,
                            effective_translation, image_translation, load_human_translated,
                            extract_hashtags, extract_money_tokens,
                            hashtags_preserved, load_translated,
                            money_preserved, normalize_money_token,
                            translation_is_current)

Platform = Literal["facebook", "instagram"]
CaptionLengthMode = Literal["codepoints", "utf16_units", "utf8_bytes"]
WarningSink = Callable[[str], None]


class ComposeError(ValueError):
    """待发帖未通过离线硬闸。"""


@dataclass(frozen=True)
class InstagramConstraints:
    """可选的实测限制；未测量的项目交给当次 Business Suite UI 校验。"""

    probe_dump: str
    min_aspect_ratio: float | None = None
    max_aspect_ratio: float | None = None
    max_images: int | None = None
    max_caption_length: int | None = None
    caption_length_mode: CaptionLengthMode | None = None
    max_hashtags: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.probe_dump, str) or not self.probe_dump.strip():
            raise ValueError("IG 约束必须写明来自哪份 G1 probe dump")
        for label, value in (("min_aspect_ratio", self.min_aspect_ratio),
                             ("max_aspect_ratio", self.max_aspect_ratio)):
            if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or value <= 0):
                raise ValueError("%s 必须是大于 0 的有限数" % label)
        if (self.min_aspect_ratio is not None
                and self.max_aspect_ratio is not None
                and self.min_aspect_ratio > self.max_aspect_ratio):
            raise ValueError("IG 最小画幅比不得大于最大画幅比")
        for label, value in (("max_images", self.max_images),
                             ("max_caption_length", self.max_caption_length),
                             ("max_hashtags", self.max_hashtags)):
            if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int)
                    or value < 0):
                raise ValueError("%s 必须是非负整数" % label)
        if self.max_images == 0:
            raise ValueError("max_images 必须大于 0")
        if self.max_caption_length == 0:
            raise ValueError("max_caption_length 必须大于 0")
        if ((self.max_caption_length is None)
                != (self.caption_length_mode is None)):
            raise ValueError("正文上限与 UI 的字符计数方式必须一起提供")
        if (self.caption_length_mode is not None
                and self.caption_length_mode not in {
                    "codepoints", "utf16_units", "utf8_bytes"}):
            raise ValueError("未知正文计数方式：%s" % self.caption_length_mode)


@dataclass(frozen=True)
class ScheduleWindow:
    """排期窗口；max_ahead=None 表示平台上限由当次 UI 校验。"""

    probe_dump: str
    min_ahead: timedelta
    max_ahead: timedelta | None
    # 窗口携带 UI 时区，用于月界判断。
    ui_timezone: str

    def __post_init__(self) -> None:
        # 空字符串是没有录证来源的配置窗口。严格入口仍要单独核对 dump，不能在这里填一个假名字。
        if not isinstance(self.probe_dump, str) or self.probe_dump != self.probe_dump.strip():
            raise ValueError("定时窗口的录证来源不能是空白；没有录证的配置窗口用空字符串")
        if not isinstance(self.min_ahead, timedelta) or (self.max_ahead is not None
                and not isinstance(self.max_ahead, timedelta)):
            raise ValueError("定时窗口上下限必须是 timedelta")
        if self.min_ahead < timedelta(0):
            raise ValueError("定时窗口下限不得为负数")
        if self.max_ahead is not None and self.max_ahead < self.min_ahead:
            raise ValueError("定时窗口上限不得早于下限")
        if not isinstance(self.ui_timezone, str) or not self.ui_timezone.strip():
            raise ValueError("定时窗口必须写明 UI 时区；跨月上限只能在 UI 时区里判")


@dataclass(frozen=True)
class DePost:
    """一篇已通过当前可用离线硬闸的德语图文帖。"""

    post_id: str
    platform: Platform
    account: str
    source_text: str
    original_text_de: str
    text_de: str
    scheduled_at: datetime
    image_paths: tuple[Path, ...]
    image_sources: tuple[Literal["media_de", "original"], ...]
    prompt_version: int
    source_author: str | None
    source_author_name: str | None
    warnings: tuple[str, ...]
    post_dir: Path
    snapshot_id: str = ''
    source_fingerprint: str = ''

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


def _probe_path(raw: str, state_dir: Path) -> Path:
    value = Path(os.path.expandvars(raw)).expanduser()
    if not value.is_absolute():
        value = ((state_dir / value) if value.parent == Path(".")
                 else (PROJECT_ROOT / value))
    return value.resolve(strict=False)


def _parse_probe_number(observations: dict, key: str, *, integer: bool):
    raw = observations.get(key)
    try:
        if integer:
            if not isinstance(raw, str) or not re.fullmatch(r"\d+", raw.strip()):
                raise ValueError
            return int(raw)
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError
        return value
    except (TypeError, ValueError) as exc:
        raise ComposeError("G1 probe 的 %s 不是有效实测数字：%r" % (key, raw)) from exc


@evidence.validation_scope()
def require_probe_evidence(probe_dumps: tuple[str, ...] = ()) -> dict:
    """核验已审核的录制文件和浏览器角色，不要求人工测量 UI 边界。"""
    c = cfg()
    if c.get("publish", "ui_constraints_verified", False) is not True:
        raise ComposeError(
            "[publish].ui_constraints_verified 仍为 false；尚未接受这份控件录制")
    configured = c.get("publish", "ui_probe_dump", "")
    if not isinstance(configured, str) or not configured.strip():
        raise ComposeError("[publish].ui_probe_dump 为空；严格发布不能伪造 G1 来源")

    state_dir = (PROJECT_ROOT / c.get('paths', 'state', 'state')).resolve(strict=False)
    expected = _probe_path(configured, state_dir)
    if (expected.parent != state_dir
            or not expected.name.startswith("publish_probe_")
            or expected.suffix.lower() != ".json"):
        raise ComposeError("已审核的 G1 probe 必须是 state/ 下的 publish_probe_*.json")
    try:
        assert_physical_direct_path(
            state_dir.parent, state_dir, kind="directory", label="state 目录")
        assert_physical_direct_path(
            state_dir, expected, kind="file", label="G1 probe dump")
    except ArchivePathError as exc:
        raise ComposeError("G1 probe 路径不安全：%s" % exc) from exc
    if not expected.is_file():
        raise ComposeError("config 指定的 G1 probe 不存在：%s" % expected)

    for supplied in probe_dumps:
        if not isinstance(supplied, str) or _probe_path(supplied, state_dir) != expected:
            raise ComposeError(
                "注入的 UI 约束必须全部来自 config 已审核的同一份 G1 probe：%s"
                % expected)
    # v2 dump 结构与截图校验统一委托 evidence，避免重复解析和规则分叉。
    data, detail = evidence.validate_v2_dump(expected.name, state_dir)
    if data is None:
        raise ComposeError("G1 probe 不满足 v2 契约：%s" % detail)

    if data.get("cdp_port") != c.publish_debug_port:
        raise ComposeError("G1 probe 不是从 [publish] 调试端口记录的")
    raw_profile = data.get("profile_dir")
    # 录制可搬到另一台机器；核对专用 profile 身份，不绑定旧机器的用户名。
    if (not isinstance(raw_profile, str)
            or PureWindowsPath(raw_profile).name.casefold()
            != c.publish_profile_dir.name.casefold()):
        raise ComposeError("G1 probe 不是从 [publish] 专用 profile 记录的")

    interactions = data.get("interactions")
    if not isinstance(interactions, list) or len(interactions) < 7:
        raise ComposeError("G1 probe 至少要有 7 条可信人工交互记录")
    screenshot_dir = state_dir / (expected.stem + "_screenshots")
    try:
        assert_physical_direct_path(
            state_dir, screenshot_dir, kind="directory", label="G1 截图目录")
    except ArchivePathError as exc:
        raise ComposeError("G1 截图目录不安全：%s" % exc) from exc
    if not screenshot_dir.is_dir():
        raise ComposeError("G1 probe 的截图目录不存在：%s" % screenshot_dir)

    event_types: set[str] = set()
    for sequence, item in enumerate(interactions, 1):
        if not isinstance(item, dict) or item.get("sequence") != sequence:
            raise ComposeError("G1 probe 的交互序号不连续")
        event_type = item.get("event_type")
        if item.get("is_trusted") is not True or event_type not in {
                "click", "input", "change", "submit"}:
            raise ComposeError("G1 probe 含非可信/未知交互事件")
        if not isinstance(item.get("target"), dict):
            raise ComposeError("G1 probe 第 %d 条交互缺少稳定目标属性" % sequence)
        if item.get("screenshot_error") not in {None, ""}:
            raise ComposeError("G1 probe 第 %d 条交互截图失败" % sequence)
        raw_screenshot = item.get("screenshot")
        if not isinstance(raw_screenshot, str):
            raise ComposeError("G1 probe 第 %d 条交互没有截图" % sequence)
        try:
            evidence.screenshot_path(raw_screenshot, expected.name, state_dir)
        except (OSError, ValueError) as exc:
            raise ComposeError("G1 probe 第 %d 条交互截图无效：%s" % (sequence, exc)) from exc
        event_types.add(event_type)
    if "click" not in event_types or not event_types.intersection({"input", "change"}):
        raise ComposeError("G1 probe 必须同时覆盖点击与输入/变更事件")
    if data.get("schema_version") == 2:
        snapshots = data.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            raise ComposeError("G1 probe v2 缺少被动语义快照")
        for sequence, item in enumerate(snapshots, 1):
            if not isinstance(item, dict) or item.get("sequence") != sequence:
                raise ComposeError("G1 probe v2 的语义快照序号不连续")
            if not isinstance(item.get("semantic_items"), list):
                raise ComposeError("G1 probe v2 第 %d 条缺少 semantic_items" % sequence)
        finals = [item for item in snapshots if item.get("reason") == "final"]
        if not finals:
            raise ComposeError("G1 probe v2 缺少停止录制时的 final 语义快照")
        final = finals[-1]
        if final.get("screenshot_error") not in {None, ""}:
            raise ComposeError("G1 probe v2 的 final 遮罩截图失败")
        raw_final_shot = final.get("screenshot")
        if not isinstance(raw_final_shot, str) or not raw_final_shot.strip():
            raise ComposeError("G1 probe v2 的 final 语义快照缺少遮罩截图")
        try:
            evidence.screenshot_path(raw_final_shot, expected.name, state_dir)
        except (OSError, ValueError) as exc:
            raise ComposeError("G1 probe v2 final 截图无效：%s" % exc) from exc
    return data


def verified_constraints_from_config(
        platform: Platform) -> tuple[InstagramConstraints, ScheduleWindow]:
    """核验控件录制；未知 UI 限制不成为内容和排期的配置门槛。"""
    if platform not in {"facebook", "instagram"}:
        raise ComposeError("未知发布平台：%r" % platform)
    require_probe_evidence()
    configured = str(cfg().get("publish", "ui_probe_dump", ""))
    ui_timezone = str(cfg().get("publish", "ui_timezone", "") or "").strip()
    if not ui_timezone:
        raise ComposeError(
            "[publish].ui_timezone 为空；跨月排期上限只能在 UI 时区里判，"
            "不填就没法确定排期落在 composer 日历的哪个月")
    resolve_ui_timezone(ui_timezone)
    window = ScheduleWindow(configured, timedelta(0), None, ui_timezone)
    limits = InstagramConstraints(probe_dump=configured)
    return limits, window


def _match_probe_measurements(data: dict,
                              limits: InstagramConstraints | None,
                              window: ScheduleWindow | None) -> None:
    """注入值必须与 dump 里人工实测并复核的结构化值逐项一致。"""
    observations = data.get("observations") or {}
    if window is not None:
        for key, actual in (("schedule_min_ahead_seconds", window.min_ahead),
                            ("schedule_max_ahead_seconds", window.max_ahead)):
            if actual is None or (key == 'schedule_min_ahead_seconds' and actual == timedelta(0)):
                continue
            observed = _parse_probe_number(observations, key, integer=True)
            if actual.total_seconds() != observed:
                raise ComposeError("注入的定时窗口与 G1 probe 实测秒数不一致")
    if limits is None:
        return
    numeric_pairs = (
        ("instagram_min_aspect_ratio", limits.min_aspect_ratio, False),
        ("instagram_max_aspect_ratio", limits.max_aspect_ratio, False),
        ("instagram_max_images", limits.max_images, True),
        ("instagram_max_caption_length", limits.max_caption_length, True),
        ("instagram_max_hashtags", limits.max_hashtags, True),
    )
    for key, actual, integer in numeric_pairs:
        if actual is None:
            continue
        observed = _parse_probe_number(observations, key, integer=integer)
        if integer:
            matches = actual == observed
        else:
            matches = actual is not None and math.isclose(
                float(actual), float(observed), rel_tol=1e-12, abs_tol=1e-12)
        if not matches:
            raise ComposeError("注入的 %s 与 G1 probe 实测值不一致" % key)
    if (limits.caption_length_mode is not None
            and limits.caption_length_mode != observations.get("instagram_caption_length_mode")):
        raise ComposeError("注入的 IG 正文计数方式与 G1 probe 不一致")


def _relative_archive_path(account_dir: Path, raw: str, *,
                           post_id: str) -> Path:
    """把归档里的跨平台相对路径还原，并拒绝越出帖子目录的路径。"""
    value = (raw or "").replace("\\", "/")
    rel = PurePosixPath(value)
    if not value or rel.is_absolute() or ".." in rel.parts:
        raise _fail(post_id, "图片 local_path 不是安全的归档相对路径：%r" % raw)
    return account_dir.joinpath(*rel.parts)


def _read_post_truth(arc: Archive, row: dict) -> tuple[dict, Path]:
    try:
        return read_post_truth(arc.base, row)
    except ArchivePathError as exc:
        raise _fail(str(row.get("post_id") or "?"), str(exc)) from exc


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

    entry = effective_translation(
        source, load_translated(arc.base / "translated.jsonl").get(post_id),
        load_human_translated(arc.base / "translated_human.jsonl").get(post_id))
    if not isinstance(entry, dict):
        raise _fail(post_id, "机器与人工译文账本中都没有译文")
    text_de = entry.get("text_de")
    if not isinstance(text_de, str) or not text_de.strip():
        raise _fail(post_id, "德语译文缺失或为空")
    if entry.get("is_human") and entry.get("stale"):
        raise _fail(post_id, "人工译文依据的源帖已变更或无法确认；请在审校台复核并保存")
    if not entry.get("is_human") and entry.get("prompt_version") != PROMPT_VERSION:
        raise _fail(
            post_id,
            "译文提示词版本已过期（实得 %r，当前 %d）"
            % (entry.get("prompt_version"), PROMPT_VERSION))
    if not translation_is_current(source, entry):
        raise _fail(post_id, "译文绑定的英文正文指纹已过期；必须先重译")

    violations = money_preserved(localization.without_urls(text), localization.without_urls(text_de))
    if violations:
        raise _fail(post_id, "金额硬闸未通过：%s" % "；".join(violations))
    draft = localization.effective_draft(arc.base, source, entry)
    check = localization.validate(draft)
    if not check['ready']:
        raise _fail(post_id, "平台文案尚未确认：%s" % "；".join(item['message'] for item in check['issues']))
    return (localization.render(draft) if draft.get('has_record') or draft.get('links')
            or localization.extract_urls(text_de) else text_de)


def _apply_publish_price_map(source_text: str, text_de: str,
                             price_map: Mapping[str, str]) -> str:
    """只生成最终发布文案；译文真相源保持原金额不动。"""
    amounts = extract_money_tokens(source_text)
    mapped_tokens = {
        normalize_money_token(str(token)) for token in price_map
    }
    missing = tuple(dict.fromkeys(token for token in amounts
                                  if normalize_money_token(token)
                                  not in mapped_tokens))
    if missing:
        raise ComposeError("最终发布文案存在未映射金额：%s" % "、".join(missing))
    try:
        mapped = apply_money_mapping(text_de, price_map)
    except ValueError as exc:
        raise ComposeError("[publish.price_map] 无效：%s" % exc) from exc
    # 价格表只能改金额，不能借机增删话题标签。
    violations = hashtags_preserved(text_de, mapped)
    if violations:
        raise ComposeError("价格映射破坏话题标签硬闸：%s" % "；".join(violations))
    return mapped


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
            image.load()
            width, height = image.size
        if width <= 0 or height <= 0:
            raise _fail(post_id, "图片尺寸无效：%s" % path)
        return int(width), int(height)
    except ComposeError:
        raise
    except (ArchivePathError, OSError, UnidentifiedImageError, ValueError) as exc:
        raise _fail(post_id, "图片无法由 Pillow 完整打开：%s（%s）" % (path, exc)) from exc


def _program_owned_media_de(account_dir: Path) -> dict[str, set[str | None]]:
    """读取程序图片所有权；含 output_sha256 时核对字节，旧记录仅按路径兼容。"""
    path = account_dir / "images_de.jsonl"
    owned: dict[str, set[str | None]] = {}
    if not path.is_file():
        return owned
    try:
        assert_physical_direct_path(
            account_dir, path, kind="file", label="images_de.jsonl")
    except ArchivePathError:
        return owned
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return owned
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue            # 逐行独立解码：一条坏行不该让整篇发不出去
        if not isinstance(row, dict):
            continue
        rel = row.get("out_path")
        if not isinstance(rel, str) or not rel.strip():
            continue
        pure = PurePosixPath(rel.strip().replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            continue
        digest = row.get("output_sha256")
        owned.setdefault(pure.as_posix(), set()).add(
            digest if isinstance(digest, str) and len(digest) == 64 else None)
    return owned


def _is_program_output(account_dir: Path, candidate: Path,
                       owned: dict[str, set[str | None]]) -> bool:
    if image_de.manual_upload_record(candidate):
        return False
    try:
        rel = candidate.relative_to(account_dir).as_posix()
    except ValueError:
        return False
    digests = owned.get(rel)
    if not digests:
        return False
    if None in digests:
        return True             # 旧 schema 没有哈希，只能按路径认
    try:
        with candidate.open("rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    # 字节被改过就不再是程序产出 —— 人在程序产出上改了几笔，也算人工版本。
    return digest.hexdigest() in digests


def _pick_localized(account_dir: Path, candidates: list[Path], *,
                    post_id: str, position: int,
                    owned: dict[str, set[str | None]]) -> tuple[Path, bool]:
    """同序号图片优先人工版；多个人工候选时拒绝猜选。"""
    manual = [item for item in sorted(candidates)
              if not _is_program_output(account_dir, item, owned)]
    if len(manual) > 1:
        raise _fail(post_id, "media_de 里第 %d 张图有多个**人工**候选（%s）；"
                             "分不清该发哪张，请只保留一个"
                    % (position, "、".join(item.name for item in manual)))
    if manual:
        return manual[0], True
    program = sorted(candidates)
    if len(program) > 1:
        raise _fail(post_id, "media_de 里第 %d 张图有多个程序产出（%s）；"
                             "多半是改过 [image].output_format，"
                             "请清理掉旧格式那张再发" % (
                                 position, "、".join(i.name for i in program)))
    return program[0], False


def _check_program_source(account_dir: Path, source: Path, output: Path, *,
                           post_id: str, position: int) -> None:
    """只校验已知程序输出的原图依据；人工文件仍由人工审校负责。"""
    path = account_dir / "images_de.jsonl"
    output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    relative = output.relative_to(account_dir).as_posix()
    matches = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (isinstance(row, dict) and row.get("post_id") == post_id
                and isinstance(row.get("out_path"), str)
                and row["out_path"].replace("\\", "/") == relative
                and row.get("output_sha256") == output_hash
                and row.get("source_sha256")):
            matches.append(row)
    if not matches and source.stem != f"{position:02d}":
        raise _fail(post_id, "第 %d 张源图版本已变化，程序德语图缺少可匹配的原图依据，请重新生成" % position)
    if matches:
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if not any(row["source_sha256"] == source_hash for row in matches):
            raise _fail(post_id, "第 %d 张原图已变化，现有程序德语图仍绑定旧源图，请重新生成并审校" % position)


def _choose_images(arc: Archive, source: dict, post_dir: Path,
                   warnings: list[str]) -> tuple[
                       tuple[Path, ...],
                       tuple[Literal["media_de", "original"], ...],
                       tuple[tuple[int, int], ...]]:
    post_id = source.get("post_id") or "?"
    if source.get("media_complete") is not True:
        raise _fail(post_id, "归档未明确标记 media_complete=True；不得发布残缺内容")
    media = source.get("media")
    if not isinstance(media, list):
        raise _fail(post_id, "post.json 的 media 不是列表")
    if not media:
        raise _fail(post_id, "至少需要 1 张图片；纯视频/无媒体帖不进入发布")
    images: list[dict] = []
    for position, item in enumerate(media, 1):
        if not isinstance(item, dict):
            raise _fail(post_id, "第 %d 个媒体项不是对象；不得静默丢弃" % position)
        kind = item.get("kind")
        if kind != "image":
            raise _fail(
                post_id,
                "第 %d 个媒体项 kind=%r；本期图文发布只支持纯图片，禁止少发混合媒体"
                % (position, kind))
        images.append(item)

    media_de = post_dir / "media_de"
    try:
        assert_physical_direct_path(
            post_dir, media_de, kind="directory", label="media_de 目录")
    except ArchivePathError as exc:
        raise _fail(post_id, str(exc)) from exc
    owned_media_de = _program_owned_media_de(arc.base)
    image_state = image_de.load_image_state(arc.base / "images_de.jsonl")
    image_text = image_translation(
        source, load_translated(arc.base / "translated.jsonl").get(post_id),
        load_human_translated(arc.base / "translated_human.jsonl").get(post_id))
    current_pairs = {pair.media_index: pair for pair in image_de.review_image_pairs(
        arc.base, source, image_text, image_state)} if image_text else {}
    latest_images = {}
    ledger = arc.base / "images_de.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("post_id") == post_id:
                latest_images[row.get("media_index")] = row

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
                             if candidate.stem == f"{position:02d}"
                             and candidate.is_file()]
            except OSError as exc:
                raise _fail(post_id, "无法读取 media_de：%s" % exc) from exc

        chosen: Path | None = None
        source_kind: Literal["media_de", "original"] = "original"
        latest = latest_images.get(position - 1) or {}
        manual = [path for path in localized if not _is_program_output(arc.base, path, owned_media_de)]
        if not manual:
            if (post_id, position - 1) in image_state.latest:
                # 与审校台共用版本判据；不能把页面已判过期的程序图冻结为发布素材。
                pair = current_pairs.get(position - 1)
                if pair is None or not pair.localized_rel:
                    raise _fail(post_id, "第 %d 张德语图依据已过期或文件缺失，请重新生成并审校" % position)
                localized = [_relative_archive_path(arc.base, pair.localized_rel, post_id=post_id)]
            elif latest.get("refine_id"):
                refined = _relative_archive_path(arc.base, latest.get("out_path", ""), post_id=post_id)
                expected_stem = f"{position:02d}_v{latest['refine_id']}"
                if (refined.parent != media_de or refined.stem != expected_stem
                        or not refined.is_file() or not _is_program_output(arc.base, refined, owned_media_de)):
                    raise _fail(post_id, "第 %d 张优化图片缺失或已变化，请重新核对" % position)
                localized = [refined]
        if localized:
            chosen, manual = _pick_localized(
                arc.base, localized, post_id=post_id, position=position,
                owned=owned_media_de)
            source_kind = "media_de"
            if manual:
                warnings.append(
                    "第 %d 张用的是**人工放置**的德语图 %s（不是程序产出），"
                    "已按人工优先选用" % (position, chosen.name))
            else:
                _check_program_source(arc.base, original, chosen,
                                      post_id=post_id, position=position)
        if chosen is None:
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


def _validate_schedule_month(post_id: str, scheduled_at: datetime,
                             now: datetime, ui_timezone: str) -> None:
    """在 UI 时区核验自然月边界；不能用固定提前天数代替。"""
    zone = resolve_ui_timezone(ui_timezone)
    target = scheduled_at.astimezone(zone)
    today = now.astimezone(zone)
    if (target.year, target.month) != (today.year, today.month):
        raise _fail(
            post_id,
            "排期在 UI 时区 %s 下落在 %04d-%02d，而 composer 的日期选择器只能选"
            "当月 %04d-%02d —— 跨月排期在 UI 上根本点不出来。"
            "改排到本月内，或等进入目标月份再发。"
            % (zone.key, target.year, target.month, today.year, today.month))


def _validate_schedule(post_id: str, scheduled_at: datetime,
                       window: ScheduleWindow, now: datetime) -> None:
    _aware(now, "now")
    delta = (scheduled_at.astimezone(timezone.utc)
             - now.astimezone(timezone.utc))
    if delta <= timedelta(0):
        raise _fail(post_id, "排期必须晚于当前时刻")
    if delta < window.min_ahead:
        raise _fail(post_id, "排期早于 G1 实测 UI 下限（%s）" % window.probe_dump)
    if window.max_ahead is not None and delta > window.max_ahead:
        raise _fail(post_id, "排期晚于 G1 实测 UI 上限（%s）" % window.probe_dump)
    _validate_schedule_month(post_id, scheduled_at, now, window.ui_timezone)


def compose_post(post_id: str, scheduled_at: datetime, *,
                 archive_root: Path | str | None = None,
                 account: str | None = None,
                 platform: Platform | None = None,
                 price_map: Mapping[str, str] | None = None,
                 instagram_constraints: InstagramConstraints | None = None,
                 schedule_window: ScheduleWindow | None = None,
                 now: datetime | None = None,
                 require_verified_ui_constraints: bool = False,
                 warning_sink: WarningSink | None = print) -> DePost:
    """离线组装待发内容；真实发布必须要求 verified UI constraints。"""
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

    if require_verified_ui_constraints:
        supplied_limits, supplied_window = instagram_constraints, schedule_window
        if schedule_window is None or instagram_constraints is None:
            configured_limits, configured_window = (
                verified_constraints_from_config(source_platform))
            schedule_window = schedule_window or configured_window
            instagram_constraints = instagram_constraints or configured_limits
        if schedule_window is None:  # 类型与失败闭合的双保险
            raise _fail(post_id, "严格发布缺少 G1 实测定时窗口")
        probe_sources = [schedule_window.probe_dump]
        if instagram_constraints is not None:
            probe_sources.append(instagram_constraints.probe_dump)
        probe_data = require_probe_evidence(tuple(probe_sources))
        # 兼容显式注入的旧实测契约；默认路径不要求、不推测这些数字。
        if supplied_window is not None or supplied_limits is not None:
            _match_probe_measurements(probe_data, supplied_limits, supplied_window)

    text_de = _load_current_translation(arc, source)
    original_text_de = text_de
    if price_map is not None:
        text_de = _apply_publish_price_map(source["text"], text_de, price_map)
    warnings: list[str] = []
    image_paths, image_sources, dimensions = _choose_images(
        arc, source, post_dir, warnings)
    require_all_media_de = cfg().get("publish", "require_all_media_de", True)
    if not isinstance(require_all_media_de, bool):
        raise _fail(post_id, "[publish].require_all_media_de 必须是 true 或 false")
    missing = [index for index, value in enumerate(image_sources)
               if value == "original"]
    if require_all_media_de and missing:
        commands = [
            'python -m localize.images --account "%s" --post-id "%s" --media-index %d'
            % (arc.base.name, post_id, index) for index in missing]
        raise _fail(
            post_id,
            "%s缺少德语图；已在浏览器操作前停止。补图后再提交：\n%s"
            % ("、".join("第 %d 张" % (index + 1) for index in missing),
               "\n".join(commands)))

    if source_platform == 'instagram' and instagram_constraints is not None:
        _validate_instagram(post_id, text_de, dimensions, instagram_constraints)

    if schedule_window is not None:
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
        original_text_de=original_text_de,
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

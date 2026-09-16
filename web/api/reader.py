"""从归档与账本组装列表和详情；复用业务判据，人工稿优先，不调用模型或浏览器。"""
from __future__ import annotations

import json
import hashlib
import mimetypes
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:                          # 支持 `python -m web.api.reader`
    sys.path.insert(0, str(ROOT))

from localize import images as image_de  # noqa: E402
from localize import suggest as text_suggestions  # noqa: E402
from core import store, review, localization                         # noqa: E402
from core.mirror import MirrorService, MirrorSettings                # noqa: E402
from core import translated as translation             # noqa: E402
from core.config import cfg                            # noqa: E402
from core.console import force_utf8                    # noqa: E402
from core.store import ArchivePathError                # noqa: E402
from pipeline import engine, hashtag_suggestions, refinement, risk_scan  # noqa: E402
from publish import compose, journal                   # noqa: E402
from web.api import query_index                   # noqa: E402

# 复用业务层正则定位高亮，避免规则分叉。
from core.translated import (_IMPERIAL_RE,             # noqa: E402
                             _MONEY_RE,
                             _MONEY_TOKEN_RE,
                             _SIZE_RE)

#: 列表默认口径：近 90 天。与 `pipeline preflight --days` 的默认值一致。
DEFAULT_DAYS = 90


#: not_ready 表示内容尚未就绪。详情仍可读取，受理翻译取决于当前来源和模型能力。
STATUS_NOT_READY = "not_ready"
STATUS_PENDING_REVIEW = "pending_review"
STATUS_EDITED = "edited"
STATUS_SCHEDULED = "scheduled"


# 字符定位

def _paired_spans(pattern, text_en: str, text_de: str
                  ) -> list[tuple[list[int] | None, list[int] | None]]:
    """按顺序配对两侧匹配范围，缺失位置补 None。"""
    left = [list(m.span()) for m in pattern.finditer(text_en or "")]
    right = [list(m.span()) for m in pattern.finditer(text_de or "")]
    return list(zip_longest(left, right))


def _hashtag_spans(text: str) -> list[list[int]]:
    """复用标签提取规则，按顺序定位原串偏移，正确处理重复标签。"""
    spans: list[list[int]] = []
    cursor = 0
    for tag in translation.extract_hashtags(text):
        found = (text or "").find(tag, cursor)
        if found < 0:                                  # 理论上不会发生
            continue
        spans.append([found, found + len(tag)])
        cursor = found + len(tag)
    return spans


def _highlight(kind: str, severity: str, label: str,
               en_span: list[int] | None,
               de_span: list[int] | None) -> dict:
    return {"kind": kind, "en_span": en_span, "de_span": de_span,
            "severity": severity, "label": label}


def build_highlights(text_en: str, text_de: str) -> list[dict]:
    """将共享检查映射为字符高亮：error 表示违规，warn 提示人工确认；不另建业务规则。"""
    text_en = text_en or ""
    text_de = text_de or ""
    out: list[dict] = []

    # ---- 金额：不该动的被动了 ----
    money_violations = translation.money_preserved(text_en, text_de)
    if money_violations:
        label = "；".join(money_violations)
        for en_span, de_span in _paired_spans(_MONEY_TOKEN_RE, text_en, text_de):
            out.append(_highlight("money", "error", label, en_span, de_span))

    # ---- 话题标签：数量、内容、大小写、顺序必须全一致 ----
    hashtag_violations = translation.hashtags_preserved(text_en, text_de)
    if hashtag_violations:
        label = "；".join(hashtag_violations)
        for en_span, de_span in zip_longest(_hashtag_spans(text_en),
                                            _hashtag_spans(text_de)):
            out.append(_highlight("hashtag", "error", label, en_span, de_span))

    # 提示按金额、尺码、英制单位顺序对应共享正则。
    flags = translation.review_numeric_flags(text_en, text_de)
    # 金额高亮用完整 token 范围，避免只标出符号与首位数字。
    categories = [("money_review", _MONEY_RE, _MONEY_TOKEN_RE),
                  ("size", _SIZE_RE, _SIZE_RE),
                  ("imperial", _IMPERIAL_RE, _IMPERIAL_RE)]
    hit = [(kind, detect, span) for kind, detect, span in categories
           if detect.search(text_en) or detect.search(text_de)]
    for (kind, detect, span_pattern), label in zip(hit, flags):
        spans = _paired_spans(span_pattern, text_en, text_de)
        if not spans:                                  # 完整 token 认不出来时
            spans = _paired_spans(detect, text_en, text_de)   # 退回判命中那个
        for en_span, de_span in spans:
            out.append(_highlight(kind, "warn", label, en_span, de_span))

    return out


# 风险账本

def load_risks(state_dir: Path, sources: Mapping[str, engine.SourcePost]) -> tuple[dict, dict]:
    """返回当前扫描视图及其中仍有效的风险；失败/过期不会伪装成空风险成功。"""
    views = {task_id: risk_scan.result_for(source.account_dir, dict(source.row),
                                           state_dir=state_dir)
             for task_id, source in sources.items()}
    risks = {task_id: list(view["risks"]) for task_id, view in views.items()
             if view["status"] == "completed"}
    return views, risks


# 归档读取

def task_id_of(source: engine.SourcePost) -> str:
    """契约里的任务 id：``<账号目录>/<post_id>``（第 6 节的例子就是这个形状）。"""
    return "%s/%s" % (source.account_dir.name, source.post_id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _hard_alerts(source: engine.SourcePost,
                 rules: engine.PublishRules) -> tuple[list[dict], dict | None]:
    """对单来源候选调用 prepaid_issue，返回告警及原始详情。"""
    candidate = engine.Candidate(source, (source,), "independent")
    issue = engine.prepaid_issue(candidate, rules)
    if issue is None:
        return [], None
    return ([{"code": issue.kind, "label": _alert_label(issue)}],
            dict(issue.details))


def _alert_label(issue: engine.HumanItem) -> str:
    """缩短已知告警，未知 kind 回落业务 summary，避免静默遗漏。"""
    details = issue.details or {}
    if issue.kind == "unmapped_price":
        amounts = [str(v) for v in (details.get("amounts") or [])]
        if amounts:
            return "新金额 %s 未收录" % "、".join(amounts)
    elif issue.kind == "unknown_collaborator":
        who = [str(v) for v in (details.get("collaborators") or [])]
        if who:
            return "第三方作者 %s 不在白名单" % "、".join("@" + w for w in who)
    elif issue.kind == "material_gate":
        return "素材不齐（正文/图片/轮播完整性未通过）"
    elif issue.kind == "unknown_owner":
        return "内容归属证据不完整"
    return issue.summary


def _author_kind(source: engine.SourcePost, details: dict | None,
                 alerts: list[dict]) -> tuple[str, str | None]:
    """返回作者类型及标记；第三方依据 prepaid_issue，其它作者不额外提示。"""
    owner = str(source.row.get("owner") or "").strip()
    account = str(source.row.get("account") or "").strip()
    if any(item["code"] == "unknown_collaborator" for item in alerts):
        who = [str(v) for v in ((details or {}).get("collaborators") or [])]
        name = str(source.row.get("owner_name") or "").strip()
        return "third_party", (name or "、".join(who) or owner or "未知作者")
    if owner and account and owner.lower() != account.lower():
        return "collab", None
    return "own", None


def _translation_of(source: engine.SourcePost) -> dict | None:
    entries = translation.load_translated(source.account_dir / "translated.jsonl")
    return entries.get(source.post_id)


def _human_translation_of(source: engine.SourcePost) -> dict | None:
    return translation.load_human_translated(
        source.account_dir / "translated_human.jsonl").get(source.post_id)


def _effective_translation_of(source: engine.SourcePost) -> dict | None:
    return translation.effective_translation(
        dict(source.row), _translation_of(source), _human_translation_of(source))


def _source_truth(source: engine.SourcePost) -> engine.SourcePost:
    """列表来自索引；展示与保存的正文始终取已校验的 post.json。"""
    arc = store.Archive(source.account_dir.parent, source.account_dir.name)
    row, _post_dir = compose._read_post_truth(arc, dict(source.row))
    if row.get("platform") != source.platform or row.get("account") != source.account:
        raise compose.ComposeError("源帖的平台或账号与归档索引不一致，请先核对归档")
    if not isinstance(row.get("text"), str) or not row["text"].strip():
        raise compose.ComposeError("源帖正文缺失，无法审校")
    return replace(source, row=row)


def excerpt(text: str, limit: int = 90) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[:limit] + " …"


def tags_revision(tags: list[str]) -> str:
    return hashlib.sha256(json.dumps(tags, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def has_schedule(source: engine.SourcePost) -> bool:
    return journal.scheduled_record_for_refs(cfg().state_dir, (source.ref,)) is not None


def _image_media(source: engine.SourcePost) -> list[Mapping[str, Any]]:
    return _row_image_media(source.row)


def _row_image_media(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    media = row.get("media")
    if not isinstance(media, list):
        return []
    return [item for item in media
            if isinstance(item, Mapping) and item.get("kind") == "image"]


def _thumbnail_url(task_id: str, images: list[Mapping[str, Any]]) -> str:
    """⚠️ 取不到第 0 张图就得给空串，前端据此改画占位符。给了地址那一行就会发一次注定 404 的
    请求，占掉浏览器每源 6 条连接之一——实测这样能把同页的列表查询压在队里等 2.2 秒，而列表
    本身只要 30ms。

    判据跟着 `image_bytes` 走：只认已经落盘的 `local_path`，不在这里逐行解析德语图——逐行解析
    正是首屏慢的原因。德语图在而原图没落盘时会少给一个地址，缩略图是装饰（`alt=""`），少画
    一张远好过每行白跑一次。"""
    local = images[0].get("local_path") if images else None
    return "/api/tasks/%s/image/0?variant=de" % task_id if isinstance(local, str) and local.strip() else ""


class _Context:
    """一次请求内的取数上下文。构造一次，列表与详情共用。"""

    def __init__(self, *, days: int = DEFAULT_DAYS,
                 now: datetime | None = None, selected_sources=None) -> None:
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.rules = engine.publish_rules()
        self.state_dir = cfg().state_dir
        # 当前队列只读 active_accounts，冻结历史不靠日期窗口排除。
        self.account_dirs = engine.active_account_dirs(
            store.account_dirs(cfg().archive_dir))
        horizon = self.now - timedelta(days=days)
        if selected_sources is None:
            sources, _issues, self.out_of_scope = engine.load_sources(self.account_dirs, horizon)
        else:
            sources, self.out_of_scope = selected_sources, []
        self.sources: dict[str, engine.SourcePost] = {
            task_id_of(item): item for item in sources}
        self.risk_scans, self.risks = load_risks(self.state_dir, self.sources)

    def reviewable(self, source: engine.SourcePost) -> bool:
        """人工稿即使源文变更也可打开复核；不能因此藏起已经做过的修改。"""
        entry = _effective_translation_of(source)
        return bool(entry and (entry.get("is_human") or
                               translation.translation_is_current(source.row, entry)))

    def scheduled_at(self, source: engine.SourcePost) -> datetime | None:
        """已经在 published.jsonl 里排过期的，用真实排期时刻。"""
        record = journal.scheduled_record_for_refs(self.state_dir, (source.ref,))
        if not isinstance(record, dict):
            return None
        raw = record.get("scheduled_at")
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def allocate_slots(self, pending: list[engine.SourcePost]
                       ) -> dict[str, datetime]:
        """仅给未排期且有译文的任务分配候选；时刻不足时保留 schedule=None。"""
        occupied = [when for when in
                    (self.scheduled_at(item) for item in self.sources.values())
                    if when is not None]
        slots = engine.next_slots(self.now, occupied, len(pending), self.rules)
        return {item.ref: slot for item, slot in zip(pending, slots)}


def list_tasks(*, days: int = DEFAULT_DAYS,
               now: datetime | None = None, status: str | None = None,
               tag: str | None = None, month: str | None = None, platform: str | None = None,
               scope: str = 'review', page: int = 1, limit: int | None = None) -> dict:
    """读取任务列表，按候选时刻及来源时间排序。"""
    if scope == 'history':
        return history_tasks(now=now, status=status, tag=tag, month=month, platform=platform, page=page, limit=limit or 50)
    ctx = _Context(days=days, now=now)
    indexed = query_index.candidates(status=status, tag=tag, month=month, now=ctx.now)
    ids = set(indexed["task_ids"]) if indexed["task_ids"] is not None else None
    ordered = sorted((_source_truth(item) for item in ctx.sources.values()
                      if ids is None or task_id_of(item) in ids),
                     key=lambda s: (s.created_at, s.ref))

    reviewable = {item.ref: ctx.reviewable(item) for item in ordered}
    already = {item.ref: ctx.scheduled_at(item) for item in ordered}
    states = {item.ref: _review_state(ctx, item, reviewable[item.ref], already[item.ref])
              for item in ordered}
    pending = [item for item in ordered
               if reviewable[item.ref] and states[item.ref]["status"] in {"pending_review", "edited"}]
    allocated = ctx.allocate_slots(pending)

    tasks: list[dict] = []
    for source in ordered:
        task_id = task_id_of(source)
        alerts, details = _hard_alerts(source, ctx.rules)
        author_kind, author_flag = _author_kind(source, details, alerts)
        entry = _effective_translation_of(source) if reviewable[source.ref] else None
        when = already[source.ref] or allocated.get(source.ref)
        images = _image_media(source)
        tasks.append({
            "id": task_id,
            "source_text_sha256": translation.source_text_sha256(source.text),
            "platform": source.platform,
            "thumbnail_url": _thumbnail_url(task_id, images),
            "text_de_excerpt": excerpt(entry.get("text_de", "")) if entry else "",
            "image_count": len(images),
            "tags": list(source.row.get("tags") or []),
            "month": _archive_month(source),
            "review": states[source.ref],
            "schedule": ({"at": _iso(when), "channel": source.platform}
                         if when is not None else None),
            "hard_alerts": alerts,
            "risk_count": len(ctx.risks.get(task_id, ())),
            "risk_scan_status": ctx.risk_scans[task_id]["status"],
            "author_flag": author_flag,
            "status": states[source.ref]["status"],
        })

    def sort_key(item: dict):
        at = (item["schedule"] or {}).get("at")
        return (0, at, "") if at else (1, "", item["id"])

    tasks.sort(key=sort_key)
    states_seen = review.STATUSES | {STATUS_NOT_READY}
    counts = {state: sum(item["status"] == state for item in tasks) for state in states_seen}
    # 两个平台各自成为独立入口，角标要按平台分开数——列表可能只加载了其中一边。
    by_platform = {name: {state: sum(item["status"] == state and item["platform"] == name
                                     for item in tasks) for state in states_seen}
                   for name in ("facebook", "instagram")}
    platform_alerts = {name: sum(bool(item['hard_alerts']) and item['platform'] == name
                                for item in tasks) for name in by_platform}
    available_tags = sorted({value for item in tasks for value in item["tags"]})
    tasks = [item for item in tasks if (not status or item["status"] == status)
             and (not tag or (not item["tags"] if tag == "__untagged__" else tag in item["tags"]))
             and (not month or item["month"] == month)
             and (not platform or item['platform'] == platform)]
    total = len(tasks)
    visible = tasks[(page-1)*limit:page*limit] if limit else tasks
    return {
        "tasks": visible,
        "pagination": {'page': page, 'limit': limit, 'total': total},
        "range": {'scope': 'review', 'days': days, 'month': month, 'platform': platform},
        "index": indexed["index"],
        "summary": {
            "total": len(tasks),
            "with_hard_alerts": sum(1 for t in tasks if t["hard_alerts"]),
            "by_status": counts,
            "by_platform_status": by_platform,
            "by_platform_hard_alerts": platform_alerts,
            "tags": available_tags,
        },
    }


def history_tasks(*, now=None, status=None, tag=None, month=None, platform=None, page=1, limit=50):
    result = query_index.history_page(now=now, status=status, tag=tag, month=month,
                                     platform=platform, page=page, limit=limit)
    tasks = []
    for row in result['rows']:
        # 数的是 image 媒体，不是全部媒体：只有视频的帖子取不到第 0 张图。
        images = _row_image_media(row)
        tasks.append({'id': row['id'], 'platform': row['platform'], 'month': row['month'],
                      'created_at': row.get('created_at'), 'account': row['account_dir'],
                      'read_only': row['account_dir'] not in cfg().active_accounts(),
                      'text_de_excerpt': excerpt(row.get('text_de') or row.get('text') or ''),
                      'tags': row['tags'], 'status': row['status'],
                      'image_count': len(images),
                      'thumbnail_url': _thumbnail_url(row['id'], images)})
    return {'tasks': tasks, 'index': result['index'], 'scope': 'history',
            'range': {'scope': 'history', 'days': None, 'month': month, 'platform': platform},
            'pagination': {'page': page, 'limit': limit, 'total': result['total']},
            'summary': {'total': result['total'], 'tags': result['tags'], 'months': result['months']}}


def _review_state(ctx: _Context, source: engine.SourcePost, reviewable: bool,
                  scheduled: datetime | None) -> dict:
    entry = _effective_translation_of(source)
    if entry and entry.get("is_human"):
        default = STATUS_PENDING_REVIEW if entry.get("stale") else STATUS_EDITED
    else:
        default = STATUS_PENDING_REVIEW if reviewable else STATUS_NOT_READY
    state = review.state_for(source.account_dir, dict(source.row),
                             default_status=default, scheduled=scheduled is not None)
    state["snooze_default_days"] = cfg().get("review", "snooze_default_days", 3)
    return state


def _images_of(source: engine.SourcePost, entry: dict | None) -> list[dict]:
    """复用 review_image_pairs，读取人工优先的逐图对照与指标。"""
    task_id = task_id_of(source)
    pairs = []
    if entry:
        try:
            pairs = image_de.review_image_pairs(
                source.account_dir, source.row, entry)
        except (OSError, ValueError, ArchivePathError):
            pairs = []
    by_index = {pair.media_index: pair for pair in pairs}
    source_version = translation.source_text_sha256(source.text)
    text_version = translation.source_text_sha256(entry["text_de"]) if entry else "none"

    out: list[dict] = []
    for index in range(len(_image_media(source))):
        pair = by_index.get(index)
        record = pair.record if pair is not None else None
        manual_record = None
        output_digest = (record or {}).get("output_sha256") or "missing"
        if pair and pair.manual and pair.localized_rel:
            path = source.account_dir / pair.localized_rel
            output_digest = image_de.sha256_file(path)
            manual_record = image_de.manual_upload_record(path)
        version = "%s-%s-%s" % (
            source_version, text_version, output_digest)
        out.append({
            "index": index,
            "original_url": "/api/tasks/%s/image/%d?variant=original&v=%s" % (
                task_id, index, source_version),
            "de_url": "/api/tasks/%s/image/%d?variant=de&v=%s" % (task_id, index, version),
            "de_present": bool(pair is not None and pair.localized_rel),
            # 人工图不能被模型重生成覆盖（红线 6），所以这一张的优化入口要禁用。
            "manual": bool(pair is not None and pair.manual),
            "replaced_at": (manual_record or {}).get("replaced_at"),
            "warnings": (manual_record or {}).get("warnings", []),
            "metrics": _metrics(record),
        })
    return out


def _mirror_storage_status(account_dir: Path, post_id: str) -> dict:
    """Read only the local mirror journal; never construct a cloud client from a page view."""
    try:
        summary = MirrorService(cfg().state_dir, MirrorSettings.load()).status()
        if not summary['enabled']:
            return {**summary, 'counts': {'pending': 0, 'completed': 0, 'uncertain': 0, 'blocked': 0},
                    'operations': [], 'posts': {}, 'incomplete_source': False, 'missing_media': []}
        key = account_dir.name + '/' + post_id
        post = summary['posts'].get(key)
        if post is None:
            return {'enabled': True, 'status': 'idle',
                    'counts': {'pending': 0, 'completed': 0, 'uncertain': 0, 'blocked': 0},
                    'last_success_at': None, 'last_error': None, 'operations': [], 'posts': {},
                    'incomplete_source': False, 'missing_media': []}
        status = post.get('status') if post.get('status') in {'pending', 'completed', 'uncertain', 'blocked'} else 'uncertain'
        counts = {'pending': 0, 'completed': 0, 'uncertain': 0, 'blocked': 0}
        counts[status] = 1
        return {'enabled': True, 'status': status, 'counts': counts,
                'last_success_at': summary.get('last_success_at') if status == 'completed' else None,
                'last_error': None, 'operations': [], 'posts': {key: post},
                'incomplete_source': bool(post.get('incomplete_source')),
                'missing_media': list(post.get('missing_media') or [])}
    except Exception:
        return {'enabled': True, 'status': 'blocked',
                'counts': {'pending': 0, 'completed': 0, 'uncertain': 0, 'blocked': 0},
                'last_success_at': None, 'last_error': '镜像记录暂时无法读取，请由维护人员核对。',
                'operations': [], 'posts': {}, 'incomplete_source': False, 'missing_media': []}

def _storage_facts(source: engine.SourcePost) -> dict:
    """Storage evidence for one source post, assembled from read-only truth and journals."""
    row, account_dir = dict(source.row), source.account_dir
    from core.capture_state import verified_images
    media = store.media_storage_info(account_dir, row)
    images = [item for item in media if item.get('kind') == 'image']
    expected_images = len(images)
    saved_images = sum(item.get('storage_status') == 'saved' for item in images)
    statuses = {item.get('storage_status') for item in media}
    if 'corrupt' in statuses:
        local_status = 'corrupt'
    elif (not row.get('media_complete', True)
          or statuses.intersection({'missing', 'metadata_only'})
          or saved_images != expected_images):
        local_status = 'partial'
    else:
        local_status = 'complete'
    tags_origin = row.get('tags_origin')
    classified_by = tags_origin if tags_origin in {'auto', 'manual'} else 'legacy'
    directory = store.post_directory(account_dir, row)
    try:
        folder = directory.relative_to(account_dir).as_posix()
    except ValueError:
        folder = None
    return {
        'classified_by': classified_by,
        'account_dir': account_dir.name,
        'folder': folder,
        'first_archived_at': row.get('archived_at') if isinstance(row.get('archived_at'), str) else None,
        'local': {'status': local_status, 'saved_images': saved_images,
                  'expected_images': expected_images,
                  'source_media_complete': row.get('source_media_complete'),
                  'media_complete': row.get('media_complete'),
                  'source_media_count': row.get('source_media_count'),
                  'verified_images': verified_images(account_dir, row)},
        'database': query_index.index_status(history=account_dir.name not in cfg().active_accounts()),
        'feishu': _mirror_storage_status(account_dir, source.post_id),
        'media': media,
    }


def _archive_month(source: engine.SourcePost) -> str:
    """The existing directory month is evidence; only a new/unlocatable row falls back to Beijing time."""
    row = dict(source.row)
    try:
        directory = store.post_directory(source.account_dir, row)
    except (OSError, ValueError):
        return store.archive_month(row)
    return store.archive_month(dict(row, folder_name=directory.name))


def _metrics(record: Mapping[str, Any] | None) -> dict | None:
    """将图片指标字段映射到接口名称，不改变数值。"""
    if not isinstance(record, Mapping):
        return None
    return {
        "dhash_distance": record.get("dhash_distance"),
        "aspect_drift": record.get("aspect_drift_percent"),
        "scale_ratio": record.get("scale_factor"),
        "elapsed_s": record.get("elapsed_seconds"),
        # 旧记录没有这一项；null 表示"没量过"，不是"没改动"。
        "changed_pixel_ratio": record.get("changed_pixel_ratio"),
    }


def _compose_warnings(source: engine.SourcePost,
                      rules: engine.PublishRules,
                      when: datetime | None) -> list[str]:
    """提取 compose 的内容告警；环境状态另行展示，失败由 prepaid_issue 报告。"""
    if when is None:
        when = datetime.now(timezone.utc) + timedelta(days=1)
    try:
        post = compose.compose_post(
            source.post_id, when.astimezone(timezone.utc),
            account=source.account_dir.name, platform=source.platform,
            price_map=rules.price_map, warning_sink=None)
    except Exception:                                  # noqa: BLE001
        return []
    return [line for line in post.warnings if "G1 实测值" not in line]


def _text_suggestions(state_dir: Path, source, localized: dict) -> dict | None:
    """最近一次的只读建议清单。刷新页面不该让已经付过费的结果消失。"""
    row = text_suggestions.latest_for(Path(state_dir) / refinement.SUGGESTIONS_FILE,
                                      account=source.account_dir.name, post_id=source.post_id)
    if row is None:
        return None
    # 译文改过之后 quote 可能定位不到，所以只标过期，不隐藏也不自动重做。
    current = text_suggestions.is_current(
        row, source_text_sha256_value=localized["source_text_sha256"],
        text_de=localized["body_de"])
    return {"items": row["items"], "dropped": row["dropped"], "current": current,
            "job_id": row["job_id"], "body_de": row.get("body_de"),
            "source_text_sha256": row["source_text_sha256"],
            "text_de_sha256": row["text_de_sha256"],
            "generated_at": row["recorded_at"],
            "prompt_version": row["prompt_version"],
            "current_prompt_version": text_suggestions.SUGGEST_PROMPT_VERSION}


def task_detail(task_id: str, *, days: int = DEFAULT_DAYS,
                now: datetime | None = None) -> dict | None:
    """一条任务的完整详情；找不到返回 ``None``。契约见第 6 节。"""
    source = source_post(task_id)
    if source is None:
        return None
    ctx = _Context(days=days, now=now, selected_sources=[source])

    entry = _translation_of(source)
    human = _human_translation_of(source)
    effective = translation.effective_translation(dict(source.row), entry, human)
    reviewable = ctx.reviewable(source)
    text_en = source.text
    text_de = str(entry.get("text_de") or "") if entry else ""
    shown_text = str(effective.get("text_de") or "") if effective else text_de

    scheduled = ctx.scheduled_at(source)
    state = _review_state(ctx, source, reviewable, scheduled)
    when = scheduled
    if when is None and reviewable and state["status"] in {"pending_review", "edited"}:
        slots = ctx.allocate_slots([source])
        when = slots.get(source.ref)

    alerts, details = _hard_alerts(source, ctx.rules)
    author_kind, _flag = _author_kind(source, details, alerts)

    # 来源变化只比较正文指纹；提示词升级另由 machine_current 表示。
    bound_entry = human or entry
    bound = str(bound_entry.get("source_text_sha256") or "") if bound_entry else ""
    stale = bool(bound_entry) and bound != translation.source_text_sha256(text_en)

    coauthors = source.row.get("coauthors")
    events = review.history(source.account_dir, source.post_id)
    trail = [{"at": event["recorded_at"], "actor": None,
              "action": "text_edited" if event["action"] == "edited" else event["action"],
              "note": event["reason"], "wake_at": event["wake_at"]} for event in events]
    if human and not any(event["action"] == "edited" for event in events):
        trail.append({"at": human["recorded_at"], "actor": None, "action": "text_edited",
                      "revision": human["revision"]})
    tags = list(source.row.get("tags") or [])
    localized = localization.effective_draft(source.account_dir, dict(source.row), effective)
    publications = journal.history_for(ctx.state_dir, source.post_id, platform=source.platform)
    publication = publications[-1] if publications else None
    from publish.observations import status as delivery_status
    from core.paid_consent import fingerprint as source_fingerprint
    try:
        full_source_fingerprint, fingerprint_error = source_fingerprint(dict(source.row), source.account_dir), None
    except (OSError, ValueError, RuntimeError):
        full_source_fingerprint, fingerprint_error = None, '源文件或图片尚不能完整核对'
    body_risks = []
    for risk in ctx.risks.get(task_id, []):
        start, end = risk["en_span"]
        phrase = text_en[start:end]
        at = localized["source_body"].find(phrase) if phrase else -1
        if at >= 0:
            body_risks.append(dict(risk, en_span=[at, at + len(phrase)]))
    return {
        "id": task_id,
        "read_only": source.account_dir.name not in cfg().active_accounts(),
        "publication": publication,
        "delivery": delivery_status(ctx.state_dir, publication),
        "platform": source.platform,
        "status": state["status"],
        "review": state,
        "hard_alerts": alerts,
        "tags": tags,
        "tags_revision": tags_revision(tags),
        "storage": _storage_facts(source),
        "localization": localized,
        "localization_validation": localization.validate(localized),
        "hashtag_suggestions_enabled": hashtag_suggestions.suggestions_enabled(),
        "text_suggestions": _text_suggestions(ctx.state_dir, source, localized),
        "body_highlights": build_highlights(localized["source_body"], localized["body_de"]),
        "body_risks": body_risks,
        "risk_scan": ctx.risk_scans[task_id],
        "text": {
            "en": text_en,
            "de_machine": text_de or None,
            "de_human": human["text_de"] if human else None,
            "human_revision": human["revision"] if human else None,
            "source_text_sha256": translation.source_text_sha256(text_en),
            "stale": stale,
            "machine_current": translation.translation_is_current(dict(source.row), entry),
            "machine_prompt_version": entry.get('prompt_version') if entry else None,
            "current_prompt_version": translation.PROMPT_VERSION,
        },
        "highlights": build_highlights(text_en, shown_text) if shown_text else [],
        "risks": ctx.risks.get(task_id, []),
        # 日常改文案沿用机器依据；机器版过期时可用已重新复核的人工稿补图。
        "images": _images_of(source, translation.image_translation(
            dict(source.row), entry, human)),
        "schedule": ({"at": _iso(when), "channel": source.platform}
                     if when is not None else None),
        "meta": {
            'platform': source.platform, 'account': source.row.get('account'),
            'source_text_sha256': translation.source_text_sha256(source.text),
            'source_fingerprint': full_source_fingerprint, 'fingerprint_error': fingerprint_error,
            'snapshot_id': publication.get('snapshot_id') if publication else None,
            "permalink": source.row.get("permalink"),
            "created_at": source.row.get("created_at"),
            "owner": source.row.get("owner"),
            "coauthors": list(coauthors) if isinstance(coauthors, list) else [],
            "author_kind": author_kind,
            # 详情明确展示缺德语图等内容降级。
            "compose_warnings": _compose_warnings(source, ctx.rules, when),
        },
        "trail": trail,
    }


def source_post(task_id: str) -> engine.SourcePost | None:
    """按账号和帖子身份直接读源文件，历史链接不受日常队列窗口限制。"""
    parts = task_id.split('/')
    if len(parts) != 2 or not parts[1] or len(parts[1]) > 500 or '\\' in parts[1] or '..' in parts[1]:
        return None
    account, pid = parts
    directory = next((path for path in store.account_dirs(cfg().archive_dir) if path.name == account), None)
    if directory is None:
        return None
    platform = {'fa': 'facebook', 'in': 'instagram'}.get(account[:2])
    if not platform:
        return None
    indexed = {'post_id': pid, 'platform': platform, 'account': account[3:], 'created_at': None}
    if not (store.post_directory(directory, indexed) / 'post.json').exists():
        return None
    row, _ = store.read_post_truth(directory, indexed)
    created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) if row.get('created_at') else datetime.min.replace(tzinfo=timezone.utc)
    return engine.SourcePost(platform, directory, row, created, journal.source_ref(platform, pid))


def image_bytes(task_id: str, index: int, variant: str = "de", *,
                days: int = DEFAULT_DAYS,
                now: datetime | None = None) -> tuple[bytes, str] | None:
    """返回归档图片字节及媒体类型；缺德语图时回退原图，详情明确标记。"""
    source = source_post(task_id)
    if source is None or index < 0:
        return None
    source = _source_truth(source)
    media = _image_media(source)
    if index >= len(media):
        return None

    path: Path | None = None
    if variant == "de":
        entry = translation.image_translation(
            dict(source.row), _translation_of(source), _human_translation_of(source))
        if entry:
            try:
                for pair in image_de.review_image_pairs(
                        source.account_dir, source.row, entry):
                    if pair.media_index == index and pair.localized_rel:
                        path = source.account_dir / Path(pair.localized_rel)
                        break
            except (OSError, ValueError, ArchivePathError):
                path = None
    if path is None:
        raw = media[index].get("local_path")
        if not isinstance(raw, str) or not raw.strip():
            return None
        path = source.account_dir / Path(raw.replace("\\", "/"))

    try:
        # 复用物理路径检查，拒绝链接指向归档外文件。
        store.assert_physical_direct_path(
            path.parent, path, kind="file", label="审校台图片")
        data = path.read_bytes()
    except (ArchivePathError, OSError):
        return None
    return data, (mimetypes.guess_type(path.name)[0] or "application/octet-stream")


# CLI

def _print_list(days: int) -> int:
    payload = list_tasks(days=days)
    summary = payload["summary"]
    print("=== 近 %d 天图文帖：%d 篇（其中 %d 篇有硬闸告警）===\n"
          % (days, summary["total"], summary["with_hard_alerts"]))
    counts: dict[str, int] = {}
    for task in payload["tasks"]:
        counts[task["status"]] = counts.get(task["status"], 0) + 1
        at = (task["schedule"] or {}).get("at") or "—— 未分配槽位"
        alerts = "".join(" 🔴 " + a["label"] for a in task["hard_alerts"])
        risks = (" ⚠ %d 处需注意" % task["risk_count"]) if task["risk_count"] else ""
        flag = (" 👤 " + task["author_flag"]) if task["author_flag"] else ""
        print("%-16s %-25s %s" % (task["status"], at[:25], task["id"]))
        print("    %s张图 · %s%s%s%s"
              % (task["image_count"], task["platform"], alerts, risks, flag))
        if task["text_de_excerpt"]:
            print("    %s" % task["text_de_excerpt"])
    print("\n状态分布：%s" % "、".join(
        "%s %d" % (k, v) for k, v in sorted(counts.items())))
    return 0


def _print_detail(task_id: str, days: int) -> int:
    detail = task_detail(task_id, days=days)
    if detail is None:
        print("找不到任务 %s（id 形如 in_neakasa.tech/3965025107383038890）" % task_id)
        return 1
    print(json.dumps(detail, ensure_ascii=False, indent=2))
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    force_utf8()
    parser = argparse.ArgumentParser(
        description="审校台只读数据层（零网络、零费用、零写盘）")
    parser.add_argument("--list", action="store_true", help="打印任务列表摘要")
    parser.add_argument("--detail", metavar="ID", help="打印一条完整详情（JSON）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="回看天数（默认 %d）" % DEFAULT_DAYS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.detail:
        return _print_detail(args.detail, max(1, args.days))
    if args.list:
        return _print_list(max(1, args.days))
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

r"""审校台的**只读数据层**：从归档算出任务列表与详情。

列表以归档索引定位源帖，再读取 post.json 真相；文案同时读取机器与人工账本，
人工稿优先展示，源文变化保留稿件并提示复核。历史原型状态不参与读取。

源帖、译文和发布事实只读；列表会按需重建可删除的 SQLite 展示索引。
不调用任何付费 API，不启动浏览器。

一条贯穿全文的纪律（REQUIREMENTS.md 第 5.2 节）：

    **Web 层不许复制任何流水线逻辑，只能调用生产代码。**

所以下面每一个判断都指得到一个既有函数：

===================  =========================================================
"这篇在范围内吗"     ``pipeline.engine.load_sources``（内部用 ``out_of_scope_reason``）
"这篇有硬闸告警吗"   ``pipeline.engine.prepaid_issue``
"排到什么时刻"       ``pipeline.engine.next_slots``
"已经排过期了吗"     ``publish.journal.scheduled_record_for_refs``
"译文还算不算数"     ``core.translated.translation_is_current``
"金额被动过吗"       ``core.translated.money_preserved``
"标签被动过吗"       ``core.translated.hashtags_preserved``
"数字要人确认吗"     ``core.translated.review_numeric_flags``
"德语图有没有"       ``localize_images.review_image_pairs``
"图选哪张/回退没有"  ``publish.compose.compose_post``（``image_sources`` / ``warnings``）
===================  =========================================================

本模块自己**只做两件事**：把这些返回值拼成 web/DESIGN.md 第 6 节
那份 JSON 契约；以及**把标记定位到字符下标**（那三个 regex 只判"有没有"，
不给位置，而界面要在正文里画出来）。定位复用的是 ``core.translated`` 里
**同一个正则对象**——不是照抄一份——所以那边改了这边自动跟着改，
不存在两份实现漂移的可能。
"""
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

import localize_images                                 # noqa: E402
from core import store, review, localization                         # noqa: E402
from core import translated as translation             # noqa: E402
from core.config import cfg                            # noqa: E402
from core.console import force_utf8                    # noqa: E402
from core.store import ArchivePathError                # noqa: E402
from pipeline import engine, risk_scan                 # noqa: E402
from publish import compose, journal                   # noqa: E402
from web.api import query_index                   # noqa: E402

# 那三条判据的正则**对象本身**，不是复制品。
# core.translated 的 review_numeric_flags 只回答"有没有"，界面还需要"在哪"——
# 直接借它的正则来定位，是唯一一种零漂移的做法：那边改了正则，这边画出来的
# 位置自动跟着改。照抄一份到 web/ 才是这个项目吃过亏的形状。
from core.translated import (_IMPERIAL_RE,             # noqa: E402
                             _MONEY_RE,
                             _MONEY_TOKEN_RE,
                             _SIZE_RE)

#: 列表默认口径：近 90 天。与 `pipeline preflight --days` 的默认值一致。
DEFAULT_DAYS = 90

# ---------------------------------------------------------------------------
# 状态模型（PROTOTYPE_DESIGN.md 第 7 节）
# ---------------------------------------------------------------------------

#: 业务视角的状态，**不是**生产侧的发布五态（那是系统视角，摆给业务同事只会造成困惑）。
#:
#: ``not_ready`` 是第 7 节四态之外新加的第五个值（2026-09-08 用户拍板）：
#: 近 90 天 62 篇图文帖里，绝大多数**还没翻译**，四态里没有一个能表达这件事——
#: ``pending_review`` 是"待我审"，而没有译文根本无从审起。前端见到这个值就把
#: 「查看」禁掉。它在切换到生产时天然对应「译文还没跑出来」，不是临时凑数。
STATUS_NOT_READY = "not_ready"
STATUS_PENDING_REVIEW = "pending_review"
STATUS_EDITED = "edited"
STATUS_SCHEDULED = "scheduled"


# ---------------------------------------------------------------------------
# 字符定位：把"有没有"变成"在哪"
# ---------------------------------------------------------------------------

def _paired_spans(pattern, text_en: str, text_de: str
                  ) -> list[tuple[list[int] | None, list[int] | None]]:
    """同一个正则在两侧各自的命中位置，按出现顺序配对。

    数量对不上时用 ``None`` 补齐——**那本身就是信号**：原文三处金额、译文只剩
    两处，第三条的 ``de_span`` 是 null，界面上就是"这处在译文里找不到"。
    """
    left = [list(m.span()) for m in pattern.finditer(text_en or "")]
    right = [list(m.span()) for m in pattern.finditer(text_de or "")]
    return list(zip_longest(left, right))


def _hashtag_spans(text: str) -> list[list[int]]:
    """按 ``extract_hashtags`` 的结果在原串里顺序定位。

    不自己写扫描：标签的边界规则（Unicode 组合记号、连续 ``#``、句尾标点）
    在 ``core.translated`` 里有一份，那份是真相。这里只负责把它认出来的那些
    串按出现顺序找回下标——游标只前进，所以重复标签不会都指向第一处。
    """
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
    """确定性检查（PROTOTYPE_DESIGN.md 第 8 节的**红色**那一列）。

    ⛔ **判断全部来自 core.translated，本函数一条规则都不新增。**
    三个函数各自回答"有没有问题"，这里只负责把它们的结论落到字符下标上，
    并按第 6 节的契约拼成 ``highlights`` 数组。

    ``severity`` 分两档，都属于红色系：

    * ``error`` —— ``money_preserved`` / ``hashtags_preserved`` 判违规。
      语义是**这里错了**：不该动的内容被动了。
    * ``warn``  —— ``review_numeric_flags`` 的三类。语义是**这里要人确认**
      （金额要换成德国站定价、尺码要不要转 EU 码、英制单位换算对不对），
      不是"错了"。

    ⚠️ **@提及没有做。** PROTOTYPE_DESIGN.md 第 8 节把它列在 regex 覆盖范围里，
    但 ``core/`` 里根本没有对应的检查函数（``hashtags_preserved`` 只管 ``#``）。
    按"找不到现成函数就不要在 web/ 里另写一份"的纪律，本轮留空，记为缺口。
    """
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

    # ---- 需人工确认的数字 ----
    # review_numeric_flags 按 金额 → 尺码 → 英制 的固定顺序**只在命中时**追加，
    # 所以用同一批正则判一次命中，就能知道返回的第 i 句对应哪一类。
    # 这不是"再判一遍"——用的是同一个正则对象，命中与否按构造完全一致。
    flags = translation.review_numeric_flags(text_en, text_de)
    # 判命中用 review_numeric_flags 自己那三个正则；**画范围**另说：
    # _MONEY_RE 只匹配「符号 + 一位数字」（它只需要回答"有没有金额"），
    # 拿它画出来就是把 $219.99 标成 $2。所以金额那一类改用完整 token 正则
    # _MONEY_TOKEN_RE 取范围——命中与否仍由 _MONEY_RE 决定，两者不会分叉。
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


# ---------------------------------------------------------------------------
# 风险预扫描（真实状态账本的只读视图）
# ---------------------------------------------------------------------------

def load_risks(state_dir: Path, sources: Mapping[str, engine.SourcePost]) -> tuple[dict, dict]:
    """返回当前扫描视图及其中仍有效的风险；失败/过期不会伪装成空风险成功。"""
    views = {task_id: risk_scan.result_for(source.account_dir, dict(source.row),
                                           state_dir=state_dir)
             for task_id, source in sources.items()}
    risks = {task_id: list(view["risks"]) for task_id, view in views.items()
             if view["status"] == "completed"}
    return views, risks


# ---------------------------------------------------------------------------
# 归档取数
# ---------------------------------------------------------------------------

def task_id_of(source: engine.SourcePost) -> str:
    """契约里的任务 id：``<账号目录>/<post_id>``（第 6 节的例子就是这个形状）。"""
    return "%s/%s" % (source.account_dir.name, source.post_id)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _hard_alerts(source: engine.SourcePost,
                 rules: engine.PublishRules) -> tuple[list[dict], dict | None]:
    """调 ``prepaid_issue`` 拿硬闸结论，返回 (告警数组, 原始 issue.details)。

    ⚠️ **单来源候选，不做跨平台归并。** REQUIREMENTS.md 第 3.3 节的目标路由
    模型是"爬 FB 只发 FB、爬 IG 只发 IG，两条独立车道互不合并"；现状代码那套
    两两配对是被明确标为**方向错**的返工项。列表按 62 篇源帖展示，正是目标模型
    的形状，所以这里构造的是 ``reconcile`` 给未配对来源用的同一种候选。
    """
    candidate = engine.Candidate(source, (source,), "independent")
    issue = engine.prepaid_issue(candidate, rules)
    if issue is None:
        return [], None
    return ([{"code": issue.kind, "label": _alert_label(issue)}],
            dict(issue.details))


def _alert_label(issue: engine.HumanItem) -> str:
    """一行能看懂的告警文案。

    ⛔ **不做 kind → 文案的全量映射表**：那张表会在 ``prepaid_issue`` 新增一种
    kind 时静默漏掉一类告警。这里只对已知几种做短句，**其余一律回落到生产自己
    写的那句 summary**——可能长一点，但绝不会消失。
    """
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
    """返回 (author_kind, author_flag)。

    三分法沿用 compose 的定义：``DePost.is_collaboration`` 就是 owner != account。
    "第三方"这一档不自己判——它等价于 ``prepaid_issue`` 报出的
    ``unknown_collaborator``（作者不在 ``[publish.trusted_owners]`` 里）。

    ``author_flag`` **常态是 null**（第 10 节：近期 IG 帖 35/36 是合作帖，
    那是常态，每行都标就是噪声）。只有第三方作者才给值，因为那才是需要她
    停下来的信号。
    """
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
    media = source.row.get("media")
    if not isinstance(media, list):
        return []
    return [item for item in media
            if isinstance(item, Mapping) and item.get("kind") == "image"]


class _Context:
    """一次请求内的取数上下文。构造一次，列表与详情共用。"""

    def __init__(self, *, days: int = DEFAULT_DAYS,
                 now: datetime | None = None, selected_sources=None) -> None:
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.rules = engine.publish_rules()
        self.state_dir = cfg().state_dir
        # 只看 `[targets]` 当前在跑的账号。冻结的 `in_neakasa.tech` 今天是靠
        # 90 天窗口碰巧挡在外面的（它最后一篇 2026-06），窗口一放宽就会漏进审校队列
        # —— 而它按业务决定已经「只读、不进流水线」。判据与 pipeline 那边同一条。
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
        """给还没排期、且**有译文**的任务分配德国 10:00/17:00 空槽。

        ⚠️ ``next_slots`` **可能返回少于请求数**，这不是异常：composer 的日期
        选择器不允许跨月（2026-09-01 实测），可排的槽在每个月末真的会用完。
        分不到的任务 ``schedule`` 是 null，界面上排在最后。

        没有译文的任务**不参与分配**——生产侧 ``pipeline run`` 本来也只给
        ``ready_to_publish`` 的候选分配槽位，凭空给它们编一个时刻就是把假数据
        混进了"读全真"的那一半。
        """
        occupied = [when for when in
                    (self.scheduled_at(item) for item in self.sources.values())
                    if when is not None]
        slots = engine.next_slots(self.now, occupied, len(pending), self.rules)
        return {item.ref: slot for item, slot in zip(pending, slots)}


# ---------------------------------------------------------------------------
# GET /api/tasks
# ---------------------------------------------------------------------------

def list_tasks(*, days: int = DEFAULT_DAYS,
               now: datetime | None = None, status: str | None = None,
               tag: str | None = None, month: str | None = None, platform: str | None = None,
               scope: str = 'review', page: int = 1, limit: int | None = None) -> dict:
    """任务列表。契约见 web/DESIGN.md 第 6 节。

    排序：按 ``schedule.at`` 升序（最急的在最上面）；没有排期的排在最后，
    内部按原帖时间倒序（最新的先看）。
    """
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
        tasks.append({
            "id": task_id,
            "source_text_sha256": translation.source_text_sha256(source.text),
            "platform": source.platform,
            "thumbnail_url": "/api/tasks/%s/image/0?variant=de" % task_id,
            "text_de_excerpt": excerpt(entry.get("text_de", "")) if entry else "",
            "image_count": len(_image_media(source)),
            "tags": list(source.row.get("tags") or []),
            "month": str(source.row.get("created_at") or "")[:7],
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
    counts = {state: sum(item["status"] == state for item in tasks)
              for state in review.STATUSES | {STATUS_NOT_READY}}
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
            "tags": available_tags,
        },
    }


def history_tasks(*, now=None, status=None, tag=None, month=None, platform=None, page=1, limit=50):
    result = query_index.history_page(now=now, status=status, tag=tag, month=month,
                                     platform=platform, page=page, limit=limit)
    tasks = []
    for row in result['rows']:
        tasks.append({'id': row['id'], 'platform': row['platform'], 'month': row['month'],
                      'created_at': row.get('created_at'), 'account': row['account_dir'],
                      'read_only': row['account_dir'] not in cfg().active_accounts(),
                      'text_de_excerpt': excerpt(row.get('text_de') or row.get('text') or ''),
                      'tags': row['tags'], 'status': row['status'],
                      'image_count': len(row.get('media') or []),
                      'thumbnail_url': '/api/tasks/' + row['id'] + '/image/0?variant=de'})
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


# ---------------------------------------------------------------------------
# GET /api/tasks/<id>
# ---------------------------------------------------------------------------

def _images_of(source: engine.SourcePost, entry: dict | None) -> list[dict]:
    """逐张图：原图 / 德语图 / 四项指标。

    德语图在不在，由 ``localize_images.review_image_pairs`` 判——它同时管着
    "人工放的图优先于程序产出"和"旧程序产物不许错配新原文/新译文"两条规则，
    照抄任何一条都会在某次改版后和 K 组分叉。
    """
    task_id = task_id_of(source)
    pairs = []
    if entry:
        try:
            pairs = localize_images.review_image_pairs(
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
        version = "%s-%s-%s" % (
            source_version, text_version, (record or {}).get("output_sha256") or "manual")
        out.append({
            "index": index,
            "original_url": "/api/tasks/%s/image/%d?variant=original&v=%s" % (
                task_id, index, source_version),
            "de_url": "/api/tasks/%s/image/%d?variant=de&v=%s" % (task_id, index, version),
            "de_present": bool(pair is not None and pair.localized_rel),
            "metrics": _metrics(record),
        })
    return out


def _metrics(record: Mapping[str, Any] | None) -> dict | None:
    """images_de.jsonl 的四项指标，字段名按第 6 节的契约改写。

    ⚠️ 只改名，不改值：``aspect_drift_percent`` → ``aspect_drift``、
    ``scale_factor`` → ``scale_ratio``、``elapsed_seconds`` → ``elapsed_s``。
    """
    if not isinstance(record, Mapping):
        return None
    return {
        "dhash_distance": record.get("dhash_distance"),
        "aspect_drift": record.get("aspect_drift_percent"),
        "scale_ratio": record.get("scale_factor"),
        "elapsed_s": record.get("elapsed_seconds"),
    }


def _compose_warnings(source: engine.SourcePost,
                      rules: engine.PublishRules,
                      when: datetime | None) -> list[str]:
    """借 ``compose_post`` 的组装结果拿"缺德语图，已回退原图"这类告警。

    只在有译文时能成；失败（金额未映射、素材不齐等）本身已经由
    ``prepaid_issue`` 报成硬闸告警，这里静默跳过不会丢信息。

    ⚠️ 只保留**跟这篇内容有关**的告警。compose 在非严格模式下还会带两条
    "G1 实测值尚无"的环境提示（IG 画幅约束、定时窗口），那是部署状态不是内容
    问题，摆到审校台上只会让人学会忽略告警。
    """
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

    # 原文变更判据：译文绑的指纹与当前正文对不上。**不是** translation_is_current
    # ——那个还包含提示词版本，而提示词升级不是"原文已变更"。
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
        "tags": tags,
        "tags_revision": tags_revision(tags),
        "localization": localized,
        "localization_validation": localization.validate(localized),
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
            # ⚠️ **这是第 6 节契约之外唯一新增的字段**，理由：
            # 「缺德语图，已回退原图」这类降级告警来自 compose 的组装结果，
            # 而第 6 节的 meta 没有任何位置放得下它。降级本身是允许的
            # （能降级的就降级），但**必须让人看见**——没有这个字段，
            # 详情页上"这篇其实有 4 张图是英文的"就彻底消失了。
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


# ---------------------------------------------------------------------------
# GET /api/tasks/<id>/image/<n>
# ---------------------------------------------------------------------------

def image_bytes(task_id: str, index: int, variant: str = "de", *,
                days: int = DEFAULT_DAYS,
                now: datetime | None = None) -> tuple[bytes, str] | None:
    """直接读归档字节（Demo board 已验证这条路），返回 (数据, media type)。

    ``variant=de`` 且这张没有德语图时**回退原图**，与 ``compose._choose_images``
    的行为一致（缺德语图不是"没有图"，是"用了原图"）。降级不会被藏起来：
    详情页的 ``images[].de_present`` 与元信息里的组装告警都会说出来。
    """
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
                for pair in localize_images.review_image_pairs(
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
        # 归档是我们自己的数据，但读之前仍然确认它是真实普通文件——
        # symlink/junction 指出去就成了任意文件读取。判据用 store 那份。
        store.assert_physical_direct_path(
            path.parent, path, kind="file", label="审校台图片")
        data = path.read_bytes()
    except (ArchivePathError, OSError):
        return None
    return data, (mimetypes.guess_type(path.name)[0] or "application/octet-stream")


# ---------------------------------------------------------------------------
# CLI —— 阶段 0 的验收口：先证明数据读得出来，再谈界面
# ---------------------------------------------------------------------------

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

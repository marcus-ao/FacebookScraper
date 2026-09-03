r"""G9 assisted 对账流水线：每次从各阶段真相源重建，不建立发布任务队列。"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from PIL import Image

from core.config import cfg
from core.store import Archive, post_dirname
from publish import journal
from publish.compose import ComposeError, compose_post
import translate as translation
from core.paid_model import FileLock
from core import imagehash

STATE_NAME = "pipeline_state.json"
NEEDS_HUMAN_NAME = "needs_human.jsonl"
NEEDS_HUMAN_HTML = "needs_human.html"
PAIR_WINDOW = timedelta(hours=30)
SIMILARITY_THRESHOLD = 0.90
DHASH_DISTANCE = 1
CANDIDATE_ITEM_KINDS = frozenset({
    "material_gate", "unknown_owner", "unknown_collaborator", "unmapped_price",
    "budget_stopped", "offline_gate", "paid_request_unresolved",
    "publish_unresolved", "late_scheduled_overlap", "ready_to_publish",
})
PREPAID_ITEM_KINDS = frozenset({
    "material_gate", "unknown_owner", "unknown_collaborator", "unmapped_price",
})


class PipelineRunError(RuntimeError):
    pass


class BudgetStopped(PipelineRunError):
    pass


def PipelineOperationLock(path: Path) -> FileLock:   # noqa: N802（保留原名）
    """让 daily/catch-up/手工 run/approve 共用同一把跨进程锁。"""
    return FileLock(path, error_type=PipelineRunError,
                    busy_message="另一个流水线实例正在运行；本次不做任何改动。")


@dataclass(frozen=True)
class PublishRules:
    timezone: str
    ui_timezone: str
    slots: tuple[time, ...]
    price_map: Mapping[str, str]
    trusted_owners: Mapping[str, frozenset[str]]
    source_accounts: Mapping[str, str]


@dataclass(frozen=True)
class SourcePost:
    platform: str
    account_dir: Path
    row: Mapping[str, Any]
    created_at: datetime
    ref: str

    @property
    def post_id(self) -> str:
        return str(self.row["post_id"])

    @property
    def text(self) -> str:
        return str(self.row.get("text") or "")

    @property
    def account(self) -> str:
        return str(self.row.get("account") or "")


@dataclass(frozen=True)
class Candidate:
    canonical: SourcePost
    sources: tuple[SourcePost, ...]
    relation: str

    @property
    def source_refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.sources)


@dataclass(frozen=True)
class HumanItem:
    item_id: str
    kind: str
    source_refs: tuple[str, ...]
    summary: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def opened_event(self, when: datetime) -> dict:
        return {
            "schema_version": 1,
            "event": "opened",
            "status": "open",
            "item_id": self.item_id,
            "kind": self.kind,
            "source_refs": list(self.source_refs),
            "summary": self.summary,
            "details": dict(self.details),
            "recorded_at": when.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        }


@dataclass(frozen=True)
class ReconcileResult:
    candidates: tuple[Candidate, ...]
    human_items: tuple[HumanItem, ...]
    source_count: int


@dataclass(frozen=True)
class BudgetSnapshot:
    daily_usd: float
    monthly_usd: float
    unknown: tuple[str, ...] = ()


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)


def _parse_aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def pipeline_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_NAME


def load_pipeline_state(state_dir: Path) -> dict:
    path = pipeline_state_path(state_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineRunError("pipeline_state.json 读不了：%s" % exc) from exc
    if not isinstance(data, dict):
        raise PipelineRunError("pipeline_state.json 顶层不是对象")
    return data


def activation_time(state_dir: Path) -> datetime | None:
    return _parse_aware(load_pipeline_state(state_dir).get("activated_at"))


def activation_blockers(state_dir: Path) -> tuple[str, ...]:
    """G8 通过与否是**可机检的**，不必靠 `--g8-verified` 那句自觉。

    激活早了不是"顺序不好看"，是**真花钱**：``assisted`` 的 run 会先付费
    翻译、再付费调图，最后才在 ``compose_post`` 的
    ``require_verified_ui_constraints`` 上失败闭合 —— 每一篇都这样，
    而且每天跑一次。所以这两条在激活那一刻就要拦住。

    返回空元组表示可以激活；否则每条是一句"缺什么、怎么补"。
    """
    blockers: list[str] = []
    if cfg().get("publish", "ui_constraints_verified", False) is not True:
        blockers.append(
            "[publish].ui_constraints_verified 仍是 false —— assisted 会先花钱"
            "翻译/调图，再逐篇卡在离线硬闸上。先按 docs/GO_LIVE.md 第 3b 步"
            "量完 14 个 UI 上限。")
    if not journal.scheduled_source_refs(state_dir):
        blockers.append(
            "state/published.jsonl 里没有任何 status=scheduled 记录 —— "
            "G8 从未在真机上成功过。先跑 run_publish_post.bat --post-id <id> "
            "--at <ISO> --submit；若排期是你手工点的，用 --mark-scheduled 结转。")
    return tuple(blockers)


def activate(state_dir: Path, *, g8_verified: bool,
             now: datetime | None = None) -> datetime:
    """原子写入一次性边界；已有边界绝不重置，避免历史内容重新入队。"""
    if not g8_verified:
        raise PipelineRunError(
            "pipeline activate 只允许在 G8 真机验收通过后执行；"
            "请显式加 --g8-verified")
    with PipelineOperationLock(Path(state_dir) / "pipeline.lock"):
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        state = load_pipeline_state(state_dir)
        existing = _parse_aware(state.get("activated_at"))
        if existing is not None:
            return existing
        state.update({
            "schema_version": 1,
            "activated_at": now.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"),
            "activation_evidence": "user-confirmed-g8",
        })
        _atomic_json(pipeline_state_path(state_dir), state)
        return now


def mark_run_success(state_dir: Path, now: datetime | None = None) -> None:
    state = load_pipeline_state(state_dir)
    state["schema_version"] = 1
    state["last_successful_run"] = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _atomic_json(pipeline_state_path(state_dir), state)


def publish_rules(raw: Mapping[str, Any] | None = None) -> PublishRules:
    raw = raw if raw is not None else (cfg()._d.get("publish", {}) or {})
    if not isinstance(raw, Mapping):
        raise PipelineRunError("[publish] 不是表")
    schedule = raw.get("schedule_rule")
    if not isinstance(schedule, Mapping):
        raise PipelineRunError("缺少 [publish.schedule_rule]")
    values = schedule.get("times")
    if not isinstance(values, list) or values != ["10:00", "17:00"]:
        raise PipelineRunError(
            "[publish.schedule_rule].times 必须固定为 ['10:00', '17:00']")
    slots: list[time] = []
    for value in values:
        try:
            slots.append(time.fromisoformat(value))
        except ValueError as exc:
            raise PipelineRunError("无效排期槽：%r" % value) from exc
    timezone_name = str(raw.get("timezone") or "")
    ui_timezone = str(raw.get("ui_timezone") or "")
    if timezone_name != "Europe/Berlin":
        raise PipelineRunError("[publish].timezone 必须是 Europe/Berlin")
    if not ui_timezone:
        raise PipelineRunError("[publish].ui_timezone 不能为空")
    price_map = raw.get("price_map") or {}
    if not isinstance(price_map, Mapping) or not all(
            isinstance(key, str) and key.strip()
            and isinstance(value, str) and value.strip()
            for key, value in price_map.items()):
        raise PipelineRunError("[publish.price_map] 必须是非空字符串到非空字符串的表")
    try:
        translation.apply_money_mapping("", price_map)
    except ValueError as exc:
        raise PipelineRunError("[publish.price_map] 冲突：%s" % exc) from exc
    trusted_raw = raw.get("trusted_owners") or {}
    if not isinstance(trusted_raw, Mapping):
        raise PipelineRunError("[publish.trusted_owners] 必须是表")
    trusted: dict[str, frozenset[str]] = {}
    for platform in ("facebook", "instagram"):
        owners = trusted_raw.get(platform, [])
        if not isinstance(owners, list) or not all(
                isinstance(owner, str) and owner.strip() for owner in owners):
            raise PipelineRunError(
                "[publish.trusted_owners].%s 必须是非空字符串数组" % platform)
        trusted[platform] = frozenset(owner.strip().lower() for owner in owners)
    targets = cfg()._d.get("targets", {}) or {}
    if not isinstance(targets, Mapping):
        raise PipelineRunError("[targets] 必须是表")
    source_accounts = {
        platform: str(targets.get(platform) or "").strip().lower()
        for platform in ("facebook", "instagram")
    }
    if any(not value for value in source_accounts.values()):
        raise PipelineRunError("[targets] 必须明确配置 facebook 与 instagram 来源账号")
    return PublishRules(
        timezone=timezone_name, ui_timezone=ui_timezone,
        slots=tuple(slots), price_map=dict(price_map), trusted_owners=trusted,
        source_accounts=source_accounts)


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def _post_dir(source: SourcePost) -> Path:
    return source.account_dir / "posts" / post_dirname(
        source.post_id, str(source.row.get("created_at") or ""))


def _image_paths(source: SourcePost) -> tuple[Path, ...]:
    paths: list[Path] = []
    for item in source.row.get("media") or []:
        if not isinstance(item, Mapping) or item.get("kind") != "image":
            continue
        raw = item.get("local_path")
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = source.account_dir / Path(raw.replace("\\", "/"))
        paths.append(candidate)
    return tuple(paths)


def dhash(path: Path) -> int:
    """素材配对用的 dHash。实现在 core/imagehash，与图片德语化共用同一份。

    ⚠️ 2026-09-02 之前这里独立实现，而 localize_images 另有一份用不同滤波器
    的同名实现——对同一张图给出不同的值。现在两边共用一份（BILINEAR，
    依据见 core/imagehash 的模块说明）。
    """
    return imagehash.dhash_file(path)


def dhash_distance(left: int, right: int) -> int:
    return imagehash.hamming(left, right)


def _media_relation(left: SourcePost, right: SourcePost,
                    cache: dict[str, tuple[int, ...] | None]
                    ) -> tuple[bool, bool, tuple[int, ...]]:
    """返回 (同数量且逐图≤1, 已有对应素材逐图≤1, 距离)。"""
    def hashes(source: SourcePost) -> tuple[int, ...] | None:
        if source.ref in cache:
            return cache[source.ref]
        paths = _image_paths(source)
        try:
            result = tuple(dhash(path) for path in paths)
        except (OSError, ValueError):
            result = None
        cache[source.ref] = result
        return result

    lhashes, rhashes = hashes(left), hashes(right)
    if lhashes is None or rhashes is None:
        return False, False, ()
    distances = tuple(dhash_distance(a, b) for a, b in zip(lhashes, rhashes))
    corresponding = bool(distances) and all(value <= DHASH_DISTANCE for value in distances)
    exact = (len(lhashes) == len(rhashes) and corresponding
             and len(distances) == len(lhashes))
    return exact, corresponding, distances


def configured_account_pairs() -> frozenset[tuple[str, str]]:
    """只允许配置中明确属于同一业务目标的 FB/IG 账号互相配对。"""
    raw = cfg()._d.get("targets", {}) or {}
    if not isinstance(raw, Mapping):
        return frozenset()
    facebook = str(raw.get("facebook") or "").strip().lower()
    instagram = str(raw.get("instagram") or "").strip().lower()
    if not facebook or not instagram:
        return frozenset()
    return frozenset({(facebook, instagram)})


def _item_id(kind: str, sources: Iterable[SourcePost], extra: str = "") -> str:
    payload = {
        "kind": kind,
        "sources": sorted((item.ref, journal.text_sha256(item.text))
                          for item in sources),
        "extra": extra,
    }
    digest = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return "%s-%s" % (kind.replace("_", "-"), digest[:20])


def needs_human_path(state_dir: Path) -> Path:
    return Path(state_dir) / NEEDS_HUMAN_NAME


def needs_human_events(state_dir: Path) -> list[dict]:
    path = needs_human_path(state_dir)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PipelineRunError("%s 第 %d 行损坏：%s" % (
                path, number, exc)) from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def latest_human_items(state_dir: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in needs_human_events(state_dir):
        item_id = row.get("item_id")
        if isinstance(item_id, str):
            latest[item_id] = row
    return latest


def append_human_item(state_dir: Path, item: HumanItem,
                      now: datetime | None = None) -> bool:
    current = latest_human_items(state_dir).get(item.item_id)
    if current is not None and current.get("status") == "open":
        return False
    path = needs_human_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = item.opened_event(now or datetime.now(timezone.utc))
    if current is not None:
        # 条件曾被结转、后来又真实出现时必须重开；否则旧 resolved 会把
        # 当前硬闸静默藏掉。连续重复 run 仍因 latest=open 而幂等。
        event["event"] = "reopened"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(
            event,
            ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    build_human_html(state_dir)
    return True


def resolve_human_item(state_dir: Path, item_id: str, *, resolution: str,
                       selected_ref: str = "",
                       now: datetime | None = None) -> None:
    current = latest_human_items(state_dir).get(item_id)
    if current is None or current.get("status") != "open":
        raise PipelineRunError("待确认项不存在或已经结转：%s" % item_id)
    event = {
        "schema_version": 1,
        "event": "resolved",
        "status": "resolved",
        "item_id": item_id,
        "kind": current.get("kind"),
        "source_refs": current.get("source_refs") or [],
        "resolution": resolution,
        "selected_ref": selected_ref,
        "recorded_at": (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = needs_human_path(state_dir)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    build_human_html(state_dir)


def resolve_cleared_items(state_dir: Path, source_refs: Iterable[str], *,
                          active_item_ids: Iterable[str] = (),
                          kinds: Iterable[str] = CANDIDATE_ITEM_KINDS,
                          resolution: str = "gate_cleared",
                          now: datetime | None = None) -> int:
    """结转同一候选已经不再成立的旧硬闸/ready 项。"""
    wanted_refs = set(source_refs)
    active = set(active_item_ids)
    allowed = set(kinds)
    matches = [
        row for row in latest_human_items(state_dir).values()
        if row.get("status") == "open"
        and row.get("kind") in allowed
        and str(row.get("item_id") or "") not in active
        and set(row.get("source_refs") or []) == wanted_refs
    ]
    for row in matches:
        resolve_human_item(
            state_dir, str(row["item_id"]), resolution=resolution, now=now)
    return len(matches)


# 这些类型 **不能** 用 approve 结转：它们是硬闸，靠改配置/补素材再 run 才会消失。
# 页面必须明说，否则人会对着一个 unmapped_price 反复敲 approve 然后被顶回来。
_GATE_ITEM_KINDS = {
    "material_gate": "补齐素材（重跑增量把图下全）后重跑 pipeline run",
    "unknown_owner": "确认归属后重跑 pipeline run",
    "unknown_collaborator": "确认二次使用授权，把作者加进 config.toml 的 "
                            "[publish.trusted_owners]，再重跑 pipeline run",
    "unmapped_price": "在 config.toml 的 [publish.price_map] 补上这几个金额串的"
                      "德国站定价，再重跑 pipeline run",
    "offline_gate": "按说明修好离线硬闸点名的问题后重跑 pipeline run",
    "budget_stopped": "预算到顶：调 [pipeline] 的预算或等下个周期",
    "paid_request_unresolved": "查 state/paid_requests.jsonl 人工结转",
    "publish_unresolved": "去 Business Suite 确认远端到底排上没有，再用 "
                          "run_publish_post.bat --mark-scheduled 结转",
}


def _approve_hints(open_rows: list[dict]) -> str:
    """把「读 ID → 手抄进命令行」变成「复制一行」。

    这是稳态里人**每天**都要做的那一下。ID 是内容哈希，手抄必然出错，
    出错的表现又是"命令报错"而不是"发错帖"，于是只会让人越来越不想看这个页面。
    """
    ready = [row for row in open_rows if row.get("kind") == "ready_to_publish"]
    similar = [row for row in open_rows
               if row.get("kind") == "similar_cross_platform"]
    gates = [row for row in open_rows if row.get("kind") in _GATE_ITEM_KINDS]
    blocks = []
    if ready:
        # item_id 是 `kind-hash20`、ref 是 `platform:post_id`，都只含
        # [a-z0-9.:-]，cmd.exe 下不需要引号 —— 加了反而会被 html.escape 成 &quot;。
        command = ("python pipeline.py approve"
                   + "".join(" --item-id %s" % row["item_id"] for row in ready))
        cards = []
        for row in ready:
            details = row.get("details") or {}
            author = str(details.get("source_author") or "")
            notes = [str(item) for item in (details.get("warnings") or [])]
            cards.append(
                "<div style='border:1px solid #ccc;padding:.7rem;margin:.5rem 0'>"
                "<div><b>%s</b> · %s · %d 张图（%s）%s</div>"
                "<pre>%s</pre>%s</div>"
                % (html.escape(str(details.get("post_id") or "")),
                   html.escape(str(details.get("platform") or "")),
                   int(details.get("image_count") or 0),
                   html.escape("、".join(details.get("image_sources") or [])),
                   ("　<span style='color:#a60'>合作帖原作者：%s</span>"
                    % html.escape(author)) if author else "",
                   html.escape(str(details.get("text_de_preview") or "")),
                   ("<div style='color:#a60'>%s</div>"
                    % "<br>".join(html.escape(item) for item in notes))
                   if notes else ""))
        blocks.append(
            "<h2>① 可以直接批准的 %d 项（会真的提交到 Business Suite）</h2>"
            "%s"
            "<p>确认过上面的正文/图片之后，复制整行执行"
            "（排期时刻由 approve 读远端空槽后分配）：</p><pre>%s</pre>"
            % (len(ready), "".join(cards), html.escape(command)))
    if similar:
        lines = []
        for row in similar:
            refs = list(row.get("source_refs") or [])
            lines.append(
                "python pipeline.py approve --item-id %s --select-source %s=%s"
                % (row["item_id"], row["item_id"], refs[0] if refs else "平台:帖子ID"))
            if len(refs) > 1:
                lines.append("#   另一个版本是：%s" % refs[1])
        blocks.append(
            "<h2>② 需要先选版本的 %d 项</h2>"
            "<p>跨平台两个版本有差异，必须指定用哪一个（下面默认选了第一个，"
            "要换就把 <code>=</code> 后面换成另一个 ref）：</p><pre>%s</pre>"
            % (len(similar), html.escape("\n".join(lines))))
    if gates:
        rows = "".join(
            "<tr><td>%s</td><td>%s</td></tr>"
            % (html.escape(str(row.get("kind"))),
               html.escape(_GATE_ITEM_KINDS[str(row.get("kind"))]))
            for row in {str(row.get("kind")): row for row in gates}.values())
        blocks.append(
            "<h2>③ 这 %d 项 <b>不能</b> 用 approve 结转</h2>"
            "<p>它们是硬闸，处理掉它点名的问题、重跑 "
            "<code>pipeline run</code> 之后会自己消失：</p>"
            "<table><thead><tr><th>类型</th><th>怎么消掉</th></tr></thead>"
            "<tbody>%s</tbody></table>" % (len(gates), rows))
    return "".join(blocks)


def build_human_html(state_dir: Path) -> Path:
    latest = latest_human_items(state_dir)
    open_rows = [row for row in latest.values() if row.get("status") == "open"]
    body = []
    for row in sorted(open_rows, key=lambda item: str(item.get("recorded_at") or "")):
        body.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % tuple(html.escape(str(value)) for value in (
                row.get("item_id") or "", row.get("kind") or "",
                "、".join(row.get("source_refs") or []), row.get("summary") or "")))
    document = ("<!doctype html><meta charset='utf-8'><title>Needs human</title>"
                "<style>body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}"
                "td,th{border:1px solid #ccc;padding:.45rem;vertical-align:top}"
                "pre{background:#f4f4f4;padding:.7rem;white-space:pre-wrap;"
                "word-break:break-all}</style>"
                "<h1>待确认项</h1><p>共 %d 项；真相源是 needs_human.jsonl。</p>"
                "%s"
                "<h2>全部明细</h2>"
                "<table><thead><tr><th>ID</th><th>类型</th><th>来源</th><th>说明</th>"
                "</tr></thead><tbody>%s</tbody></table>"
                % (len(open_rows), _approve_hints(open_rows), "".join(body)))
    path = Path(state_dir) / NEEDS_HUMAN_HTML
    temporary = path.with_suffix(".html.tmp")
    temporary.write_text(document, encoding="utf-8")
    os.replace(temporary, path)
    return path


def _resolved_selections(state_dir: Path) -> dict[str, str]:
    return {
        item_id: str(row.get("selected_ref") or "")
        for item_id, row in latest_human_items(state_dir).items()
        if row.get("status") == "resolved" and row.get("selected_ref")
    }


def _open_items_intersecting(state_dir: Path, source_refs: Iterable[str], *,
                             kinds: Iterable[str]) -> list[dict]:
    """找仍打开且碰到任一来源的人工闸；配对变化也不能绕开它。"""
    wanted = {str(ref) for ref in source_refs if str(ref)}
    allowed = set(kinds)
    return [
        row for row in latest_human_items(state_dir).values()
        if row.get("status") == "open"
        and row.get("kind") in allowed
        and wanted & {str(ref) for ref in row.get("source_refs") or []}
    ]


def out_of_scope_reason(row: Mapping[str, Any]) -> str | None:
    """这篇帖子**本来就不该**进发布流水线吗？在范围内返回 None。

    ⚠️ **这与 ``material_gate`` 是两件事，不要合并。**

    - 本函数判的是「永远不会变得可发」：视频帖、纯文字帖、无媒体帖。
      本项目的范围是**图文帖**（视频只在 manifest 里留记录，从不下载）。
      把它们排进 ``needs_human`` 是纯噪声 —— 实测最近 90 天 125 篇里
      **59 篇是视频帖**，账号还在日更，队列每天都会多出谁也处理不掉的项。
      队列一旦失去信号，人就不看了，那比没有队列更糟。
    - ``material_gate`` 判的是「本该可发，但素材现在不齐」：漏下载、0 字节、
      轮播缺图。**那类是人能处理的**，必须继续进队列。

    ⛔ 跳过 ≠ 静默丢弃（CR-19 那 263 篇的教训）：调用方**必须**把条数和原因
    报出来，`load_sources` 的第三个返回值就是为此存在的。
    """
    if not isinstance(row, Mapping):
        return "不是合法归档行"
    if not isinstance(row.get("text"), str) or not row["text"].strip():
        return "无正文"
    media = row.get("media")
    if not isinstance(media, list) or not media:
        return "无媒体"
    kinds = sorted({
        str(item.get("kind") or "?") if isinstance(item, Mapping) else "?"
        for item in media
    })
    if kinds != ["image"]:
        # 图片 + 视频的混合帖同样出局：只发其中的图会**丢内容**，
        # 而 compose 的硬闸本来就要求媒体项全是 image。
        return "非纯图文帖（媒体：%s）" % "、".join(kinds)
    return None


def load_sources(account_dirs: Iterable[Path], activated_at: datetime
                 ) -> tuple[list[SourcePost], list[HumanItem],
                            list[tuple[str, str]]]:
    """返回 (激活边界后的图文帖, 边界人工项, 被判为不在范围内的 (ref, 原因))。"""
    sources: list[SourcePost] = []
    issues: list[HumanItem] = []
    out_of_scope: list[tuple[str, str]] = []
    for account_dir in account_dirs:
        rows = Archive(account_dir.parent, account_dir.name).rows()
        for row in rows:
            post_id = row.get("post_id")
            platform = str(row.get("platform") or "").lower()
            if not isinstance(post_id, str) or platform not in {"facebook", "instagram"}:
                continue
            created = _parse_aware(row.get("created_at"))
            if created is None:
                # 没有时刻的行绝不跨过激活边界；不把历史误当新帖。
                continue
            if created <= activated_at.astimezone(timezone.utc):
                continue
            ref = journal.source_ref(platform, post_id)
            reason = out_of_scope_reason(row)
            if reason is not None:
                out_of_scope.append((ref, reason))
                continue
            sources.append(SourcePost(
                platform=platform, account_dir=Path(account_dir), row=row,
                created_at=created, ref=ref))
    sources.sort(key=lambda item: (item.created_at, item.ref))
    out_of_scope.sort()
    return sources, issues, out_of_scope


def reconcile(sources: list[SourcePost], *,
              selected: Mapping[str, str] | None = None,
              account_pairs: Iterable[tuple[str, str]] | None = None
              ) -> ReconcileResult:
    selected = selected or {}
    allowed_pairs = {
        (str(left).strip().lower(), str(right).strip().lower())
        for left, right in (configured_account_pairs()
                            if account_pairs is None else account_pairs)
        if str(left).strip() and str(right).strip()
    }
    facebook = [item for item in sources if item.platform == "facebook"]
    instagram = [item for item in sources if item.platform == "instagram"]
    hashes: dict[str, tuple[int, ...] | None] = {}
    edges = []
    for left in facebook:
        for right in instagram:
            if ((left.account.strip().lower(), right.account.strip().lower())
                    not in allowed_pairs):
                continue
            delta = abs(left.created_at - right.created_at)
            if delta > PAIR_WINDOW:
                continue
            similarity = SequenceMatcher(
                None, _normalize_text(left.text), _normalize_text(right.text)).ratio()
            media_exact, media_corresponding, distances = _media_relation(
                left, right, hashes)
            text_exact = _normalize_text(left.text) == _normalize_text(right.text)
            exact = text_exact and media_exact
            suspected = (similarity >= SIMILARITY_THRESHOLD
                         or (media_corresponding and not exact))
            if exact or suspected:
                edges.append((2 if exact else 1, similarity,
                              -delta.total_seconds(), left, right, distances))
    edges.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    used: set[str] = set()
    candidates: list[Candidate] = []
    human: list[HumanItem] = []
    for tier, similarity, _delta, left, right, distances in edges:
        if left.ref in used or right.ref in used:
            continue
        pair = (left, right)
        used.update((left.ref, right.ref))
        if tier == 2:
            candidates.append(Candidate(left, pair, "exact_cross_platform"))
            continue
        item_id = _item_id(
            "similar_cross_platform", pair,
            extra="%.6f:%s" % (similarity, ",".join(map(str, distances))))
        chosen_ref = selected.get(item_id, "")
        chosen = next((item for item in pair if item.ref == chosen_ref), None)
        if chosen is not None:
            candidates.append(Candidate(chosen, pair, "selected_cross_platform"))
            continue
        human.append(HumanItem(
            item_id=item_id, kind="similar_cross_platform",
            source_refs=(left.ref, right.ref),
            summary=("30 小时内正文相似 %.4f；素材 dHash=%s。"
                     "版本有差异，付费处理前必须选一个。"
                     % (similarity, list(distances))),
            details={"similarity": round(similarity, 6),
                     "dhash_distances": list(distances)}))
    for source in sources:
        if source.ref not in used:
            candidates.append(Candidate(source, (source,), "independent"))
    candidates.sort(key=lambda item: (item.canonical.created_at,
                                      item.canonical.ref))
    return ReconcileResult(tuple(candidates), tuple(human), len(sources))


def _prepaid_issue(candidate: Candidate, rules: PublishRules) -> HumanItem | None:
    # exact merge 最终会把所有来源一起写进 journal；secondary 的残缺轮播或
    # 缺失原图不能躲在 canonical 的完整素材后面。
    for material_source in candidate.sources:
        material_row = material_source.row
        media = material_row.get("media")
        paths = _image_paths(material_source)
        if (not material_source.text.strip()
                or not isinstance(media, list) or not media
                or material_row.get("media_complete") is not True
                or any(not isinstance(item, Mapping)
                       or item.get("kind") != "image" for item in media)
                or len(paths) != len(media)
                or any(not path.is_file() or path.stat().st_size <= 0
                       for path in paths)):
            return HumanItem(
                _item_id("material_gate", candidate.sources,
                         material_source.ref),
                "material_gate", candidate.source_refs,
                "%s 的正文/图片/轮播完整性硬闸未通过，未调用付费服务。"
                % material_source.ref,
                {"source_ref": material_source.ref})
    # 合并候选会用一篇 canonical 代表多个来源并共同记入 journal；因此每个
    # 来源的 owner/coauthors 都是版权真相，不能只审 canonical。
    for provenance in candidate.sources:
        source_row = provenance.row
        owner = str(source_row.get("owner") or "").strip().lower()
        account = provenance.account.strip().lower()
        expected_account = rules.source_accounts[provenance.platform]
        expected_dir = ("fa_" if provenance.platform == "facebook" else "in_") \
            + expected_account
        if account != expected_account or provenance.account_dir.name != expected_dir:
            return HumanItem(
                _item_id("unknown_owner", candidate.sources,
                         provenance.ref + ":unconfigured-source-account"),
                "unknown_owner", candidate.source_refs,
                "%s 来自未配置来源账号/目录 %s；禁止发布到固定 DE 目标账号。"
                % (provenance.ref, provenance.account_dir.name),
                {"source_ref": provenance.ref,
                 "source_account": account,
                 "account_dir": provenance.account_dir.name})
        if not owner or not account:
            return HumanItem(
                _item_id("unknown_owner", candidate.sources, provenance.ref),
                "unknown_owner", candidate.source_refs,
                "%s 的 owner/account 缺失，无法确认内容归属。" % provenance.ref,
                {"source_ref": provenance.ref})
        raw_coauthors = source_row.get("coauthors")
        if not isinstance(raw_coauthors, list):
            return HumanItem(
                _item_id("unknown_collaborator", candidate.sources,
                         provenance.ref + ":invalid-coauthors"),
                "unknown_collaborator", candidate.source_refs,
                "%s 的 coauthors 证据不是数组，未调用付费服务。" % provenance.ref,
                {"source_ref": provenance.ref})
        coauthors = {
            str(value).strip().lower() for value in raw_coauthors
            if str(value).strip()
        }
        external = set(coauthors)
        external.discard(account)
        if owner != account:
            # 真实归档约定：合作帖 owner 是原作者，目标账号必须出现在
            # coauthors；缺这条关系时不能仅凭名字推断它是合作帖。
            if account not in coauthors:
                return HumanItem(
                    _item_id("unknown_collaborator", candidate.sources,
                             provenance.ref + ":missing-account-coauthor"),
                    "unknown_collaborator", candidate.source_refs,
                    "%s 的 owner 与账号不同，但 coauthors 没有目标账号；"
                    "归属证据不完整。" % provenance.ref,
                    {"source_ref": provenance.ref, "owner": owner})
            external.add(owner)
        untrusted = sorted(
            value for value in external
            if value not in rules.trusted_owners[provenance.platform])
        if untrusted:
            return HumanItem(
                _item_id("unknown_collaborator", candidate.sources,
                         provenance.ref + ":" + "|".join(untrusted)),
                "unknown_collaborator", candidate.source_refs,
                "%s 含未列入 trusted_owners 的合作方：%s；未调用付费服务。"
                % (provenance.ref, "、".join("@" + item for item in untrusted)),
                {"source_ref": provenance.ref, "collaborators": untrusted})
    mapped = {
        translation.normalize_money_token(str(token))
        for token in rules.price_map
    }
    for priced_source in candidate.sources:
        amounts = translation.extract_money_tokens(priced_source.text)
        unmapped = [token for token in amounts
                    if translation.normalize_money_token(token) not in mapped]
        if unmapped:
            return HumanItem(
                _item_id("unmapped_price", candidate.sources,
                         priced_source.ref + ":" + "|".join(unmapped)),
                "unmapped_price", candidate.source_refs,
                "%s 金额未映射：%s；未调用付费服务。"
                % (priced_source.ref, "、".join(unmapped)),
                {"source_ref": priced_source.ref, "amounts": unmapped})
    return None


def prepaid_issue(candidate: Candidate, rules: PublishRules) -> HumanItem | None:
    """`_prepaid_issue` 的公开名字。

    给 `pipeline preflight` 这类**只读预演**用：预检必须用生产同一套判据，
    否则"预检说能跑"和"真跑起来"会分叉 —— 那正是本项目一再吃亏的形状。
    """
    return _prepaid_issue(candidate, rules)


def _jsonl(path: Path):
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BudgetStopped("费用日志读不了：%s（%s）" % (path, exc)) from exc
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BudgetStopped(
                "费用日志 %s 第 %d 行损坏；不能在漏算账单时继续付费"
                % (path, number)) from exc
        if not isinstance(row, dict):
            raise BudgetStopped(
                "费用日志 %s 第 %d 行不是对象；不能在漏算账单时继续付费"
                % (path, number))
        yield row


def budget_snapshot(account_dirs: Iterable[Path], *, now: datetime,
                    zone_name: str = "Europe/Berlin",
                    state_dir: Path | None = None) -> BudgetSnapshot:
    zone = ZoneInfo(zone_name)
    local = now.astimezone(zone)
    day_start = datetime.combine(local.date(), time.min, tzinfo=zone).astimezone(
        timezone.utc)
    month_start = datetime(local.year, local.month, 1, tzinfo=zone).astimezone(
        timezone.utc)
    daily = monthly = 0.0
    unknown: list[str] = []
    paid_ids: frozenset[str] = frozenset()
    if state_dir is not None:
        from core import paid_requests
        try:
            paid = paid_requests.ledger_snapshot(
                state_dir, now=now, zone_name=zone_name)
        except paid_requests.PaidRequestBlocked as exc:
            raise BudgetStopped(str(exc)) from exc
        daily += paid.daily_usd
        monthly += paid.monthly_usd
        paid_ids = paid.request_ids
        unknown.extend(paid.unknown)
    try:
        text_settings = translation.Settings()
    except SystemExit as exc:
        raise PipelineRunError("[translate] 配置不可用：%s" % exc) from exc
    import localize_images as image_de
    try:
        image_settings = image_de.Settings()
    except SystemExit as exc:
        raise PipelineRunError("[image] 配置不可用：%s" % exc) from exc
    for account_dir in account_dirs:
        for row in _jsonl(account_dir / "translated.jsonl"):
            paid_id = str(row.get("paid_request_id") or "")
            if paid_id:
                if paid_id not in paid_ids:
                    unknown.append("%s translated:%s 引用缺失 paid request %s" % (
                        account_dir.name, row.get("post_id") or "?", paid_id))
                continue
            when = _parse_aware(row.get("translated_at"))
            if when is None:
                unknown.append("%s translated:%s 时间无效" % (
                    account_dir.name, row.get("post_id") or "?"))
                continue
            if when < month_start:
                continue
            usage = row.get("usage")
            cost = None
            if (isinstance(usage, dict)
                    and not translation.translation_usage_errors(usage)):
                cost = translation.usage_cost_upper_bound(text_settings, usage)
            if cost is None or not math.isfinite(cost) or cost < 0:
                unknown.append("%s translated:%s" % (
                    account_dir.name, row.get("post_id") or "?"))
                continue
            monthly += cost
            if when >= day_start:
                daily += cost
        for row in _jsonl(account_dir / "images_de.jsonl"):
            paid_id = str(row.get("paid_request_id") or "")
            if paid_id:
                if paid_id not in paid_ids:
                    unknown.append("%s image:%s/%s 引用缺失 paid request %s" % (
                        account_dir.name, row.get("post_id") or "?",
                        row.get("media_index")
                        if row.get("media_index") is not None else "?", paid_id))
                continue
            when = _parse_aware(row.get("created_at"))
            if when is None:
                unknown.append("%s image:%s/%s 时间无效" % (
                    account_dir.name, row.get("post_id") or "?",
                    row.get("media_index")
                    if row.get("media_index") is not None else "?"))
                continue
            if when < month_start:
                continue
            usage = row.get("usage")
            cost = (image_de.image_usage_cost(image_settings, usage)
                    if isinstance(usage, Mapping) else None)
            if cost is None or not math.isfinite(cost) or cost < 0:
                unknown.append("%s image:%s/%s" % (
                    account_dir.name, row.get("post_id") or "?",
                    row.get("media_index") if row.get("media_index") is not None else "?"))
                continue
            monthly += cost
            if when >= day_start:
                daily += cost
    return BudgetSnapshot(daily, monthly, tuple(unknown))


def assert_budget(account_dirs: Iterable[Path], settings: Mapping[str, Any], *,
                  now: datetime,
                  state_dir: Path | None = None) -> BudgetSnapshot:
    snapshot = budget_snapshot(account_dirs, now=now, state_dir=state_dir)
    if snapshot.unknown:
        raise BudgetStopped(
            "存在无法映射真实 usage 的费用，预算失败闭合：%s"
            % "；".join(snapshot.unknown))
    daily_limit = float(settings["daily_budget_usd"])
    monthly_limit = float(settings["monthly_budget_usd"])
    if (not math.isfinite(daily_limit) or daily_limit < 0
            or not math.isfinite(monthly_limit) or monthly_limit < 0):
        raise BudgetStopped("日/月预算必须是非负有限数字")
    if snapshot.daily_usd >= daily_limit or snapshot.monthly_usd >= monthly_limit:
        raise BudgetStopped(
            "预算已到上限（日 US$%.4f/%.2f，月 US$%.4f/%.2f）"
            % (snapshot.daily_usd, daily_limit,
               snapshot.monthly_usd, monthly_limit))
    return snapshot


def assert_budget_after(snapshot: BudgetSnapshot,
                        settings: Mapping[str, Any]) -> None:
    daily_limit = float(settings["daily_budget_usd"])
    monthly_limit = float(settings["monthly_budget_usd"])
    if (not math.isfinite(daily_limit) or daily_limit < 0
            or not math.isfinite(monthly_limit) or monthly_limit < 0):
        raise BudgetStopped("日/月预算必须是非负有限数字")
    if (snapshot.daily_usd >= daily_limit
            or snapshot.monthly_usd >= monthly_limit):
        raise BudgetStopped(
            "刚完成的在途请求使预算到达/超过上限；已立即停止"
            "（日 US$%.4f/%.2f，月 US$%.4f/%.2f）"
            % (snapshot.daily_usd, float(settings["daily_budget_usd"]),
               snapshot.monthly_usd, float(settings["monthly_budget_usd"])))


class RealStageRunner:
    """只调用各阶段现有入口；每个付费调用最多处理一篇/一张。"""

    def delta(self, *, if_stale: bool) -> int:
        from routes import delta
        args = ["--platform", "all"]
        if if_stale:
            args.append("--if-stale")
        return delta.main(args)

    def translate(self, source: SourcePost) -> int:
        return translation.main([
            "--account", source.account_dir.name,
            "--post-id", source.post_id])

    def image(self, source: SourcePost, media_index: int) -> int:
        import localize_images
        return localize_images.main([
            "--account", source.account_dir.name,
            "--post-id", source.post_id,
            "--media-index", str(media_index)])


def translation_needed(source: SourcePost) -> bool:
    entries = translation.load_translated(source.account_dir / "translated.jsonl")
    return not translation.translation_is_current(
        source.row, entries.get(source.post_id))


def pending_image_indices(source: SourcePost) -> tuple[int, ...]:
    """调用 K 组已有内容寻址判据，准确识别哪些单图会产生付费请求。"""
    import localize_images
    try:
        settings = localize_images.Settings()
        jobs, _state, _stats = localize_images.build_jobs(
            settings, source.account_dir, [dict(source.row)], report=None)
    except SystemExit as exc:
        raise PipelineRunError("[image] 配置不可用：%s" % exc) from exc
    except (OSError, ValueError) as exc:
        raise PipelineRunError("调图真相源无法对账（%s）：%s" % (
            source.ref, exc)) from exc
    return tuple(job.media_index for job in jobs)


def next_slots(now: datetime, occupied: Iterable[datetime], count: int,
               rules: PublishRules) -> tuple[datetime, ...]:
    """分配还没被占的德国 10:00/17:00 槽。

    ⚠️ **可能返回少于 ``count`` 个，调用方必须自己判断。**
    composer 的日期选择器**不允许跨月**（2026-09-01 实测），所以可排的槽在
    每个月末会真的用完 —— 那不是异常，是 UI 的事实。以前这里是
    ``while len(found) < count`` 的无上界循环，加了月末边界就必须给它一个出口，
    否则要么死循环、要么在月末把整次 run 抛崩。
    """
    # 复用发布层的时区歧义判据；导入模块本身不会附着或启动浏览器。
    from publish import business_suite as bs

    zone = ZoneInfo(rules.timezone)
    ui_zone = bs.resolve_ui_timezone(rules.ui_timezone)
    local_now = now.astimezone(zone)
    ui_now = now.astimezone(ui_zone)
    ui_month = (ui_now.year, ui_now.month)
    busy = {item.astimezone(zone).replace(second=0, microsecond=0)
            for item in occupied}
    found: list[datetime] = []
    day = local_now.date()
    exhausted = False
    while len(found) < count and not exhausted:
        for slot in rules.slots:
            candidate = datetime.combine(day, slot, tzinfo=zone)
            # 月份在 **UI 时区**里判：德国 10-01 00:00 在美西还是 09-30。
            in_ui = candidate.astimezone(ui_zone)
            if (in_ui.year, in_ui.month) != ui_month:
                exhausted = True
                break
            if (candidate <= local_now or candidate in busy
                    or bs.ui_time_is_ambiguous(candidate, rules.ui_timezone)):
                continue
            found.append(candidate)
            if len(found) == count:
                break
        day += timedelta(days=1)
    return tuple(found)


def _ready_item(candidate: Candidate, post) -> HumanItem:
    source = candidate.canonical
    fingerprint = _publish_fingerprint(post)
    return HumanItem(
        _item_id("ready_to_publish", candidate.sources, fingerprint),
        "ready_to_publish", candidate.source_refs,
        "翻译、调图与离线硬闸已通过，等待批量确认和远端空槽分配。",
        {"canonical_ref": source.ref,
         "post_id": source.post_id,
         "platform": source.platform,
         "account_dir": source.account_dir.name,
         "relation": candidate.relation,
         "publish_fingerprint": fingerprint,
         # ⬇️ 以下只为让人**在批准之前**能判断，不参与任何判据。
         # 稳态里人每天只做一件事：看一眼待确认清单然后 approve。
         # 清单上只有 item_id / kind / 一句通用说明的话，那一眼**什么也判断不了**，
         # 人只能盲批 —— 而这是整条链上唯一一次人工复核。
         "text_de_preview": _preview(post.text_de),
         "image_count": len(post.image_paths),
         "image_sources": list(post.image_sources),
         "source_author": post.source_author_name or post.source_author or "",
         "warnings": list(post.warnings)})


def _preview(text: str, limit: int = 400) -> str:
    value = (text or "").strip()
    return value if len(value) <= limit else value[:limit] + " …"


def _publish_fingerprint(post) -> str:
    """ready 审批绑定最终正文与逐张图片；排期槽变化不使内容审批失效。"""
    return "%s:%s" % (
        journal.text_sha256(post.text_de),
        ",".join(journal.file_sha256(path) for path in post.image_paths))


def _queue(state_dir: Path, items: Iterable[HumanItem], now: datetime) -> int:
    added = 0
    for item in items:
        added += int(append_human_item(state_dir, item, now))
    build_human_html(state_dir)
    return added


def _run_unlocked(*, account_dirs: list[Path], state_dir: Path,
                  settings: Mapping[str, Any], if_stale: bool = False,
                  processing_account_dirs: list[Path] | None = None,
                  budget_account_dirs: list[Path] | None = None,
                  now: datetime | None = None,
                  runner: RealStageRunner | None = None,
                  report: Callable[[str], None] = print) -> int:
    """运行一次 manual/assisted 对账；assisted 永不导入或触碰发布浏览器。"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    activated = activation_time(state_dir)
    if activated is None:
        raise PipelineRunError(
            "流水线尚未激活；G8 通过后运行 pipeline activate --g8-verified")
    autonomy = settings["autonomy"]
    if autonomy in {"supervised", "autonomous"}:
        raise PipelineRunError(
            "%s 本轮明确失败闭合；尚未实现后续发布分流规则" % autonomy)
    rules = publish_rules()
    budget_dirs = (account_dirs if budget_account_dirs is None
                   else budget_account_dirs)
    if not budget_dirs:
        raise PipelineRunError("全局预算扫描范围为空；不能在可能漏算费用时继续")
    runner = runner or RealStageRunner()
    processing = {
        str(path.resolve()) for path in (
            account_dirs if processing_account_dirs is None
            else processing_account_dirs)
    }
    if autonomy == "assisted":
        report("[pipeline] assisted：先跑 delta，再从真相源重新对账。")
        code = runner.delta(if_stale=if_stale)
        if code != 0:
            return code
    else:
        report("[pipeline] manual：只做本地对账，不抓取、不翻译、不调图。")

    sources, boundary_issues, out_of_scope = load_sources(
        account_dirs, activated)
    result = reconcile(sources, selected=_resolved_selections(state_dir))
    added = _queue(state_dir, (*boundary_issues, *result.human_items), now)
    report("[pipeline] 激活后来源 %d 条；候选 %d；新增待确认 %d。"
           % (result.source_count, len(result.candidates), added))
    if result.source_count == 0 and not out_of_scope:
        # 刚 activate 完的第一次 run 必然是这个样子（边界之后还没有新帖）。
        # 不说清楚的话，联调时很容易把"设计如此"读成"没接上"。
        report("[pipeline] 激活边界（%s）之后还没有新帖 —— **这是正常的**："
               "流水线不补发历史，要等下一次增量抓到新帖才会有事做。"
               % activated.isoformat())
    if out_of_scope:
        # 跳过必须留声：静默丢弃正是这个项目丢过 263 篇合作帖的方式。
        tally: dict[str, int] = {}
        for _ref, reason in out_of_scope:
            tally[reason] = tally.get(reason, 0) + 1
        report("[pipeline] 另有 %d 篇不在图文发布范围内，已跳过（不进人工队列）：%s"
               % (len(out_of_scope),
                  "；".join("%s×%d" % (reason, count) for reason, count
                           in sorted(tally.items(), key=lambda kv: -kv[1]))))
    if autonomy == "manual":
        mark_run_success(state_dir, now)
        return 0

    scheduled_refs = journal.scheduled_source_refs(state_dir)
    for candidate in result.candidates:
        if str(candidate.canonical.account_dir.resolve()) not in processing:
            continue
        refs = set(candidate.source_refs)
        scheduled_overlap = refs & scheduled_refs
        if scheduled_overlap and scheduled_overlap != refs:
            # 典型场景：FB 已独立排期，30h 窗口内的 IG 抓取迟到。静默跳过会
            # 没给新 IG ref 留下耐久覆盖；若以后 greedy 换边，它就可能再次付费/
            # 发布。先交给人确认“这条 IG 已由既有远端帖覆盖”。
            scheduled = journal.scheduled_record_for_refs(
                state_dir, scheduled_overlap)
            late = HumanItem(
                _item_id("late_scheduled_overlap", candidate.sources,
                         ",".join(sorted(scheduled_overlap))),
                "late_scheduled_overlap", candidate.source_refs,
                "部分来源已 scheduled、部分来源迟到：%s。确认覆盖关系前，"
                "所有相交候选持续禁止付费或发布。" % "、".join(
                    sorted(scheduled_overlap)),
                {"scheduled_refs": sorted(scheduled_overlap),
                 "uncovered_refs": sorted(refs - scheduled_overlap),
                 "scheduled_attempt_id": str(
                     (scheduled or {}).get("attempt_id") or ""),
                 "relation": candidate.relation})
            resolve_cleared_items(
                state_dir, candidate.source_refs,
                active_item_ids=(late.item_id,), now=now)
            _queue(state_dir, (late,), now)
            continue
        if scheduled_overlap == refs:
            resolve_cleared_items(
                state_dir, candidate.source_refs,
                resolution="already_scheduled", now=now)
            continue
        late_blocks = _open_items_intersecting(
            state_dir, candidate.source_refs,
            kinds=("late_scheduled_overlap",))
        if late_blocks:
            report("[pipeline] 来源仍受迟到配对人工闸阻塞：%s"
                   % "、".join(candidate.source_refs))
            continue
        pending_publish = journal.pending_record_for_refs(
            state_dir, candidate.source_refs)
        if pending_publish is not None:
            unresolved = HumanItem(
                _item_id("publish_unresolved", candidate.sources,
                         str(pending_publish.get("attempt_id") or "")),
                "publish_unresolved", candidate.source_refs,
                "至少一个来源仍有未闭合发布状态 %s；人工结转前禁止付费或重试。"
                % pending_publish.get("status"),
                {"attempt_id": pending_publish.get("attempt_id") or "",
                 "status": pending_publish.get("status") or ""})
            resolve_cleared_items(
                state_dir, candidate.source_refs,
                active_item_ids=(unresolved.item_id,), now=now)
            _queue(state_dir, (unresolved,), now)
            continue
        issue = _prepaid_issue(candidate, rules)
        resolve_cleared_items(
            state_dir, candidate.source_refs,
            active_item_ids=(() if issue is None else (issue.item_id,)),
            kinds=(PREPAID_ITEM_KINDS if issue is None
                   else CANDIDATE_ITEM_KINDS), now=now)
        if issue is not None:
            _queue(state_dir, (issue,), now)
            continue
        # 30h 是可配对窗口，不只是同一快照里的筛选条件。窗口尚未闭合时
        # counterpart/第三条更优边仍可能晚到并改变 greedy 匹配，因此任何候选
        # （包括眼下已配成 exact 的）都只展示对账，不付费、不生成 ready。
        if any(source.created_at > now - PAIR_WINDOW
               for source in candidate.sources):
            report("[pipeline] 配对窗口尚未闭合，暂缓付费：%s"
                   % "、".join(candidate.source_refs))
            continue
        source = candidate.canonical
        try:
            if translation_needed(source):
                assert_budget(budget_dirs, settings, now=now,
                              state_dir=state_dir)
                if runner.translate(source) != 0:
                    raise PipelineRunError("翻译阶段失败：%s" % source.ref)
                # 每个真实请求后按刚落盘的 usage 重算；允许的超额只可能来自这一请求。
                after_translation = budget_snapshot(
                    budget_dirs, now=now, state_dir=state_dir)
                if after_translation.unknown:
                    raise BudgetStopped("翻译后 usage 无法计费：%s"
                                        % "；".join(after_translation.unknown))
                assert_budget_after(after_translation, settings)
            for index in pending_image_indices(source):
                assert_budget(budget_dirs, settings, now=now,
                              state_dir=state_dir)
                if runner.image(source, index) != 0:
                    raise PipelineRunError("调图阶段失败：%s/%d" % (
                        source.ref, index))
                after_image = budget_snapshot(
                    budget_dirs, now=now, state_dir=state_dir)
                if after_image.unknown:
                    raise BudgetStopped("调图后 usage 无法计费：%s"
                                        % "；".join(after_image.unknown))
                assert_budget_after(after_image, settings)
        except BudgetStopped as exc:
            budget_item = HumanItem(
                _item_id("budget_stopped", candidate.sources),
                "budget_stopped", candidate.source_refs, str(exc))
            resolve_cleared_items(
                state_dir, candidate.source_refs,
                active_item_ids=(budget_item.item_id,), now=now)
            _queue(state_dir, (budget_item,), now)
            report("[pipeline] %s" % exc)
            return 4
        except (PipelineRunError, RuntimeError) as exc:
            paid_item = HumanItem(
                _item_id("paid_request_unresolved", candidate.sources,
                         type(exc).__name__ + ":" + str(exc)),
                "paid_request_unresolved", candidate.source_refs,
                "翻译/调图请求或其付费产出未闭合：%s。"
                "已停止本次对账；检查 paid_requests.jsonl 后人工处理。" % exc)
            resolve_cleared_items(
                state_dir, candidate.source_refs,
                active_item_ids=(paid_item.item_id,), now=now)
            _queue(state_dir, (paid_item,), now)
            report("[pipeline] %s" % paid_item.summary)
            return 5

        # 仅用于离线硬闸的未来占位槽；真正槽位在 approve 前读取远端后重分配。
        placeholder_slots = next_slots(now, (), 1, rules)
        if not placeholder_slots:
            # 月末：composer 不允许跨月，本月已经没有可排的槽了。翻译/调图的
            # 产物不会作废，只是这一轮排不出 ready 项。**这不是错误**，
            # 所以照常 mark_run_success，不要让死人开关误报。
            report("[pipeline] 本月已无可排槽位（composer 不允许跨月）；"
                   "本轮不生成待确认项，进入下个月后重跑即可。")
            break
        placeholder = placeholder_slots[0]
        try:
            post = compose_post(
                source.post_id, placeholder, archive_root=cfg().archive_dir,
                account=source.account_dir.name,
                price_map=rules.price_map,
                require_verified_ui_constraints=True, warning_sink=None)
        except ComposeError as exc:
            offline_item = HumanItem(
                _item_id("offline_gate", candidate.sources, str(exc)),
                "offline_gate", candidate.source_refs,
                "付费阶段后离线硬闸失败：%s" % exc)
            resolve_cleared_items(
                state_dir, candidate.source_refs,
                active_item_ids=(offline_item.item_id,), now=now)
            _queue(state_dir, (offline_item,), now)
            continue
        ready = _ready_item(candidate, post)
        resolve_cleared_items(
            state_dir, candidate.source_refs,
            active_item_ids=(ready.item_id,), now=now)
        _queue(state_dir, (ready,), now)
    mark_run_success(state_dir, now)
    return 0


def run(*, account_dirs: list[Path], state_dir: Path,
        settings: Mapping[str, Any], if_stale: bool = False,
        processing_account_dirs: list[Path] | None = None,
        budget_account_dirs: list[Path] | None = None,
        now: datetime | None = None, runner: RealStageRunner | None = None,
        report: Callable[[str], None] = print) -> int:
    """串行运行一次对账；预算始终可使用独立的全账号扫描范围。"""
    with PipelineOperationLock(Path(state_dir) / "pipeline.lock"):
        return _run_unlocked(
            account_dirs=account_dirs, state_dir=state_dir, settings=settings,
            if_stale=if_stale,
            processing_account_dirs=processing_account_dirs,
            budget_account_dirs=budget_account_dirs,
            now=now, runner=runner, report=report)


def open_human_items(state_dir: Path, item_ids: Iterable[str]) -> list[dict]:
    latest = latest_human_items(state_dir)
    out = []
    for item_id in item_ids:
        row = latest.get(item_id)
        if row is None or row.get("status") != "open":
            raise PipelineRunError("待确认项不存在或已结转：%s" % item_id)
        out.append(row)
    return out


def submit_batch(items: Iterable[Any], submitter: Callable[[Any], int],
                 on_success: Callable[[Any], None]) -> tuple[int, int]:
    """逐项提交；返回 (退出码, 已成功数)，首个非零后绝不触碰余项。"""
    completed = 0
    for item in items:
        code = submitter(item)
        if code != 0:
            return code, completed
        on_success(item)
        completed += 1
    return 0, completed


def _approve_unlocked(*, item_ids: list[str], selections: Mapping[str, str],
                      state_dir: Path, now: datetime | None = None,
                      assume_yes: bool = False,
                      confirm: Callable[[str], bool] | None = None) -> int:
    """批量确认 ready 项；同一批遇到首个浏览器/提交不明确状态立即停止。"""
    if not item_ids:
        raise PipelineRunError("approve 至少要一个 --item-id")
    if len(set(item_ids)) != len(item_ids):
        raise PipelineRunError("approve 的 --item-id 不能重复，避免同一篇在一批里提交两次")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    activated = activation_time(state_dir)
    if activated is None:
        raise PipelineRunError("流水线尚未激活；G8 通过前不能批准任何发布项")
    rows = open_human_items(state_dir, item_ids)

    # 相似版本选择本身不花钱、不碰浏览器。一次可结转多个；下一次 run 会从
    # 追加式选择证据重新构建 canonical candidate。
    ready_rows: list[dict] = []
    selections_to_apply: list[tuple[dict, str]] = []
    coverage_rows: list[dict] = []
    for row in rows:
        if row.get("kind") == "similar_cross_platform":
            chosen = selections.get(str(row["item_id"]), "")
            if chosen not in set(row.get("source_refs") or []):
                raise PipelineRunError(
                    "相似版本项 %s 必须用 --select-source ITEM=platform:post_id"
                    % row["item_id"])
            selections_to_apply.append((row, chosen))
        elif row.get("kind") == "ready_to_publish":
            ready_rows.append(row)
        elif row.get("kind") == "late_scheduled_overlap":
            coverage_rows.append(row)
        else:
            raise PipelineRunError(
                "待确认项 %s（%s）不能直接批准发布；先处理它点名的硬闸"
                % (row.get("item_id"), row.get("kind")))

    # 同一批 ready 不得覆盖同一个来源；旧 ready 与后到的跨平台配对重叠时，
    # 两篇都提交会把同一内容发两次。让下一次 run 重新对账并结转旧项。
    seen_refs: set[str] = set()
    for row in ready_rows:
        refs = {str(ref) for ref in row.get("source_refs") or [] if str(ref)}
        overlap = seen_refs & refs
        if overlap:
            raise PipelineRunError(
                "同一 approve 批次有重叠 source_refs：%s；请先重新 pipeline run 对账"
                % "、".join(sorted(overlap)))
        seen_refs.update(refs)
    already = seen_refs & journal.scheduled_source_refs(state_dir)
    if already:
        raise PipelineRunError(
            "ready 项已有来源被记为 scheduled：%s；为防重复发布，请先重新 pipeline run"
            % "、".join(sorted(already)))
    pending = journal.pending_record_for_refs(state_dir, seen_refs)
    if pending is not None:
        raise PipelineRunError(
            "ready 项至少一个来源仍有未闭合发布状态 %s；"
            "必须先人工结转，整批未触碰浏览器" % pending.get("status"))

    # ready 不是任务队列。批准时必须从全部账号的最新归档重新对账，证明
    # canonical 与完整 source_refs 仍是同一个候选；旧配对不能直接拿来发。
    rules = publish_rules()
    all_dirs = translation.account_dirs(cfg().archive_dir)
    latest_sources, boundary_issues, _out_of_scope = load_sources(
        all_dirs, activated)
    if boundary_issues:
        raise PipelineRunError("最新激活边界对账存在未决来源；请先 pipeline run")
    latest_result = reconcile(
        latest_sources, selected=_resolved_selections(state_dir))
    current = {
        frozenset(candidate.source_refs): candidate
        for candidate in latest_result.candidates
    }
    coverage_actions: list[tuple[dict, dict]] = []
    projected_scheduled = set(journal.scheduled_source_refs(state_dir))
    for row in coverage_rows:
        refs = frozenset(str(ref) for ref in row.get("source_refs") or [])
        candidate = current.get(refs)
        if candidate is None or candidate.relation != "exact_cross_platform":
            raise PipelineRunError(
                "迟到覆盖项的最新 exact 配对已变化；请重新 pipeline run 对账")
        scheduled = journal.scheduled_record_for_refs(state_dir, refs)
        overlap = set(refs) & projected_scheduled
        if scheduled is None or not overlap or overlap == set(refs):
            raise PipelineRunError(
                "迟到覆盖项不再是“部分来源已 scheduled”；请重新 pipeline run")
        expected_attempt = str((row.get("details") or {}).get(
            "scheduled_attempt_id") or "")
        if expected_attempt and str(scheduled.get("attempt_id") or "") != expected_attempt:
            raise PipelineRunError(
                "迟到覆盖项引用的 scheduled attempt 已变化；请重新 pipeline run")
        coverage_actions.append((row, scheduled))
        projected_scheduled.update(refs)
    projected_ready_overlap = seen_refs & projected_scheduled
    if projected_ready_overlap:
        raise PipelineRunError(
            "本批迟到覆盖会与 ready 来源重叠：%s；请分批重新对账"
            % "、".join(sorted(projected_ready_overlap)))
    for row in ready_rows:
        refs = frozenset(str(ref) for ref in row.get("source_refs") or [])
        candidate = current.get(refs)
        details = row.get("details") or {}
        if (candidate is None
                or candidate.canonical.ref != details.get("canonical_ref")):
            raise PipelineRunError(
                "ready 项的最新 canonical/source_refs 已变化；请重新 pipeline run 对账")
        issue = _prepaid_issue(candidate, rules)
        if issue is not None:
            raise PipelineRunError(
                "ready 项当前重新触发 %s：%s" % (issue.kind, issue.summary))

    # 先把整批结构校验完，再追加选择证据，避免后一个坏 item 造成半批结转。
    for row, chosen in selections_to_apply:
        resolve_human_item(
            state_dir, str(row["item_id"]), resolution="source_selected",
            selected_ref=chosen, now=now)
    for row, scheduled_row in coverage_actions:
        prior = journal.attempt_from_row(scheduled_row)
        refs = tuple(dict.fromkeys(
            tuple(prior.source_refs)
            + tuple(str(ref) for ref in row.get("source_refs") or [])))
        covered = journal.transition(
            prior, journal.STATUS_SCHEDULED,
            recorded_at=now.isoformat(), source_refs=refs,
            step="G9 迟到 exact 来源人工覆盖结转", manual_evidence=True,
            note=("人工通过 pipeline approve 确认迟到 exact 来源已由既有"
                  " scheduled 远端帖覆盖；本次零浏览器操作"),
            verification=(str(prior.verification or "")
                          + "；人工确认迟到 exact 来源覆盖"))
        journal.append(state_dir, covered)
        resolve_human_item(
            state_dir, str(row["item_id"]),
            resolution="covered_by_existing_scheduled",
            selected_ref=str(prior.attempt_id), now=now)
    selected_count = len(selections_to_apply)
    coverage_count = len(coverage_actions)
    if not ready_rows:
        if selected_count:
            print("已选择 %d 个跨平台版本；请再跑一次 pipeline run，"
                  "让选定版本完成翻译/调图并生成 ready 项。" % selected_count)
        if coverage_count:
            print("已人工结转 %d 个迟到 exact 来源为既有 scheduled 帖的覆盖引用；"
                  "本次零浏览器操作。" % coverage_count)
        return 0

    from publish import business_suite as bs
    # 在任何浏览器读取之前先把整批严格离线硬闸重做一遍。
    provisional = next_slots(now, (), len(ready_rows), rules)
    if len(provisional) < len(ready_rows):
        raise PipelineRunError(
            "本月只剩 %d 个可排槽位，而本批有 %d 篇 —— composer 的日期选择器"
            "不允许跨月。本批零提交：请减少 --item-id 数量，或等进入下个月再批。"
            % (len(provisional), len(ready_rows)))
    prepared = []
    for row, slot in zip(ready_rows, provisional):
        details = row.get("details") or {}
        try:
            post = compose_post(
                str(details["post_id"]), slot,
                archive_root=cfg().archive_dir,
                account=str(details["account_dir"]),
                price_map=rules.price_map,
                require_verified_ui_constraints=True, warning_sink=None)
        except (KeyError, ComposeError) as exc:
            raise PipelineRunError(
                "批准前离线硬闸失败（%s）：%s" % (row.get("item_id"), exc)) from exc
        if journal.source_ref(post.platform, post.post_id) != details.get("canonical_ref"):
            raise PipelineRunError("ready 项 canonical_ref 与当前归档真相不一致")
        expected_fingerprint = str(details.get("publish_fingerprint") or "")
        if not expected_fingerprint:
            raise PipelineRunError(
                "ready 项缺少内容指纹；请重新 pipeline run 生成本版审批项")
        if _publish_fingerprint(post) != expected_fingerprint:
            raise PipelineRunError(
                "ready 项生成后最终正文或图片已经变化；请重新 pipeline run 后再确认")
        prepared.append((row, post))

    try:
        bs.require_submission_evidence()
        bs.require_readback_evidence()
    except bs.ProbeRequired as exc:
        raise PipelineRunError(str(exc)) from exc
    c = cfg()
    c.assert_publish_chrome_isolated()

    async def read_slots():
        from core.chrome import attach
        pw = None
        try:
            pw, _browser, context = await attach(
                port=c.publish_debug_port, profile=c.publish_profile_dir,
                start_script=r"scripts\start_chrome_publish.bat",
                login_hint="DE 发布账号")
            page = await context.new_page()
            return await bs.read_remote_slot_inventory(
                page, ui_timezone=rules.ui_timezone,
                business_timezone=rules.timezone,
                timeout=float(c.get("publish", "ui_timeout_seconds",
                                    bs.DEFAULT_UI_TIMEOUT)))
        finally:
            if pw is not None:
                await pw.stop()

    import asyncio
    try:
        inventory = asyncio.run(read_slots())
    except Exception as exc:
        raise PipelineRunError("读取远端已占用槽位失败：%s" % exc) from exc
    slots = next_slots(now, inventory.occupied, len(prepared), rules)
    if len(slots) < len(prepared):
        # 远端已占槽位读回来之后本月剩余槽位可能不够。**整批不提交** ——
        # 这里已经开过浏览器，但一个提交都还没点。
        raise PipelineRunError(
            "扣掉远端已占用的槽位后，本月只剩 %d 个可排槽位，而本批有 %d 篇；"
            "composer 不允许跨月。本批零提交。"
            % (len(slots), len(prepared)))
    if not inventory.covers(slots):
        rendered = "%s 至 %s" % (
            inventory.visible_start or "未知", inventory.visible_end or "未知")
        raise PipelineRunError(
            "最终空槽超出本次 Planner DOM 已证明的 UI 日期范围（%s）；"
            "本批零提交。请补录/实现翻页证据，或在目标日期进入可见范围后重跑"
            % rendered)
    # 槽位变化后再做一次完整硬闸，尤其是 UI 最大/最小提前量。
    final_batch = []
    for (row, _post), slot in zip(prepared, slots):
        details = row["details"]
        post = compose_post(
            str(details["post_id"]), slot, archive_root=c.archive_dir,
            account=str(details["account_dir"]),
            price_map=rules.price_map,
            require_verified_ui_constraints=True, warning_sink=None)
        if _publish_fingerprint(post) != str(details["publish_fingerprint"]):
            raise PipelineRunError("读取远端槽位期间发布内容发生变化；整批未提交")
        final_batch.append((row, post, slot))

    print("=== 本次批量确认（%d 篇；德国时间 10:00/17:00）===" % len(final_batch))
    from tools import publish_post
    for index, (row, post, slot) in enumerate(final_batch, 1):
        print("%d. %s  %s  %s  来源=%s" % (
            index, row["item_id"], post.post_id, slot.isoformat(),
            "、".join(row.get("source_refs") or [])))
        publish_post.print_checklist(post, slot, rules.ui_timezone)
    require = c.get("publish", "require_confirmation", True) is not False
    if require and not assume_yes:
        ask = confirm
        if ask is None:
            def ask(prompt: str) -> bool:
                try:
                    return input(prompt).strip().lower() in {"y", "yes"}
                except EOFError:
                    return False
        if not ask("确认按上面槽位逐篇自动提交吗？(yes/no) "):
            print("已取消；没有提交任何帖子。")
            return 0

    def submit_one(item) -> int:
        row, post, slot = item
        args = [
            "--post-id", post.post_id,
            "--at", slot.isoformat(),
            "--account", str(row["details"]["account_dir"]),
            "--submit", "--assume-yes", "--apply-price-map",
        ]
        for ref in row.get("source_refs") or []:
            args.extend(("--source-ref", str(ref)))
        return publish_post.main(args)

    def resolved(item) -> None:
        row, _post, _slot = item
        resolve_human_item(
            state_dir, str(row["item_id"]), resolution="scheduled",
            selected_ref=str(row["details"].get("canonical_ref") or ""), now=now)

    code, completed = submit_batch(final_batch, submit_one, resolved)
    if code != 0:
        failed = final_batch[completed][0]["item_id"]
        print("[pipeline] 批量在 %s 停止；后续 %d 篇未触碰。"
              % (failed, len(final_batch) - completed - 1))
    return code


def approve(*, item_ids: list[str], selections: Mapping[str, str],
            state_dir: Path, now: datetime | None = None,
            assume_yes: bool = False,
            confirm: Callable[[str], bool] | None = None) -> int:
    """以 pipeline 全局互斥锁执行批量批准。"""
    with PipelineOperationLock(Path(state_dir) / "pipeline.lock"):
        # 远端空槽回读、最终排期分配及逐篇提交属于同一个临界区；否则单帖
        # CLI 能在回读后抢占槽位。workflow 内的同 Context 重入会复用此锁。
        with journal.PublishOperationLock(Path(state_dir) / "publish.lock"):
            return _approve_unlocked(
                item_ids=item_ids, selections=selections, state_dir=state_dir,
                now=now, assume_yes=assume_yes, confirm=confirm)

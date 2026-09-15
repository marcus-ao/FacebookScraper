"""五阶段状态与 CLI；status/preflight 仅读本地事实，命令参数见 --help。"""
from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                         # noqa: E402
from core.console import force_utf8                 # noqa: E402
from core.notify import notify                      # noqa: E402
from core.store import Archive, ArchivePathError    # noqa: E402
from localize import text as translation                     # noqa: E402
from pipeline.settings import (AUTONOMY_LEVELS as AUTONOMY_LEVELS,      # noqa: E402
                               PIPELINE_CONFIG_KEYS as PIPELINE_CONFIG_KEYS,
                               PipelineConfigError,
                               pipeline_settings)
from core import paid_requests                      # noqa: E402
from core.heartbeat import HeartbeatSettings, heartbeat_status  # noqa: E402
from core.network_evidence import (NetworkEvidenceSettings, network_evidence_status,  # noqa: E402
                                   detection_failure_kind)  # noqa: E402
from publish.compose import (                        # noqa: E402
    _PROBE_REQUIRED_OBSERVATIONS as required)
from tools.schedule import (ALIVE_TASK, CATCHUP_TASK,  # noqa: E402
                            DAILY_TASK, SCHEDULER_TASK, _task_state)
from publish.capabilities import checks as capability_checks  # noqa: E402
from pipeline import engine as pipeline_assisted     # noqa: E402
from pipeline import engine as assisted              # noqa: E402


def _parse_iso_z(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None                      # 不猜时区；宁可当作没有证据
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def last_successful_run(state_dir: Path | None = None
                        ) -> tuple[datetime | None, list[str]]:
    """返回最近成功运行及其证据；无新帖的成功扫描仍算运行，不代表下游加工成功。"""
    state_dir = state_dir or cfg().state_dir
    stamps: list[tuple[datetime, str]] = []

    delta = _load_json(state_dir / "delta_state.json")
    if isinstance(delta, dict):
        for platform, entry in delta.items():
            if not isinstance(entry, dict):
                continue
            when = _parse_iso_z(entry.get("last_success"))
            if when is not None:
                stamps.append((when, "delta_state.json[%s]" % platform))

    pipeline_state = _load_json(state_dir / "pipeline_state.json")
    if isinstance(pipeline_state, dict):
        when = _parse_iso_z(pipeline_state.get("last_successful_run"))
        if when is not None:
            stamps.append((when, "pipeline_state.json"))

    if not stamps:
        return None, []
    newest = max(stamp for stamp, _ in stamps)
    return newest, [name for stamp, name in stamps if stamp == newest]


# 阶段积压

def publishable_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """返回有正文和已下载图片的基础候选；完整发布校验由 compose 执行。"""
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        post_id = row.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            continue
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            continue
        media = row.get("media")
        if not isinstance(media, list):
            continue
        for item in media:
            if (isinstance(item, Mapping) and item.get("kind") == "image"
                    and isinstance(item.get("local_path"), str)
                    and item["local_path"].strip()):
                out.add(post_id.strip())
                break
    return out


def pending_translation_ids(rows: list[dict], arc_base: Path,
                            publishable: set[str]) -> set[str]:
    """待译 = 可发 ∩ ``translate.pending()``。判据完全交给 F 组，不另写一套。"""
    done = translation.load_translated(arc_base / "translated.jsonl")
    todo = translation.pending(rows, done, False)
    return {row["post_id"] for row in todo
            if isinstance(row.get("post_id"), str)
            and row["post_id"] in publishable}


def pending_image_ids(rows: list[dict], arc_base: Path, publishable: set[str],
                      pending_translation: set[str],
                      report: Callable[[str], None]) -> tuple[set[str], int]:
    """返回待调图帖子及图片数；复用 build_jobs，并计入缺当前译文的候选。"""
    from localize import images as image_de          # 延迟导入：它会拉起 Pillow

    settings = image_de.Settings()
    jobs, _state, _stats = image_de.build_jobs(
        settings, arc_base, rows, force=False, report=report)
    with_pending_jobs = {job.post_id for job in jobs} & publishable
    pending_jobs = sum(1 for job in jobs if job.post_id in publishable)
    return (with_pending_jobs | (pending_translation & publishable),
            pending_jobs)


def published_source_refs(state_dir: Path | None = None) -> set[str]:
    """只读最终 ``scheduled`` 的 source_refs；文件不存在就是尚无成功记录。"""
    state_dir = state_dir or cfg().state_dir
    path = state_dir / "published.jsonl"
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue                    # 一条坏行不该让整张表打不出来
        if not isinstance(row, dict) or row.get("status") != "scheduled":
            continue
        refs = row.get("source_refs")
        if isinstance(refs, list):
            out.update(str(ref).strip() for ref in refs if str(ref).strip())
        elif isinstance(row.get("post_id"), str):
            platform = str(row.get("platform") or "").strip().lower()
            out.add("%s:%s" % (platform, row["post_id"].strip()))
    return out


def published_ids(state_dir: Path | None = None) -> set[str]:
    """兼容旧调用方：只返回最终 scheduled 来源 ID。"""
    refs = published_source_refs(state_dir)
    return {ref.rsplit(":", 1)[-1] for ref in refs}


# 费用

def _month_key(when: datetime) -> str:
    return when.strftime("%Y-%m")


def _previous_month(month: str) -> str:
    year, mon = (int(part) for part in month.split("-"))
    return "%04d-%02d" % (year - 1, 12) if mon == 1 else "%04d-%02d" % (year, mon - 1)


def month_spend(dirs: list[Path], month: str,
                warn: Callable[[str], None], *,
                state_dir: Path | None = None) -> tuple[float, float, list[str]]:
    """返回文本/图片美元费用及未知原因；优先付费账本，按 request ID 去重。"""
    problems: list[str] = []
    text_cost = image_cost = 0.0
    paid_ids: frozenset[str] = frozenset()
    if state_dir is not None:
        try:
            paid = paid_requests.ledger_month_snapshot(
                state_dir, month=month, zone_name="Europe/Berlin")
        except paid_requests.PaidRequestBlocked as exc:
            problems.append("[paid ledger] %s" % exc)
        else:
            text_cost += paid.translation_usd
            image_cost += paid.image_usd
            paid_ids = paid.request_ids
            problems.extend("[paid ledger] %s" % value
                            for value in paid.unknown)

    try:
        ts = translation.Settings()
    except SystemExit as exc:
        problems.append("[translate] 配置不可用，翻译花费未计入（%s）" % exc)
        ts = None
    try:
        # 延迟导入：只读状态按需加载图片计价依赖。
        from localize import images as image_de
        isettings = image_de.Settings()
    except SystemExit as exc:
        problems.append("[image] 配置不可用，图片花费未计入（%s）" % exc)
        image_de, isettings = None, None

    for arc_base in dirs:
        if ts is not None:
            for row in _jsonl_rows(arc_base / "translated.jsonl"):
                paid_id = str(row.get("paid_request_id") or "")
                if paid_id:
                    if paid_id not in paid_ids:
                        problems.append("[translate] %s/%s 引用缺失 paid request %s" % (
                            arc_base.name, row.get("post_id") or "?", paid_id))
                    continue
                when = _parse_iso_z(row.get("translated_at"))
                if when is None or _month_key(when) != month:
                    continue
                usage = row.get("usage")
                if not isinstance(usage, dict):
                    problems.append("[translate] %s/%s usage 缺失" % (
                        arc_base.name, row.get("post_id") or "?"))
                    continue
                errors = translation.translation_usage_errors(usage)
                if errors:
                    problems.append("[translate] %s/%s usage 不完整：%s" % (
                        arc_base.name, row.get("post_id") or "?",
                        "、".join(errors)))
                    continue
                cost = translation.usage_cost_upper_bound(ts, usage)
                if cost is None or not math.isfinite(cost) or cost < 0:
                    problems.append("[translate] %s/%s 费用无法计算" % (
                        arc_base.name, row.get("post_id") or "?"))
                    continue
                text_cost += cost
        if isettings is not None:
            for row in _jsonl_rows(arc_base / "images_de.jsonl"):
                paid_id = str(row.get("paid_request_id") or "")
                if paid_id:
                    if paid_id not in paid_ids:
                        problems.append("[image] %s/%s/%s 引用缺失 paid request %s" % (
                            arc_base.name, row.get("post_id") or "?",
                            row.get("media_index") or "?", paid_id))
                    continue
                when = _parse_iso_z(row.get("created_at"))
                if when is None or _month_key(when) != month:
                    continue
                usage = row.get("usage")
                if not isinstance(usage, Mapping):
                    problems.append("[image] %s/%s/%s usage 缺失" % (
                        arc_base.name, row.get("post_id") or "?",
                        row.get("media_index") or "?"))
                    continue
                cost = image_de.image_usage_cost(isettings, usage)
                if cost is None or not math.isfinite(cost) or cost < 0:
                    problems.append("[image] %s/%s/%s 费用无法计算" % (
                        arc_base.name, row.get("post_id") or "?",
                        row.get("media_index") or "?"))
                    continue
                image_cost += cost
    for problem in problems:
        warn(problem)
    return text_cost, image_cost, problems


def _jsonl_rows(path: Path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


# 状态输出

def account_counts(arc_base: Path, report: Callable[[str], None], *,
                   activated_at: datetime | None = None) -> dict:
    """一个账号的各阶段积压。任何一段炸掉都只影响这个账号，不掀掉整张表。"""
    counts = {"name": arc_base.name, "archived": 0, "publishable": 0,
              "pending_translation": 0, "pending_images": 0,
              "pending_image_files": 0, "publishable_refs": set(),
              "pipeline_publishable_refs": set(), "errors": []}
    try:
        rows = Archive(arc_base.parent, arc_base.name).rows()
    except (OSError, ArchivePathError, ValueError) as exc:
        counts["errors"].append("读不了 manifest：%s" % exc)
        return counts
    counts["archived"] = len(rows)

    publishable = publishable_ids(rows)
    counts["publishable"] = len(publishable)
    counts["publishable_refs"] = {
        "%s:%s" % (str(row.get("platform") or "").strip().lower(), row["post_id"])
        for row in rows if row.get("post_id") in publishable}
    if activated_at is not None:
        counts["pipeline_publishable_refs"] = {
            "%s:%s" % (str(row.get("platform") or "").strip().lower(), row["post_id"])
            for row in rows
            if row.get("post_id") in publishable
            and (created := _parse_iso_z(row.get("created_at"))) is not None
            and created > activated_at.astimezone(timezone.utc)}

    try:
        pending_tr = pending_translation_ids(rows, arc_base, publishable)
        counts["pending_translation"] = len(pending_tr)
    except (translation.SourceDataError, OSError, ValueError) as exc:
        counts["errors"].append("待译算不出：%s" % exc)
        return counts

    try:
        pending_img, files = pending_image_ids(
            rows, arc_base, publishable, pending_tr, report)
        counts["pending_images"] = len(pending_img)
        counts["pending_image_files"] = files
    except SystemExit as exc:
        counts["errors"].append("[image] 配置不可用，待调图未计算：%s" % exc)
    except (OSError, ArchivePathError, ValueError) as exc:
        counts["errors"].append("待调图算不出：%s" % exc)
    return counts


def display_width(text: str) -> int:
    """计算终端列宽，CJK 和全角字符计两列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1
               for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    fill = " " * max(width - display_width(text), 0)
    return (fill + text) if right else (text + fill)


def _age_text(now: datetime, when: datetime) -> str:
    seconds = (now - when).total_seconds()
    if seconds < 0:
        return "在未来（本机时钟或状态文件有问题）"
    hours = seconds / 3600
    if hours < 1:
        return "%d 分钟前" % int(seconds // 60)
    if hours < 48:
        return "%.1f 小时前" % hours
    return "%.1f 天前" % (hours / 24)


def pending_publish_count(account: Mapping[str, Any],
                          published: set[str]) -> int:
    """按当前账号 publishable refs 与 scheduled refs 的交集扣减。"""
    refs = account.get("pipeline_publishable_refs")
    if refs is None:
        refs = account.get("publishable_refs")
    return len(set(refs or ()) - published)


def run_status(dirs: list[Path], now: datetime | None = None,
               state_dir: Path | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    state_dir = state_dir or cfg().state_dir
    print("=== 流水线对账（只读：零网络、零费用、零写盘）===")

    try:
        settings = pipeline_settings()
    except PipelineConfigError as exc:
        print("[!] %s" % exc)
        return 1

    activated = None
    open_count = 0
    g9_error = None
    try:
        activated = pipeline_assisted.activation_time(state_dir)
        latest = pipeline_assisted.latest_human_items(state_dir)
        open_count = sum(1 for row in latest.values() if row.get("status") == "open")
    except Exception as exc:                         # 状态页必须能降级展示
        g9_error = exc

    quiet: list[str] = []
    rows_out = [account_counts(
        d, quiet.append, activated_at=activated) for d in dirs]
    published = published_source_refs(state_dir)

    labels = ("归档", "可发", "待译", "待调图", "待发布")
    name_width = max([display_width("账号"), display_width("合计")]
                     + [display_width(item["name"]) for item in rows_out]) + 2
    col = 8

    def line(name: str, cells) -> str:
        return _pad(name, name_width) + "".join(
            _pad(str(cell), col, right=True) for cell in cells)

    header = line("账号", labels)
    print("\n" + header)
    print("-" * display_width(header))
    total = {"archived": 0, "publishable": 0, "pending_translation": 0,
             "pending_images": 0, "pending_image_files": 0}
    for item in rows_out:
        for key in total:
            total[key] += item.get(key, 0)
        pub_cell = ("—" if activated is None else
                    pending_publish_count(item, published))
        print(line(item["name"], (
            item["archived"], item["publishable"],
            item["pending_translation"], item["pending_images"], pub_cell)))
    print("-" * display_width(header))
    print(line("合计", (
        total["archived"], total["publishable"],
        total["pending_translation"], total["pending_images"],
        "—" if activated is None else sum(
            pending_publish_count(item, published) for item in rows_out))))

    for item in rows_out:
        for problem in item["errors"]:
            print("  ! %s：%s" % (item["name"], problem))

    print()
    print("「可发」= 有正文 **且** 至少 1 张已下载的图（发布口径，不是翻译口径）。")
    print("         比 compose 的完整硬闸宽：它还要查译文当前、金额、标签、"
          "媒体完整性。")
    print("         想看某几篇现在到底发不发得出去，跑 "
          "scripts\\run_publish.bat --latest 3。")
    print("「待调图」按**帖**计；本次要付费的**图片**张数是 %d 张。"
          % total["pending_image_files"])
    if activated is None:
        print("「待发布」显示 —— 因为流水线尚未激活；G8 通过前不把历史帖算进队列。")
    else:
        print("「待发布」只统计激活边界之后的可发 source_refs；prepared/failed 与"
              "其它账号的 scheduled 都不会从本账号扣减。")

    print()
    when, sources = last_successful_run(state_dir)
    days = settings["dead_man_days"]
    if when is None:
        print("最近一次成功运行：**从未**（没有任何运行标记）")
        print("                  → 计划任务大概还没装，见 MANUAL_STEPS 第 9 步。")
    else:
        stale = (now - when).total_seconds() > days * 86400
        mark = "⚠️ 超过 %d 天" % days if stale else "✅"
        print("最近一次成功运行：%s（%s）%s"
              % (when.strftime("%Y-%m-%dT%H:%M:%SZ"), _age_text(now, when), mark))
        print("                  证据：%s" % "、".join(sources))

    month = _month_key(now)
    text_cost, image_cost, _ = month_spend(
        dirs, month, quiet.append, state_dir=state_dir)
    print("本月（%s）花费：US$%.4f / %.2f　（翻译 US$%.4f + 图片 US$%.4f）"
          % (month, text_cost + image_cost, settings["monthly_budget_usd"],
             text_cost, image_cost))
    # 同时显示上月费用，避免月初零值掩盖已有支出。
    prev = _previous_month(month)
    prev_text, prev_image, _ = month_spend(
        dirs, prev, lambda _m: None, state_dir=state_dir)
    if prev_text or prev_image:
        print("上月（%s）：US$%.4f　（翻译 US$%.4f + 图片 US$%.4f）"
              % (prev, prev_text + prev_image, prev_text, prev_image))
    print("                  按独立 paid ledger + 旧产物的**真实 usage** 算，"
          "output_rejected 也计费，不按字符/张数外推。")
    for problem in quiet:
        print("  ! %s" % problem)

    print()
    print("[pipeline] autonomy 与日/月预算均已由 pipeline run 消费。")
    print("           autonomy = %s" % settings["autonomy"])
    if g9_error is not None:
        print("           G9 状态读不了：%s" % g9_error)
    else:
        print("           激活边界 = %s" % (
            activated.isoformat() if activated else "未激活（G8 通过后再 activate）"))
        print("           待确认项 = %d（state\\needs_human.html）" % open_count)
    return 0


# 预检

def _probe_observation_gaps() -> tuple[str, ...]:
    """`ui_constraints_verified` 到底还差哪几个观察项。判据借 compose 的，不另写。"""

    configured = cfg().get("publish", "ui_probe_dump", "")
    if not isinstance(configured, str) or not configured.strip():
        return ("[publish].ui_probe_dump 为空",)
    path = ROOT / cfg().get('paths', 'state', 'state') / configured.strip()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return ("probe dump 读不了：%s" % exc,)
    observations = data.get("observations")
    if not isinstance(observations, Mapping):
        observations = {}
    return tuple(key for key in required
                 if not isinstance(observations.get(key), str)
                 or not observations[key].strip())


def _publish_gate_states() -> list[tuple[str, bool, str]]:
    """复用 business_suite 发布校验；导入不启动浏览器。"""
    return [(item['name'], item['available'], item['reason'] or '证据已回查') for item in capability_checks()]


def _task_states() -> list[tuple[str, str]]:
    out = []
    for name in (SCHEDULER_TASK, DAILY_TASK, CATCHUP_TASK, ALIVE_TASK):
        try:
            result = _task_state(name)
        except (OSError, ValueError):
            out.append((name, '状态无法读取'))
            continue
        label = ('未注册' if not result['registered'] else
                 '启用' if result.get('enabled') is True else
                 '停用' if result.get('enabled') is False else '状态无法解析')
        out.append((name, label))
    return out


def _print_heartbeat_preflight(state_dir: Path, now: datetime) -> None:
    """只读本地发送事实；外部告警是否已接通仍需服务方验收。"""
    print("[7] 外部心跳")
    try:
        settings = HeartbeatSettings.load(cfg())
        status = heartbeat_status(Path(state_dir) / "heartbeat.json", settings, now)
    except ValueError:
        print("    配置无效，请核对 [heartbeat] 的启用开关、间隔和超时。")
        return
    labels = {"disabled": "未启用", "never": "尚无成功记录", "unknown": "本地记录无法读取",
              "healthy": "最近有成功记录", "stale": "成功记录已过期", "clock_skew": "成功时刻晚于当前时钟"}
    print("    %s" % labels[status["status"]])
    if status.get("last_success_at"):
        print("    最后成功 %s；距今 %.1f 分钟。" % (
            status["last_success_at"], status["age_seconds"] / 60))
    if status.get("error_code"):
        print("    最近结果：%s" % status["error_code"])
    print("    缺席告警由外部服务承担；本机记录无法证明停机时告警仍能送达。")


def _print_network_preflight(state_dir: Path, now: datetime) -> None:
    print("[8] 出口 IP / ASN（只读已采集证据）")
    try:
        settings = NetworkEvidenceSettings.load(cfg())
        status = network_evidence_status(Path(state_dir) / "network_evidence.json", settings, now)
    except ValueError:
        print("    出口观测配置无效，请核对 [network_evidence]。")
        return
    labels = {"disabled": "未启用采集", "never": "尚无成功记录", "unknown": "本地证据无法读取",
              "healthy": "最近有出口记录", "stale": "出口记录已过期", "clock_skew": "记录时刻晚于当前时钟"}
    print("    %s" % labels[status["status"]])
    if status["latest"]:
        row = status["latest"]
        print("    最后成功 %s（距今 %.1f 分钟）：%s / %s / ASN 类型 %s" % (
            row["at"], status["age_seconds"] / 60, row["ip"], row["asn"], row["asn_type"]))
        stability = {"insufficient_samples": "样本不足", "stable_observed": "样本内出口相同", "changed": "观察到出口变化"}
        print("    最近 %d 次：%s，%d 个 IP，%d 个已知 ASN。" % (
            len(status["history"]), stability[status["stability"]], status["distinct_ips"], status["distinct_asns"]))
        if status["network_type"] == "hosting":
            print("    提供方标记为机房/托管网络，请检查出口配置。")
    if status.get("last_error"):
        print("    IP 信息服务最近结果：%s（不能据此判断社媒账号被封）。" % status["last_error"])
    print("    ISP 分类不能证明住宅出口；此进程到 IPinfo 的出口也不能证明浏览器未使用代理/分流。")
    detection = _load_json(Path(state_dir) / "delta_state.json")
    if isinstance(detection, dict):
        reasons = {"account_checkpoint": "账号 checkpoint / challenge，需要人工核对",
                   "session_or_permission": "会话或权限问题", "rate_limited": "社媒接口限流",
                   "connection_or_timeout": "连接或超时问题", "unclassified": "未分类失败，请查看探测日志"}
        for platform in ("facebook", "instagram"):
            entry = detection.get(platform)
            kind = detection_failure_kind(entry.get("last_error")) if isinstance(entry, dict) else None
            if kind:
                print("    %s 上次探测：%s。" % (platform, reasons[kind]))


def run_preflight(days: int = 90, now: datetime | None = None) -> int:
    """只读预检激活条件，并按实际付费判据检查近期归档。**零网络、零费用、零写盘**。"""

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir = ROOT / cfg().get('paths', 'state', 'state')
    print("=== 上线预检（只读：零网络、零费用、零写盘）===\n")

    ready = True
    print("[1] G6/G6c 三道发布校验")
    for label, ok, detail in _publish_gate_states():
        ready = ready and ok
        print("    %s %-22s %s" % ("[开]" if ok else "[关]", label, detail))

    verified = cfg().get("publish", "ui_constraints_verified", False) is True
    gaps = () if verified else _probe_observation_gaps()
    ready = ready and verified
    print("\n[2] [publish].ui_constraints_verified = %s" % str(verified).lower())
    if not verified:
        print("    还差 %d 个只能人亲眼量的观察项：" % len(gaps))
        for key in gaps:
            print("      - %s" % key)
        print("    填法：docs/MANUAL_STEPS.md 第 8 节（probe_publish 的 --set-note）")

    blockers = assisted.activation_blockers(state_dir)
    scheduled_refs = len(assisted.journal.scheduled_source_refs(state_dir))
    print("\n[3] G8 真机证据（state/published.jsonl 里的 scheduled）：%d 条"
          % scheduled_refs)
    activated = assisted.activation_time(state_dir)
    print("[4] 激活边界：%s"
          % (activated.isoformat() if activated else "未激活"))
    settings = pipeline_settings()
    print("[5] [pipeline].autonomy = %s" % settings["autonomy"])
    print("[6] 计划任务")
    for name, state in _task_states():
        print("    %-24s %s" % (name, state))
    _print_heartbeat_preflight(state_dir, now)
    _print_network_preflight(state_dir, now)

    # ---- 业务配置：激活后决定"每天有多少帖能自己走完" ----
    rules = assisted.publish_rules()
    dirs = translation.account_dirs(cfg().archive_dir)
    horizon = now - timedelta(days=days)
    sources, _issues, out_of_scope = assisted.load_sources(assisted.active_account_dirs(dirs), horizon)
    result = assisted.reconcile(sources)
    tally: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    untrusted: dict[str, int] = {}
    auto = 0
    for candidate in result.candidates:
        issue = assisted.prepaid_issue(candidate, rules)
        if issue is None:
            auto += 1
            continue
        tally[issue.kind] = tally.get(issue.kind, 0) + 1
        if issue.kind == "unmapped_price":
            for token in issue.details.get("amounts") or []:
                unmapped[str(token)] = unmapped.get(str(token), 0) + 1
        elif issue.kind == "unknown_collaborator":
            for who in issue.details.get("collaborators") or []:
                untrusted[str(who)] = untrusted.get(str(who), 0) + 1

    scope_tally: dict[str, int] = {}
    for _ref, reason in out_of_scope:
        scope_tally[reason] = scope_tally.get(reason, 0) + 1

    total = len(out_of_scope) + result.source_count
    print("\n=== 若此刻激活，最近 %d 天的 %d 篇归档会怎么走 ==="
          % (days, total))
    print("  不在图文发布范围，直接跳过（不进人工队列）：%d 篇" % len(out_of_scope))
    for reason, count in sorted(scope_tally.items(), key=lambda kv: -kv[1]):
        print("      %-30s %4d" % (reason, count))
    print("  进入对账的图文帖：%d 篇 → %d 个独立平台候选"
          % (result.source_count, len(result.candidates)))
    for kind, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("      停在人工队列  %-24s %4d" % (kind, count))
    print("  跨平台合并候选：0（FB/IG 各自处理）")
    print("      可自动跑到待确认                  %4d" % auto)
    denominator = len(result.candidates) + len(result.human_items)
    if denominator:
        print("  —— 图文帖里 %d/%d（%.0f%%）能不问人走完付费阶段"
              % (auto, denominator, 100.0 * auto / denominator))

    if untrusted:
        print("\n[!] [publish.trusted_owners] 缺人：这些作者的帖会**逐篇**停在人工队列")
        for who, count in sorted(untrusted.items(), key=lambda kv: -kv[1]):
            print("    @%-28s %4d 篇" % (who, count))
        print("    确认二次使用授权后，把它加进 config.toml 的 "
              "[publish.trusted_owners] 对应平台数组即可。")

    if unmapped:
        print("\n[!] [publish.price_map] 缺行：带这些金额的帖会**逐篇**停在人工队列")
        print("    把下面这段填好右侧德国站定价，贴进 config.toml 的 "
              "[publish.price_map]：")
        print("    ⛔ 右侧由业务定，程序不换算 —— 发错价格是商业事故。\n")
        for token, count in sorted(unmapped.items(), key=lambda kv: (-kv[1], kv[0])):
            print('    "%s" = ""    # 近 %d 天出现 %d 篇' % (token, days, count))

    print("\n=== 下一步 ===")
    if blockers:
        print("  还不能 activate：")
        for line in blockers:
            print("    - %s" % line)
    elif activated is None:
        print("  可以了：python -m pipeline activate --g8-verified")
    elif settings["autonomy"] == "manual":
        print("  已激活。把 config.toml 的 [pipeline].autonomy 改成 assisted。")
    else:
        print("  已激活且 autonomy=%s。剩下的是装计划任务："
              % settings["autonomy"])
        print("    python -m tools.schedule install")
    return 0 if (ready and not blockers) else 1


# 业务缺席检查

ALIVE = 0
ALIVE_CHECK_FAILED = 1
DEAD = 2


def run_check_alive(now: datetime | None = None, state_dir: Path | None = None,
                    notify_fn: Callable[..., None] = notify,
                    popup: bool = True) -> int:
    """检查业务运行缺席：0 正常，1 无法检查，2 已告警；由独立任务触发。"""
    now = now or datetime.now(timezone.utc)
    state_dir = state_dir or cfg().state_dir
    try:
        settings = pipeline_settings()
    except PipelineConfigError as exc:
        print("[!] %s" % exc)
        return ALIVE_CHECK_FAILED

    days = settings["dead_man_days"]
    when, sources = last_successful_run(state_dir)

    if when is None:
        # 区分从未成功与成功后停摆，给出对应处理方法。
        message = ("流水线**从未**成功运行过：没有任何运行标记。\n"
                   "· 刚装完计划任务？那这条是正常的，"
                   "而且正好证明告警通道是通的——等第一次增量跑成就不再响。\n"
                   "· 装了有一阵了？说明它一次都没跑成——"
                   "查 state\\delta.log 与 Task Scheduler 里的「上次运行结果」。\n"
                   "· 还没装？MANUAL_STEPS.md 第 9 步。")
        print("[!] " + message.replace("\n", "\n    "))
        notify_fn("流水线从未运行", message, popup=popup)
        return DEAD

    elapsed_days = (now - when).total_seconds() / 86400
    if elapsed_days > days:
        message = ("流水线已经 %.1f 天没有成功运行（阈值 %d 天）。\n"
                   "最近一次：%s（证据：%s）\n"
                   "「没跑」和「跑了但没新内容」在输出上长得一模一样，"
                   "所以这条告警是唯一会告诉你的东西。\n"
                   "先查：Task Scheduler 里 FBScraperDelta 是不是被禁用了；"
                   "state\\delta.log 最后一条写了什么。"
                   % (elapsed_days, days,
                      when.strftime("%Y-%m-%dT%H:%M:%SZ"), "、".join(sources)))
        print("[!] " + message.replace("\n", "\n    "))
        notify_fn("流水线可能已经停了", message, popup=popup)
        return DEAD

    print("[ok] 流水线活着：最近一次成功运行 %s（%s），阈值 %d 天。"
          % (when.strftime("%Y-%m-%dT%H:%M:%SZ"), _age_text(now, when), days))
    print("     证据：%s" % "、".join(sources))
    return ALIVE


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description="流水线对账、激活与 assisted 执行入口（G9）")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser(
        "status", help="各阶段积压 + 最近一次成功运行 + 本月花费")
    status_parser.add_argument(
        "--account", default=None,
        help="只看一个账号归档目录，如 in_neakasa.tech")

    alive_parser = sub.add_parser(
        "check-alive",
        help="死人开关：太久没成功运行就告警（退出码 0 活 / 2 死）")
    alive_parser.add_argument(
        "--no-popup", action="store_true",
        help="只写 state\\alerts.log，不弹桌面通知（自动化验收用）")

    preflight_parser = sub.add_parser(
        "preflight", help="上线预检：还差什么 + 激活后的处理范围（只读）")
    preflight_parser.add_argument("--json", action="store_true", help="输出与页面共用的五阶段只读 JSON")
    preflight_parser.add_argument(
        "--days", type=int, default=90,
        help="拿最近 N 天归档当「假如那时就激活了」预演（默认 90）")

    notify_parser = sub.add_parser('notifications', help='群机器人投递状态、人工核对与一次性自检')
    notify_parser.add_argument('--resolve', metavar='DELIVERY_ID', help='核对一条不确定或待重试的投递')
    notify_parser.add_argument('--version', help='状态输出里同一条投递的 version')
    notify_parser.add_argument('--delivered', metavar='NOTE',
                               help='已在群里看到；机器人不返回 message ID，这里填人写的核对说明')
    notify_parser.add_argument('--not-delivered', action='store_true', help='已核对确实没有送到')
    notify_parser.add_argument('--self-test', action='store_true',
                               help='真的往两个群各发一张自检卡片（外发动作，只能人工敲）')

    recovery_parser = sub.add_parser('recover-processing', help='核账后关闭中断批次；不会重新调用模型')
    recovery_parser.add_argument('--batch-id', required=True)
    recovery_parser.add_argument('--version', required=True, help='preflight --json 中的 state_revision')
    recovery_parser.add_argument('--outputs-reviewed', action='store_true', help='已核对本批模型产物')

    activate_parser = sub.add_parser(
        "activate", help="G8 通过后原子记录发布边界；不会补发历史")
    activate_parser.add_argument(
        "--g8-verified", action="store_true",
        help="显式确认 G8 真机验收已通过（必填安全闸）")

    run_parser = sub.add_parser(
        "run", help="按 autonomy 对账；assisted 自动 delta/翻译/调图但不碰发布浏览器")
    run_parser.add_argument("--if-stale", action="store_true",
                            help="补跑触发器：delta 未过 stale 窗口就跳过")
    run_parser.add_argument("--account", default=None,
                            help="只处理一个归档账号目录（排障用）")

    approve_parser = sub.add_parser(
        "approve", help="批量选择版本/确认 ready 项；首次不明确失败即停止整批")
    approve_parser.add_argument("--item-id", action="append", required=True,
                                help="待确认 item_id；可重复")
    approve_parser.add_argument(
        "--select-source", action="append", default=None,
        metavar="ITEM=PLATFORM:POST_ID",
        help="相似跨平台项选择哪个源版本；可重复")
    approve_parser.add_argument("--assume-yes", action="store_true",
                                help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.command == 'notifications':
        from core.feishu import FeishuSettings, Outbox, WebhookBot, notification_card
        try:
            settings = FeishuSettings.load()
            outbox = Outbox(cfg().state_dir / 'feishu_outbox.json', settings)
            if args.self_test:
                bot = WebhookBot.from_environment()
                try:
                    for role, group in ((settings.recipients[0], '业务组'),
                                        (settings.technical_recipients[0], '技术组')):
                        card = notification_card('selftest', [{'text':
                            '通道自检，没有业务含义。\n本条发往：%s（%s）\n'
                            '两个群看到的角色应当不同；相同说明两个地址配成了同一个群。'
                            % (group, role)}], settings)
                        print('%s（%s）：%s' % (group, role, bot.send(role, card, 'selftest')))
                finally:
                    bot.close()
                return 0
            if args.resolve:
                if not args.version or bool(args.delivered) == args.not_delivered:
                    print('请给出 --version，并在 --delivered "说明" 与 --not-delivered 中选一个')
                    return 2
                print(json.dumps(outbox.resolve(
                    args.resolve, action='delivered' if args.delivered else 'not_delivered',
                    expected_version=args.version, message_id=args.delivered or '',
                    now=datetime.now(timezone.utc)), ensure_ascii=False, indent=2))
                return 0
            print(json.dumps(outbox.status(), ensure_ascii=False, indent=2))
            return 0
        except (ValueError, RuntimeError) as exc:
            print(str(exc))
            return 2

    if args.command == 'recover-processing':
        from core.monitoring import MonitoringJournal
        try:
            now = datetime.now(timezone.utc)
            result = MonitoringJournal(cfg().state_dir, now=now, inspect_running=False).recover(
                batch_id=args.batch_id, expected_revision=args.version, now=now, outputs_reviewed=args.outputs_reviewed)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, RuntimeError) as exc:
            print(str(exc))
            return 2

    if args.command == "check-alive":
        return run_check_alive(popup=not args.no_popup)

    if args.command == "preflight":
        try:
            if args.json:
                from pipeline.runtime_status import snapshot
                print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
                return 0
            return run_preflight(days=max(1, args.days))
        except (PipelineConfigError, ArchivePathError, OSError) as exc:
            print("[!] 预检失败：%s" % exc)
            return 2

    if args.command == "activate":
        # 激活前核验能力与边界，避免先付费再卡在发布条件。
        blockers = assisted.activation_blockers(cfg().state_dir)
        if blockers and assisted.activation_time(cfg().state_dir) is None:
            print("[!] G8 验收证据不成立，拒绝激活：")
            for line in blockers:
                print("    - %s" % line)
            print("    想看完整清单：python -m pipeline preflight")
            return 2
        try:
            when = assisted.activate(
                cfg().state_dir, g8_verified=args.g8_verified)
        except assisted.PipelineRunError as exc:
            print("[!] %s" % exc)
            return 2
        print("[ok] 流水线激活边界：%s" % when.isoformat())
        print("     只处理此后新帖；重复 activate 不会移动边界。")
        return 0

    if args.command == "approve":
        selections = {}
        for raw in args.select_source or []:
            if "=" not in raw:
                parser.error("--select-source 必须是 ITEM=platform:post_id")
            item_id, ref = raw.split("=", 1)
            if not item_id.strip() or ":" not in ref:
                parser.error("--select-source 必须是 ITEM=platform:post_id")
            selections[item_id.strip()] = ref.strip()
        try:
            return assisted.approve(
                item_ids=args.item_id, selections=selections,
                state_dir=cfg().state_dir, assume_yes=args.assume_yes)
        except (assisted.PipelineRunError, PipelineConfigError) as exc:
            print("[!] %s" % exc)
            return 2

    root = cfg().archive_dir
    all_dirs = translation.account_dirs(root)
    dirs = translation.account_dirs(root, args.account)
    if args.account and not dirs:
        available = [path.name for path in translation.account_dirs(root)]
        print("[!] archive/ 下没有账号目录 %r。" % args.account)
        print("    现有：" + ("、".join(available) if available else "（无）"))
        return 1
    if not dirs:
        print("[!] %s 下没有含 manifest.jsonl 的账号目录；先完成抓取。" % root)
        return 1
    if args.command == "status":
        return run_status(dirs)
    try:
        settings = pipeline_settings()
        return assisted.run(
            account_dirs=all_dirs, processing_account_dirs=dirs,
            state_dir=cfg().state_dir, settings=settings,
            if_stale=args.if_stale, budget_account_dirs=all_dirs)
    except (assisted.PipelineRunError, PipelineConfigError) as exc:
        print("[!] %s" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

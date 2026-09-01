r"""L 组：把四个阶段的产物对起来。**这是一个只读对账器，不是队列。**

    pipeline.py status        各阶段积压 + 最近一次成功 + 本月花费
    pipeline.py check-alive   死人开关：太久没有成功运行就告警

两个子命令都**零网络、零费用、零写盘**（`check-alive` 唯一的副作用是
`core.notify` 那条告警，它本来就要落 `state/alerts.log`）。

### 三条设计约束，改这个文件之前先读

1. **不建第五个真相源。** 四个阶段"做完了没有"全都是内容寻址的，
   每次运行现算即可（`PIPELINE_PLAN` 第 2 节）。所以这里**没有** `pipeline` 自己的
   队列文件、没有缓存、没有索引。
2. **不重写别人的判据，调他们已有的入口。**
   待译走 :func:`translate.pending`，待调图走 :func:`localize_images.build_jobs`。
   `publish/compose.py` 因为跨组边界另写了一份 `media_de` 所有权判定，
   结果就是 CR-48 那种"两组对同一个目录有两套语义"。**这里不重复那个代价。**
3. **不打自己不知道的数。** 没有真相源的阶段一律显示 ``—`` 并在脚注里说明，
   **不显示 0** —— 0 会被读成"没有积压"，而实际含义是"这个阶段还没接上"。
   这条与 CR-19 同源：静默的 0 比缺失更危险。
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                         # noqa: E402
from core.console import force_utf8                 # noqa: E402
from core.notify import notify                      # noqa: E402
from core.store import Archive, ArchivePathError    # noqa: E402
import translate as translation                     # noqa: E402


# `[pipeline]` 的全部合法键。CR-40 立的规矩：不许有改了不生效的死旋钮，
# 也不许有拼错了却静默被忽略的键。所以这张表是双向的 ——
# 表外的键报错，表里标 False 的键在 status 里显式说明"还没接上"。
PIPELINE_CONFIG_KEYS: dict[str, bool] = {
    "autonomy": False,             # L1d 才消费
    "dead_man_days": True,         # ← 本文件的 check-alive 就在消费它
    "monthly_budget_usd": False,   # L1b 才真正当闸用；status 只拿它做分母
    "daily_budget_usd": False,     # L1b
}
AUTONOMY_LEVELS = ("manual", "assisted", "supervised", "autonomous")


class PipelineConfigError(RuntimeError):
    """`[pipeline]` 配置本身不合法。失败闭合，不猜默认值。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def pipeline_settings(raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """严格读 `[pipeline]`；未知键、错类型都失败闭合。

    形状照抄 ``localize_images.Settings.validate`` 的双向对账，
    但这里只有四个键，不值得为它建一个类。
    """
    if raw is None:
        raw = cfg()._d.get("pipeline", {}) or {}
    if not isinstance(raw, Mapping):
        raise PipelineConfigError("[pipeline] 不是一张表")

    unknown = sorted(set(raw) - set(PIPELINE_CONFIG_KEYS))
    if unknown:
        raise PipelineConfigError(
            "[pipeline] 有代码不认识的键：" + "、".join(unknown)
            + "\n    （拼错的键会静默失效，所以这里直接拒绝；"
              "真要加新键，请同时改 pipeline.py 的 PIPELINE_CONFIG_KEYS）")
    missing = sorted(set(PIPELINE_CONFIG_KEYS) - set(raw))
    if missing:
        raise PipelineConfigError(
            "[pipeline] 缺少必需键：" + "、".join(missing)
            + "\n    （config.toml 里那一整段是 L 组的骨架，不要删键）")

    autonomy = raw["autonomy"]
    if autonomy not in AUTONOMY_LEVELS:
        raise PipelineConfigError(
            "[pipeline].autonomy 必须是 %s 之一，实得 %r"
            % ("/".join(AUTONOMY_LEVELS), autonomy))

    days = raw["dead_man_days"]
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise PipelineConfigError(
            "[pipeline].dead_man_days 必须是 >=1 的整数，实得 %r" % (days,))

    budgets = {}
    for key in ("monthly_budget_usd", "daily_budget_usd"):
        value = raw[key]
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or value < 0):
            raise PipelineConfigError(
                "[pipeline].%s 必须是非负数，实得 %r" % (key, value))
        budgets[key] = float(value)

    return {"autonomy": autonomy, "dead_man_days": days, **budgets}


# ---------------------------------------------------------------------------
# 「最近一次成功运行」—— 死人开关的判据
# ---------------------------------------------------------------------------

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
    """返回 (最近一次成功**运行**的时刻, 证据来源列表)。

    ⚠️ **判据必须是"跑过"，不能是"产出过"。** `PIPELINE_PLAN` 第 7 节点명了
    这个项目最怕的失效：**「没跑」和「跑了但没新内容」在输出上长得一模一样**。
    如果拿 `manifest.jsonl` / `translated.jsonl` 的最新一条当判据，
    一个每天准时跑、但恰好没有新帖的流水线会被误判成"死了"，
    而误报会让整条告警通道失效（`core/integrity.py` 已经立过这条规矩）。

    所以只认**运行标记**：

    - `state/delta_state.json` 的各平台 ``last_success`` —— 抓到 0 篇也会更新它，
      这正是我们要的语义。**今天它是唯一在真跑的东西。**
    - `state/pipeline_state.json` 的 ``last_successful_run`` —— L1a 的
      ``pipeline run`` 落地之后才会有这个文件。**现在不存在是正常的**，
      所以这里只把它当补充证据，缺了不报错。

    > **为什么不新建一个 `pipeline_state.json` 来记这件事**（任务书原文的做法）：
    > `delta_state.json` 已经记了，再记一份就是第二个真相源（第 2 节明令不要）。
    > 等 L1a 真的有了跨阶段的运行概念，它自然会写自己那一份，这里已经预留了读它的位置。
    """
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


# ---------------------------------------------------------------------------
# 各阶段积压
# ---------------------------------------------------------------------------

def publishable_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """「可发」= 有正文 **且** 至少 1 张**已下载**的图。

    ⚠️ **这是发布口径，不是翻译口径**，两者差 597 篇（纯视频 / 无图 / 无正文）。
    实测 1067 篇归档里只有 470 篇可发。**这是这个项目最容易搞错的一处数字**，
    改这个函数之前先确认你要的是哪一个。

    ⚠️ 它比 ``publish/compose.py::compose_post`` 的完整硬闸**宽**：
    compose 还要求 ``media_complete=True``、媒体项全部是图片、译文当前、
    金额与标签逐字符未变……这里**不重复那套判据**（第 2 节：不重写别人的逻辑）。
    所以「可发」的含义是"原则上进得了发布流水线"，
    **不是"现在就能发出去"**。status 的脚注里写着这句话，不要把它去掉。
    """
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
    """待调图 = 可发 ∩（缺当前译文 **或** 还有图没生成）。

    返回 (待调图的 post_id 集合, 待处理**图片**张数)。
    图片张数是花钱的那个数，帖子数是积压的那个数，两个都要。

    判据整个交给 :func:`localize_images.build_jobs` —— 它已经把
    「人工优先」「当前记录」「素材问题」全考虑进去了，重写一遍必然漂移。
    没有当前译文的帖子 K 组根本不排队，所以那部分要在这里补上。
    """
    import localize_images as image_de          # 延迟导入：它会拉起 Pillow

    settings = image_de.Settings()
    jobs, _state, _stats = image_de.build_jobs(
        settings, arc_base, rows, force=False, report=report)
    with_pending_jobs = {job.post_id for job in jobs} & publishable
    pending_jobs = sum(1 for job in jobs if job.post_id in publishable)
    return (with_pending_jobs | (pending_translation & publishable),
            pending_jobs)


def published_ids(state_dir: Path | None = None) -> set[str] | None:
    """读 `state/published.jsonl`；**文件不存在时返回 ``None``，不是空集**。

    ⚠️ 这个区分是有意的：`published.jsonl` 由 **G6** 写，而 G6 被 G1 卡着，
    今天这个文件根本不存在。返回空集会让"待发布"等于"可发"，
    打印出来是一个**看起来像积压、实际是"这个阶段还没接上"**的数字。
    调用方看到 ``None`` 应当显示 ``—`` 并注明原因。
    """
    state_dir = state_dir or cfg().state_dir
    path = state_dir / "published.jsonl"
    if not path.is_file():
        return None
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue                    # 一条坏行不该让整张表打不出来
        if isinstance(row, dict) and isinstance(row.get("post_id"), str):
            out.add(row["post_id"].strip())
    return out


# ---------------------------------------------------------------------------
# 本月花费（读两个阶段已经逐条写下的真实 usage）
# ---------------------------------------------------------------------------

def _month_key(when: datetime) -> str:
    return when.strftime("%Y-%m")


def _previous_month(month: str) -> str:
    year, mon = (int(part) for part in month.split("-"))
    return "%04d-%02d" % (year - 1, 12) if mon == 1 else "%04d-%02d" % (year, mon - 1)


def month_spend(dirs: list[Path], month: str,
                warn: Callable[[str], None]) -> tuple[float, float, list[str]]:
    """返回 (翻译花费, 图片花费, 无法计算的原因列表)，单位 US$。

    ⚠️ **只读两个阶段已经逐条记下的真实 usage，不按字符数或张数外推。**
    CR-40 的教训：F 组按字符估给出 US$1.08–5.41，真实是 US$23.73，
    低估一个数量级 —— 漏掉了 thinking 那个主导项。
    """
    problems: list[str] = []
    text_cost = image_cost = 0.0

    try:
        ts = translation.Settings()
    except SystemExit as exc:
        problems.append("[translate] 配置不可用，翻译花费未计入（%s）" % exc)
        ts = None
    try:
        import localize_images as image_de
        isettings = image_de.Settings()
    except SystemExit as exc:
        problems.append("[image] 配置不可用，图片花费未计入（%s）" % exc)
        image_de, isettings = None, None

    for arc_base in dirs:
        if ts is not None:
            for row in _jsonl_rows(arc_base / "translated.jsonl"):
                when = _parse_iso_z(row.get("translated_at"))
                if when is None or _month_key(when) != month:
                    continue
                usage = row.get("usage")
                if not isinstance(usage, dict):
                    continue
                cost = translation.usage_cost_upper_bound(ts, usage)
                if cost is not None:
                    text_cost += cost
        if isettings is not None:
            for row in _jsonl_rows(arc_base / "images_de.jsonl"):
                when = _parse_iso_z(row.get("created_at"))
                if when is None or _month_key(when) != month:
                    continue
                usage = row.get("usage")
                if not isinstance(usage, Mapping):
                    continue
                cost = image_de.image_usage_cost(isettings, usage)
                if cost is not None:
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


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def account_counts(arc_base: Path, report: Callable[[str], None]) -> dict:
    """一个账号的各阶段积压。任何一段炸掉都只影响这个账号，不掀掉整张表。"""
    counts = {"name": arc_base.name, "archived": 0, "publishable": 0,
              "pending_translation": 0, "pending_images": 0,
              "pending_image_files": 0, "errors": []}
    try:
        rows = Archive(arc_base.parent, arc_base.name).rows()
    except (OSError, ArchivePathError, ValueError) as exc:
        counts["errors"].append("读不了 manifest：%s" % exc)
        return counts
    counts["archived"] = len(rows)

    publishable = publishable_ids(rows)
    counts["publishable"] = len(publishable)

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
    """终端列宽：CJK / 全角算 2 列。

    ``%-24s`` 按**字符**补齐，而账号名旁边的表头是中文，
    于是列会歪。这个项目的输出是给人在 cmd 窗口里看的，歪掉的表读起来很费劲。
    """
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

    quiet: list[str] = []
    rows_out = [account_counts(d, quiet.append) for d in dirs]
    published = published_ids(state_dir)

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
        pub_cell = "—" if published is None else max(
            item["publishable"] - len(published), 0)
        print(line(item["name"], (
            item["archived"], item["publishable"],
            item["pending_translation"], item["pending_images"], pub_cell)))
    print("-" * display_width(header))
    print(line("合计", (
        total["archived"], total["publishable"],
        total["pending_translation"], total["pending_images"],
        "—" if published is None else max(
            total["publishable"] - len(published), 0))))

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
    if published is None:
        print("「待发布」显示 —— 因为 state\\published.jsonl 还不存在："
              "它由 G6 写，而 G6 被 G1 卡着。")
        print("         这里不打 0 —— 0 会被读成「没有积压」，"
              "实际含义是「这个阶段还没接上」。")

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
    text_cost, image_cost, _ = month_spend(dirs, month, quiet.append)
    print("本月（%s）花费：US$%.4f / %.2f　（翻译 US$%.4f + 图片 US$%.4f）"
          % (month, text_cost + image_cost, settings["monthly_budget_usd"],
             text_cost, image_cost))
    # 月初刚翻篇时本月必然是 0，而上个月可能刚花过钱。只打本月会让人误以为
    # "这东西从来没花过钱"，顺带也看不出费用计算到底通没通。
    prev = _previous_month(month)
    prev_text, prev_image, _ = month_spend(dirs, prev, lambda _m: None)
    if prev_text or prev_image:
        print("上月（%s）：US$%.4f　（翻译 US$%.4f + 图片 US$%.4f）"
              % (prev, prev_text + prev_image, prev_text, prev_image))
    print("                  按两个阶段逐条记下的**真实 usage** 算，不按字符/张数外推。")
    for problem in quiet:
        print("  ! %s" % problem)

    print()
    not_wired = [k for k, wired in PIPELINE_CONFIG_KEYS.items() if not wired]
    print("[pipeline] 当前只有 dead_man_days 被真的消费了（本文件的 check-alive）。")
    print("           还没接上的：%s —— autonomy 归 L1d，两个 budget 归 L1b。"
          % "、".join(not_wired))
    print("           autonomy = %s" % settings["autonomy"])
    return 0


# ---------------------------------------------------------------------------
# check-alive（死人开关）
# ---------------------------------------------------------------------------

ALIVE = 0
ALIVE_CHECK_FAILED = 1
DEAD = 2


def run_check_alive(now: datetime | None = None, state_dir: Path | None = None,
                    notify_fn: Callable[..., None] = notify,
                    popup: bool = True) -> int:
    """死人开关。退出码：0 = 活着，1 = 查不了，2 = 判定为死并已告警。

    ⚠️ **这个检查必须由一个独立的计划任务触发**（`FBScraperAlive`），
    不能只挂在 `pipeline run` 或增量任务里 —— **它自己不跑的时候，
    正是最需要它响的时候**。挂在增量任务里，增量任务一被禁用就一起哑了。
    独立任务意味着要两个东西同时失效才会静默，而不是一个。
    """
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
        # 「从未跑过」和「跑过但停了」是两件事，消息要分开写：
        # 前者的动作是"去装计划任务"，后者是"去看它为什么停了"。
        #
        # ⚠️ 刚装完计划任务、第一次增量还没跑成之前，这条**一定**会响一次。
        # 那不是误报，是这条告警通道的自检 —— 消息里必须说清楚，
        # 否则用户第一次见到它就会把通知关掉，而那正是最坏的结果
        # （core/integrity.py 立过的规矩：误报的代价是让整条通道失效）。
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
        description="流水线只读对账器（L 组）。零网络、零费用、零写盘。")
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

    args = parser.parse_args(argv)

    if args.command == "check-alive":
        return run_check_alive(popup=not args.no_popup)

    root = cfg().archive_dir
    dirs = translation.account_dirs(root, args.account)
    if args.account and not dirs:
        available = [path.name for path in translation.account_dirs(root)]
        print("[!] archive/ 下没有账号目录 %r。" % args.account)
        print("    现有：" + ("、".join(available) if available else "（无）"))
        return 1
    if not dirs:
        print("[!] %s 下没有含 manifest.jsonl 的账号目录；先完成抓取。" % root)
        return 1
    return run_status(dirs)


if __name__ == "__main__":
    raise SystemExit(main())

"""离线生成原文、译文和图片对照清单，以及 text_de.txt 副本。"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localize import images as image_de                  # noqa: E402
from core.paid_model import FileLockBusy           # noqa: E402
from core.store import (Archive, ArchivePathError,  # noqa: E402
                        assert_physical_direct_path, post_directory, read_post_truth)
from core.translated import (HumanRevisionConflict, effective_translation,  # noqa: E402
                             hashtags_preserved, image_translation,
                             load_human_translated, load_translated,
                             money_preserved, preserve_legacy_translation,
                             review_numeric_flags, translation_is_current,
                             translation_text_history)


def run_review(arc_base: Path) -> int:
    """生成 review.md，用相对路径展示同目录的文案与配图。"""
    arc = Archive(arc_base.parent, arc_base.name)
    sync_notices: list[str] = []
    row_list = _review_sources(arc, sync_notices)
    rows = {r["post_id"]: r for r in row_list}
    # 目录名是 `<平台前缀>_<账号>`；用它判断一篇帖子是本账号原创还是合作帖
    this_account = arc_base.name.split("_", 1)[-1].strip().lower()
    machine = load_translated(arc_base / "translated.jsonl")
    # 先保全旧版 text_de.txt 里的手工修改，再生成清单；首次迁移也能看到它。
    n_de = sync_text_de(arc_base, row_list, machine, notices=sync_notices)
    human = load_human_translated(arc_base / "translated_human.jsonl")
    trans = {pid: entry for pid, source in rows.items()
             if (entry := effective_translation(source, machine.get(pid), human.get(pid)))}
    if not trans:
        print(f"  {arc_base.name}：还没有译文，跳过（先跑一次翻译）")
        return 0
    image_state = image_de.load_image_state(arc_base / "images_de.jsonl")
    # 图片配置仅供告警，错误时不阻断文案审校清单。
    try:
        image_settings = image_de.Settings()
    except SystemExit as exc:
        image_settings = None
        print(f"  ! [image] 配置当前不可用（{exc}）；"
              "仍生成对照清单，跳过形变和放大告警")

    eligible = {pid for pid, r in rows.items() if (r.get("text") or "").strip()}
    orphan_ids = sorted((set(machine) | set(human)) - set(rows))
    translated_ids = {
        pid for pid in set(trans) & eligible
        if translation_is_current(rows[pid], trans[pid])
    }
    stale_ids = sorted((set(trans) & eligible) - translated_ids)
    stale_human_ids = {pid for pid in stale_ids if trans[pid]["is_human"]}
    stale_machine_ids = sorted(set(stale_ids) - stale_human_ids)
    missing_ids = eligible - translated_ids
    ordered = sorted(
        (trans[pid] for pid in translated_ids | stale_human_ids),
        key=lambda t: (rows.get(t["post_id"], {}).get("created_at") or "", t["post_id"]))

    n_flagged = sum(1 for t in ordered
                    if review_numeric_flags(rows[t["post_id"]].get("text") or "",
                                            t.get("text_de", "")))
    n_money_violated = sum(1 for t in ordered
                           if money_preserved(rows.get(t["post_id"], {}).get("text") or "",
                                              t.get("text_de", "")))
    n_hashtag_violated = sum(1 for t in ordered
                             if hashtags_preserved(
                                 rows.get(t["post_id"], {}).get("text") or "",
                                 t.get("text_de", "")))

    lines = [
        f"# 德语文案审校清单 · {arc_base.name}",
        "",
        f"生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}　"
        f"可译 {len(eligible)} 篇 / 当前有效译文 {len(translated_ids)} 篇 / "
        f"待译 {len(missing_ids)} 篇（过期 {len(stale_ids)} 篇），"
        f"其中 **{n_flagged} 篇含需人工确认的数字**",
        "",
        "审校方式：逐篇看「德语译文」并勾选/批注。再次运行 `--review` 会重建本文件，"
        "程序会先把上一版保存为 `review.previous.md`；本文件不是译文真相源。",
        "",
        "两类必须人工处理的事：",
        "",
        "1. **数字**：人工确认德国定价和尺码，模型不换算。",
        "2. **图片**：逐张核对德语、保留文字和产品外观；人工图优先。",
        "",
    ]
    for notice in sync_notices:
        lines += ["> ⚠️ " + notice, ""]
    if orphan_ids:
        lines += [f"> ⚠️ `translated.jsonl` 有 {len(orphan_ids)} 条在当前 manifest 中找不到的"
                  "孤儿记录，本清单已忽略：" + "、".join(orphan_ids[:10]), ""]
    if stale_machine_ids:
        lines += [f"> ⚠️ 有 {len(stale_machine_ids)} 条译文对应旧版英文正文或旧提示词版本，"
                  "本清单已忽略；"
                  "普通重跑会只重译这些帖子：" + "、".join(stale_machine_ids[:10]), ""]
    if stale_human_ids:
        lines += [f"> ⚠️ {len(stale_human_ids)} 篇人工译文的原文已变更或旧文件来源未确认；"
                  "人工内容已保留，请复核后重新保存。复核前不继续生成或发布。", ""]
    if missing_ids:
        lines += [f"> ℹ️ 当前是部分审校：还有 {len(missing_ids)} 篇正文尚未翻译。", ""]
    if n_money_violated:
        lines += [
            f"> ❗ **{n_money_violated} 篇的金额没有被原样保留**——模型没照提示词做，"
            f"这几篇下面标了 ❗，请优先核对。",
            "> 规则是：美元金额逐字符原样复制，`$49.99` 就得还是 `$49.99`，"
            "不许变成 `49,99 €`，也不许变成 `49,99 $`。",
            "",
        ]
    if n_hashtag_violated:
        lines += [
            f"> ❗ **{n_hashtag_violated} 篇的话题标签没有逐个原样照搬**——"
            "数量、内容、大小写或顺序与原帖不一致，请优先核对。",
            "",
        ]
    lines += ["---", ""]

    for i, t in enumerate(ordered, 1):
        pid = t["post_id"]
        src = rows.get(pid, {})
        created = (src.get("created_at") or "")[:10]
        lines += [f"## {i}. `{pid}`　{created}", ""]
        if src.get("permalink"):
            lines += [f"原帖：<{src['permalink']}>", ""]
        # 展示合作作者，供审核二次使用授权。
        if (src.get("owner")
                and str(src["owner"]).strip().lower() != this_account):
            lines += [f"> 🤝 **合作帖**：原作者是 `@{src['owner']}`，"
                      f"本账号是 coauthor。发布前确认二次使用授权。", ""]

        lines += ["**英文原文**", ""]
        lines += markdown_text_block(src.get("text") or "") + [""]
        lines += ["**德语译文**", ""]
        if t["is_human"]:
            lines += ["> 人工版本优先。" + (
                "原文已变更或旧文件来源未确认，请复核后重新保存。" if t["stale"] else ""), ""]
        lines += markdown_text_block(t.get("text_de", "")) + [""]

        money_violations = money_preserved(src.get("text") or "", t.get("text_de", ""))
        hashtag_violations = hashtags_preserved(
            src.get("text") or "", t.get("text_de", ""))
        if money_violations:
            lines.append("> ❗ **金额没有原样保留——模型没照提示词做，这条要重点看**")
            lines += [f"> - {v}" for v in money_violations]
            lines.append("> - 规则：金额必须逐字符原样复制（数值/小数点/货币符号/"
                         "符号位置都不许改），德国站定价由人工替换")
            lines.append("")
        if hashtag_violations:
            lines.append("> ❗ **话题标签没有逐个原样照搬——这条要重点看**")
            lines += [f"> - {v}" for v in hashtag_violations]
            lines.append("")

        flags = review_numeric_flags(src.get("text") or "", t.get("text_de", ""))
        if flags:
            lines.append("> ⚠️ **需人工确认的数字**")
            lines += [f"> - {f}" for f in flags]
            lines.append("")

        media = [m for m in (src.get("media") or []) if m.get("kind") == "image"]
        local = [m for m in media if m.get("local_path")]
        if local:
            lines.append("**配图**")
            lines.append("")
            for m in local:
                p = str(m["local_path"]).replace("\\", "/")
                dim = (f"（{m.get('width')}×{m.get('height')}）"
                       if m.get("width") else "")
                lines.append(f"![{pid}]({p}) {dim}")
            lines.append("")
        elif media:
            lines += ["**配图**：有 %d 张，但本地文件缺失（media 未下载成功）" % len(media), ""]
        else:
            lines += ["**配图**：无", ""]

        if not src.get("media_complete", True):
            lines += ["> ⚠️ 该帖媒体不全（登出增量只拿到轮播封面），"
                      "配图可能少于实际。", ""]

        image_pairs = image_de.review_image_pairs(
            arc_base, src, image_translation(src, machine.get(pid), human.get(pid)) or {},
            state=image_state)
        if image_pairs:
            lines += ["**原图 / 德语图对照**", "",
                      "| 原图 | 德语图 |", "| --- | --- |"]
            for pair in image_pairs:
                source_ref = pair.source_rel.replace("\\", "/")
                original_cell = f"![{pid} 原图 {pair.media_index + 1}](<{source_ref}>)"
                if pair.localized_rel:
                    localized_ref = pair.localized_rel.replace("\\", "/")
                    kind = "人工覆盖" if pair.manual else "程序产出"
                    localized_cell = (
                        f"![{pid} 德语图 {pair.media_index + 1}](<{localized_ref}>)"
                        f"<br>{kind}")
                else:
                    localized_cell = "⚠️ **尚未生成德语图**"
                lines.append(f"| {original_cell} | {localized_cell} |")
            lines.append("")

            for pair in image_pairs:
                lines += [f"**第 {pair.media_index + 1} 张图逐类检查**", ""]
                if pair.record:
                    distance = pair.record.get("dhash_distance", "?")
                    drift = pair.record.get("aspect_drift_percent", "?")
                    scale = pair.record.get("scale_factor")
                    lines.append(
                        f"> 程序记录：size {pair.record.get('size_requested', '?')} → "
                        f"{pair.record.get('size_returned', '?')}；dHash 距离 {distance}；"
                        f"宽高比形变 {drift} %"
                        + (f"；放大 {scale}x" if scale is not None else "") + "。")
                    if (image_settings is not None and isinstance(drift, (int, float))
                            and drift > image_settings.aspect_drift_warn_percent):
                        lines.append(
                            f"> ⚠️ 宽高比形变超过配置阈值 "
                            f"{image_settings.aspect_drift_warn_percent:g} %，重点检查构图。")
                    if (image_settings is not None and isinstance(scale, (int, float))
                            and scale > image_settings.scale_warn_factor):
                        lines.append(
                            f"> ⚠️ 原图被放大 {scale}x（超过 "
                            f"{image_settings.scale_warn_factor:g}x）——"
                            "德语图是放大件，请确认清晰度可接受再勾选。")
                    lines.append("")
                elif pair.manual:
                    lines += ["> ℹ️ 当前德语图是人工覆盖版本，仍需逐类验收。", ""]
                else:
                    lines += ["> ⚠️ 先生成/补齐德语图，以下项目才能勾选。", ""]
                lines += [
                    "- [ ] 德语文字正确，且所有应译英文均已替换（无漏译/错译）",
                    "- [ ] 优惠码 / 折扣码逐字符未变",
                    "- [ ] 品牌名 / Logo / 商标逐字符未变",
                    "- [ ] 产品型号逐字符未变",
                    "- [ ] 合作方水印 / 署名 / 作者账号逐字符未变",
                    "- [ ] 金额 / 货币符号 / 小数点 / 符号位置逐字符未变",
                    "- [ ] 数值与单位逐字符未变，且没有换算",
                    "- [ ] 认证、合规与法律标记逐字符未变",
                    "- [ ] @提及 / #标签 / URL / 二维码 / 条码 / 人名地名未变",
                    "- [ ] 产品外观、人物、背景、构图、配色与非文字元素未变",
                    "",
                ]

        lines.append("- [ ] 译文已审校")
        if money_violations:
            lines.append("- [ ] **金额已核对回原文写法**（模型改动过，必查）")
        if hashtag_violations:
            lines.append("- [ ] **话题标签已恢复为与原帖完全一致**（模型改动过，必查）")
        if flags:
            lines.append("- [ ] 数字已按德国站确认/替换")
        lines += ["- [ ] 已逐张核对德语图片",
                  "", "---", ""]

    out = arc_base / "review.md"
    backup = arc_base / "review.previous.md"
    assert_physical_direct_path(
        arc_base, out, kind="file", label="review.md")
    assert_physical_direct_path(
        arc_base, backup, kind="file", label="review.previous.md")
    backed_up = False
    if out.exists():
        out.replace(backup)
        backed_up = True
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {arc_base.name}：{out}　（{len(ordered)} 篇，"
          f"另写出 {n_de} 个 text_de.txt）")
    if backed_up:
        print(f"    上一版审校清单已备份：{backup.name}")
    return len(ordered)


def _review_sources(arc: Archive, notices: list[str]) -> list[dict]:
    """索引只用于定位；已有帖子目录必须读 post.json，不能用旧索引判人稿过期。"""
    rows: list[dict] = []
    for indexed in arc.rows():
        if (not isinstance(indexed, dict)
                or not isinstance(indexed.get("post_id"), str)
                or not indexed["post_id"].strip()
                or not isinstance(indexed.get("text"), str)):
            continue
        try:
            directory = post_directory(arc.base, indexed)
            if not directory.exists():
                # 尚未迁移的历史布局仍能离线查看；sync 不会向不存在的目录写副本。
                rows.append(indexed)
                continue
            truth, _directory = read_post_truth(arc.base, indexed)
            rows.append(truth)
        except (OSError, ValueError) as exc:
            notice = (f"{indexed['post_id']} 的 post.json 无法安全读取，已跳过本篇审校与文本同步，"
                      f"旧文件保持不变：{exc}")
            print("    ! " + notice)
            notices.append(notice)
    return rows


def sync_text_de(arc_base: Path, rows: list[dict], trans: dict[str, dict], *,
                 notices: list[str] | None = None) -> int:
    """同步人工优先的德文副本；未知旧文件先留档，不覆盖已有人工版本。"""
    written = removed = 0
    human_path = arc_base / "translated_human.jsonl"
    human = load_human_translated(human_path)
    history = translation_text_history(arc_base)
    for row in rows:
        # 派生副本跳过脏输入，不改变事实源。
        if not isinstance(row, dict) or not row.get("post_id"):
            continue
        pid = row["post_id"]
        try:
            d = post_directory(arc_base, row)
        except ArchivePathError as exc:
            print(f"    ! 跳过不安全的 text_de.txt 目标：{exc}")
            continue
        if not d.is_dir():
            continue          # 还没迁移到新布局，或该帖文件夹被人删了
        text_de = d / "text_de.txt"
        try:
            assert_physical_direct_path(
                d, text_de, kind="file", label="text_de.txt")
        except ArchivePathError as exc:
            print(f"    ! 跳过不安全的 text_de.txt 目标：{exc}")
            continue
        if text_de.exists():
            try:
                existing = text_de.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                print(f"    ! 保留无法读取的 text_de.txt，未覆盖：{exc}")
                continue
            known = set(history.get(pid, set()))
            supplied = trans.get(pid)
            if isinstance(supplied, dict) and isinstance(supplied.get("text_de"), str):
                known.add(supplied["text_de"])
            if existing not in known:
                if not existing.strip():
                    print(f"    ! {pid} 的 text_de.txt 为空，保留原文件供人工确认")
                    continue
                if pid in human:
                    message = (f"{pid} 的 text_de.txt 与已保存人工稿不同；旧文件已原样保留，"
                               "本清单展示已保存人工稿。请比较后在审校台明确保存所需内容。")
                    print("    ! " + message)
                    if notices is not None:
                        notices.append(message)
                    continue
                try:
                    human[pid] = preserve_legacy_translation(human_path, pid, existing)
                except (HumanRevisionConflict, FileLockBusy):
                    message = (f"{pid} 的人工译文正在保存或已有更新；text_de.txt 已原样保留，"
                               "未导入旧文件。请保存完成后重新生成审校清单。")
                    print("    ! " + message)
                    if notices is not None:
                        notices.append(message)
                    continue
                history.setdefault(pid, set()).add(existing)
                print(f"    ! {pid} 的旧手工译文已留档，来源待复核")
        entry = effective_translation(row, trans.get(pid), human.get(pid))
        de = entry.get("text_de") if entry else None
        current = bool(entry and translation_is_current(row, entry))
        # 过期人工版仍保留供审校人看；只能清理可重建的旧机器副本。
        keep_human = bool(entry and entry["is_human"])
        if not current and not keep_human:
            if text_de.exists():
                try:
                    text_de.unlink()
                    removed += 1
                except OSError as exc:
                    print(f"    ! 无法移除过期的 text_de.txt：{exc}")
            continue
        text_de.write_text(de, encoding="utf-8")
        written += 1
    if removed:
        print(f"    - 已移除 {removed} 个过期 text_de.txt；旧付费记录仍保留在 translated.jsonl")
    return written


def markdown_text_block(text: str) -> list[str]:
    """把外部正文放进不会被其自身反引号提前闭合的 Markdown 围栏。"""
    runs = re.findall(r"`+", text or "")
    width = max(3, max((len(run) for run in runs), default=0) + 1)
    fence = "`" * width
    return [fence + "text", (text or "").rstrip(), fence]

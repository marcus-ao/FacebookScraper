"""当前本地批次的只读处理预览；不调用执行器，也不创建账本或派生文件。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core import paid_requests, review, translated
from localize import images, text as translation
from pipeline import engine, risk_scan
from publish import journal


def _image_plan(source: engine.SourcePost, needs_translation: bool,
                settings: images.Settings) -> dict:
    messages: list[str] = []
    if not needs_translation:
        jobs, _state, stats = images.build_jobs(
            settings, source.account_dir, [dict(source.row)], report=messages.append)
        conditional: list[int] = []
    else:
        # 尚未存在的模型文案只参与内存规划。已生成图是否因新译文失效须等翻译后核对。
        state = images.load_image_state(source.account_dir / "images_de.jsonl")
        stats = images.RunStats()
        jobs = images.image_jobs_for_translation(
            settings, source.account_dir, dict(source.row),
            {"text_de": "<translation pending>"}, state, [], stats,
            report=messages.append)
        conditional = []
        required = []
        for job in jobs:
            record = state.latest.get(job.key)
            same_basis = replace(job, text_de_sha256=str(
                (record or {}).get("text_de_sha256") or ""))
            if (images.image_record_is_current(same_basis, record)
                    and images._record_output_exists(source.account_dir, record)):
                conditional.append(job.media_index)
            else:
                required.append(job)
        jobs = required
    return {
        "required_indices": [job.media_index for job in jobs],
        "conditional_indices": conditional,
        "new_images_min": len(jobs),
        "new_images_max": len(jobs) + len(conditional),
        "kept_current": stats.skipped_current,
        "kept_manual": stats.skipped_manual,
        "bad_sources": stats.skipped_bad_source,
        "notes": messages,
    }


def _candidate_reason(candidate: engine.Candidate, rules: engine.PublishRules,
                      state_dir: Path, scheduled_refs: set[str]) -> str | None:
    source = candidate.canonical
    status = review.state_for(source.account_dir, dict(source.row))["status"]
    if status in {"snoozed", "skipped", "handed_off", "approved", "scheduled"}:
        return "审校状态 %s，自动处理跳过" % status
    if scheduled_refs.intersection(candidate.source_refs):
        return "来源已有 scheduled 回读记录，自动处理跳过"
    if engine._open_items_intersecting(
            state_dir, candidate.source_refs, kinds=("late_scheduled_overlap",)):
        return "来源仍受迟到配对人工闸阻塞"
    pending = journal.pending_record_for_refs(state_dir, candidate.source_refs)
    if pending is not None:
        return "发布状态 %s 未闭合，核对前禁止再次付费" % pending.get("status")
    issue = engine.prepaid_issue(candidate, rules)
    return issue.summary if issue is not None else None


def _text_reference(settings: translation.Settings, source: engine.SourcePost,
                    account_rows: list[dict]) -> float | None:
    examples = translation.pick_style_examples(
        account_rows, settings.style_examples,
        owner=source.account_dir.name.split("_", 1)[-1].strip().lower())
    prompt = translation.build_system_prompt(settings, examples, source.platform)
    source_tokens = translation._approx_tokens(translation.without_urls(source.text.strip()))
    return translation.usage_cost_upper_bound(settings, {
        "input_tokens": translation._approx_tokens(prompt) + source_tokens,
        "output_tokens": round(source_tokens * 1.25),
    })


def snapshot(*, account_dirs: list[Path], state_dir: Path,
             settings: Mapping[str, Any],
             processing_account_dirs: list[Path] | None = None,
             now: datetime | None = None) -> dict:
    """复用实际激活/来源/审校/付费前置与图片规划；只预览已落入本地的内容。"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    account_dirs = [Path(path) for path in account_dirs]
    selected = set(account_dirs if processing_account_dirs is None
                   else map(Path, processing_account_dirs))
    # engine.load_sources 内部 Archive 会 mkdir；预先要求 posts 已存在，不能借预览补目录。
    rows = {path: images.readonly_archive(path).rows() for path in account_dirs}
    active = set(engine.active_account_dirs(account_dirs))
    activated = engine.activation_time(state_dir)
    blockers = []
    if activated is None:
        blockers.append("流水线尚未激活；不能把历史归档当成本次自动处理范围")
    if settings["autonomy"] != "assisted":
        blockers.append("autonomy=%s 不执行自动文案翻译或出图" % settings["autonomy"])
    budget = {
        "daily_limit_usd": settings["daily_budget_usd"],
        "monthly_limit_usd": settings["monthly_budget_usd"],
        "daily_spent_usd": None, "monthly_spent_usd": None,
    }
    payment_blockers = []
    try:
        spent = engine.budget_snapshot(account_dirs, now=now, state_dir=state_dir)
        budget.update(daily_spent_usd=spent.daily_usd, monthly_spent_usd=spent.monthly_usd)
        if spent.unknown:
            raise engine.BudgetStopped("存在无法核算的费用：" + "；".join(spent.unknown))
        engine.assert_budget(account_dirs, settings, now=now, state_dir=state_dir)
        paid_requests._assert_startable(state_dir, "processing-preview")
    except (engine.BudgetStopped, paid_requests.PaidRequestBlocked) as exc:
        payment_blockers.append(str(exc))

    sources, boundary, out_of_scope = (engine.load_sources(active, activated)
                                      if activated else ([], [], []))
    candidates = { (c.canonical.account_dir, c.canonical.post_id): c
                   for c in engine.reconcile(sources).candidates }
    boundary_reasons = {ref: item.summary for item in boundary for ref in item.source_refs}
    boundary_reasons.update(dict(out_of_scope))
    rules = engine.publish_rules()
    scheduled_refs = journal.scheduled_source_refs(state_dir)
    text_settings, image_settings = translation.Settings(), images.Settings()
    posts = []
    for account_dir, account_rows in rows.items():
        for row in account_rows:
            pid, platform = row["post_id"], str(row.get("platform") or "").lower()
            ref = "%s:%s" % (platform, pid)
            item = {
                "account": account_dir.name, "post_id": pid, "platform": platform,
                "source_ref": ref, "created_at": row.get("created_at"),
                "status": "excluded", "reasons": [], "notes": [],
                "text_action": "不处理", "risk_scan_action": "不处理",
                "new_images_min": 0, "new_images_max": 0,
                "required_indices": [], "conditional_indices": [],
                "kept_current": 0, "kept_manual": 0, "bad_sources": 0,
                "text_reference_usd": 0.0,
            }
            posts.append(item)
            if account_dir not in selected:
                item["reasons"].append("不在 --account 指定范围")
                continue
            if account_dir not in active:
                item["reasons"].append("历史/冻结账号，不进入自动处理")
                continue
            created = engine._parse_aware(row.get("created_at"))
            if not created:
                item["reasons"].append("缺少带时区的源帖时间，不能跨过激活边界")
                continue
            if activated and created <= activated:
                item["reasons"].append("源帖在激活边界之前或恰在边界，不自动回补")
                continue
            if blockers:
                item.update(status="blocked", reasons=list(blockers))
                continue
            candidate = candidates.get((account_dir, pid))
            if candidate is None:
                item["reasons"].append(boundary_reasons.get(ref, "不在实际图文候选范围"))
                continue
            reason = _candidate_reason(candidate, rules, state_dir, scheduled_refs)
            if reason:
                item.update(status="blocked", reasons=[reason])
                continue
            source = candidate.canonical
            needs_text = engine.translation_needed(source)
            human = translated.load_human_translated(
                account_dir / "translated_human.jsonl").get(pid)
            item["text_action"] = ("英译德" if needs_text else "保留人工文案" if human
                                   else "保留当前机器文案")
            item.update(_image_plan(source, needs_text, image_settings))
            scan_needed = needs_text and risk_scan.result_for(
                account_dir, dict(source.row), state_dir=state_dir)["status"] not in {"completed", "failed"}
            item["risk_scan_action"] = "付费英文风险预扫" if scan_needed else "复用或无需预扫"
            item["text_reference_usd"] = (_text_reference(text_settings, source, account_rows)
                                          if needs_text else 0.0)
            item["status"] = "planned" if needs_text or item["new_images_max"] else "current"
            if needs_text:
                item["notes"].append("图片数以翻译成功为前提；翻译后再次按真实文案核对")
            if item["conditional_indices"]:
                item["notes"].append("已有图仅在新译文使其失效时重做，未按必然重做计数")
            if item["status"] == "planned" and payment_blockers:
                item.update(status="blocked", reasons=list(payment_blockers))

    posts.sort(key=lambda item: (str(item["created_at"] or ""), item["source_ref"]))
    planned = [item for item in posts if item["status"] == "planned"]
    min_images = sum(item["new_images_min"] for item in planned)
    max_images = sum(item["new_images_max"] for item in planned)
    unknown_text = [item["source_ref"] for item in planned if item["text_reference_usd"] is None]
    text_reference = (None if unknown_text else sum(item["text_reference_usd"] for item in planned))
    return {
        "mode": "local_processing_preview", "read_only": True,
        "activated_at": activated.isoformat() if activated else None,
        "autonomy": settings["autonomy"], "blockers": blockers + payment_blockers,
        "posts": posts, "budget": budget,
        "totals": {
            "planned_posts": len(planned),
            "translations": sum(item["text_action"] == "英译德" for item in planned),
            "risk_scans": sum(item["risk_scan_action"] == "付费英文风险预扫" for item in planned),
            "new_images_min": min_images, "new_images_max": max_images,
            "excluded_or_blocked": sum(item["status"] in {"excluded", "blocked"} for item in posts),
            "current_posts": sum(item["status"] == "current" for item in posts),
        },
        "cost": {
            "text_base_reference_usd": text_reference,
            "text_reference_unknown_refs": unknown_text,
            "image_historical_unit_usd": 0.211,
            "image_reference_min_usd": min_images * 0.211,
            "image_reference_max_usd": max_images * 0.211,
            "partial_reference_min_usd": None if text_reference is None else text_reference + min_images * 0.211,
            "partial_reference_max_usd": None if text_reference is None else text_reference + max_images * 0.211,
            "assumptions": [
                "文案按现配费率、字符粗估 token、缓存全未命中、德语可见输出为英文 token 的 1.25 倍；不含 reasoning",
                "英文风险预扫另走付费模型，费用未知，未计入参考小计",
                "图片按 2026-09-11 历史 US$0.211/张参考；不是当前报价，也不是上界",
                "参考小计不是完整账单或预算上限；实际逐次请求按 usage 核账，预算或失败可使批次提前停止",
                "只读当前本地归档；未访问浏览器，未包含下一次扫描才会发现的新帖，未验证凭据或外部服务",
            ],
        },
    }


def print_snapshot(value: dict) -> None:
    print("=== 本地内容处理 dry-run（零网络、零账本/派生写入） ===")
    print("激活边界：%s；autonomy=%s" % (value["activated_at"] or "未激活", value["autonomy"]))
    for item in value["posts"]:
        low, high = item["new_images_min"], item["new_images_max"]
        count = str(low) if low == high else "%d–%d" % (low, high)
        print("[%s] %s / %s / %s：文案=%s；预计新出图=%s 张；%s" % (
            item["status"], item["platform"], item["account"], item["source_ref"],
            item["text_action"], count, item["risk_scan_action"]))
        if item["required_indices"] or item["conditional_indices"]:
            print("    图片索引（从 0 起）：待生成=%s；取决于新译文=%s" % (
                item["required_indices"], item["conditional_indices"]))
        if item["kept_current"] or item["kept_manual"]:
            print("    保留当前程序图 %d 张 / 人工图 %d 张" % (
                item["kept_current"], item["kept_manual"]))
        for note in item["reasons"] + item["notes"]:
            print("    " + note)
    total, cost, budget = value["totals"], value["cost"], value["budget"]
    print("本次可处理 %d 篇；英译德 %d 篇；付费风险预扫 %d 篇；预计新出图 %d–%d 张；无需新增付费 %d 篇；排除/阻塞 %d 篇" % (
        total["planned_posts"], total["translations"], total["risk_scans"],
        total["new_images_min"], total["new_images_max"], total["current_posts"],
        total["excluded_or_blocked"]))
    if cost["text_reference_unknown_refs"]:
        print("文案参考费用及小计未知：翻译费率不完整；涉及 " + "、".join(cost["text_reference_unknown_refs"]))
        print("图片历史参考：US$%.4f–%.4f" % (
            cost["image_reference_min_usd"], cost["image_reference_max_usd"]))
    else:
        print("美元参考小计：US$%.4f–%.4f（文案基础 US$%.4f + 图片 US$%.4f–%.4f）" % (
            cost["partial_reference_min_usd"], cost["partial_reference_max_usd"],
            cost["text_base_reference_usd"], cost["image_reference_min_usd"], cost["image_reference_max_usd"]))
    print("预算上限：日 US$%.2f / 月 US$%.2f；已记费用：日 %s / 月 %s" % (
        budget["daily_limit_usd"], budget["monthly_limit_usd"],
        budget["daily_spent_usd"], budget["monthly_spent_usd"]))
    for message in value["blockers"] + value.get("diagnostics", []) + cost["assumptions"]:
        print("  - " + message)

"""G9 assisted 激活、跨平台对账、预算/待确认与批量停手验收。"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from activation_fixtures import activate as fixture_activate

from pipeline import engine as A
from core.config import cfg
from core.console import force_utf8

force_utf8()

fails = []
TEST_ACCOUNT_PAIRS = {("acme", "acme")}
TEST_RULES = replace(
    A.publish_rules(),
    trusted_owners={"facebook": frozenset({"acme"}),
                    "instagram": frozenset({"acme"})},
    source_accounts={"facebook": "acme", "instagram": "acme"})


def check(condition, message):
    print(("  OK   " if condition else "  FAIL ") + message)
    if not condition:
        fails.append(message)


def make_account(root: Path, name: str, rows: list[dict]) -> Path:
    account = root / name
    account.mkdir(parents=True)
    (account / "manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    for row in rows:
        for index, item in enumerate(row.get("media") or [], 1):
            path = account / Path(item["local_path"].replace("\\", "/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (96, 96), (index * 15, 40, 90)).save(path)
    return account


def row(post_id: str, platform: str, created: str, text: str,
        account: str, *, owner=None, coauthors=None, images=1) -> dict:
    stamp = created[:16].replace("-", "").replace(":", "").replace("T", "_")
    folder = "posts/%s_%s" % (stamp, post_id)
    return {
        "post_id": post_id, "platform": platform, "account": account,
        "text": text, "created_at": created, "owner": owner or account,
        "owner_name": owner or account,
        "coauthors": list(coauthors or []), "media_complete": True,
        "media": [{"kind": "image", "local_path": "%s/%02d.jpg" % (folder, i)}
                  for i in range(1, images + 1)],
    }


print("[1] activate 是一次性原子边界，激活前历史零候选")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    first = fixture_activate(A,
        state, g8_verified=True,
        now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    second = fixture_activate(A,
        state, g8_verified=True,
        now=datetime(2026, 9, 2, 12, tzinfo=timezone.utc))
    check(first == second and A.activation_time(state) == first,
          "重复 activate 不移动边界，避免历史重新进入候选")
    check((state / A.STATE_NAME).is_file(), "激活边界原子落 pipeline_state.json")
    try:
        fixture_activate(A, Path(folder) / "other", g8_verified=False)
    except A.PipelineRunError:
        blocked = True
    else:
        blocked = False
    check(blocked, "没有 --g8-verified 时不能激活")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    account = make_account(root, "fa_acme", [
        row("old", "facebook", "2026-08-31T11:00:00Z", "old", "acme"),
        row("new", "facebook", "2026-09-01T13:00:00Z", "new", "acme")])
    sources, _, _ = A.load_sources(
        [account], datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    check([source.post_id for source in sources] == ["new"],
          "激活前历史候选为零，只处理边界之后的新帖")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    boundary = datetime.fromisoformat("2026-09-01T12:00:00.123456+00:00")
    account = make_account(root, "fa_acme", [
        row("same", "facebook", boundary.isoformat(), "same", "acme"),
        row("after", "facebook", "2026-09-01T12:00:00.123457+00:00",
            "after", "acme")])
    sources, _, _ = A.load_sources([account], boundary)
    check([source.post_id for source in sources] == ["after"],
          "激活边界保留微秒并使用严格大于，不把同一时刻的历史帖纳入")


print("\n[2] FB/IG 独立处理；相似与相同内容都不互相覆盖")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    fb = make_account(root, "fa_acme", [row("fb1", "facebook", "2026-09-01T10:00:00Z", "Same caption", "acme")])
    ig = make_account(root, "in_acme", [row("ig1", "instagram", "2026-09-01T11:00:00Z", "Same caption", "acme", coauthors=["mystery.creator"])])
    sources, _, _ = A.load_sources([fb, ig], datetime(2026, 9, 1, tzinfo=timezone.utc))
    result = A.reconcile(sources, account_pairs=TEST_ACCOUNT_PAIRS)
    check(len(result.candidates) == 2 and not result.human_items,
          "旧账号配对参数不再产生合并或选版本人工项")
    independent = {candidate.canonical.platform: candidate for candidate in result.candidates}
    check(A._prepaid_issue(independent["facebook"], TEST_RULES) is None,
          "FB 自有内容不受 IG 合作作者影响")
    issue = A._prepaid_issue(independent["instagram"], TEST_RULES)
    check(issue is not None and issue.kind == "unknown_collaborator",
          "IG 独立保留合作作者审核闸")
    source = independent["instagram"].canonical
    source.row["media_complete"] = False
    check(A._prepaid_issue(independent["instagram"], TEST_RULES).kind == "material_gate",
          "独立帖子仍检查素材完整性")
    source.row.update(media_complete=True, coauthors=[], text="Sale $777")
    check(A._prepaid_issue(independent["instagram"], TEST_RULES).kind == "unmapped_price",
          "独立帖子仍检查未映射金额")


print("\n[4] needs_human 追加式、幂等，并派生 HTML")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    item = A.HumanItem("item-1", "similar_cross_platform",
                       ("facebook:f", "instagram:i"), "choose one")
    check(A.append_human_item(state, item), "首次未决项会追加")
    check(not A.append_human_item(state, item), "重复运行不产生重复未决项")
    check(len(A.needs_human_events(state)) == 1, "幂等后 ledger 仍只有一条 opened")
    check((state / A.NEEDS_HUMAN_HTML).is_file(), "每次变更派生 needs_human.html")
    A.resolve_human_item(state, "item-1", resolution="source_selected",
                         selected_ref="facebook:f")
    check(A.latest_human_items(state)["item-1"]["status"] == "resolved",
          "确认通过追加 resolved 事件结转，不重写 opened 历史")
    check(A.append_human_item(state, item),
          "同一问题结转后若真实条件再次出现，会重新打开而不是被旧 resolved 隐藏")
    check(A.latest_human_items(state)["item-1"]["status"] == "open",
          "重开后派生状态重新显示为待确认")
    ready = A.HumanItem("ready-1", "ready_to_publish",
                        ("facebook:f", "instagram:i"), "ready")
    gate = A.HumanItem("gate-1", "material_gate",
                       ("facebook:f", "instagram:i"), "gate")
    A.append_human_item(state, gate)
    A.append_human_item(state, ready)
    A.resolve_cleared_items(
        state, ("facebook:f", "instagram:i"),
        active_item_ids=(ready.item_id,))
    latest = A.latest_human_items(state)
    check(latest["gate-1"]["status"] == "resolved"
          and latest["ready-1"]["status"] == "open",
          "硬闸清除时结转旧项，但保留当前 active ready 项")

check(A.translation.apply_money_mapping(
          "Jetzt $ 10 statt $20", {"$10": "9,99 €", "$20": "19,99 €"})
      == "Jetzt 9,99 € statt 19,99 €",
      "价格映射沿用金额硬闸的空白归一化，并只替换完整 token")


print("\n[5] manual 只对账；assisted 不触碰发布浏览器；预算到线立即停")


class Runner:
    def __init__(self):
        self.calls = []

    def delta(self, *, if_stale):
        self.calls.append(("delta", if_stale))
        return 0

    def translate(self, source):
        self.calls.append(("translate", source.ref))
        return 0

    def image(self, source, media_index):
        self.calls.append(("image", source.ref, media_index))
        return 0


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    account = make_account(root / "archive", "fa_neakasaofficial", [
        row("n1", "facebook", "2026-09-01T13:00:00Z",
            "No price here", "neakasaofficial")])
    fixture_activate(A, state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    runner = Runner()
    code = A.run(
        account_dirs=[account], state_dir=state,
        settings={"autonomy": "manual", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 20, tzinfo=timezone.utc), runner=runner,
        report=lambda _message: None)
    check(code == 0 and not runner.calls,
          "manual 模式只从本地真相源对账，不调用 delta/翻译/调图")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    account = make_account(root / "archive", "fa_neakasaofficial", [
        row("n2", "facebook", "2026-09-01T13:00:00Z",
            "No price here", "neakasaofficial")])
    fixture_activate(A, state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    runner = Runner()
    code = A.run(
        account_dirs=[account], state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 0,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 20, tzinfo=timezone.utc), runner=runner,
        report=lambda _message: None)
    check(code == 4 and runner.calls == [("delta", False)],
          "付费请求前预算达到日上限立即停，连翻译请求都不发")
    kinds = {row.get("kind") for row in A.latest_human_items(state).values()}
    check("budget_stopped" in kinds, "预算停手进入追加式 needs_human")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    account = make_account(root / "archive", "fa_neakasaofficial", [
        row("n3", "facebook", "2026-09-01T13:00:00Z",
            "No price here", "neakasaofficial")])
    other = root / "archive" / "in_other"
    other.mkdir(parents=True)
    (other / "translated.jsonl").write_text(json.dumps({
        "post_id": "spent", "translated_at": "2026-09-01T13:30:00Z",
        "usage": {"input_tokens": 100000000, "output_tokens": 100000000,
                  "prompt_cache_hit_tokens": 0},
    }) + "\n", encoding="utf-8")
    fixture_activate(A, state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    runner = Runner()
    code = A.run(
        account_dirs=[account], budget_account_dirs=[account, other],
        state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 20, tzinfo=timezone.utc), runner=runner,
        report=lambda _message: None)
    check(code == 4 and runner.calls == [("delta", False)],
          "--account 只缩小处理范围；全局预算仍统计其它账号并在付费前停手")

with tempfile.TemporaryDirectory() as folder:
    broken = Path(folder) / "account"
    broken.mkdir()
    (broken / "translated.jsonl").write_text("{broken\n", encoding="utf-8")
    try:
        A.budget_snapshot(
            [broken], now=datetime(2026, 9, 1, 14, tzinfo=timezone.utc))
    except A.BudgetStopped:
        corrupt_blocked = True
    else:
        corrupt_blocked = False
    check(corrupt_blocked, "损坏费用 JSONL 失败闭合，不能静默漏算后继续花钱")

print("\n[5b] 新帖立即处理；冻结账号不消费；历史双渠道回执仍幂等")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    fb = make_account(root / "archive", "fa_neakasaofficial", [
        row("fresh-fb", "facebook", "2026-09-01T13:00:00Z", "Fresh caption", "neakasaofficial")])
    legacy = make_account(root / "archive", "in_neakasa.tech", [
        row("fresh-ig", "instagram", "2026-09-01T13:30:00Z", "Fresh caption", "neakasa.tech")])
    fixture_activate(A, state, g8_verified=True, now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    runner = Runner()
    A.run(account_dirs=[fb, legacy], state_dir=state,
          settings={"autonomy": "assisted", "daily_budget_usd": 5, "monthly_budget_usd": 60},
          now=datetime(2026, 9, 1, 14, tzinfo=timezone.utc), runner=runner,
          report=lambda _message: None)
    check(("translate", "facebook:fresh-fb") in runner.calls,
          "发布仅一小时的来源进入翻译，不再等待 30 小时")
    check(not any("fresh-ig" in str(call) for call in runner.calls),
          "冻结 .tech 不再自动翻译或生成图片")
    A.journal.append(state, A.journal.PublishAttempt(
        post_id="fresh-fb", platform="facebook", status=A.journal.STATUS_SCHEDULED,
        scheduled_at="2026-09-02T10:00:00+02:00", recorded_at="2026-09-01T14:00:00+00:00",
        text_de_sha256="abc", source_refs=("facebook:fresh-fb", "instagram:fresh-ig")))
    again = Runner()
    A.run(account_dirs=[fb, legacy], state_dir=state,
          settings={"autonomy": "assisted", "daily_budget_usd": 5, "monthly_budget_usd": 60},
          now=datetime(2026, 9, 1, 15, tzinfo=timezone.utc), runner=again,
          report=lambda _message: None)
    check(again.calls == [("delta", False)], "历史覆盖两来源的 scheduled 回执仍防止重复处理")


print("\n[6] 北京槽位跨美西 DST 窗口仍正确")
rules = A.publish_rules()
slot_hours = {slot.hour for slot in rules.slots}
# 北京不切夏令时，槽位小时固定；会动的是它在美西 UI 里显示成几点。
for when, want_hour in [
        (datetime.fromisoformat("2026-10-28T00:00:00-07:00"), 1),   # 美西仍是 PDT
        (datetime.fromisoformat("2026-11-03T00:00:00-08:00"), 7),   # 美西已回到 PST
        (datetime.fromisoformat("2027-03-20T00:00:00-07:00"), 1)]:
    slot = A.next_slots(when, (), 1, rules)[0]
    shown = slot.astimezone(A.ZoneInfo("America/Los_Angeles"))
    check(slot.hour in slot_hours and shown.hour == want_hour,
          "北京槽小时来自配置；%s 的美西 UI 正确显示 %02d:00"
          % (slot.date(), want_hour))
# now 与目标时刻须在同一 UI 月份，避免月界检查遮蔽 DST 测试。
fallback = A.next_slots(
    datetime.fromisoformat("2026-11-01T00:30:00-07:00"), (), 1, rules)[0]
check(fallback.date().isoformat() == "2026-11-01" and fallback.hour == 23,
      "美西回拨日 16:00 槽（=美西 01:00，当天出现两次）无法无歧义表达时，改用 23:00 安全槽")

# 夹具限制为可见月份，跨月必须明确失败。
month_end = A.next_slots(
    datetime.fromisoformat("2026-09-30T20:00:00-07:00"), (), 3, rules)
check(month_end == (),
      "UI 月末之后没有可排的槽：返回空而不是无上界地翻到下个月")
plenty = A.next_slots(
    datetime.fromisoformat("2026-09-02T08:00:00-07:00"), (), 3, rules)
check(len(plenty) == 3 and all(
          item.astimezone(A.ZoneInfo("America/Los_Angeles")).month == 9
          for item in plenty),
      "月中照常给满 count 个槽，且每个在 UI 时区里都还在本月")
crossing = A.next_slots(
    datetime.fromisoformat("2026-09-29T08:00:00-07:00"), (), 20, rules)
check(0 < len(crossing) < 20 and all(
          item.astimezone(A.ZoneInfo("America/Los_Angeles")).month == 9
          for item in crossing),
      "月底槽位不够时返回**少于** count 个，由调用方决定怎么办（整批不提交）")


print("\n[7] 批量首个不明确失败立即停止，后续零调用")
seen = []
resolved = []


def submitter(item):
    seen.append(item)
    return 5 if item == "second" else 0


code, completed = A.submit_batch(
    ["first", "second", "third"], submitter, resolved.append)
check(code == 5 and completed == 1 and seen == ["first", "second"]
      and resolved == ["first"],
      "第二篇不明确时整批停止，第三篇完全未触碰")
with tempfile.TemporaryDirectory() as folder:
    try:
        A.approve(item_ids=["same", "same"], selections={},
                  state_dir=Path(folder))
    except A.PipelineRunError as exc:
        duplicate_blocked = "不能重复" in str(exc)
    else:
        duplicate_blocked = False
check(duplicate_blocked, "同一 item_id 不能在一个 approve 批次里出现两次")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    try:
        with A.PipelineOperationLock(state / "pipeline.lock"):
            with A.PipelineOperationLock(state / "pipeline.lock"):
                pass
    except A.PipelineRunError:
        concurrent_blocked = True
    else:
        concurrent_blocked = False
    check(concurrent_blocked,
          "daily/catch-up/run/approve 共用跨进程锁，并发批次不能同时越过预算/幂等")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    fixture_activate(A, state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    ready = A.HumanItem(
        "ready-stale", "ready_to_publish", ("facebook:missing",), "ready",
        {"canonical_ref": "facebook:missing", "post_id": "missing",
         "account_dir": "fa_missing", "publish_fingerprint": "x"})
    A.append_human_item(state, ready)
    try:
        A.approve(item_ids=[ready.item_id], selections={}, state_dir=state,
                  assume_yes=True)
    except A.PipelineRunError as exc:
        stale_blocked = "canonical/source_refs" in str(exc)
    else:
        stale_blocked = False
    check(stale_blocked,
          "approve 在浏览器前从最新归档重新 reconcile，过期 ready 不能当任务队列使用")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    fixture_activate(A, state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    for item in (
            A.HumanItem("ready-a", "ready_to_publish",
                        ("facebook:f", "instagram:i"), "ready", {}),
            A.HumanItem("ready-b", "ready_to_publish",
                        ("instagram:i",), "ready", {})):
        A.append_human_item(state, item)
    try:
        A.approve(item_ids=["ready-a", "ready-b"], selections={},
                  state_dir=state, assume_yes=True)
    except A.PipelineRunError as exc:
        overlap_blocked = "重叠 source_refs" in str(exc)
    else:
        overlap_blocked = False
    check(overlap_blocked, "同批 ready source_refs 重叠时整批在浏览器前停止")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    fixture_activate(A, state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    item = A.HumanItem(
        "ready-scheduled", "ready_to_publish", ("facebook:f",), "ready", {})
    A.append_human_item(state, item)
    A.journal.append(state, A.journal.PublishAttempt(
        post_id="f", platform="facebook", status=A.journal.STATUS_SCHEDULED,
        scheduled_at="2026-09-02T10:00:00+02:00",
        recorded_at="2026-09-01T13:00:00+00:00",
        text_de_sha256="abc", source_refs=("facebook:f",)))
    try:
        A.approve(item_ids=[item.item_id], selections={}, state_dir=state,
                  assume_yes=True)
    except A.PipelineRunError as exc:
        scheduled_blocked = "scheduled" in str(exc)
    else:
        scheduled_blocked = False
    check(scheduled_blocked,
          "approve 每次重查所有 source_refs 的 scheduled 交集，已排来源不会重复提交")


print("\n[9] 非图文帖不进人工队列；激活闸是机检不是自觉")
check(A.out_of_scope_reason(
    {"text": "x", "media": [{"kind": "image"}]}) is None,
    "纯图文帖在范围内")
check("video" in (A.out_of_scope_reason(
    {"text": "x", "media": [{"kind": "video"}]}) or ""),
    "视频帖判为不在范围（它永远不会变得可发）")
check("video" in (A.out_of_scope_reason(
    {"text": "x", "media": [{"kind": "image"}, {"kind": "video"}]}) or ""),
    "图片+视频混合帖同样出局：只发其中的图会丢内容")
check(A.out_of_scope_reason({"text": "  ", "media": [{"kind": "image"}]})
      == "无正文", "无正文出局")
check(A.out_of_scope_reason({"text": "x", "media": []}) == "无媒体",
      "无媒体出局")
check(A.out_of_scope_reason(
    {"text": "x", "media": [{"kind": "image"}], "media_complete": False})
    is None,
    "素材不齐**不算**出局：那是人能处理的 material_gate，必须继续进队列")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    picture = row("pic", "facebook", "2026-09-01T14:00:00Z", "photo", "acme")
    video_row = row("vid", "facebook", "2026-09-01T13:00:00Z", "clip", "acme")
    account = make_account(root, "fa_acme", [picture, video_row])
    # 视频在归档里只有 manifest 记录（从不下载），所以改的是 kind，不是文件。
    video_row["media"] = [{"kind": "video", "url": "https://x.invalid/v"}]
    (account / "manifest.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in (picture, video_row)),
        encoding="utf-8")
    sources, _issues, skipped = A.load_sources(
        [account], datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    check([source.post_id for source in sources] == ["pic"],
          "load_sources 只放图文帖过去")
    check(skipped and skipped[0][0] == "facebook:vid"
          and "video" in skipped[0][1],
          "跳过的帖子必须带 ref 和原因返回 —— 跳过不等于静默丢弃")
    result = A.reconcile(sources, account_pairs=TEST_ACCOUNT_PAIRS)
    check(len(result.candidates) == 1
          and result.candidates[0].canonical.post_id == "pic",
          "视频帖不产生候选，因此也不会每天在 needs_human 里堆一条处理不掉的项")

with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    # 创建配置指向的 state，避免目录缺失短路实际能力检查。
    cfg().state_dir.mkdir(parents=True, exist_ok=True)
    verified = cfg().get("publish", "ui_constraints_verified", False) is True
    blockers = A.activation_blockers(state)
    check(any("单渠道真实验收记录" in item for item in blockers),
          "没有任何 scheduled 记录时，激活闸拦住（G8 从未真机通过）")
    check(verified != any("ui_constraints_verified" in item
                          for item in blockers),
          "ui_constraints_verified 与激活闸的判断一致，不会两边各说各话")
    A.journal.append(state, A.journal.PublishAttempt(
        post_id="g8", platform="facebook", status=A.journal.STATUS_SCHEDULED,
        scheduled_at="2026-09-10T10:00:00+02:00",
        recorded_at="2026-09-01T13:00:00+00:00",
        text_de_sha256="abc", source_refs=("facebook:g8",)))
    after = A.activation_blockers(state)
    check(any("单渠道真实验收记录" in item for item in after),
          "历史双渠道 scheduled 仅保留防重，不替代单渠道验收")
    prepared_only = Path(folder + "-prepared")
    prepared_only.mkdir()
    A.journal.append(prepared_only, A.journal.PublishAttempt(
        post_id="g8", platform="facebook", status=A.journal.STATUS_PREPARED,
        scheduled_at="2026-09-10T10:00:00+02:00",
        recorded_at="2026-09-01T13:00:00+00:00",
        text_de_sha256="abc", source_refs=("facebook:g8",)))
    check(any("单渠道真实验收记录" in item
              for item in A.activation_blockers(prepared_only)),
          "只有 prepared 不算 G8 通过 —— prepared 可能只留下一个草稿")


print("\n[10] needs_human.html 给的是可复制的命令，不是让人手抄哈希 ID")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    A.append_human_item(state, A.HumanItem(
        "ready-a", "ready_to_publish", ("facebook:a",), "ready", {}))
    A.append_human_item(state, A.HumanItem(
        "ready-b", "ready_to_publish", ("facebook:b",), "ready", {}))
    A.append_human_item(state, A.HumanItem(
        "sim-1", "similar_cross_platform",
        ("facebook:c", "instagram:d"), "两个版本有差异", {}))
    A.append_human_item(state, A.HumanItem(
        "price-1", "unmapped_price", ("instagram:e",), "金额未映射",
        {"amounts": ["$219.99"]}))
    page = A.build_human_html(state).read_text(encoding="utf-8")
    check("--item-id ready-a --item-id ready-b" in page,
          "多个 ready 合成一条命令：稳态里人每天做的就是这一下")
    check("--select-source sim-1=facebook:c" in page
          and "instagram:d" in page,
          "选版本项给出填好的 --select-source，并把另一个 ref 也列出来")
    check("不能" in page and "price_map" in page,
          "硬闸类明说不能 approve，并写清怎么才能消掉它")
    check("ready-a" in page and "price-1" in page,
          "明细表仍然完整，命令区只是入口不是替代")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

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

import pipeline_assisted as A
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
    first = A.activate(
        state, g8_verified=True,
        now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    second = A.activate(
        state, g8_verified=True,
        now=datetime(2026, 9, 2, 12, tzinfo=timezone.utc))
    check(first == second and A.activation_time(state) == first,
          "重复 activate 不移动边界，避免历史重新进入候选")
    check((state / A.STATE_NAME).is_file(), "激活边界原子落 pipeline_state.json")
    try:
        A.activate(Path(folder) / "other", g8_verified=False)
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
    sources, _ = A.load_sources(
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
    sources, _ = A.load_sources([account], boundary)
    check([source.post_id for source in sources] == ["after"],
          "激活边界保留微秒并使用严格大于，不把同一时刻的历史帖纳入")


print("\n[2] 跨平台完全重复自动合并；相似/素材一致版本进入待确认")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    fb_row = row("fb1", "facebook", "2026-09-01T10:00:00Z",
                 "Same caption", "acme")
    ig_row = row("ig1", "instagram", "2026-09-01T11:00:00Z",
                 "Same caption", "acme")
    fb = make_account(root, "fa_acme", [fb_row])
    ig = make_account(root, "in_acme", [ig_row])
    # 两边源图设成逐像素相同。
    source_image = next((fb / "posts").rglob("01.jpg"))
    target_image = next((ig / "posts").rglob("01.jpg"))
    target_image.write_bytes(source_image.read_bytes())
    sources, _ = A.load_sources(
        [fb, ig], datetime(2026, 9, 1, tzinfo=timezone.utc))
    result = A.reconcile(sources, account_pairs=TEST_ACCOUNT_PAIRS)
    check(len(result.candidates) == 1
          and result.candidates[0].canonical.platform == "facebook"
          and set(result.candidates[0].source_refs) == {"facebook:fb1", "instagram:ig1"},
          "30h 内归一化正文相同、图片数相同、dHash≤1 时自动合并并优先 FB")
    check(not result.human_items, "完全重复不制造待确认项")

    # canonical 是 FB 自有帖；secondary IG 的未知作者仍必须拦住。
    ig_row["owner"] = "mystery.creator"
    ig_row["coauthors"] = ["acme"]
    (ig / "manifest.jsonl").write_text(json.dumps(ig_row) + "\n", encoding="utf-8")
    sources, _ = A.load_sources(
        [fb, ig], datetime(2026, 9, 1, tzinfo=timezone.utc))
    merged = A.reconcile(
        sources, account_pairs=TEST_ACCOUNT_PAIRS).candidates[0]
    issue = A._prepaid_issue(merged, TEST_RULES)
    check(issue is not None and issue.kind == "unknown_collaborator"
          and issue.details.get("source_ref") == "instagram:ig1",
          "自动合并候选会审全部 source owner，secondary 未知作者不能躲在 FB canonical 后")

    # material/amount 也必须审 secondary；它最终与 canonical 共用 journal。
    secondary = merged.sources[1]
    secondary.row["media_complete"] = False
    issue = A._prepaid_issue(merged, TEST_RULES)
    check(issue is not None and issue.kind == "material_gate"
          and issue.details.get("source_ref") == "instagram:ig1",
          "secondary 轮播不完整时 exact merge 也在付费前失败闭合")
    secondary.row["media_complete"] = True
    secondary.row["owner"] = secondary.account
    secondary.row["coauthors"] = []
    secondary.row["text"] = "Same caption with $777"
    issue = A._prepaid_issue(merged, TEST_RULES)
    check(issue is not None and issue.kind == "unmapped_price"
          and issue.details.get("source_ref") == "instagram:ig1",
          "secondary 未映射金额不能躲在 FB canonical 后进入付费阶段")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    own = row("co1", "instagram", "2026-09-01T11:00:00Z",
              "Caption", "neakasa.tech", coauthors=["mystery.creator"])
    account = make_account(root, "in_neakasa.tech", [own])
    source = A.load_sources(
        [account], datetime(2026, 9, 1, tzinfo=timezone.utc))[0][0]
    issue = A._prepaid_issue(
        A.Candidate(source, (source,), "independent"), A.publish_rules())
    check(issue is not None and issue.kind == "unknown_collaborator",
          "owner 是自有账号也会审外部 coauthors，未知合作方进入人工项")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    common = "A long campaign caption with identical product details and hashtags " * 4
    fb_row = row("fb2", "facebook", "2026-09-01T10:00:00Z",
                 common + "Buy at example.com", "acme")
    ig_row = row("ig2", "instagram", "2026-09-01T11:00:00Z",
                 common + "Use the link in bio", "acme")
    fb = make_account(root, "fa_acme", [fb_row])
    ig = make_account(root, "in_acme", [ig_row])
    next((ig / "posts").rglob("01.jpg")).write_bytes(
        next((fb / "posts").rglob("01.jpg")).read_bytes())
    sources, _ = A.load_sources(
        [fb, ig], datetime(2026, 9, 1, tzinfo=timezone.utc))
    result = A.reconcile(sources, account_pairs=TEST_ACCOUNT_PAIRS)
    item = result.human_items[0]
    check(item.kind == "similar_cross_platform" and not result.candidates,
          "正文≥0.90 或素材高度一致但版本不同，付费前进入待确认")
    selected = A.reconcile(
        sources, selected={item.item_id: "instagram:ig2"},
        account_pairs=TEST_ACCOUNT_PAIRS)
    check(selected.candidates[0].canonical.ref == "instagram:ig2"
          and set(selected.candidates[0].source_refs) == {
              "facebook:fb2", "instagram:ig2"},
          "人工选定版本后才生成 canonical candidate，并保留两个 source_refs")

    unpaired = A.reconcile(sources, account_pairs={("another", "brand")})
    check(len(unpaired.candidates) == 2 and not unpaired.human_items,
          "未在显式账号映射中的 FB/IG 即使同文同图也不跨品牌合并")


print("\n[3] 真实 8 月 27 日 FB/IG 配对：CTA 不同，必须待确认")
archive = cfg().archive_dir
real_dirs = [archive / "fa_neakasaofficial", archive / "in_neakasa.tech"]
if all(path.is_dir() for path in real_dirs):
    sources, _ = A.load_sources(
        real_dirs, datetime(2026, 8, 27, tzinfo=timezone.utc))
    real = A.reconcile(sources)
    matching = [item for item in real.human_items
                if "facebook:122123185335379375" in item.source_refs]
    check(len(matching) == 1, "真实 FB/IG 配对没有被自动挑选")
    if matching:
        details = matching[0].details
        check(details["similarity"] >= .90
              and details["dhash_distances"] == [0, 0, 0, 0, 0],
              "真实配对正文高度相似、五张图 dHash 均为 0，但 CTA 差异使其待确认")
else:
    check(True, "CI 无真实 archive 时跳过 8 月 27 日标定（本机存在则强制验证）")


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
    A.activate(state, g8_verified=True,
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
    A.activate(state, g8_verified=True,
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
    A.activate(state, g8_verified=True,
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

print("\n[5b] 30h 配对窗口按跨次真相源成熟，--account 不缩小候选发现")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    fb = make_account(root / "archive", "fa_neakasaofficial", [
        row("fresh-fb", "facebook", "2026-09-01T13:00:00Z",
            "Same fresh caption", "neakasaofficial")])
    ig = make_account(root / "archive", "in_neakasa.tech", [
        row("fresh-ig", "instagram", "2026-09-01T13:30:00Z",
            "Same fresh caption", "neakasa.tech")])
    next((ig / "posts").rglob("01.jpg")).write_bytes(
        next((fb / "posts").rglob("01.jpg")).read_bytes())
    A.activate(state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    fresh_runner = Runner()
    fresh_code = A.run(
        account_dirs=[fb, ig], processing_account_dirs=[fb], state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 1, 14, tzinfo=timezone.utc),
        runner=fresh_runner, report=lambda _message: None)
    check(fresh_code == 0 and fresh_runner.calls == [("delta", False)],
          "fresh exact pair 任一来源未满 30h 时仍零翻译/调图/ready")

    mature_runner = Runner()
    A.run(
        account_dirs=[fb, ig], processing_account_dirs=[fb], state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 20, tzinfo=timezone.utc),
        runner=mature_runner, report=lambda _message: None)
    check(("translate", "facebook:fresh-fb") in mature_runner.calls
          and not any(call[0] == "translate" and "fresh-ig" in str(call)
                      for call in mature_runner.calls),
          "成熟后 --account 只限制 canonical 执行；发现仍读取两端并合并一次")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    other = make_account(root / "archive", "fa_other", [
        row("other", "facebook", "2026-09-01T13:00:00Z",
            "Other brand caption", "other")])
    A.activate(state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    other_runner = Runner()
    A.run(
        account_dirs=[other], state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 20, tzinfo=timezone.utc),
        runner=other_runner, report=lambda _message: None)
    open_kinds = {row.get("kind") for row in A.latest_human_items(state).values()
                  if row.get("status") == "open"}
    check(other_runner.calls == [("delta", False)] and "unknown_owner" in open_kinds,
          "非 [targets] 来源账号即使自有且成熟也零付费/零 ready，进入人工项")

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root / "state"
    fb_row = row("late-fb", "facebook", "2026-09-01T13:00:00Z",
                 "Late exact caption", "neakasaofficial")
    ig_row = row("late-ig", "instagram", "2026-09-01T13:30:00Z",
                 "Late exact caption", "neakasa.tech")
    fb = make_account(root / "archive", "fa_neakasaofficial", [fb_row])
    ig = make_account(root / "archive", "in_neakasa.tech", [ig_row])
    next((ig / "posts").rglob("01.jpg")).write_bytes(
        next((fb / "posts").rglob("01.jpg")).read_bytes())
    A.activate(state, g8_verified=True,
               now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    A.journal.append(state, A.journal.PublishAttempt(
        post_id="late-fb", platform="facebook",
        status=A.journal.STATUS_SCHEDULED,
        scheduled_at="2026-09-02T10:00:00+02:00",
        recorded_at="2026-09-01T14:00:00+00:00",
        text_de_sha256="abc", source_refs=("facebook:late-fb",)))
    late_runner = Runner()
    A.run(
        account_dirs=[fb, ig], state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 20, tzinfo=timezone.utc),
        runner=late_runner, report=lambda _message: None)
    late_rows = [row for row in A.latest_human_items(state).values()
                 if row.get("kind") == "late_scheduled_overlap"
                 and row.get("status") == "open"]
    check(late_runner.calls == [("delta", False)] and len(late_rows) == 1,
          "成熟 FB 已 scheduled 后迟到 exact IG 写耐久人工闸，零付费/零发布")

    # 即使下一次 greedy/真相源让 IG 暂时变成独立候选，旧人工闸按 ref 交集继续挡。
    (fb / "manifest.jsonl").write_text("", encoding="utf-8")
    independent_runner = Runner()
    A.run(
        account_dirs=[fb, ig], state_dir=state,
        settings={"autonomy": "assisted", "daily_budget_usd": 5,
                  "monthly_budget_usd": 60},
        now=datetime(2026, 9, 3, 21, tzinfo=timezone.utc),
        runner=independent_runner, report=lambda _message: None)
    check(independent_runner.calls == [("delta", False)],
          "迟到覆盖项未结转时，IG 后续换 pair/独立仍持续阻塞")

    # approve 是显式人工结转：扩展旧 scheduled 的 source_refs，但不碰浏览器。
    (fb / "manifest.jsonl").write_text(json.dumps(fb_row) + "\n", encoding="utf-8")
    original_account_dirs = A.translation.account_dirs
    A.translation.account_dirs = lambda *_args, **_kwargs: [fb, ig]
    try:
        code = A.approve(
            item_ids=[str(late_rows[0]["item_id"])], selections={},
            state_dir=state, assume_yes=True,
            now=datetime(2026, 9, 3, 22, tzinfo=timezone.utc))
    finally:
        A.translation.account_dirs = original_account_dirs
    check(code == 0 and A.journal.scheduled_source_refs(state) >= {
              "facebook:late-fb", "instagram:late-ig"}
          and A.latest_human_items(state)[late_rows[0]["item_id"]].get(
              "status") == "resolved",
          "人工 approve 把迟到 exact ref 标为既有 scheduled 覆盖并明确留 manual_evidence")


print("\n[6] 德国 10:00/17:00 槽位跨两地 DST 窗口仍正确")
rules = A.publish_rules()
for when, want_hour in [
        (datetime.fromisoformat("2026-10-28T00:00:00-07:00"), 2),
        (datetime.fromisoformat("2026-11-03T00:00:00-08:00"), 1),
        (datetime.fromisoformat("2027-03-20T00:00:00-07:00"), 2)]:
    slot = A.next_slots(when, (), 1, rules)[0]
    shown = slot.astimezone(A.ZoneInfo("America/Los_Angeles"))
    check(slot.hour == 10 and shown.hour == want_hour,
          "德国槽始终 10:00；%s 的美西 UI 正确显示 %02d:00"
          % (slot.date(), want_hour))
fallback = A.next_slots(
    datetime.fromisoformat("2026-11-01T00:00:00+01:00"), (), 1, rules)[0]
check(fallback.date().isoformat() == "2026-11-01" and fallback.hour == 17,
      "美西回拨日 10:00 槽无法在 UI 无歧义表达时，自动改用当天 17:00 安全槽")


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
    A.activate(state, g8_verified=True,
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
    A.activate(state, g8_verified=True,
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
    A.activate(state, g8_verified=True,
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


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

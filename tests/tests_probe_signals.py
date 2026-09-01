"""tools/probe_signals.py 的离线验收：dump → 推导 → 回查 → 落盘 → 闸打开。

这一套的目的不是"覆盖分支"，而是**证明那条缝真的接上了**：
拿一份形状与真实录制一致的 v2 dump，跑完整条
`--check` → `--emit` → `selectors` 装载 → `business_suite.require_*` 三道闸全开，
中间没有任何一处需要人去手写 dataclass。

⚠️ **夹具照抄 2026-09-01 的真实 dump**（`docs/PROBE_FINDINGS_20260901.md`）：
composer 上只有 FB 主页名、没有 IG 帐号名；Planner 上只有一条同时带正文与
时刻的 `link`，渠道要点开**每个渠道各一个**的详情弹窗才读得到。
上一版夹具是照着想象中的"富卡片"写的，被真实数据整段推翻。

⚠️ 同样重要的是反向那一半：**残缺的 dump 必须推不出来**。
第 [4] 节逐条把好 dump 弄坏（少一个渠道弹窗、没有可信点击、成功提示提交前就在、
final 截图没了、顺序被打乱），确认每一种都被拦下——
否则这个工具就成了一个"看起来验过"的假绿灯，比没有更糟。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8
from publish import evidence
from publish.selectors import EvidenceSignal, Locator
from tools import probe_signals as ps

force_utf8()

fails = []


def check(condition, message):
    print(("  OK   " if condition else "  FAIL ") + message)
    if not condition:
        fails.append(message)


TARGET_FB = "Neakasa Deutschland"
TARGET_IG = "neakasa.de"
CAPTION = "Sueszes echtes Feedback von unseren Kickstarter-Backern"
MOMENT = "September 15, 2026, 10:00 AM"
FB_ID = "1887083152480681"
IG_ID = "4378984725697354"

COMPOSER_URL = "https://business.facebook.com/latest/composer/"
PLANNER_URL = "https://business.facebook.com/latest/content_calendar/"


def account_items(*, facebook=TARGET_FB) -> list[dict]:
    """composer 的 Facebook 预览。**IG 帐号名在真实 composer 上不存在。**"""
    preview = "%s Just now · %s Like Comment Share" % (facebook, CAPTION)
    return [
        {"tag": "img", "role": "img", "accessible_name": "Facebook",
         "visible_text": ""},
        {"tag": "img", "role": "img", "accessible_name": "Instagram",
         "visible_text": ""},
        {"tag": "div", "role": "article", "accessible_name": preview,
         "visible_text": preview},
        {"container_role": "article", "container_accessible_name": preview,
         "tag": "h2", "role": "heading", "accessible_name": facebook,
         "visible_text": facebook},
    ]


def dialog_text(channel, *, remote_id, account, caption=CAPTION) -> str:
    """详情弹窗那一整串可访问名 —— 账号与正文只存在于这里，没有独立子元素。"""
    surface = ("Facebook's Feed" if channel == "facebook"
               else "your Instagram feed")
    head = "Post details ID: %s Close " % remote_id if remote_id else \
        "Post details Close "
    return ("%sPost overview This view of your post may not represent "
            "exactly how it appears on %s. Actions %s %s Boost Publish now"
            % (head, surface, account, caption))


def card_items(*, instagram=True, facebook=True, entry=True, month=True,
               caption=CAPTION, moment=MOMENT, ig_account=TARGET_IG,
               fb_id=FB_ID, ig_id=IG_ID) -> list[dict]:
    """内容日历：月份/年份 heading + 条目 link + 每渠道一个详情弹窗。"""
    rows: list[dict] = []
    if month:
        rows += [
            {"tag": "div", "role": "heading", "accessible_name": "September",
             "visible_text": "September"},
            {"tag": "span", "role": "heading", "accessible_name": "2026",
             "visible_text": "2026"},
        ]
    rows.append({"tag": "div", "role": "link", "accessible_name": "10:00 AM",
                 "visible_text": "10:00 AM"})
    if entry:
        text = "%s %s" % (caption, moment)
        rows.append({"tag": "div", "role": "link", "accessible_name": text,
                     "visible_text": text})
    if facebook:
        rows.append({"tag": "div", "role": "dialog", "visible_text": "",
                     "accessible_name": dialog_text(
                         "facebook", remote_id=fb_id, account=TARGET_FB,
                         caption=caption)})
    if instagram:
        rows.append({"tag": "div", "role": "dialog", "visible_text": "",
                     "accessible_name": dialog_text(
                         "instagram", remote_id=ig_id, account=ig_account,
                         caption=caption)})
    return rows


def success_items() -> list[dict]:
    blob = ("Your post is scheduled Close Reach a wider audience by boosting "
            "See your potential advertising results")
    return [
        {"tag": "div", "role": "dialog", "accessible_name": blob,
         "visible_text": blob},
        {"container_role": "dialog", "container_accessible_name": blob,
         "tag": "div", "role": "heading",
         "accessible_name": "Your post is scheduled",
         "visible_text": "Your post is scheduled"},
    ]


def build_dump(root: Path, *, name="publish_probe_fixture.json",
               account=None, success=None, card=None,
               final_ok=True, click=True) -> Path:
    """一份**形状与真实录制一致**的完整 v2 dump。

    因果顺序刻意排成 账号(1) → 点击(2) → 成功(3) → Planner 就绪+条目+弹窗(4)
    → final(5)，这正是 verify_publish_chain 要求的那条链。
    """
    shots = root / (Path(name).stem + "_screenshots")
    shots.mkdir(parents=True, exist_ok=True)
    final_shot = shots / "final.png"
    final_shot.write_bytes(b"masked-png")

    interactions = []
    if click:
        interactions.append({
            "sequence": 1, "page_id": "page-001", "evidence_order": 2,
            "recorded_at": "2026-09-01T12:00:05+00:00",
            "session_id": "fixture", "event_type": "click", "is_trusted": True,
            "page_url": COMPOSER_URL,
            "target": {"tag": "div", "role": "", "accessible_name": "Schedule",
                       "visible_text": "Schedule"},
            # 第 0 条 role 为空是真实形状；带 role 的那条必须被优先选中。
            "candidates": [
                {"tag": "div", "role": "", "accessible_name": "Schedule",
                 "visible_text": "Schedule"},
                {"tag": "div", "role": "button", "explicit_role": "button",
                 "accessible_name": "Schedule", "visible_text": "Schedule"},
            ],
        })
    snapshots = [
        {"sequence": 1, "page_id": "page-001", "evidence_order": 1,
         "recorded_at": "2026-09-01T12:00:01+00:00", "reason": "periodic",
         "page_url": COMPOSER_URL,
         "semantic_items": account_items() if account is None else account},
        {"sequence": 2, "page_id": "page-001", "evidence_order": 3,
         "recorded_at": "2026-09-01T12:00:09+00:00", "reason": "periodic",
         "page_url": COMPOSER_URL,
         "semantic_items": success_items() if success is None else success},
        # ⚠️ 三段 Planner 证据**故意分在三张快照里** —— 真实录制就是这样：
        # 日历、FB 弹窗、IG 弹窗要点三次才看得到，不可能同框。
        # 这也是 verify_signal 对排期证据按并集验的原因。
        {"sequence": 3, "page_id": "page-001", "evidence_order": 4,
         "recorded_at": "2026-09-01T12:00:20+00:00", "reason": "periodic",
         "page_url": PLANNER_URL,
         "semantic_items": (card_items(facebook=False, instagram=False)
                            if card is None else card)},
        {"sequence": 4, "page_id": "page-001", "evidence_order": 5,
         "recorded_at": "2026-09-01T12:00:25+00:00", "reason": "periodic",
         "page_url": PLANNER_URL,
         "semantic_items": (card_items(instagram=False, entry=False)
                            if card is None else card)},
        {"sequence": 5, "page_id": "page-001", "evidence_order": 6,
         "recorded_at": "2026-09-01T12:00:28+00:00", "reason": "periodic",
         "page_url": PLANNER_URL,
         "semantic_items": (card_items(facebook=False, entry=False)
                            if card is None else card)},
        {"sequence": 6, "page_id": "page-001", "evidence_order": 7,
         "recorded_at": "2026-09-01T12:00:30+00:00", "reason": "final",
         "page_url": PLANNER_URL,
         "semantic_items": (card_items(facebook=False, instagram=False)
                            if card is None else card),
         "screenshot": str(final_shot) if final_ok else "",
         "screenshot_error": None if final_ok else "截图失败"},
    ]
    if not click:                                  # evidence_order 仍要连续
        for index, row in enumerate(snapshots, start=1):
            row["evidence_order"] = index
    dump = {
        "schema_version": 2, "mode": "record-and-passive-evidence",
        "session_id": "fixture-session",
        "started_at": "2026-09-01T11:59:00+00:00",
        "finished_at": "2026-09-01T12:01:00+00:00",
        "interactions": interactions, "snapshots": snapshots,
    }
    path = root / name
    path.write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")
    return path


def derive(root: Path, path: Path, **kwargs):
    data, detail = evidence.validate_v2_dump(path.name, root)
    assert data is not None, detail
    kwargs.setdefault("caption_hint", CAPTION)
    return ps.derive_all(data, path.name, facebook=TARGET_FB,
                         instagram=TARGET_IG, **kwargs)


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    good = build_dump(root)

    print("[1] 完整 dump：五件证据全部推得出来")
    results = derive(root, good)
    by_key = {item.key: item for item in results}
    check(set(by_key) == set(ps.REQUIRED),
          "五件必需证据都有推导结论（%d 项）" % len(ps.REQUIRED))
    for key in ps.REQUIRED:
        check(by_key[key].ok, "推导成功：%s —— %s" % (key, by_key[key].detail))

    print("[2] 推导结果原路回查（用的就是生产闸那套 evidence 代码）")
    checks = ps.verify_derived(results, root)
    flags = {key: flag for key, flag, _ in checks}
    detail = {key: text for key, _, text in checks}
    for key in ps.REQUIRED:
        check(flags.get(key) is True, "回查命中：%s（%s）" % (key, detail.get(key)))
    check(flags.get("__chain__") is True,
          "同页因果链成立：账号 → 提交 → 成功 → Planner 就绪 → 卡片 → final")

    account = by_key["composer_account_context"].spec
    submit = by_key["composer_submit_button"].spec
    success = by_key["composer_success_signal"].spec
    card = by_key["planner_scheduled_card"].spec
    check(account.attributes["facebook_account_token"] == TARGET_FB
          and "instagram_account_token" not in account.attributes,
          "提交前账号闸只认 FB —— composer 上没有 IG 帐号名，这是实测不是妥协")
    check(any("IG" in text for text in
              by_key["composer_account_context"].warnings),
          "但会**明说** IG 提交前证不了、由回读兜住，不是悄悄放过")
    check(submit.role == "button" and submit.name == "Schedule",
          "提交按钮取的是候选表里带真实 role 的那条（role='' 的第 0 条不能用）")
    check(success.role == "heading"
          and success.name == "Your post is scheduled",
          "成功信号取的是短而准的 heading，不是整段弹窗文本")
    check(card.attributes["date_format"] == "%B %d, %Y"
          and card.attributes["time_format"] == "%I:%M %p",
          "日期时间格式是**试出来**的（英文长月份 + 12 小时制），不是写死的")
    check(card.attributes["facebook_marker"] == "Facebook's Feed"
          and card.attributes["instagram_marker"] == "Instagram feed",
          "两个渠道各有能把对方排除掉的标记")
    check(len(card.sequences) >= 3,
          "排期证据横跨多张快照（条目 / FB 弹窗 / IG 弹窗不可能同框）")

    print("[2b] 定位名必须**与帖子无关**（它们会变成运行时 get_by_role 的过滤名）")
    check(card.attributes["entry_role"] == "link"
          and not card.attributes.get("entry_name"),
          "条目按 role 全取回来再在 Python 侧按内容筛 —— 条目名就是正文本身，"
          "没有固定标签可用")
    check(card.attributes["dialog_name"] == "Post details"
          and not any(ch.isdigit() for ch in card.attributes["dialog_name"]),
          "弹窗定位名是固定抬头 'Post details'，不含 remote id / 正文")
    check(card.attributes["visible_month_format"] == "%B"
          and card.attributes["visible_year_format"] == "%Y",
          "可见区间靠**按格式解析**月份/年份 heading，不靠固定名字（月份每月变）")
    check(any("回查 dump" in text
              for text in by_key["planner_scheduled_card"].warnings),
          "entry_probe_text 是这一篇的正文片段，会明说它只用于回查 dump")

    print("[3] 落盘：生成文件能解析、能装载、内容与推导一致")
    target = root / "signals_backfilled.py"
    ps.emit(results, good.name, target)
    text = target.read_text(encoding="utf-8")
    namespace: dict = {"EvidenceSignal": EvidenceSignal, "Locator": Locator}
    exec(compile(text, str(target), "exec"), namespace)     # noqa: S102
    loaded_signals = namespace["SIGNALS"]
    loaded_locators = namespace["LOCATORS"]
    check("composer_submit_button" in loaded_locators,
          "提交按钮落在 LOCATORS（require_submission_evidence 从 COMPOSER 取它）")
    check(set(loaded_signals) == {
        "composer_account_context", "composer_success_signal",
        "planner_scheduled_card", "planner_loaded_signal"},
        "四条 EvidenceSignal 落在 SIGNALS")
    check(loaded_signals["planner_scheduled_card"].attributes
          == card.attributes, "落盘后属性逐字一致，没有在渲染时丢字段")
    for spec in list(loaded_signals.values()):
        passed, why = evidence.verify_signal(spec, root)
        check(passed is True, "落盘后仍能回查：%s（%s）" % (spec.key, why or "命中"))
    passed, why = evidence.verify(loaded_locators["composer_submit_button"], root)
    check(passed is True, "落盘后仍能回查：composer_submit_button（%s）" % (why or "命中"))
    check("不要手工编辑" in text and good.name in text,
          "生成文件写明了来源 dump 与「不要手工编辑」")

    print("[4] 反向：残缺的 dump 必须推不出来（这一节比第 1 节重要）")
    broken = build_dump(root, name="wrong_page.json",
                        account=account_items(facebook="Andere Seite"))
    got = {item.key: item.ok for item in derive(root, broken)}
    check(not got["composer_account_context"],
          "composer 预览显示的是别的主页 → 账号上下文拒绝推导")

    early = build_dump(root, name="success_too_early.json",
                       account=account_items() + success_items())
    picked = {item.key: item.spec for item in derive(root, early)}
    early_signal = picked["composer_success_signal"]
    check(early_signal is None or "is scheduled" not in early_signal.name,
          "提交前就已经在页面上的提示不会被当成成功信号（它证明不了这次提交）")

    noclick = build_dump(root, name="no_click.json", click=False)
    got = {item.key: item.ok for item in derive(root, noclick)}
    check(not got["composer_submit_button"],
          "composer 上没有带名字的可信点击 → 提交按钮拒绝推导")

    noig = build_dump(root, name="no_ig_dialog.json",
                      card=card_items(instagram=False))
    got = {item.key: item.ok for item in derive(root, noig)}
    check(not got["planner_scheduled_card"],
          "只点开了 FB 弹窗、没点 IG → 排期证据拒绝推导"
          "（少一个渠道不能记 scheduled）")

    nofb = build_dump(root, name="no_fb_dialog.json",
                      card=card_items(facebook=False))
    got = {item.key: item.ok for item in derive(root, nofb)}
    check(not got["planner_scheduled_card"], "没点开 FB 弹窗 → 同样拒绝推导")

    noid = build_dump(root, name="no_remote_id.json",
                      card=card_items(ig_id=""))
    got = {item.key: item.ok for item in derive(root, noid)}
    check(not got["planner_scheduled_card"],
          "弹窗上读不到 `ID: <数字>` → 拒绝推导（没有远端 ID 就没法跨渠道对账）")

    nearmiss = build_dump(root, name="near_ig.json",
                          card=card_items(ig_account=TARGET_IG + "als"))
    got = {item.key: item.ok for item in derive(root, nearmiss)}
    check(not got["planner_scheduled_card"],
          "弹窗里是 neakasa.deals 这种近碰撞账号 → 拒绝推导")

    noentry = build_dump(root, name="no_entry.json",
                         card=card_items(entry=False))
    got = {item.key: item.ok for item in derive(root, noentry)}
    check(not got["planner_scheduled_card"],
          "日历上只有 '10:00 AM' 那种月视图条目（没有正文）→ 认不出是哪一篇，拒绝推导")

    nomonth = build_dump(root, name="no_month.json",
                         card=card_items(month=False))
    got = {item.key: item.ok for item in derive(root, nomonth)}
    check(not got["planner_scheduled_card"],
          "读不到可见月份/年份 heading → 拒绝推导（否则视图外的旧卡会被当不存在）")

    build_dump(root, name="no_final.json", final_ok=False)
    data, why = evidence.validate_v2_dump("no_final.json", root)
    check(data is None and "final" in why,
          "final 遮罩截图缺失 → v2 契约当场不成立，根本走不到推导")

    print("[5] 顺序被打乱时因果链不成立")
    # ⚠️ 这里验的是**因果链检查器**，所以拿好 dump 推出来的那几条 spec
    # 原样去核对被打乱的 dump —— 不能重新推导：重新推导会在乱序数据里
    # 另找一条合法的链，那验的就不是排序检查了。
    swapped = json.loads(good.read_text(encoding="utf-8"))
    # 内容一个字没改，只把成功快照排到点击之前。
    swapped["interactions"][0]["evidence_order"] = 3
    swapped["snapshots"][1]["evidence_order"] = 2
    name = "swapped.json"
    shots = root / "swapped_screenshots"
    shots.mkdir(exist_ok=True)
    (shots / "final.png").write_bytes(b"masked-png")
    swapped["snapshots"][-1]["screenshot"] = str(shots / "final.png")
    (root / name).write_text(json.dumps(swapped, ensure_ascii=False),
                             encoding="utf-8")
    from dataclasses import replace as _replace
    moved = {item.key: _replace(item.spec, source_dump=name)
             for item in results if item.ok}
    passed, why = evidence.verify_publish_chain(
        moved["composer_submit_button"], moved["composer_account_context"],
        moved["composer_success_signal"], moved["planner_loaded_signal"],
        moved["planner_scheduled_card"], root)
    check(passed is not True and "顺序" in why,
          "同一批证据放到乱序 dump 上 → 因果链检查当场拒绝（%s）" % why)

    print("[6] --emit 的原子写与语法保证")
    partial = [item for item in results if item.key != "planner_scheduled_card"]
    out = root / "partial.py"
    ps.emit(partial, good.name, out)
    check(out.is_file() and not (root / "partial.py.tmp").exists(),
          "先写 .tmp 再原子替换，不留半截文件")
    check("planner_scheduled_card" not in out.read_text(encoding="utf-8"),
          "没推导出来的条目不会被写进生成文件")

    print("[7] 装载之后 G6/G6c 三道生产闸真的打开（这一节是整条缝的终点）")
    import core.config as config_module
    from publish import business_suite as bs
    from publish import selectors as sel

    class StubConfig:
        """只回答生产闸会问的那几个问题。"""

        state_dir = root

        def get(self, section, key, default=None):
            table = {
                ("publish", "facebook_page_name"): TARGET_FB,
                ("publish", "instagram_account"): TARGET_IG,
                ("publish", "ui_probe_dump"): good.name,
            }
            return table.get((section, key), default)

    saved_cfg = config_module._cfg
    saved_signals = dict(sel.SIGNALS)
    saved_composer = dict(sel.COMPOSER)
    try:
        sel.SIGNALS.clear()
        sel.COMPOSER.pop("composer_submit_button", None)
        check(not bs.submission_evidence_ready()
              and not bs.readback_evidence_ready(),
              "装载之前：三道闸是关的（SIGNALS 为空 → --submit 失败闭合）")
        config_module._cfg = StubConfig()
        sel.SIGNALS.update(loaded_signals)
        sel.COMPOSER.update(loaded_locators)
        opened, why = True, ""
        try:
            bs.require_readback_evidence()
        except Exception as exc:                   # noqa: BLE001
            opened, why = False, str(exc).splitlines()[0]
        check(opened, "装载之后：账号 + 提交 + 成功 + Planner 回读三道闸全开（%s）"
              % (why or "全部回查通过"))

        # 换一个发布目标 → 同一份证据必须立刻失效，否则等于"证据可以张冠李戴"。
        class WrongTarget(StubConfig):
            def get(self, section, key, default=None):
                if (section, key) == ("publish", "instagram_account"):
                    return "someone.else"
                return StubConfig.get(self, section, key, default)

        config_module._cfg = WrongTarget()
        check(not bs.readback_evidence_ready(),
              "把 config 的 IG 目标改掉 → 同一份证据立刻不再放行")

        # ui_probe_dump 是人工审核的签字栏：留空必须挡住。
        class NoSignoff(StubConfig):
            def get(self, section, key, default=None):
                if (section, key) == ("publish", "ui_probe_dump"):
                    return ""
                return StubConfig.get(self, section, key, default)

        config_module._cfg = NoSignoff()
        check(not bs.submission_evidence_ready(),
              "[publish].ui_probe_dump 留空 → 挡住（人工签字栏程序不替你填）")
    finally:
        config_module._cfg = saved_cfg
        sel.SIGNALS.clear()
        sel.SIGNALS.update(saved_signals)
        sel.COMPOSER.clear()
        sel.COMPOSER.update(saved_composer)

print()
if fails:
    print("失败 %d 项：" % len(fails))
    for item in fails:
        print("  - " + item)
    raise SystemExit(1)
print("全部通过")
print("真实验收：录一份 v2 dump 之后跑 "
      r".venv\Scripts\python.exe tools\probe_signals.py --check state\<dump>.json")

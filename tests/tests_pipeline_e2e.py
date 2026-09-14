"""I1 全链路离线串跑：抓取之后的每一段**真的接上了吗**。

零网络、零费用、零浏览器、零真实归档写入。

### 为什么单独开一套，而不是往 tests_pipeline_assisted.py 里加

那一套测的是**每一段各自的判据**（配对、预算、幂等、停手）。它有一个共同的
盲区：`_run_unlocked` 里的 `compose_post(archive_root=cfg().archive_dir)` 读的是
**真实 config 的归档目录**，而夹具建在临时目录里，于是那些用例**从来没有真的
走通过 compose**，全都在 `offline_gate` 上停下。换句话说：

> **「翻译 + 调图做完 → 离线硬闸通过 → 生成 ready_to_publish」这一段，
> 在这次之前一条断言都没有。** 而它恰恰是整条链上最关键的一次交接。

这正是 CODE_REVIEW 18.9 那条教训的形状：**mock 掉的边界就是没被测到的边界。**
所以这一套把 `cfg` 也换掉，让整条链在临时归档上**真的跑一遍**。

### 这里的 runner 是"假的调用、真的后果"

`Runner` 桩只记调用、什么都不产出，于是 `translation_is_current` 永远是假、
`compose_post` 永远失败。这里的 `StageRunner` 不同：它**真的把译文写进
`translated.jsonl`、真的把德语图写进 `media_de/`** —— 只是不花钱。
只有这样，下游那一段才是被测到的，而不是被绕过的。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from activation_fixtures import activate as fixture_activate

from pipeline import engine as A          # noqa: E402
import translate as translation        # noqa: E402
from core.config import cfg            # noqa: E402
from core.store import post_dirname    # noqa: E402
from core.console import force_utf8    # noqa: E402
import core.config as config_module   # noqa: E402
from publish_fixtures import verified_probe_config  # noqa: E402

force_utf8()

fails = []


def check(condition, message):
    print(("  OK   " if condition else "  FAIL ") + message)
    if not condition:
        fails.append(message)


class ArchiveOverride:
    """归档放在临时目录；G1 使用临时记录，但仍经过真实约束校验。"""

    def __init__(self, real, archive_dir: Path):
        self._real = real
        self.archive_dir = archive_dir

    def __getattr__(self, name):
        return getattr(self._real, name)


def make_post(account_dir: Path, post_id: str, created: str, text: str,
              account: str, *, owner=None, coauthors=None, images=1,
              size=(1080, 1080), tint=(20, 60, 120), pattern=0) -> dict:
    """按真实归档形态造一篇：manifest 行 + post.json + 图片。

    ⚠️ 图片必须直接放在帖子目录下（`posts/<dir>/01.jpg`），不是 `media/` 子目录 ——
    `compose._choose_images` 会断言 `original.parent == post_dir`。
    """
    # ⚠️ **必须用实际的 post_dirname**，不要自己拼时间戳。
    # 第一版照抄了 tests_pipeline_assisted 的拼法（`20260901_1300_<id>`），
    # 而真实的是 `2026-09-01_1300_<id>` —— 那一套从来没走到 compose，
    # 所以那个偏差在那边永远不会暴露。这里一走到 compose 就是"帖子目录不存在"。
    folder = "posts/" + post_dirname(post_id, created)
    row = {
        "post_id": post_id,
        "platform": "facebook" if account_dir.name.startswith("fa_") else "instagram",
        "account": account,
        "text": text,
        "created_at": created,
        "owner": owner or account,
        "owner_name": owner or account,
        "coauthors": list(coauthors or []),
        "media_complete": True,
        "media": [{"kind": "image", "local_path": "%s/%02d.jpg" % (folder, i)}
                  for i in range(1, images + 1)],
    }
    post_dir = account_dir / folder
    post_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, images + 1):
        # ⚠️ **纯色图在 dHash 下全部相等**（dHash 比的是相邻像素梯度，纯色一律是 0），
        # 于是两个平台的图被判成 media_corresponding，两篇进 similar_cross_platform。
        # 那是**正确的行为**，只是这里要测的是两篇独立帖，所以夹具必须有真实结构。
        # 第一版夹具用纯色，连换两次颜色都没用 —— 记在这里免得下次再试一遍。
        shade = tuple(min(255, value + index * 17) for value in tint)
        canvas = Image.new("RGB", size, shade)
        draw = ImageDraw.Draw(canvas)
        step = 40 + (pattern + index) * 37
        for x in range(0, size[0], step):
            draw.rectangle([x, 0, x + step // 2, size[1]],
                           fill=(255 - shade[0], 200, 60))
        canvas.save(post_dir / ("%02d.jpg" % index))
    (post_dir / "post.json").write_text(
        json.dumps(row, ensure_ascii=False), encoding="utf-8")
    with (account_dir / "manifest.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def german(text: str) -> str:
    """造一份"合法"的德语译文：金额与话题标签**逐字符保留**。

    这两条是 compose 的硬闸（`money_preserved` / `hashtags_preserved`），
    随手改一个就会在 ready 之前被拦下 —— 那正是它们存在的意义。
    """
    tail = "".join(" " + token for token in translation.extract_hashtags(text))
    money = "".join(" " + token for token in translation.extract_money_tokens(text))
    return "Deutscher Text." + money + tail


class StageRunner:
    """假的付费调用、真的落盘后果。"""

    def __init__(self, archive_dir: Path):
        self.archive_dir = archive_dir
        self.calls: list[tuple] = []

    def delta(self, *, if_stale: bool) -> int:
        self.calls.append(("delta", if_stale))
        return 0

    def translate(self, source) -> int:
        self.calls.append(("translate", source.ref))
        entry = {
            "post_id": source.post_id,
            "translated_at": "2026-09-03T00:00:00Z",
            # ⚠️ `load_translated` 会**静默丢掉**缺 model / prompt_version 的行
            # （坏行不许污染付费产物）。少写一个字段的表现是"没有译文"，
            # 不是"译文格式错" —— 造夹具时最容易在这里卡住。
            "model": "deepseek-chat",
            "prompt_version": translation.PROMPT_VERSION,
            "source_text_sha256": translation.source_text_sha256(source.text),
            "text_de": german(source.text),
            "usage": {"input_tokens": 1000, "output_tokens": 1000,
                      "prompt_cache_hit_tokens": 0},
        }
        with (source.account_dir / "translated.jsonl").open(
                "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return 0

    def image(self, source, media_index: int) -> int:
        self.calls.append(("image", source.ref, media_index))
        # 真的产出德语图。调图是这条链上**最贵**的一段（≈US$33/月 vs 翻译 US$1/月），
        # 所以"跑第二次不会重新花钱"必须被真的测到，而不是靠桩返回 0 蒙混过去。
        post_dir = A._post_dir(source)
        media_de = post_dir / "media_de"
        media_de.mkdir(exist_ok=True)
        original = post_dir / ("%02d.jpg" % (media_index + 1))
        Image.open(original).save(media_de / ("%02d.png" % (media_index + 1)))
        return 0


print("[1] 造一份真实形态的临时归档，整条链在它上面跑")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    archive = root / "archive"
    state = root / "state"
    original_global_config = cfg()
    config_module._cfg = verified_probe_config(original_global_config, state)
    fb = archive / "fa_neakasaofficial"
    ig = archive / "in_neakasa.global"
    fb.mkdir(parents=True)
    ig.mkdir(parents=True)
    make_post(fb, "e2e-fb", "2026-09-01T13:00:00Z",
              "Autumn campaign is live. #Neakasa", "neakasaofficial")
    make_post(ig, "e2e-ig", "2026-09-01T15:00:00Z",
              "Completely different caption about cats. #Neabot", "neakasa.global",
              tint=(210, 40, 30), pattern=5)

    boundary = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    fixture_activate(A, state, g8_verified=True, now=boundary)
    # 配对窗口是 30 小时；两篇都要"成熟"才会进入付费处理。
    now = datetime(2026, 9, 3, 20, tzinfo=timezone.utc)
    settings = {"autonomy": "assisted", "daily_budget_usd": 5,
                "monthly_budget_usd": 60}

    runner = StageRunner(archive)
    original_cfg = A.cfg
    try:
        A.cfg = lambda: ArchiveOverride(original_cfg(), archive)
        lines: list[str] = []
        code = A.run(account_dirs=[fb, ig], state_dir=state,
                     settings=settings, now=now, runner=runner,
                     report=lines.append)
    finally:
        A.cfg = original_cfg

    check(code == 0, "assisted 全链路一次跑完，退出码 0")
    check(("delta", False) in runner.calls, "先跑增量抓取")
    translated = {call[1] for call in runner.calls if call[0] == "translate"}
    check(translated == {"facebook:e2e-fb", "instagram:e2e-ig"},
          "两篇都真的走了翻译流程（内容不同，不会被误当成跨平台重复）")
    imaged = {call[1] for call in runner.calls if call[0] == "image"}
    check(imaged == {"facebook:e2e-fb", "instagram:e2e-ig"},
          "两篇都真的走了调图流程")

    items = A.latest_human_items(state)
    ready = [item for item in items.values()
             if item.get("kind") == "ready_to_publish"
             and item.get("status") == "open"]
    gates = [item for item in items.values()
             if item.get("kind") not in {"ready_to_publish"}
             and item.get("status") == "open"]
    check(len(ready) == 2 and not gates,
          "**翻译+调图 → 离线硬闸 → ready_to_publish 这一段真的通了**"
          "（此前从没有断言覆盖过；实得 ready=%d 其它=%s）"
          % (len(ready), [
              "%s: %s" % (item.get("kind"), item.get("summary"))
              for item in gates]))
    check(all(item["details"].get("publish_fingerprint")
              for item in ready),
          "每条 ready 都绑定了正文+逐张图片的指纹，排期槽变化不使内容审批失效")

    page = (state / A.NEEDS_HUMAN_HTML).read_text(encoding="utf-8")
    check("python -m pipeline approve" in page
          and all(item["item_id"] in page for item in ready),
          "needs_human.html 直接给出把这两条一起批准的命令")
    check("Deutscher Text" in page and "e2e-fb" in page and "e2e-ig" in page,
          "清单上直接看得到德语正文与 post_id —— 稳态里人每天只看这一眼，"
          "只列 item_id 等于逼人盲批")
    check(all(item["details"].get("image_count") == 1
              and item["details"].get("text_de_preview")
              for item in ready),
          "ready 的 details 带正文预览与图片张数，供 HTML 与以后的通知复用")

    print("\n[2] 原样再跑一次：不重复付费、不重复排队")
    second = StageRunner(archive)
    try:
        A.cfg = lambda: ArchiveOverride(original_cfg(), archive)
        code2 = A.run(account_dirs=[fb, ig], state_dir=state,
                      settings=settings, now=now, runner=second,
                      report=lambda _message: None)
    finally:
        A.cfg = original_cfg
    check(code2 == 0, "第二次跑同样成功")
    check(not [call for call in second.calls if call[0] == "translate"],
          "译文已当前 → 第二次零翻译请求（内容寻址幂等，不靠状态文件记账）")
    check(not [call for call in second.calls if call[0] == "image"],
          "德语图已存在 → 第二次零调图请求（这一段最贵，重复付费最要命）")
    again = [item for item in A.latest_human_items(state).values()
             if item.get("kind") == "ready_to_publish"
             and item.get("status") == "open"]
    check(len(again) == 2 and {item["item_id"] for item in again}
          == {item["item_id"] for item in ready},
          "ready 项的 item_id 是内容哈希，重跑不产生第二批重复项")

    print("\n[2b] 批量审批两次组装均沿用注入时钟；确认前不提交")
    from publish import business_suite as bs
    from tools import publish_post

    class OfflineSession:
        async def new_page(self):
            return object()

        async def stop(self):
            return None

    session = OfflineSession()
    inventory = bs.RemoteSlotInventory(
        occupied=(datetime.fromisoformat("2026-09-04T10:00:00+02:00"),),
        ui_timezone="America/Los_Angeles",
        visible_start=date(2026, 9, 1), visible_end=date(2026, 9, 30))
    with patch.object(A, "cfg", lambda: ArchiveOverride(cfg(), archive)), \
            patch.object(A, "attach", AsyncMock(return_value=(session, None, session))), \
            patch('publish.capabilities.require', return_value=None), \
            patch.object(bs, "require_submission_evidence", return_value=None), \
            patch.object(bs, "require_readback_evidence", return_value=None), \
            patch.object(A.month_inventory, "read", AsyncMock(return_value=inventory)), \
            patch.object(publish_post, "main", side_effect=AssertionError("不得真实提交")), \
            contextlib.redirect_stdout(io.StringIO()) as approval_output:
        approved = A.approve(
            item_ids=[item["item_id"] for item in again], selections={},
            state_dir=state, now=now, confirm=lambda _message: False)
    check(approved == 0 and "已取消" in approval_output.getvalue()
          and "2026-09-04T17:00:00+02:00" in approval_output.getvalue(),
          "批准前和远端占位后都通过真实时间闸，顺延槽位使用同一注入时钟")

    print("\n[3] 硬闸仍然拦得住：正文被改脏之后 ready 立刻消失")
    dirty = ig / "translated.jsonl"
    rows = [json.loads(line) for line in
            dirty.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[-1]["text_de"] = "Deutscher Text ohne Hashtag."      # 抹掉 #Neabot
    dirty.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n"
                             for item in rows), encoding="utf-8")
    third = StageRunner(archive)
    try:
        A.cfg = lambda: ArchiveOverride(original_cfg(), archive)
        A.run(account_dirs=[fb, ig], state_dir=state, settings=settings,
              now=now, runner=third, report=lambda _message: None)
    finally:
        A.cfg = original_cfg
    kinds = {item.get("kind") for item in A.latest_human_items(state).values()
             if item.get("status") == "open"}
    check("offline_gate" in kinds,
          "人工改坏译文（标签被抹掉）后转人工 —— 写盘侧的闸挡不住手工修改，"
          "发布侧这道复查才是最后一层")


config_module._cfg = original_global_config
print("\n[4] 跨进程 argv 契约：这几段是靠命令行拼起来的，拼错了只在真跑时才炸")

# ⚠️ 这四条是**整条链上仅有的四个字符串接缝**。它们在别处全被 mock 掉了：
# 单元测试用桩 runner，桩不校验参数名。真实调用要等到 assisted 第一次跑、
# 或者 approve 第一次开浏览器之后才发生 —— 那是最差的发现时机。
from routes import delta as delta_module           # noqa: E402
import localize_images                             # noqa: E402
from tools import publish_post as publish_module   # noqa: E402

parsed = delta_module._parse_args(["--platform", "all", "--if-stale"])
check(parsed.platform == "all" and parsed.if_stale is True,
      "RealStageRunner.delta 拼的 --platform all / --if-stale 能被 routes.delta 接受")


def accepts(fn, argv) -> bool:
    """能走过 argparse 就算契约成立；之后失败在业务闸上是预期的。"""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            fn(argv)
    except SystemExit:
        # argparse 拒绝参数时抛 SystemExit(2)；业务闸只 return，不抛。
        return False
    except Exception:                                          # noqa: BLE001
        return True
    return True


check(accepts(translation.main,
              ["--account", "__no_such_account__", "--post-id", "__none__"]),
      "RealStageRunner.translate 拼的 --account/--post-id 能被 translate.py 接受")
check(accepts(localize_images.main,
              ["--account", "__no_such_account__", "--post-id", "__none__",
               "--media-index", "0"]),
      "RealStageRunner.image 拼的 --account/--post-id/--media-index "
      "能被 localize_images.py 接受")

# approve 的 submit_one 拼的就是下面这串。多一个不认识的参数 = 整批在
# 浏览器已经开着的时候炸掉。
submit_argv = [
    "--post-id", "__none__",
    "--at", datetime(2026, 9, 20, 10, tzinfo=timezone(timedelta(hours=2))).isoformat(),
    "--account", "in_neakasa.global",
    "--submit", "--assume-yes", "--apply-price-map",
    "--source-ref", "instagram:__none__",
]
check(accepts(publish_module.main, submit_argv),
      "approve 的 submit_one 拼的全部参数（含被 SUPPRESS 隐藏的 "
      "--apply-price-map/--source-ref）能被 publish_post.py 接受")

source = (ROOT / "pipeline" / "engine.py").read_text(encoding="utf-8")
check('"--apply-price-map"' in source and '"--source-ref"' in source,
      "submit_one 仍然传 --apply-price-map（否则德语帖会带着美元价发出去）")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

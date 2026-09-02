"""G0/G0b 与 G1 recorder 的离线测试；零浏览器、零对外发布。"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import io
import json
import sys
import tempfile
import threading
import time
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8  # noqa: E402

force_utf8()

from core.config import cfg  # noqa: E402
from core.store import post_dirname  # noqa: E402
from publish.business_suite import (ProbeRequired, PublishStepError,  # noqa: E402
                                    ensure_logged_in, fill_caption,
                                    set_schedule, submit, upload_images)
from publish.compose import (ComposeError, InstagramConstraints,  # noqa: E402
                             ScheduleWindow, compose_post)
from publish.evidence import verify_all  # noqa: E402
from tools.probe_publish import ProbeRecorder, install_script  # noqa: E402
from translate import PROMPT_VERSION, source_text_sha256  # noqa: E402
import publish.business_suite as bs  # noqa: E402
import publish.compose as compose_module  # noqa: E402
import publish.journal as journal  # noqa: E402
import publish.selectors as selectors  # noqa: E402
import tools.probe_publish as probe_module  # noqa: E402
import tools.publish_post as publish_entry  # noqa: E402
import tools.start_chrome_publish as start_publish  # noqa: E402


fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def raises(exc_type, call, contains: str = ""):
    try:
        call()
    except exc_type as exc:
        return not contains or contains in str(exc)
    except Exception:
        return False
    return False


WHEN = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def make_fixture(root: Path, *, platform: str = "instagram",
                 post_id: str = "fixture-post", image_count: int = 2,
                 media_complete: bool = True, collaboration: bool = True):
    account = "neakasa.tech" if platform == "instagram" else "neakasaofficial"
    account_name = ("in_" if platform == "instagram" else "fa_") + account
    account_dir = root / account_name
    created_at = "2026-08-31T02:10:58Z"
    dirname = post_dirname(post_id, created_at)
    post_dir = account_dir / "posts" / dirname
    post_dir.mkdir(parents=True)

    text = "Offer stays at $10.\n\n#One #Two"
    media = []
    for index in range(1, image_count + 1):
        name = "%02d.jpg" % index
        path = post_dir / name
        Image.new("RGB", (100 + index, 100), (20 * index, 40, 60)).save(path)
        media.append({
            "url": "https://example.invalid/%s" % name,
            "kind": "image",
            "local_path": "posts/%s/%s" % (dirname, name),
            "width": 100 + index,
            "height": 100,
        })

    owner = "partner.account" if collaboration else account
    source = {
        "post_id": post_id,
        "platform": platform,
        "account": account,
        "text": text,
        "created_at": created_at,
        "permalink": "https://example.invalid/post",
        "media": media,
        "source_route": "delta",
        "owner": owner,
        "owner_name": "Partner Account" if collaboration else "Own Account",
        "coauthors": [account] if collaboration else [],
        "media_complete": media_complete,
    }
    payload = json.dumps(source, ensure_ascii=False)
    (account_dir / "manifest.jsonl").write_text(payload + "\n", encoding="utf-8")
    (post_dir / "post.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")

    translation = {
        "post_id": post_id,
        "source_text_sha256": source_text_sha256(text),
        "text_de": "Das Angebot bleibt bei $10.\n\n#One #Two",
        "translated_at": "2026-08-31T05:00:00Z",
        "model": "fixture",
        "prompt_version": PROMPT_VERSION,
    }
    translated_path = account_dir / "translated.jsonl"
    translated_path.write_text(
        json.dumps(translation, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "account": account_name,
        "account_dir": account_dir,
        "post_dir": post_dir,
        "source": source,
        "translation": translation,
        "translated_path": translated_path,
    }


def rewrite_translation(fixture, **changes):
    row = dict(fixture["translation"])
    row.update(changes)
    fixture["translated_path"].write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


class FakeLocator:
    pass


class FakePage:
    url = "https://business.example.invalid/create?access_token=must-not-leak#dialog"

    def __init__(self):
        self.mask_selectors = []
        self.screenshot_timeouts = []

    def locator(self, selector):
        self.mask_selectors.append(selector)
        return FakeLocator()

    async def screenshot(self, *, path, full_page, mask, timeout=None):
        # timeout 是 CR-64 加的：帧树坏掉的页面上 Playwright 截图会一直挂着，
        # 而 record() 是持锁的，一次挂住就把后面所有事件堵死。
        self.screenshot_timeouts.append(timeout)
        Image.new("RGB", (24, 16), (1, 2, 3)).save(path, format="PNG")


PROBE_OBSERVATIONS = {
    "business_suite_entry_url": "https://business.example.invalid/create?token=drop-me",
    "ui_timezone": "Europe/Berlin shown by Page UI",
    "schedule_min_ahead": "1 hour",
    "schedule_max_ahead": "10 days",
    "schedule_min_ahead_seconds": "3600",
    "schedule_max_ahead_seconds": "864000",
    "schedule_input_behavior": "direct input accepted and read back",
    "success_signal": "scheduled-list row appeared",
    "instagram_min_aspect_ratio": "0.5",
    "instagram_max_aspect_ratio": "2.0",
    "instagram_max_images": "5",
    "instagram_max_caption_length": "1000",
    "instagram_caption_length_mode": "codepoints",
    "instagram_max_hashtags": "10",
    "instagram_aspect_ratio_rejection": "UI rejected 0.49 and 2.01",
    "instagram_image_count_rejection": "UI rejected the sixth image",
    "instagram_caption_length_rejection": "UI rejected 1001 codepoints",
    "instagram_hashtag_rejection": "UI rejected the eleventh hashtag",
}


async def make_completed_probe(state_dir: Path, profile: Path):
    recorder = ProbeRecorder(
        state_dir, port=9223, profile=profile,
        timestamp="20260831_120000")
    page = FakePage()
    events = ["click", "change", "input", "click", "click", "input", "submit"]
    for index, event_type in enumerate(events):
        tag = "input" if event_type in {"input", "change"} else "button"
        role = "textbox" if tag == "input" else "button"
        accepted = await recorder.record(page, {
            "session_id": recorder.session_id,
            "event_type": event_type,
            "is_trusted": True,
            "client_timestamp": "2026-08-31T19:00:%02dZ" % index,
            "page_url": FakePage.url,
            "document_title": "Create post",
            "target": {
                "ancestor_depth": 0,
                "tag": tag,
                "role": role,
                "aria_label": "Step %d" % index,
                "data_testid": "fixture-step",
                "name": "",
                "placeholder": "",
                "visible_text": "Step %d" % index,
                "is_contenteditable": False,
                "input_type": "text" if tag == "input" else "button",
                "href": "https://business.example.invalid/action?secret=drop",
                "injected_secret": "must not persist",
            },
            "candidates": [],
            "injected_secret": "must not persist",
        })
        if not accepted:
            raise AssertionError("fixture trusted event was rejected")
    final_shot = recorder.screenshot_dir / "semantic_001_final.png"
    Image.new("RGB", (24, 16), (4, 5, 6)).save(final_shot, format="PNG")
    recorder.data["snapshots"] = [{
        "sequence": 1,
        "recorded_at": "2026-08-31T19:01:00Z",
        "reason": "final",
        "page_url": "https://business.example.invalid/create",
        "document_title": "Create post",
        "semantic_items": [{"tag": "div", "role": "status",
                            "accessible_name": "fixture final",
                            "visible_text": "fixture final", "aria_live": "polite",
                            "href": ""}],
        "screenshot": str(final_shot),
        "screenshot_error": None,
    }]
    recorder._write()
    await recorder.set_observations(PROBE_OBSERVATIONS)
    await recorder.finish()
    return recorder, page


class VerifiedProbeConfig:
    def __init__(self, state_dir: Path, profile: Path, dump: Path, *,
                 verified: bool = True):
        self.state_dir = state_dir
        self.publish_profile_dir = profile
        self.publish_debug_port = 9223
        self._dump = dump
        self._verified = verified

    def get(self, section, key, default=None):
        values = {
            ("publish", "ui_constraints_verified"): self._verified,
            ("publish", "ui_probe_dump"): str(self._dump),
            # 跨月上限只能在 UI 时区里判，所以严格路径要读它（见
            # compose._validate_schedule_month）。
            ("publish", "ui_timezone"): "America/Los_Angeles",
        }
        return values.get((section, key), default)


print("[1] G0 发布 Chrome 配置/入口与抓取侧硬隔离")
c = cfg()
check(c.debug_port == 9222 and c.publish_debug_port == 9223,
      "抓取 9222 与发布 9223 同时存在")
check(c.profile_dir != c.publish_profile_dir,
      "发布 profile 与抓取小号 profile 不是同一路径")
check(".fbscraper-publish/" in (ROOT / ".gitignore").read_text(encoding="utf-8"),
      "发布 profile 即使改到项目内也不会被提交")

bat = ROOT / "scripts" / "start_chrome_publish.bat"
raw = bat.read_bytes() if bat.exists() else b""
check(bool(raw), "scripts\\start_chrome_publish.bat 存在")
check(bool(raw) and all(byte <= 127 for byte in raw),
      "发布 .bat 纯 ASCII")
check(bool(raw) and raw.replace(b"\r\n", b"").count(b"\n") == 0,
      "发布 .bat 只有 CRLF、没有裸 LF")
check(bool(raw) and not raw.startswith(b"\xef\xbb\xbf"),
      "发布 .bat 无 BOM")
decoded = raw.decode("ascii") if raw else ""
check("PYTHONIOENCODING=utf-8" in decoded
      and "tools\\start_chrome_publish.py" in decoded,
      "发布 .bat 强制 UTF-8 并只转交 Python 入口")

with tempfile.TemporaryDirectory() as d:
    class FakeConfig:
        chrome_exe = "fake-chrome.exe"
        publish_profile_dir = Path(d) / "publish"
        publish_debug_port = 45678

        @staticmethod
        def assert_publish_chrome_isolated():
            return None

    original = (start_publish.cfg, start_publish.cdp_ready,
                start_publish.port_open, start_publish.launch)
    launch_calls = []
    try:
        start_publish.cfg = lambda: FakeConfig()
        start_publish.cdp_ready = lambda _port, **_kwargs: False
        start_publish.port_open = lambda _port: False

        def fake_launch(wait, on_tick, **kwargs):
            launch_calls.append((wait, kwargs))
            return True

        start_publish.launch = fake_launch
        rc = start_publish.main()
    finally:
        (start_publish.cfg, start_publish.cdp_ready,
         start_publish.port_open, start_publish.launch) = original
    check(rc == 0, "发布 Chrome 启动入口在显式目标就绪时成功")
    check(len(launch_calls) == 1
          and launch_calls[0][1].get("port") == 45678,
          "启动入口把 [publish] 端口显式传给 core.chrome.launch")
    check(launch_calls and launch_calls[0][1].get("profile") == FakeConfig.publish_profile_dir,
          "启动入口把 [publish] profile 显式传入，没有用抓取默认值")


print("\n[2] G0b 正常组装：当前译文、金额、德语图优先、原图回退、合作方提示")
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    media_de = fixture["post_dir"] / "media_de"
    media_de.mkdir()
    Image.new("RGB", (101, 100), (1, 2, 3)).save(media_de / "01.png")
    emitted = []
    post = compose_post(
        "fixture-post", WHEN, archive_root=root,
        warning_sink=emitted.append)
    check(post.text_de.startswith("Das Angebot"),
          "正文取自 translated.jsonl 的当前德语译文")
    mapped_post = compose_post(
        "fixture-post", WHEN, archive_root=root,
        price_map={"$10": "9,99 €"}, warning_sink=None)
    check("9,99 €" in mapped_post.text_de and "$10" not in mapped_post.text_de,
          "流水线价格表只改最终发布文案，不把美元价原样发到德国站")
    normalized_mapped = compose_post(
        "fixture-post", WHEN, archive_root=root,
        price_map={"$ 10": "9,99 €"}, warning_sink=None)
    check("9,99 €" in normalized_mapped.text_de,
          "价格预检与实际替换共用金额空白归一化，不会前后判据漂移")
    check("$10" in mapped_post.original_text_de,
          "价格映射不回写/伪装原始德语译文，journal 可分别留两份指纹")
    check(raises(
              ComposeError,
              lambda: compose_post(
                  "fixture-post", WHEN, archive_root=root,
                  price_map={}, warning_sink=None),
              "未映射金额"),
          "价格表缺项在浏览器前失败闭合")
    check(post.image_sources == ("media_de", "original"),
          "每张按序优先 media_de；缺一张时只回退对应原图")
    check([path.name for path in post.image_paths] == ["01.png", "02.jpg"],
          "德语图扩展名可不同，但帖内序号与原图一致")
    check(any("第 2 张" in warning and "英文" in warning
              for warning in post.warnings),
          "回退原图会显式点名哪张可能仍有英文")
    check(post.is_collaboration and post.source_author == "partner.account",
          "合作帖保留真实原作者，不把 coauthor 伪装成原创者")
    check(any("Partner Account" in line for line in post.review_lines()),
          "提交前输出明确点名合作帖原作者")
    check(any("合作帖原作者" in line for line in emitted),
          "compose 调用本身也把原作者告警送到输出")
    check(post.scheduled_at is WHEN,
          "排期保留调用方显式时区，不做本机隐式转换")
    check(any("G1" in warning for warning in post.warnings),
          "尚无 UI 实测约束时不假绿，明确记录 G1 仍待完成")


print("\n[CR-58] compose 的用户入口：tools/compose_publish.py")
# PUBLISH_PLAN 第 5 节的【验收】要求"对最新 3 篇真实帖组装成功、人为改坏各自被拒"。
# 在此之前 compose_post 的唯一调用方是本测试文件 —— 那条验收没有任何命令
# 可以让用户自己复跑，而项目工作协议要求需要用户操作的功能进 MANUAL_STEPS。
import tools.compose_publish as compose_cli  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    state_dir = Path(d) / "state"
    state_dir.mkdir()

    class CliCfg:
        archive_dir = root

        def get(self, section, key, default=None):
            if (section, key) == ("publish", "timezone"):
                return "Europe/Berlin"
            return default

    original = compose_cli.cfg
    try:
        compose_cli.cfg = lambda: CliCfg()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_ok = compose_cli.main(["--post-id", "fixture-post"])
        printed = buf.getvalue()

        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc_missing = compose_cli.main(["--post-id", "no-such-post"])
        missing_out = buf2.getvalue()

        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            rc_latest = compose_cli.main(["--latest", "3"])
        latest_out = buf3.getvalue()
    finally:
        compose_cli.cfg = original

    check(rc_ok == 0, "入口对可组装的帖子返回 0")
    check("fixture-post" in printed and "德语正文" in printed,
          "入口把译文与帖子标识打出来 —— 这就是 require_confirmation 要看的清单")
    check("原图" in printed or "德语图" in printed,
          "逐张标出用的是德语图还是回退的原图")
    check(rc_missing == 1 and "no-such-post" in missing_out,
          "找不到的 post_id 返回非零并点名，不静默成功")
    check(rc_latest == 0 and "fixture-post" in latest_out,
          "--latest 跨账号取最新 N 篇，不需要人手抄 post_id")
    check("零浏览器" in printed and "零写盘" in printed,
          "入口明确声明自己不碰浏览器、不写盘")
    check(not (state_dir / "published.jsonl").exists(),
          "离线预演确实没有写出任何发布留痕")


print("\n[CR-60] 排期时区：Windows 上必须有 tzdata，且夏令时切换日要对")
# ⚠️ 实测发现（2026-08-31）：本机 zoneinfo.TZPATH 是**空的**，
# 缺 tzdata 时 ZoneInfo("Europe/Berlin") 直接抛 ZoneInfoNotFoundError。
# 而 PUBLISH_PLAN 第 3.3 节把显式时区转换写成硬要求，还要求在切换日各测一次。
# 这一节存在的意义：**别让人用"写死 UTC 偏移"绕过去**。
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: E402

configured_tz = "Europe/Berlin"
try:
    berlin = ZoneInfo(configured_tz)
except ZoneInfoNotFoundError:
    berlin = None
check(berlin is not None,
      "Europe/Berlin 可解析（Windows 需要 requirements.txt 里的 tzdata；"
      "缺它时 G5 的显式时区转换根本无法实现）")
if berlin is not None:
    # 2026 年德国夏令时：3/29 前进，10/25 后退。
    transitions = {
        "2026-03-29T01:30": timedelta(0),
        "2026-03-29T03:30": timedelta(hours=1),
        "2026-10-25T01:30": timedelta(hours=1),
        "2026-10-25T03:30": timedelta(0),
    }
    correct = all(
        datetime.fromisoformat(stamp).replace(tzinfo=berlin).dst() == expected
        for stamp, expected in transitions.items())
    check(correct,
          "两个夏令时切换日的偏移都正确 —— 写死 +01:00/+02:00 会在这两天发错时刻")

tz_error = ""
original_cfg = compose_cli.cfg
try:
    class BadTzCfg:
        archive_dir = Path(".")

        def get(self, section, key, default=None):
            if (section, key) == ("publish", "timezone"):
                return "Not/AZone"
            return default

    compose_cli.cfg = lambda: BadTzCfg()
    try:
        compose_cli._schedule_timezone()
    except SystemExit as exc:
        tz_error = str(exc)
finally:
    compose_cli.cfg = original_cfg
check("tzdata" in tz_error or "有效时区名" in tz_error,
      "时区解析失败时给出可直接照做的处置，而不是抛一个裸异常")


print("\n[2b][CR-48] media_de 同序号多候选：改成与 K 组一致的『人工优先』")
# ⚠️ 这一节钉住的是一条**语义对齐**，不是普通回归。
# K 组刻意支持「程序图 01.jpg 与设计同事的 01.png 并存、人工优先」，
# 而 compose 原先见到多个候选就硬失败 ——
# K 专门为设计同事设计的那个场景会让这篇帖子发不出去。
def _sha256_of(path):
    import hashlib as _h
    return _h.sha256(path.read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    media_de = fixture["post_dir"] / "media_de"
    media_de.mkdir()
    program_img = media_de / "01.jpg"
    manual_img = media_de / "01.png"
    Image.new("RGB", (101, 100), (9, 9, 9)).save(program_img)
    Image.new("RGB", (101, 100), (7, 7, 7)).save(manual_img)
    rel = program_img.relative_to(fixture["account_dir"]).as_posix()
    (fixture["account_dir"] / "images_de.jsonl").write_text(
        json.dumps({"post_id": "fixture-post", "media_index": 0,
                    "out_path": rel,
                    "output_sha256": _sha256_of(program_img)},
                   ensure_ascii=False) + "\n",
        encoding="utf-8")

    post = compose_post("fixture-post", WHEN, archive_root=root,
                        warning_sink=None)
    check(post.image_paths[0].name == "01.png",
          "程序图与人工图并存时选人工那张 —— 与 K 组 _candidate_is_program_owned "
          "同一套规则（CR-48）")
    check(post.image_sources[0] == "media_de", "人工图仍然算 media_de 来源")
    check(any("人工放置" in w for w in post.warnings),
          "用了人工版本要显式说出来：这一篇被设计同事动过手，操作者应当看见")

    # 程序产出的字节被人改过 -> 不再算程序产出（与 K 组一致的保守方向）
    Image.new("RGB", (101, 100), (5, 5, 5)).save(program_img)
    two_manual = ""
    try:
        compose_post("fixture-post", WHEN, archive_root=root, warning_sink=None)
    except ComposeError as exc:
        two_manual = str(exc)
    check("多个**人工**候选" in two_manual,
          "两张都不是程序产出时仍然失败闭合 —— 机器不替人猜该发哪张")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    media_de = fixture["post_dir"] / "media_de"
    media_de.mkdir()
    Image.new("RGB", (101, 100), (9, 9, 9)).save(media_de / "01.jpg")
    Image.new("RGB", (101, 100), (8, 8, 8)).save(media_de / "01.png")
    rows = []
    for name in ("01.jpg", "01.png"):
        target = media_de / name
        rows.append(json.dumps({
            "post_id": "fixture-post", "media_index": 0,
            "out_path": target.relative_to(fixture["account_dir"]).as_posix(),
            "output_sha256": _sha256_of(target)}, ensure_ascii=False))
    (fixture["account_dir"] / "images_de.jsonl").write_text(
        "\n".join(rows) + "\n", encoding="utf-8")
    both_program = ""
    try:
        compose_post("fixture-post", WHEN, archive_root=root, warning_sink=None)
    except ComposeError as exc:
        both_program = str(exc)
    check("多个程序产出" in both_program and "output_format" in both_program,
          "两张都是程序产出时点名真实原因（多半改过 output_format），"
          "而不是笼统报『有多个候选』")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    media_de = fixture["post_dir"] / "media_de"
    media_de.mkdir()
    Image.new("RGB", (101, 100), (9, 9, 9)).save(media_de / "01.png")
    (fixture["account_dir"] / "images_de.jsonl").write_text(
        "{ not json at all\n", encoding="utf-8")
    post = compose_post("fixture-post", WHEN, archive_root=root,
                        warning_sink=None)
    check(post.image_paths[0].name == "01.png",
          "images_de.jsonl 整份坏掉时按『不是程序产出』保守处理，"
          "不让一条坏行把整篇帖子挡下来")


print("\n[3] G0b 硬失败：过期译文、金额变化、删图、坏图、残缺轮播")
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, prompt_version=PROMPT_VERSION - 1)
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "版本已过期"),
          "人为把译文版本改过期会在碰浏览器前拒绝并说清原因")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot kostet 10 €。\n\n#One #Two")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "$10"),
          "发布前再次复用 money_preserved，金额被改动会点名原金额")

# 标签与金额在 translate.py 里是同一类「不可改内容规则」，写盘时一起判；
# 发布侧此前只再判金额，于是**手工改过的 translated.jsonl** 里被改坏的标签
# 能一路进到真实发布（CR-61）。这三条断言指向"这道闸不许被顺手去掉"。
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot bleibt bei $10.\n\n#Eins #Two")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "话题标签"),
          "发布前再次复用 hashtags_preserved：标签被改写会被拦下，"
          "而不是把改错的标签发到德语主页")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot bleibt bei $10.\n\n#One")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "话题标签"),
          "标签被删掉一个也算违规——数量、内容、大小写、顺序都必须与原帖一致")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot bleibt bei $10.\n\n#Two #One")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "话题标签"),
          "只调换顺序同样被拦下：口径与 translate.py 写盘闸完全一致，不放宽")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    (fixture["post_dir"] / "02.jpg").unlink()
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "图片不存在"),
          "人为删掉一张图会被正确拒绝，而不是少发一张")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root, image_count=1)
    (fixture["post_dir"] / "01.jpg").write_bytes(b"not-an-image")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "Pillow"),
          "非 0 字节但内容损坏的图片也会被 Pillow 硬闸拒绝")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root, image_count=1)
    image_path = fixture["post_dir"] / "01.jpg"
    image_path.write_bytes(image_path.read_bytes()[:-2])
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "Pillow"),
          "缺少 JPEG 结尾但 verify 可能放过的截断图会在完整像素解码时被拒绝")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    make_fixture(root, media_complete=False)
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "media_complete=True"),
          "只拿到轮播封面时不发布残缺内容")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root, image_count=1)
    source = dict(fixture["source"])
    source.pop("media_complete")
    (fixture["post_dir"] / "post.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "media_complete=True"),
          "缺少完整性标记时失败闭合，不把缺字段当作完整")

for bad_item, label in [
        ({"kind": "video", "local_path": "posts/x/02.mp4"}, "混合图片/视频"),
        ({"local_path": "posts/x/02.jpg"}, "缺 kind 的脏媒体行"),
        ("not-an-object", "非对象媒体行")]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "archive"
        fixture = make_fixture(root, image_count=1)
        source = dict(fixture["source"])
        source["media"] = [*source["media"], bad_item]
        (fixture["post_dir"] / "post.json").write_text(
            json.dumps(source, ensure_ascii=False), encoding="utf-8")
        check(raises(ComposeError,
                     lambda: compose_post("fixture-post", WHEN,
                                          archive_root=root, warning_sink=None),
                     "不得静默丢弃" if isinstance(bad_item, str) else "第 2 个媒体项"),
              "%s会整体拒绝，不会悄悄缩成少图帖" % label)

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root, image_count=0)
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "至少需要 1 张"),
          "纯视频/无图帖子不会混进图文发布")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    source = dict(fixture["source"])
    source["coauthors"] = []
    (fixture["post_dir"] / "post.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "归属证据不完整"),
          "owner 不同但 coauthor 证据缺失时失败闭合，不静默漏掉原作者提示")


print("\n[4] G1 数值未实测时不造事实；有 dump 来源后契约可离线执行")
check(raises(ValueError, lambda: InstagramConstraints(probe_dump=""), "probe"),
      "IG 数值约束必须点名真实 probe dump 来源")
check(raises(ValueError,
             lambda: ScheduleWindow("", timedelta(minutes=1), timedelta(days=1),
                                    "America/Los_Angeles"),
             "probe"),
      "定时窗口同样必须点名真实 probe dump 来源")
check(raises(ValueError,
             lambda: InstagramConstraints(
                 probe_dump="x", min_aspect_ratio=float("nan")),
             "有限数"),
      "NaN/Infinity 不能伪装成已实测画幅边界")

with tempfile.TemporaryDirectory() as d:
    temp = Path(d)
    root = temp / "archive"
    state_dir = temp / "state"
    profile = temp / "publish-profile"
    make_fixture(root)
    make_fixture(
        root, platform="facebook", post_id="fixture-fb-too-many",
        image_count=6)
    recorder, _page = asyncio.run(make_completed_probe(state_dir, profile))
    limits = InstagramConstraints(
        probe_dump=str(recorder.output_path),
        min_aspect_ratio=0.5,
        max_aspect_ratio=2.0,
        max_images=5,
        max_caption_length=1000,
        caption_length_mode="codepoints",
        max_hashtags=10)
    window = ScheduleWindow(
        probe_dump=str(recorder.output_path),
        min_ahead=timedelta(hours=1),
        max_ahead=timedelta(days=10),
        ui_timezone="America/Los_Angeles")
    original_cfg = compose_module.cfg
    try:
        compose_module.cfg = lambda: VerifiedProbeConfig(
            state_dir, profile, recorder.output_path)
        post = compose_post(
            "fixture-post", WHEN, archive_root=root,
            instagram_constraints=limits, schedule_window=window, now=NOW,
            require_verified_ui_constraints=True, warning_sink=None)
        auto_post = compose_post(
            "fixture-post", WHEN, archive_root=root, now=NOW,
            require_verified_ui_constraints=True, warning_sink=None)
        fb_target_ig_blocked = raises(
            ComposeError,
            lambda: compose_post(
                "fixture-fb-too-many", WHEN, archive_root=root, now=NOW,
                require_verified_ui_constraints=True, warning_sink=None),
            "图片数")
    finally:
        compose_module.cfg = original_cfg
    check(len(post.image_paths) == 2,
          "审核过且数值一致的完整 probe 才能严格组装，空白可选 FB slug 不阻塞")
    check(len(auto_post.image_paths) == 2,
          "生产 strict 会从 config 审核的 dump 自动构造窗口/IG 约束，不再要求 CLI 手工注入")
    check(fb_target_ig_blocked,
          "FB canonical 仍会同时发到 IG，因此严格发布不能按来源平台绕过 IG 图片上限")
    check(not any("尚无 G1" in item or "只是 API 占位" in item
                  for item in post.warnings),
          "严格组装不再携带『约束未知』假绿警告")

    original_cfg = compose_module.cfg
    try:
        compose_module.cfg = lambda: VerifiedProbeConfig(
            state_dir, profile, recorder.output_path, verified=False)
        disabled = raises(
            ComposeError,
            lambda: compose_post(
                "fixture-post", WHEN, archive_root=root,
                instagram_constraints=limits, schedule_window=window, now=NOW,
                require_verified_ui_constraints=True, warning_sink=None),
            "ui_constraints_verified")
        missing_dump = state_dir / "publish_probe_missing.json"
        compose_module.cfg = lambda: VerifiedProbeConfig(
            state_dir, profile, missing_dump)
        fabricated = raises(
            ComposeError,
            lambda: compose_post(
                "fixture-post", WHEN, archive_root=root,
                instagram_constraints=InstagramConstraints(
                    probe_dump=str(missing_dump), min_aspect_ratio=0.5,
                    max_aspect_ratio=2.0, max_images=5,
                    max_caption_length=1000, caption_length_mode="codepoints",
                    max_hashtags=10),
                schedule_window=ScheduleWindow(
                    str(missing_dump), timedelta(hours=1), timedelta(days=10),
                    "America/Los_Angeles"),
                now=NOW, require_verified_ui_constraints=True,
                warning_sink=None),
            "不存在")
    finally:
        compose_module.cfg = original_cfg
    check(disabled, "config 人工审核开关未开启时严格发布失败闭合")
    check(fabricated, "仅写一个不存在的 probe_dump 字符串不能解锁严格发布")

    mismatched_limits = InstagramConstraints(
        probe_dump=str(recorder.output_path), min_aspect_ratio=0.5,
        max_aspect_ratio=2.0, max_images=4, max_caption_length=1000,
        caption_length_mode="codepoints", max_hashtags=10)
    original_cfg = compose_module.cfg
    try:
        compose_module.cfg = lambda: VerifiedProbeConfig(
            state_dir, profile, recorder.output_path)
        mismatch = raises(
            ComposeError,
            lambda: compose_post(
                "fixture-post", WHEN, archive_root=root,
                instagram_constraints=mismatched_limits,
                schedule_window=window, now=NOW,
                require_verified_ui_constraints=True, warning_sink=None),
            "与 G1 probe 实测值不一致")
    finally:
        compose_module.cfg = original_cfg
    check(mismatch, "注入数字与已审核 dump 不一致时不能借真 dump 夹带猜测值")

    too_few = InstagramConstraints(
        probe_dump=str(recorder.output_path),
        min_aspect_ratio=0.5, max_aspect_ratio=2.0, max_images=1,
        max_caption_length=1000, caption_length_mode="codepoints",
        max_hashtags=10)
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN, archive_root=root,
                     instagram_constraints=too_few, schedule_window=window,
                     now=NOW, warning_sink=None),
                 "图片数"),
          "注入的 G1 图片数上限会在浏览器前生效")

    base_limits = {
        "probe_dump": str(recorder.output_path),
        "min_aspect_ratio": 0.5,
        "max_aspect_ratio": 2.0,
        "max_images": 5,
        "max_caption_length": 1000,
        "caption_length_mode": "codepoints",
        "max_hashtags": 10,
    }
    cases = [
        ({"max_aspect_ratio": 0.5}, "画幅比"),
        ({"max_caption_length": 1}, "正文长度"),
        ({"max_hashtags": 1}, "标签数"),
    ]
    for changes, expected in cases:
        values = dict(base_limits)
        values.update(changes)
        constrained = InstagramConstraints(**values)
        check(raises(ComposeError,
                     lambda constrained=constrained: compose_post(
                         "fixture-post", WHEN, archive_root=root,
                         instagram_constraints=constrained,
                         schedule_window=window, now=NOW,
                         warning_sink=None),
                     expected),
              "注入的 G1 %s上限会在浏览器前生效" % expected)
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN, archive_root=root,
                     instagram_constraints=limits,
                     schedule_window=ScheduleWindow(
                         str(recorder.output_path),
                         timedelta(hours=1), timedelta(days=2),
                         "America/Los_Angeles"),
                     now=NOW, warning_sink=None),
                 "晚于"),
          "排期超出注入的 G1 UI 上限会被拒绝")
    # composer 的日期选择器**不允许跨月**（2026-09-01 用户实测）。
    # 这条闸没法用 ScheduleWindow 的固定时长表达：上限是日历边界，不是时长。
    wide = ScheduleWindow(str(recorder.output_path), timedelta(0),
                          timedelta(days=60), "America/Los_Angeles")
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
                     archive_root=root, instagram_constraints=limits,
                     schedule_window=wide, now=NOW, warning_sink=None),
                 "只能选当月"),
          "跨月排期被拒 —— 固定时长窗口放行它，日历边界这道闸不放")
    # 关键的是**在哪个时区判月份**：柏林 10-01 06:00 在美西还是 09-30，
    # composer 画的是美西日历，所以这一篇是可以排的。写死柏林或 UTC 都会判错。
    crosses_in_berlin_only = compose_post(
        "fixture-post", datetime.fromisoformat("2026-10-01T06:00:00+02:00"),
        archive_root=root, instagram_constraints=limits,
        schedule_window=wide, now=NOW, warning_sink=None)
    check(crosses_in_berlin_only.post_id == "fixture-post",
          "月份在 UI 时区里判：柏林已跨月但美西仍是本月的时刻照常放行")
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN.replace(tzinfo=None), archive_root=root,
                     warning_sink=None),
                 "显式带时区"),
          "naive datetime 直接拒绝，不依赖本机时区")
    # ⚠️ 这条断言以前靠"仓库里 ui_constraints_verified 恰好是 false"成立。
    # 2026-09-01 那一位被量完之后改成了 true，于是断言跟着失效 ——
    # **把当时的状态写成断言，就是把"现在没上线"当成了不变量。**
    # 现在显式构造"未复核"这个条件，与真实 config 的取值无关。
    original_cfg = compose_module.cfg
    try:
        compose_module.cfg = lambda: VerifiedProbeConfig(
            state_dir, profile, recorder.output_path, verified=False)
        unverified_blocked = raises(
            ComposeError,
            lambda: compose_post(
                "fixture-post", WHEN, archive_root=root,
                require_verified_ui_constraints=True,
                warning_sink=None),
            "ui_constraints_verified")
    finally:
        compose_module.cfg = original_cfg
    check(unverified_blocked,
          "真正发布模式下 ui_constraints_verified 未复核就失败闭合")


print("\n[5] G1 回填：每一条定位都能回查到真实 dump（红线 5 的机器校验）")
# ⚠️ 这一节以前断言的是"selectors.py 里一个常量都没有"——那是 G1 之前的正确状态。
# 用户 2026-09-01 录到真实 dump 之后，那条断言反过来会挡住回填，
# 于是换成**更强**的守法：不是"不许有定位"，而是"每一条都必须查得到出处"。
selector_source = (ROOT / "publish" / "selectors.py").read_text(encoding="utf-8")
check(bool(selectors.REGISTRY), "publish/selectors.py 已按 G1 dump 回填")
missing_source = [key for key, spec in selectors.REGISTRY.items()
                  if not (spec.source_dump and spec.sequences
                          and spec.step and spec.breaks_when)]
check(not missing_source,
      "每条定位都写清了「对应哪一步 / 出自哪份 dump 的第几条 / 什么信号说明它失效」"
      "（缺的：%s）" % missing_source)
check(all(spec.name or spec.attributes for spec in selectors.REGISTRY.values()),
      "没有既没有可访问名、也没有其它稳定属性的空壳定位")

verdicts = verify_all(cfg().state_dir)
bad_evidence = {key: why for key, (ok, why) in verdicts.items() if ok is False}
skipped = [key for key, (ok, _) in verdicts.items() if ok is None]
check(not bad_evidence,
      "逐条回查 dump：没有一条是编出来的（编出来的：%s）" % bad_evidence)
if skipped:
    print("  ..   %d 条无法回查（dump 不进版本库，本机没有）：%s"
          % (len(skipped), "、".join(sorted(skipped))))
else:
    print("  ..   %d 条全部回查到真实 dump" % len(verdicts))

def code_only(source: str) -> str:
    """去掉注释与字符串常量再查。

    说明文字里**引用**反面写法（"不要写 div > div:nth-child(3)"）是有意的，
    项目里已有同款做法（tests_publish 末尾对 ProbeRecorder 那条）。
    这里连字符串一起去掉，是因为反面例子写在模块 docstring 里。
    """
    pieces = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        pieces.append(token.string)
    return "\n".join(pieces)


selector_code = code_only(selector_source)
for token in ("nth-child", "querySelector", "className", "cssPath"):
    check(token not in selector_code,
          "selectors.py 的**代码**里没有 %s 这类随构建期混淆漂移的东西" % token)
check("nth-child" in selector_source,
      "但文档里保留着那个反面例子——下一个人要看得见为什么不这么写")

suite_source = (ROOT / "publish" / "business_suite.py").read_text(encoding="utf-8")
# 唯一允许出现的原始选择器是截图用的凭据遮罩，它不是流程定位器。
raw_locators = suite_source.count("page.locator(")
check(raw_locators >= 1
      and suite_source.count("page.locator(SENSITIVE_INPUT_SELECTOR)") == raw_locators,
      "business_suite.py 里的原始 locator 全部只用于截图遮罩，流程定位走证据登记表")
check("password" in bs.SENSITIVE_INPUT_SELECTOR
      and "contenteditable" in probe_module._SENSITIVE_INPUT_SELECTOR
      and "textarea" in probe_module._SENSITIVE_INPUT_SELECTOR,
      "发布截图遮凭据；probe 的无输入值契约额外遮正文/日期/时间等有值控件")


print("\n[5b] 没录到的东西必须失败闭合，不许猜一个顶上")


class UntouchablePage:
    def __getattr__(self, name):
        raise AssertionError("不该接触 page.%s" % name)


async def gated_errors():
    """⚠️ 这一段必须在**证据被清空**的条件下跑。

    2026-09-01 起本机 `publish/signals_backfilled.py` 已经落地、
    `[publish].ui_probe_dump` 也签了字，三道闸是**开着**的 ——
    那时 `submit()` 本来就该往下走去碰页面。
    要验的是"证据缺失时不碰浏览器"，所以先把登记表清空再验，
    验完原样放回去。**不清就跑，等于把闸打开当成了测试通过。**
    """
    saved_signals = dict(selectors.SIGNALS)
    saved_composer = dict(selectors.COMPOSER)
    selectors.SIGNALS.clear()
    selectors.COMPOSER.pop("composer_submit_button", None)
    messages = []
    try:
        for call in (submit(UntouchablePage()),
                     set_schedule(UntouchablePage(), WHEN, ui_timezone="")):
            try:
                await call
            except ProbeRequired as exc:
                messages.append(str(exc))
    finally:
        selectors.SIGNALS.clear()
        selectors.SIGNALS.update(saved_signals)
        selectors.COMPOSER.clear()
        selectors.COMPOSER.update(saved_composer)
    return messages


gated = asyncio.run(gated_errors())
check(len(gated) == 2,
      "证据登记表被清空时，G6 提交与「UI 时区未实测」都在接触页面前失败闭合")
check(any("红线 5" in item or "不得凭截图" in item for item in gated),
      "至少一条失败信息点明这是红线，不会让维护者误以为可以临时猜一个")
check("composer_submit_button" in gated[0]
      and "composer_success_signal" in gated[0],
      "submit() 明确点名它缺的是提交按钮与成功信号两样")
check(bs.submission_evidence_ready() and bs.readback_evidence_ready(),
      "而在**本机当前状态**下（signals_backfilled.py 已落地 + ui_probe_dump "
      "已签字）三道闸是开着的 —— 上面那两条验的是缺证据时的行为，不是常态")
check("ui_timezone" in gated[1] and "怎么补上" in gated[1],
      "缺 UI 时区时给出可照做的补录命令，而不是拿 [publish].timezone 顶上")

check(raises(KeyError, lambda: bs.locator_for(object(), "dashboard_page_switcher"),
             "composer"),
      "旧版后台那几条定位拿不到 composer 上用（两份 dump 是两个界面）")
check(raises(KeyError, lambda: bs.locator_for(object(), "no_such_key")),
      "没登记过的 key 直接报错，不会静默回退到某个默认定位")

for key in ("composer_submit_button", "composer_success_signal",
            "composer_placement_toggles", "composer_account_context",
            "composer_hours_spinbutton", "composer_upload_thumbnails",
            "composer_file_input", "ui_timezone"):
    gap = selectors.GAPS.get(key)
    check(gap is not None and gap.why_missing and gap.blocks and gap.how_to_close,
          "缺口 %s 写清了「为什么没有 / 挡住了谁 / 怎么补上」" % key)


print("\n[6] G1 recorder 记录稳定属性并逐步截图，但没有驱动页面代码")
script = install_script("fixture-session")
for token, label in [
        ("tag", "tag"), ("role", "role"), ("aria-label", "aria-label"),
        ("data-testid", "data-testid"), ("name", "name"),
        ("placeholder", "placeholder"), ("visible_text", "可见文本"),
        ("is_contenteditable", "contenteditable"), ("accept", "file accept"),
        ("multiple", "file multiple")]:
    check(token in script, "监听脚本会记录 %s" % label)
check("addEventListener" in script and "candidates" in script,
      "工具监听人工事件，并记录命中元素到语义祖先链")
check("isTrusted" in script and "sensitiveTarget" in script,
      "监听器只接收浏览器标记的真人事件，并跳过敏感输入")
check("className" not in script and "cssPath" not in script,
      "dump 不记录混淆 class 或脆 CSS path")
check(all(forbidden not in script for forbidden in (
    ".click(", ".fill(", ".goto(", "setInputFiles(",
    "document.querySelector(", "document.querySelectorAll(")),
    "监听脚本没有点击/填写/导航/上传等页面驱动")
check("createTreeWalker" in script and "isVisible(parent)" in script,
      "交互祖先文本只遍历真实可见且非 editable 的 text node，隐藏 Boost 不会提前泄漏")
with tempfile.TemporaryDirectory() as d:
    temp = Path(d)
    recorder, page = asyncio.run(make_completed_probe(
        temp / "state", temp / "publish-profile"))
    data = json.loads(recorder.output_path.read_text(encoding="utf-8"))
    item = data["interactions"][0]
    check(recorder.output_path.name == "publish_probe_20260831_120000.json",
          "dump 文件名符合 state/publish_probe_<时间戳>.json 契约")
    check(len(data["interactions"]) == 7 and item["sequence"] == 1,
          "每次人工交互按顺序持久化")
    check(Path(item["screenshot"]).is_file() and item["screenshot_error"] is None,
          "每条交互都有对应截图")
    check(data["observations"]["ui_timezone"] == PROBE_OBSERVATIONS["ui_timezone"],
          "时区/窗口等人工观察与事件 dump 存在同一份记录里")
    serialized = json.dumps(data, ensure_ascii=False)
    check("must not persist" not in serialized and "access_token" not in serialized,
          "Python 侧白名单会丢弃任意注入字段，并从 URL 移除 query/fragment")
    check(item["page_url"] == "https://business.example.invalid/create"
          and item["target"]["href"] == "https://business.example.invalid/action",
          "页面 URL 与 href 只保留稳定的 scheme/host/path")
    check(bool(page.mask_selectors) and all(
              "contenteditable" in value and "input" in value
              for value in page.mask_selectors),
          "probe 每步截图遮罩正文、日期/时间和全部输入控件")
    check(data["schema_version"] == 2
          and data["mode"] == "record-and-passive-evidence"
          and data["finished_at"],
          "dump 明确标记 v2 交互+被动证据模式，并在正常收尾时写完成时间")


async def rejected_probe_payloads(state_dir: Path):
    recorder = ProbeRecorder(
        state_dir, port=9223, profile=Path("publish-profile"),
        timestamp="20260831_130000")
    page = FakePage()
    base = {
        "session_id": recorder.session_id,
        "event_type": "input",
        "is_trusted": False,
        "target": {"tag": "input", "input_type": "text"},
        "candidates": [],
    }
    synthetic = await recorder.record(page, base)
    password = dict(base)
    password["is_trusted"] = True
    password["target"] = {"tag": "input", "input_type": "password"}
    sensitive = await recorder.record(page, password)
    await recorder.finish()
    return synthetic, sensitive, recorder


with tempfile.TemporaryDirectory() as d:
    synthetic, sensitive, rejected = asyncio.run(
        rejected_probe_payloads(Path(d) / "state"))
    rejected_data = json.loads(rejected.output_path.read_text(encoding="utf-8"))
    check(not synthetic and not sensitive and not rejected_data["interactions"],
          "合成事件与密码输入即使直接调用 exposed binding 也不会持久化或截图")


print("\n[7][CR-64] 监听器改走 CDP 安装，且**装不上必须报出来**")

# 背景：用户 2026-09-01 走完整个发帖流程后才发现 dump 是空的。
# 实测那个 Business Suite 标签页上 Playwright 的 page 对象是坏的：
#   page.url='' / frames=1 个空帧 / page.evaluate('1+1') -> TimeoutError
#   expose_binding 之后 typeof window[binding] -> undefined
# 而**同一个 target 的原始 CDP 完全正常**。旧的 _install_existing 用
# `except Exception: continue` 把这个 TimeoutError 整个吞掉，于是一条都没记，
# 全程零提示。下面每条断言都指向"这种静默不许回来"。

import tools.probe_publish as probe_module  # noqa: E402


class FakeCDPSession:
    def __init__(self, verify="object", fail_on=()):
        self.sent = []
        self.handlers = {}
        self.verify = verify
        self.fail_on = set(fail_on)
        self.detached = False

    def on(self, event, handler):
        self.handlers[event] = handler

    def remove_listener(self, event, handler):
        if self.handlers.get(event) is handler:
            self.handlers.pop(event, None)

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method in self.fail_on:
            raise RuntimeError("boom:%s" % method)
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            if "filter:blur" in expr:
                return {"result": {"value": True}}
            if "typeof window." in expr and "Handlers" in expr:
                return {"result": {"value": self.verify}}
            return {"result": {"value": None}}
        if method == "Page.captureScreenshot":
            import base64 as _b64
            buf = io.BytesIO()
            Image.new("RGB", (8, 6), (4, 5, 6)).save(buf, format="PNG")
            return {"data": _b64.b64encode(buf.getvalue()).decode()}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            return {"identifier": "probe-script-1"}
        return {}

    async def detach(self):
        self.detached = True


class FakeCtx:
    def __init__(self, session):
        self.session = session

    async def new_cdp_session(self, _page):
        return self.session


async def _install(verify="object", fail_on=()):
    sess = FakeCDPSession(verify=verify, fail_on=fail_on)
    out = await probe_module.install_on_page(
        FakeCtx(sess), FakePage(), "SCRIPT", lambda *_a: None)
    return sess, out


sess, (_s, ok, detail) = asyncio.run(_install())
methods = [m for m, _ in sess.sent]
check(ok and detail == "ok", "正常情况下安装成功")
check("Runtime.enable" in methods and "Page.enable" in methods,
      "**先 enable Runtime 与 Page 域**：实测不 enable 时首个文档一切正常，"
      "一导航就再也收不到 bindingCalled —— 症状和这次的 bug 一模一样")
check(methods.index("Runtime.enable") < methods.index("Runtime.addBinding"),
      "enable 必须在 addBinding 之前，否则新执行上下文不会被跟踪")
check("Runtime.addBinding" in methods,
      "用 CDP 的 Runtime.addBinding 做通路，不依赖 Playwright 的 expose_binding —— "
      "后者在坏掉的 page 对象上是 undefined")
check("Page.addScriptToEvaluateOnNewDocument" in methods,
      "覆盖后续顶层文档；子 frame 由 top-frame 闸跳过")
check(any(m == "Runtime.evaluate" and p.get("expression") == "SCRIPT"
          for m, p in sess.sent),
      "**同时对当前文档注入一次**：SPA 客户端路由不产生新文档，只靠 init script 会漏")
check("Runtime.bindingCalled" in sess.handlers,
      "挂上 bindingCalled 监听，事件才有地方回来")

_, (_s2, ok2, detail2) = asyncio.run(_install(verify="undefined"))
check(not ok2 and "回读不到" in detail2,
      "**注入后回读不到标记就算失败**，不许假装装上了 —— "
      "旧实现连回读都没有，装没装上全靠猜")
_, (_s3, ok3, detail3) = asyncio.run(_install(fail_on=("Runtime.addBinding",)))
check(not ok3 and "RuntimeError" in detail3,
      "任何一步抛异常都如实带回原因，不再 except: continue 吞掉")

# 只看代码，不看 docstring —— 那段说明里**引用**了旧写法，是有意保留的。
src = inspect.getsource(probe_module.install_on_page).split('"""')[-1]
check("except Exception: continue" not in src,
      "install_on_page 的代码里不许再出现吞掉一切的 except: continue")
check("JSON.stringify" in probe_module.INSTALL_FUNCTION,
      "页面侧传 JSON 字符串 —— CDP 的 addBinding 只接受 string 参数")
check("window !== window.top" in probe_module.INSTALL_FUNCTION,
      "DOM listener 只装顶层文档；iframe 整体遮罩，停止时不会遗留跨源 frame timer")
check(probe_module._safe_payload(json.dumps(
        {"session_id": "s", "is_trusted": True, "event_type": "click",
         "target": {"tag": "button"}}), "s") is not None,
      "_safe_payload 收得下 JSON 字符串（新通路的实际形态）")
check(probe_module._safe_payload("{坏 json", "s") is None,
      "坏 JSON 被丢弃而不是抛异常炸掉 binding 回调")
check(probe_module._safe_payload(json.dumps(
        {"session_id": "别人", "is_trusted": True, "event_type": "click",
         "target": {"tag": "button"}}), "s") is None,
      "session_id 对不上仍然拒收 —— 换了通路，这道闸不能松")

# 截图：CDP 是真实录制主路径，隐私边界必须一起带过去
with tempfile.TemporaryDirectory() as d:
    shot = Path(d) / "s.png"
    sess4 = FakeCDPSession()
    okk, det = asyncio.run(probe_module._cdp_screenshot(sess4, shot))
    exprs = [p.get("expression", "") for m, p in sess4.sent if m == "Runtime.evaluate"]
    check(okk and shot.is_file() and shot.stat().st_size > 0,
          "CDP 截图能真的写出 PNG")
    check(any("blur" in e for e in exprs),
          "**回退路径也要遮罩敏感输入**：Playwright 的 mask= 用不了，"
          "改成临时插一条 CSS 把正文、日期/时间与输入控件模糊掉")
    check(any("remove()" in e for e in exprs), "截完把临时样式撤掉，不留痕迹")

    sess5 = FakeCDPSession(fail_on=("Runtime.evaluate",))
    shot2 = Path(d) / "s2.png"
    ok5, det5 = asyncio.run(probe_module._cdp_screenshot(sess5, shot2))
    check(not ok5 and not shot2.exists(),
          "**遮罩插不进去就不截图** —— 宁可没有截图，也不能把敏感输入拍进去")


class MaskExceptionSession(FakeCDPSession):
    async def send(self, method, params=None):
        expression = (params or {}).get("expression", "")
        if method == "Runtime.evaluate" and "filter:blur" in expression:
            self.sent.append((method, params or {}))
            return {"exceptionDetails": {"text": "documentElement unavailable"}}
        return await super().send(method, params)


with tempfile.TemporaryDirectory() as d:
    mask_exception_session = MaskExceptionSession()
    mask_exception_ok, _mask_exception_detail = asyncio.run(
        probe_module._cdp_screenshot(
            mask_exception_session, Path(d) / "exception.png"))
    check(not mask_exception_ok
          and not any(method == "Page.captureScreenshot"
                      for method, _params in mask_exception_session.sent),
          "Runtime.evaluate 以 exceptionDetails 返回时按遮罩失败闭合，不拍未遮罩页面")
    check("iframe" in probe_module._SENSITIVE_INPUT_SELECTOR
          and "plaintext-only" in probe_module._SENSITIVE_INPUT_SELECTOR,
          "截图整体遮住跨域 iframe，并覆盖标准 contenteditable=plaintext-only")


class CancelDuringMaskSession(FakeCDPSession):
    def __init__(self):
        super().__init__()
        self.mask_present = False
        self.add_started = asyncio.Event()

    async def send(self, method, params=None):
        expression = (params or {}).get("expression", "")
        self.sent.append((method, params or {}))
        if method == "Runtime.evaluate" and "filter:blur" in expression:
            # 模拟浏览器已执行插入，但 Python 还在等 CDP response 时被 cancel。
            self.mask_present = True
            self.add_started.set()
            await asyncio.Event().wait()
        if method == "Runtime.evaluate" and "let s; let n" in expression:
            self.mask_present = False
            return {"result": {"value": 1}}
        return await super().send(method, params)


async def _cancel_during_mask(folder: Path):
    session = CancelDuringMaskSession()
    task = asyncio.create_task(
        probe_module._cdp_screenshot(session, folder / "cancelled.png"))
    await session.add_started.wait()
    task.cancel()
    cancelled = False
    try:
        await task
    except asyncio.CancelledError:
        cancelled = True
    return cancelled, session.mask_present, session.sent


with tempfile.TemporaryDirectory() as d:
    mask_cancelled, mask_left, mask_calls = asyncio.run(
        _cancel_during_mask(Path(d)))
    check(mask_cancelled and not mask_left
          and any("let s; let n" in params.get("expression", "")
                  for method, params in mask_calls if method == "Runtime.evaluate"),
          "cancel 落在遮罩插入 await 中也会先撤掉页面遮罩，再保留取消语义")

page = FakePage()
_rec = ProbeRecorder(Path(tempfile.mkdtemp()), port=1, profile=Path("."))
asyncio.run(_rec.record(page, {"session_id": _rec.session_id, "is_trusted": True,
                               "event_type": "click", "target": {"tag": "b"}}))
check(page.screenshot_timeouts and page.screenshot_timeouts[0] is not None,
      "没有 CDP session 时 Playwright 后备截图仍有短超时，不会无限挂住")

with tempfile.TemporaryDirectory() as d:
    page_cdp = FakePage()
    session_cdp = FakeCDPSession()
    recorder_cdp = ProbeRecorder(Path(d), port=1, profile=Path("."))
    accepted_cdp = asyncio.run(recorder_cdp.record(
        page_cdp,
        {"session_id": recorder_cdp.session_id, "is_trusted": True,
         "event_type": "click", "target": {"tag": "button"}},
        session=session_cdp))
    cdp_row = recorder_cdp.data["interactions"][0]
    check(accepted_cdp and not page_cdp.screenshot_timeouts
          and any(method == "Page.captureScreenshot"
                  for method, _params in session_cdp.sent)
          and Path(cdp_row["screenshot"]).is_file(),
          "有 CDP session 时直接截图，不再先耗满 Playwright 超时导致画面排队错位")


class SemanticCDPSession(FakeCDPSession):
    async def send(self, method, params=None):
        expression = (params or {}).get("expression", "")
        if method == "Runtime.evaluate" and "semantic_items" in expression:
            self.sent.append((method, params or {}))
            return {"result": {"value": {
                "page_url": "https://business.facebook.com/latest/content_calendar",
                "document_title": "Planner",
                "semantic_items": [{
                    "tag": "div", "role": "dialog",
                    "accessible_name": "Boost your scheduled post",
                    "visible_text": "Boost your scheduled post",
                }],
            }}}
        return await super().send(method, params)


with tempfile.TemporaryDirectory() as d:
    recorder_semantic = ProbeRecorder(Path(d), port=1, profile=Path("."))
    semantic_session = SemanticCDPSession()
    changed = asyncio.run(recorder_semantic.snapshot(
        semantic_session, reason="periodic", page_id="page-boost"))
    duplicate = asyncio.run(recorder_semantic.snapshot(
        semantic_session, reason="periodic", page_id="page-boost"))
    semantic_row = recorder_semantic.data["snapshots"][0]
    check(changed and not duplicate and Path(semantic_row["screenshot"]).is_file(),
          "URL/语义首次变化自动截图，能留下落地主页与 Boost 建议；完全相同快照仍去重")


class DynamicButtonSession(FakeCDPSession):
    def __init__(self):
        super().__init__()
        self.label = "Button A"

    async def send(self, method, params=None):
        expression = (params or {}).get("expression", "")
        if method == "Runtime.evaluate" and "semantic_items" in expression:
            self.sent.append((method, params or {}))
            return {"result": {"value": {
                "page_url": "https://business.facebook.com/latest/home",
                "document_title": "Home",
                "semantic_items": [{"tag": "button", "role": "button",
                                    "accessible_name": self.label,
                                    "visible_text": self.label}],
            }}}
        return await super().send(method, params)


with tempfile.TemporaryDirectory() as d:
    dynamic_recorder = ProbeRecorder(Path(d), port=1, profile=Path("."))
    dynamic_session = DynamicButtonSession()
    asyncio.run(dynamic_recorder.snapshot(
        dynamic_session, reason="periodic", page_id="page-dynamic"))
    dynamic_session.label = "Button B"
    asyncio.run(dynamic_recorder.snapshot(
        dynamic_session, reason="periodic", page_id="page-dynamic"))
    dynamic_rows = dynamic_recorder.data["snapshots"]
    check(len(dynamic_rows) == 2 and dynamic_rows[0]["screenshot"]
          and not dynamic_rows[1]["screenshot"],
          "普通动态 button/link 变化仍落语义但不反复截图；URL/dialog/status/Planner 才触发视觉证据")

with tempfile.TemporaryDirectory() as d:
    fallback_recorder = ProbeRecorder(Path(d), port=1, profile=Path("."))
    fallback_session = SemanticCDPSession(fail_on=("Page.captureScreenshot",))
    fallback_page = FakePage()
    fallback_changed = asyncio.run(fallback_recorder.snapshot(
        fallback_session, reason="periodic", page_id="page-fallback",
        page=fallback_page))
    fallback_row = fallback_recorder.data["snapshots"][0]
    check(fallback_changed and fallback_page.screenshot_timeouts == [3000]
          and Path(fallback_row["screenshot"]).is_file(),
          "被动 URL/Boost/Planner 的 CDP 图失败时也走 3 秒 Playwright 隐私遮罩后备")

with tempfile.TemporaryDirectory() as d:
    retry_recorder = ProbeRecorder(Path(d), port=1, profile=Path("."))
    retry_session = SemanticCDPSession(fail_on=("Page.captureScreenshot",))
    first_failed = asyncio.run(retry_recorder.snapshot(
        retry_session, reason="periodic", page_id="page-retry"))
    second_retry = asyncio.run(retry_recorder.snapshot(
        retry_session, reason="periodic", page_id="page-retry"))
    capture_attempts = sum(
        method == "Page.captureScreenshot" for method, _params in retry_session.sent)
    check(first_failed and second_retry and capture_attempts == 2
          and len(retry_recorder.data["snapshots"]) == 2,
          "视觉截图失败不推进已捕获指纹；相同 Boost/toast 状态下一轮会自动重试")


class QueuedSemanticSession(FakeCDPSession):
    def __init__(self):
        super().__init__()
        self.semantic_calls = 0

    async def send(self, method, params=None):
        expression = (params or {}).get("expression", "")
        if method == "Runtime.evaluate" and "semantic_items" in expression:
            self.semantic_calls += 1
            name = "OLD Boost" if self.semantic_calls == 1 else "Planner card scheduled"
            role = "dialog" if self.semantic_calls == 1 else "status"
            return {"result": {"value": {
                "page_url": "https://business.facebook.com/latest/content_calendar",
                "document_title": "Planner",
                "semantic_items": [{"tag": "div", "role": role,
                                    "accessible_name": name, "visible_text": name}],
            }}}
        return await super().send(method, params)


with tempfile.TemporaryDirectory() as d:
    queued_recorder = ProbeRecorder(Path(d), port=1, profile=Path("."))
    queued_session = QueuedSemanticSession()
    asyncio.run(queued_recorder.snapshot(
        queued_session, reason="periodic", page_id="page-queued"))
    queued_text = json.dumps(queued_recorder.data["snapshots"], ensure_ascii=False)
    check("Planner card scheduled" in queued_text and "OLD Boost" not in queued_text,
          "语义在锁内紧邻截图重采：排队前的旧状态不会配到稍后的新页面图片")

check(inspect.iscoroutinefunction(probe_module.cdp_page_targets),
      "有一条直接问 CDP 要 page 目标的路 —— 用来核对 Playwright 有没有漏页")

# 监听器"不知为何就没了"是这一轮最难复现的一类：scratch 环境怎么试都正常，
# 用户真机上就是丢。与其继续猜是哪一种原因，不如让它**自己好起来**。
sessN, (_sn, okN, _dn) = asyncio.run(_install())
check(okN and "Page.frameNavigated" in sessN.handlers,
      "装完注册 Page.frameNavigated：主帧一导航就**立刻**补注入一次，"
      "不用等巡检那 2 秒")


async def _exercise_callback_boundaries():
    session = FakeCDPSession()
    accept = {"on": True}
    stop = {"on": False}
    seen = []
    payload_tasks = set()
    navigation_tasks = set()
    sessions = set()
    callbacks = {}
    instrumentation = {}

    async def receive(_page, payload):
        seen.append(payload)

    _session, installed_ok, _detail = await probe_module.install_on_page(
        FakeCtx(session), FakePage(), "SCRIPT", receive,
        task_tracker=payload_tasks,
        navigation_task_tracker=navigation_tasks,
        session_tracker=sessions,
        callback_tracker=callbacks,
        instrumentation_tracker=instrumentation,
        accepting=lambda: accept["on"],
        stopping=lambda: stop["on"])
    binding = session.handlers["Runtime.bindingCalled"]
    binding({"name": probe_module.BINDING_NAME, "payload": "before-enter"})
    accept["on"] = False
    binding({"name": probe_module.BINDING_NAME, "payload": "after-enter"})
    await asyncio.sleep(0)
    before_navigation = len([
        1 for method, params in session.sent
        if method == "Runtime.evaluate" and params.get("expression") == "SCRIPT"])
    stop["on"] = True
    session.handlers["Page.frameNavigated"]({"frame": {"id": "main"}})
    await asyncio.sleep(0)
    after_navigation = len([
        1 for method, params in session.sent
        if method == "Runtime.evaluate" and params.get("expression") == "SCRIPT"])
    probe_module._remove_tracked_callbacks(callbacks, session)
    await probe_module._disable_probe_instrumentation(
        session, instrumentation.pop(session, None))
    removal_methods = [method for method, _params in session.sent]
    return (installed_ok, seen, sessions == {session}, not session.handlers,
            before_navigation, after_navigation, removal_methods)


(callback_ok, callback_seen, callback_session_tracked, callbacks_removed,
 nav_before, nav_after, removal_methods) = asyncio.run(
     _exercise_callback_boundaries())
check(callback_ok and callback_seen == ["before-enter"],
      "binding 到达 Python 时同步 admission：Enter 前事件进入 drain，Enter 后事件当场拒绝")
check(callback_session_tracked and callbacks_removed,
      "半初始化起就登记 session/callback，停止时能统一移除而不是只管成功安装表")
check(nav_before == nav_after,
      "停止闸关闭后 frameNavigated 不会重新注入页面 listener")
check("Page.removeScriptToEvaluateOnNewDocument" in removal_methods
      and "Runtime.removeBinding" in removal_methods,
      "停止会撤销 init-script 与 CDP binding，之后再导航也不会复活 listener")


class DisableNavigationRaceSession(FakeCDPSession):
    def __init__(self):
        super().__init__()
        self.init_script = True
        self.binding = True
        self.listener = True

    async def send(self, method, params=None):
        if method == "Page.removeScriptToEvaluateOnNewDocument":
            # 模拟撤注册前那一瞬间发生导航：只要 init 仍有效，新 DOM 就会装监听。
            if self.init_script:
                self.listener = True
            self.init_script = False
            return {}
        if method == "Runtime.removeBinding":
            self.binding = False
            return {}
        if method == "Runtime.evaluate" and "removeEventListener" in (
                (params or {}).get("expression", "")):
            self.listener = False
            return {"result": {"value": True}}
        return await super().send(method, params)


race_session = DisableNavigationRaceSession()
asyncio.run(probe_module._disable_probe_instrumentation(race_session, {
    "binding_name": probe_module.BINDING_NAME,
    "new_document_ids": ["probe-script-1"],
}))
check(not race_session.init_script and not race_session.binding
      and not race_session.listener,
      "先撤 init-script/binding、最后清 DOM，导航夹在停止两步之间也不会遗留 listener")

sess_re = FakeCDPSession()
asyncio.run(probe_module._reinject(sess_re, "SCRIPT"))
check(any(m == "Runtime.evaluate" and p.get("expression") == "SCRIPT"
          for m, p in sess_re.sent),
      "_reinject 真的把脚本再打一遍")
sess_bad = FakeCDPSession(fail_on=("Runtime.evaluate",))
asyncio.run(probe_module._reinject(sess_bad, "SCRIPT"))
check(True, "_reinject 失败时不抛异常 —— 巡检那一层还会再兜一次")

run_src = inspect.getsource(probe_module.run_probe)
check("repair_if_lost" in run_src and "REGISTRY_NAME" in run_src,
      "**巡检会回读监听器标记并就地补装**：不管因为什么丢的（跨进程导航、"
      "装到一半页面跳走、page 对象本身是坏的），每 4 个采样 tick 发起自愈")
check("installed[page]" in run_src and "installed[id(" not in run_src
      and "installed.get(id(" not in run_src,
      "已装表用 page 对象本身做键 —— id() 会在对象回收后重用，"
      "那会让一个新页面被误当成'已经装过了'而跳过"
      "（注释里提到 id(page) 是有意的，所以只查真正的取值写法）")
check("installation_tasks" in run_src and "start_install" in run_src
      and "all_sessions" in run_src and "session_callbacks" in run_src,
      "新页回调与 sweep 共用每页安装单飞，且所有成功/失败 session 统一归属")
check("next_tick += 0.5" in run_src and "cycle % 4 == 0" in run_src
      and "observation_tasks" in run_src and "start_observation" in run_src,
      "语义观察按单调时钟每 0.5 秒发起且每页单飞合并；监听修复每 4 个 tick 发起")


print("\n[8][CR-66] 「按 Enter 停止记录」要真的停；观察项不再挡在出口")

# 两次真机教训：第一次 Enter 后还继续收事件；第二次每张 Playwright 图先耗满
# 8 秒，积压任务挡在 final/finished_at 前，Enter 虽被读到却像没有停止。
check('recording["on"] = False' in run_src,
      "按 Enter 之后**真的关闸**：receive 直接丢弃后续事件")
check("session.detach()" in run_src,
      "并且断开各页面的 CDP 会话，浏览器不再回送事件")
check("_wait_for_stop_enter" in run_src and "_read_line(" not in
      run_src[run_src.index("按 Enter 停止记录：") - 100:
              run_src.index("按 Enter 停止记录：") + 100],
      "停止键走独立守护 stdin 线程，不再依赖 executor 里的 input() 收尾")
release_stdin = threading.Event()


def _blocked_readline():
    release_stdin.wait(1)
    return "\n"


stdin_done, stdin_thread = probe_module._stdin_stop_waiter(
    readline=_blocked_readline)
check(stdin_thread.daemon and not stdin_done.is_set(),
      "Enter 等待线程是 daemon；等待期间不会误报已停止")
release_stdin.set()
stdin_thread.join(timeout=1)
check(stdin_done.is_set(), "读到 Enter 后停止信号立即置位")

check("await asyncio.sleep(0.9)" not in run_src
      and run_src.index('recording["on"] = False')
      < run_src.index("stop_sweep.set()"),
      "Enter 后先关接收闸门，不再额外开放 0.9 秒让截图继续排队")
check("_disable_probe_instrumentation" in run_src and "_drain_tasks" in run_src
      and "timeout=5.0" in run_src,
      "页面 listener/timer/init-script/binding 会拆除，Enter 前任务最多再等 5 秒")
check("latest_page_id" in run_src and "capture_finals(), timeout=10" in run_src,
      "final 优先最近活动页面，全部旧标签页合计最多等待 10 秒")
check("last_admitted_page" in run_src
      and run_src.index('if last_admitted_page["page"] is not None')
      < run_src.index("elif evidence_rows:"),
      "final 主页面取最后一条获准真人交互，不会被后台周期快照抢走优先级")
check("detach_session" in run_src and "pw.stop(), timeout=5" in run_src,
      "CDP sessions 并行限时断开，Playwright 自身停止也有 5 秒上限")
check(run_src.index('recording["on"] = False') < run_src.index("if collect_notes:"),
      "关闸发生在问观察项**之前** —— 这正是死锁的来源")


async def _exercise_stop_drain():
    finished = {"fast": False}

    async def fast():
        await asyncio.sleep(0)
        finished["fast"] = True

    async def stuck():
        await asyncio.Event().wait()

    tasks = {asyncio.create_task(fast()), asyncio.create_task(stuck())}
    done_count, cancelled_count = await probe_module._drain_tasks(
        tasks, timeout=0.02)
    return finished["fast"], done_count, cancelled_count, all(t.done() for t in tasks)


fast_finished, done_count, cancelled_count, all_done = asyncio.run(
    _exercise_stop_drain())
check(fast_finished and done_count == 1 and cancelled_count == 1 and all_done,
      "停止收尾保留已经能完成的事件，并取消挂住任务，不阻塞 final/finished_at")


async def _exercise_cancel_resistant_drain():
    release = asyncio.Event()

    async def resistant():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(resistant())
    started = asyncio.get_running_loop().time()
    done_count, cancelled_count = await probe_module._drain_tasks(
        {task}, timeout=0.04)
    elapsed = asyncio.get_running_loop().time() - started
    still_running = not task.done()
    release.set()
    await task
    return done_count, cancelled_count, elapsed, still_running


(resistant_done, resistant_cancelled, resistant_elapsed,
 resistant_was_running) = asyncio.run(_exercise_cancel_resistant_drain())
check(resistant_done == 0 and resistant_cancelled == 1 and resistant_was_running
      and resistant_elapsed < 0.12,
      "抗拒第一次 cancel 的任务也受同一绝对 deadline 约束，不再进入无界 gather")


async def _exercise_hard_wait():
    release = asyncio.Event()

    async def resistant():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    started = asyncio.get_running_loop().time()
    completed, _value = await probe_module._hard_wait(
        resistant(), timeout=0.02)
    elapsed = asyncio.get_running_loop().time() - started
    release.set()
    await asyncio.sleep(0)
    return completed, elapsed


hard_completed, hard_elapsed = asyncio.run(_exercise_hard_wait())
check(not hard_completed and hard_elapsed < 0.1,
      "final/detach/pw.stop 的硬 deadline 不等待吞掉 CancelledError 的第三方 coroutine")

stop_session = FakeCDPSession()
asyncio.run(probe_module._stop_page_listeners(stop_session))
stop_expressions = [params.get("expression", "") for method, params in stop_session.sent
                    if method == "Runtime.evaluate"]
check(any("removeEventListener" in expression and "clearTimeout" in expression
          and probe_module._MASK_STYLE_ID in expression
          for expression in stop_expressions),
      "Enter 会移除 listener/input debounce 并防御性清残留遮罩，不改变业务页面")

with tempfile.TemporaryDirectory() as d:
    closing_recorder = ProbeRecorder(Path(d), port=1, profile=Path("."))
    closing_recorder.close_admission()
    late_accepted = asyncio.run(closing_recorder.record(
        FakePage(), {"session_id": closing_recorder.session_id,
                     "is_trusted": True, "event_type": "click",
                     "target": {"tag": "button"}}))
    asyncio.run(closing_recorder.finish(timeout=0.02))
    check(not late_accepted and closing_recorder.data["finished_at"]
          and not closing_recorder.data["interactions"],
          "final 后关闭 recorder admission，迟到 task 不能在 finished_at 后追加 evidence")


class DelayedCaptureSession(FakeCDPSession):
    def __init__(self):
        super().__init__()
        self.capture_started = asyncio.Event()
        self.release_capture = asyncio.Event()

    async def send(self, method, params=None):
        if method == "Page.captureScreenshot":
            self.capture_started.set()
            await self.release_capture.wait()
        return await super().send(method, params)


async def _close_while_capture_in_flight(folder: Path):
    recorder = ProbeRecorder(folder, port=1, profile=Path("."))
    session = DelayedCaptureSession()
    task = asyncio.create_task(recorder.record(
        FakePage(), {"session_id": recorder.session_id, "is_trusted": True,
                     "event_type": "click", "target": {"tag": "button"}},
        session=session))
    await session.capture_started.wait()
    recorder.close_admission()
    session.release_capture.set()
    accepted = await task
    return accepted, recorder.data["interactions"]


with tempfile.TemporaryDirectory() as d:
    in_flight_accepted, in_flight_rows = asyncio.run(
        _close_while_capture_in_flight(Path(d)))
    check(not in_flight_accepted and not in_flight_rows,
          "final 边界在截图 await 期间关闭时，迟到恢复的任务也不会追加 evidence")


async def _finish_with_stuck_lock(folder: Path):
    recorder = ProbeRecorder(folder, port=1, profile=Path("."))
    await recorder._lock.acquire()
    normal_lock_path = await recorder.finish(timeout=0.01)
    recorder._lock.release()
    on_disk = json.loads(recorder.output_path.read_text(encoding="utf-8"))
    return normal_lock_path, on_disk.get("finished_at")


with tempfile.TemporaryDirectory() as d:
    finish_normal, forced_finished_at = asyncio.run(
        _finish_with_stuck_lock(Path(d)))
    check(not finish_normal and forced_finished_at,
          "recorder 锁异常未释放时 finished_at 仍在有限时间内原子落盘")


async def _runner_leaves_resistant_task():
    async def resistant():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()
    asyncio.create_task(resistant())
    await asyncio.sleep(0)
    return "done"


runner_started = time.monotonic()
runner_value = probe_module._run_with_bounded_shutdown(
    _runner_leaves_resistant_task())
check(runner_value == "done" and time.monotonic() - runner_started < 1.2,
      "probe runner 关环最多再等 0.5 秒，不被遗留抗取消 task 拖住进程")

sig = inspect.signature(probe_module.run_probe)
check(sig.parameters["collect_notes"].default is False,
      "**默认不问那 20 个观察项**：录交互和量 UI 边界是两件事、两个时间点，"
      "捆在一起只会逼人一路回车跳过，填出来的是假数据，比空着更糟")
argsrc = inspect.getsource(probe_module._parse_args)
check("--fill-notes" in argsrc and "--notes" in argsrc,
      "给出两条路：--notes 就地问，--fill-notes 事后补填")

# 事后补填：不连浏览器，改完交互记录一条不少
with tempfile.TemporaryDirectory() as d:
    dump = Path(d) / "publish_probe_x.json"
    asyncio.run(make_completed_probe(Path(d), Path(d) / "prof"))
    made = sorted(Path(d).glob("publish_probe_*.json"))[0]
    dump.write_text(made.read_text(encoding="utf-8"), encoding="utf-8")
    # 清空观察项，才测得到"缺必填项要如实列出来"那条
    _blank = json.loads(dump.read_text(encoding="utf-8"))
    _blank["observations"] = {}
    dump.write_text(json.dumps(_blank, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    before = json.loads(dump.read_text(encoding="utf-8"))
    answers = iter(["https://ok.invalid/x"] + [""] * 40)

    async def fake_read(prompt):
        return next(answers, "")

    real_read = probe_module._read_line
    probe_module._read_line = fake_read
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = asyncio.run(probe_module.fill_notes(dump))
    finally:
        probe_module._read_line = real_read
    after = json.loads(dump.read_text(encoding="utf-8"))
    check(rc == 0, "fill_notes 正常返回，不挂住")
    check(after["interactions"] == before["interactions"],
          "**只动 observations，交互记录一条不改** —— 补填不该有机会毁掉录制成果")
    check(after["observations"].get("business_suite_entry_url")
          == "https://ok.invalid/x", "填进去的值确实写回了")
    check("还缺" in buf.getvalue(),
          "缺必填项时如实列出来，而不是让人以为可以 --strict 了")
    rc2 = asyncio.run(probe_module.fill_notes(Path(d) / "nope.json"))
    check(rc2 == 1, "dump 不存在时返回 1，不抛裸异常")

missing_required = [k for k in compose_module._PROBE_REQUIRED_OBSERVATIONS
                    if k not in probe_module.OBSERVATION_PROMPTS]
check(not missing_required,
      "问答覆盖 compose 要求的**全部**必填观察项——"
      "少问一个，用户就会在 --strict 那一步才发现（缺 %s）" % missing_required)

# 中文观察项曾经直接把整份 dump 写崩（孤立代理字符）
check(probe_module.console_text("直接输入可以") == "直接输入可以",
      "正常中文原样通过")
broken = "拒绝".encode("gbk").decode("utf-8", "surrogateescape")
check(probe_module.console_text(broken) == "拒绝",
      "**控制台留下的孤立代理字符能按本机编码还原**——"
      "不修的话，观察项里写中文会让 json 写盘直接 UnicodeEncodeError")
check(probe_module.console_text(broken).encode("utf-8"),
      "还原之后必须是能 utf-8 编码的字符串，否则写盘还是会炸")
with tempfile.TemporaryDirectory() as d:
    rec = ProbeRecorder(Path(d), port=1, profile=Path(d))
    rec.data["observations"] = {"extra_notes": "\udcaf"}   # 强行塞一个坏字符
    rec._write()
    check(rec.output_path.is_file() and rec.output_path.stat().st_size > 0,
          "**兜底**：真有编不出来的字符时也要写得出文件，"
          "宁可换掉那几个字符，也不能让整份 dump 丢掉")


print("\n[9][CR-67] 一次失败的记录不许把 sequence 永久空掉")


class ExplodingPage(FakePage):
    """第 2 次截图直接炸，模拟中途失败/任务被取消。"""

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def screenshot(self, *, path, full_page, mask, timeout=None):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("boom")
        Image.new("RGB", (8, 6), (1, 2, 3)).save(path, format="PNG")


async def _seq_probe(page):
    rec = ProbeRecorder(Path(tempfile.mkdtemp()), port=1, profile=Path("."))
    for index in range(4):
        await rec.record(page, {
            "session_id": rec.session_id, "is_trusted": True,
            "event_type": "click", "target": {"tag": "button"},
            "client_timestamp": "2026-09-01T00:00:0%dZ" % index,
        })
    return rec


rec_seq = asyncio.run(_seq_probe(ExplodingPage()))
seqs = [i["sequence"] for i in rec_seq.data["interactions"]]
check(seqs == list(range(1, len(seqs) + 1)),
      "截图中途炸过一次，**序号仍然从 1 起严格连续**（实得 %s）—— "
      "compose._validated_probe_dump 要求连续，空一个号整份 dump 就永远用不了" % seqs)
check(len(seqs) == 4,
      "截图失败不丢交互本身：4 次事件仍然记下 4 条（失败那条带 screenshot_error）")
orders = [i["evidence_order"] for i in rec_seq.data["interactions"]]
check(orders == list(range(1, len(orders) + 1)),
      "交互与快照共用的 evidence_order 在普通截图失败后也连续")


class CancelledScreenshotPage(FakePage):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def screenshot(self, *, path, full_page, mask, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise asyncio.CancelledError()
        Image.new("RGB", (8, 6), (1, 2, 3)).save(path, format="PNG")


async def _cancelled_order_probe():
    rec = ProbeRecorder(Path(tempfile.mkdtemp()), port=1, profile=Path("."))
    payload = {
        "session_id": rec.session_id, "is_trusted": True,
        "event_type": "click", "target": {"tag": "button"},
    }
    try:
        await rec.record(CancelledScreenshotPage(), payload)
    except asyncio.CancelledError:
        pass
    await rec.record(FakePage(), payload)
    return rec


cancelled_rec = asyncio.run(_cancelled_order_probe())
check([row["evidence_order"] for row in cancelled_rec.data["interactions"]] == [1],
      "Ctrl+C/任务取消落在截图 await 上也不能永久占掉一个 evidence_order")
# 去掉注释行再查：说明文字里**引用**旧写法是有意的，不能当成还在用它。
rec_code = "\n".join(
    line for line in inspect.getsource(ProbeRecorder.record).splitlines()
    if not line.lstrip().startswith("#"))
check("self._counter +=" not in rec_code
      and 'len(self.data["interactions"]) + 1' in rec_code,
      "record() 不再用只增不减的计数器取号，改由已落盘条数推出")


print("\n[10] G2–G5 在一个仿真 composer 上的真实行为")
# ⚠️ 这一节是 mock，**它证明的是逻辑，不是真实 UI**。
# CODE_REVIEW 18.9 的教训就是「mock 掉的边界就是没被测到的边界」：
# 真实 Business Suite 上还没跑过一次，所以 G2–G5 一项都不勾。
# 但下面这些恰恰是 mock **能**证明的部分：时区换算、12 小时制换算、
# 回读比对会不会真的拦住、排除法定位在个数变化时会不会失败闭合。


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        target = self.page.focused
        if key == "Control+A":
            self.page.select_all = True
            return
        if target is None:
            return
        if key == "Delete":
            if target.segmented:
                # 分段时间输入：Ctrl+A 根本没选中这一格，Delete 也就清不掉它。
                # 这正是 CR-72 的真机形态 —— 旧值 1 上再敲 1 得到 11。
                self.page.select_all = False
                return
            target.text = ""
            target.value = ""
            self.page.select_all = False
            return
        if key == "Backspace":
            target.text = target.text[:-1]
            target.value = target.value[:-1]
            return
        if key == "End":
            return
        if key == "Shift+Enter":
            target.text += "\n"

    async def type(self, text):
        """逐字符按键。**生产代码不该再用它**（CR-71），这里保留是为了能测出
        "改回去就会坏"——见下面 `on_key_type` 那个只在按键路径上生效的钩子。"""
        target = self.page.focused
        if target is None:
            return
        if self.page.select_all and not target.segmented:
            target.text = ""
            target.value = ""
            self.page.select_all = False
        target.text += text
        target.value += text
        if target.on_key_type is not None:
            target.on_key_type(target)
        if target.on_type is not None:
            target.on_type(target)

    async def insert_text(self, text):
        """对应 Playwright 的 `keyboard.insert_text`（CDP Input.insertText）。

        **不派发按键事件**，所以 `on_key_type`（模拟 @/# typeahead）不触发；
        但编辑器仍收到 input 事件，所以 `on_type`（模拟编辑器重写）照常触发。
        """
        target = self.page.focused
        if target is None:
            return
        if self.page.select_all:
            target.text = ""
            target.value = ""
            self.page.select_all = False
        newline = chr(10)
        if newline in text and self.page.block_insert_drops_newlines:
            # 有些富文本编辑器不会把整段插入里的换行符变成真正的换行。
            text = text.replace(newline, "")
        target.text += text
        target.value += text
        if target.on_type is not None:
            target.on_type(target)


class FakeElement:
    def __init__(self, role, name="", *, aria_label=None, value="", text="",
                 checked=None, children=(), on_type=None, render=None,
                 on_key_type=None, segmented=False, deferred=False):
        self.role = role
        self.name = name
        self.aria_label = aria_label
        self.value = value
        self.text = text
        self.checked = checked
        self.children = list(children)
        self.on_type = on_type
        # 只在**按键**路径上生效：模拟 @/# 的 typeahead 打乱正文（CR-71）。
        self.on_key_type = on_key_type
        # 分段输入（时/分/AM-PM）：Ctrl+A 选不中它，Delete 清不掉（CR-72）。
        self.segmented = segmented
        # 打开定时开关之后才异步渲染出来的元素（CR-74）。
        self.deferred = deferred
        self.render = render
        self.clicks = 0

    def accessible_name(self):
        return self.aria_label if self.aria_label is not None else self.name

    def inner_text(self):
        return self.render(self) if self.render is not None else self.text


class FakeLocator:
    """⚠️ **惰性解析**：每次用到时重新算命中了哪些元素。

    真实 Playwright 的 locator 就是这样 —— 它是"怎么找"，不是"找到的东西"。
    早先这里把命中结果**在构造时就冻住**，于是"等一会儿元素才渲染出来"
    这件事在假页面上根本不可能发生，`.all()` 不等待的 bug（CR-74）
    也就无从被测到。
    """

    def __init__(self, page, elements=None, resolver=None):
        self.page = page
        self._resolver = resolver if resolver is not None else (
            lambda snapshot=list(elements or []): list(snapshot))

    @property
    def elements(self):
        return self._resolver()

    @property
    def first(self):
        resolve = self._resolver
        return FakeLocator(self.page, resolver=lambda: resolve()[:1])

    def _one(self):
        found = self.elements
        if not found:
            raise AssertionError("定位没有命中任何元素")
        return found[0]

    async def count(self):
        return len(self.elements)

    async def all(self):
        return [FakeLocator(self.page, [item]) for item in self.elements]

    async def wait_for(self, state=None, timeout=None):
        if not self.elements:
            # 模拟"再等一会儿它就渲染出来了"。真实 locator 会一直轮询到超时。
            self.page.reveal_deferred()
        self._one()

    async def click(self, timeout=None):
        element = self._one()
        element.clicks += 1
        self.page.focused = element
        self.page.select_all = False
        if element.role == "switch":
            element.checked = not element.checked

    async def is_checked(self):
        return bool(self._one().checked)

    async def input_value(self):
        return self._one().value

    async def inner_text(self):
        return self._one().inner_text()

    async def get_attribute(self, name):
        if name == "aria-label":
            return self._one().aria_label
        return None

    async def fill(self, value, timeout=None):
        element = self._one()
        if element.segmented:
            # ⚠️ **分段输入上 fill() 不生效**，这是照真机建模的：CR-72 那次
            # 打字得到 "11" 之后 fill("1") 跑过了，而最终回读**仍然是 11**。
            # 不建这一条的话，假页面里 fill() 会把 bug 掩盖掉，
            # 这套断言就变成"测了个寂寞"。
            return
        element.value = value
        element.text = value
        # fill() 和逐字输入走同一个"页面会不会改写我填的值"的钩子——
        # 只在 type 那条路上模拟改写，等于给 fill 开了后门。
        if element.on_type is not None:
            element.on_type(element)

    def get_by_role(self, role, name=None, exact=True):
        def resolve():
            found = []
            for element in self.elements:
                found.extend(_walk_role(element.children, role, name, exact))
            return found
        return FakeLocator(self.page, resolver=resolve)


def _walk_role(elements, role, name, exact):
    hits = []
    for element in elements:
        if getattr(element, "deferred", False):
            continue                      # 还没渲染出来（CR-74 的形状）
        if element.role == role and _name_ok(element.accessible_name(), name, exact):
            hits.append(element)
        hits.extend(_walk_role(element.children, role, name, exact))
    return hits


def _name_ok(actual, wanted, exact):
    if wanted is None:
        return True
    return actual == wanted if exact else wanted.lower() in (actual or "").lower()


class FakeChooser:
    def __init__(self, multiple):
        self.multiple = multiple
        self.files = None

    def is_multiple(self):
        return self.multiple

    async def set_files(self, files):
        self.files = list(files)


class FakeComposer:
    url = "https://business.facebook.com/latest/composer/"

    def __init__(self, elements, *, texts=(), chooser=None, hang=False):
        self.elements = list(elements)
        self.texts = list(texts)
        self.chooser = chooser
        self.hang = hang
        self.focused = None
        self.select_all = False
        self.keyboard = FakeKeyboard(self)
        self.settled = 0
        self.block_insert_drops_newlines = False
        self.deferred_elements = []

    def reveal_deferred(self):
        """模拟异步渲染完成：把 deferred 的元素挂上去。"""
        for element in self.deferred_elements:
            element.deferred = False
        self.deferred_elements = []

    async def evaluate(self, expr):
        if self.hang:
            await asyncio.sleep(3600)
        return 2

    def get_by_role(self, role, name=None, exact=True):
        return FakeLocator(
            self, resolver=lambda: _walk_role(self.elements, role, name, exact))

    def get_by_text(self, text, exact=False):
        hits = [item for item in self.texts if text.lower() in item.lower()]
        return FakeLocator(self, [FakeElement("text", item) for item in hits])

    async def wait_for_load_state(self, state, timeout=None):
        self.settled += 1

    def expect_file_chooser(self, timeout=None):
        page = self

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            @property
            def value(self):
                async def _get():
                    if page.chooser is None:
                        raise TimeoutError("没有文件选择器")
                    return page.chooser
                return _get()

        return _Ctx()


CAPTION_NAME = selectors.COMPOSER["caption_box"].name


def make_composer(*, spinbuttons=3, page_texts=("Neakasa Deutschland",),
                  caption_hook=None, chooser=None, date_hook=None,
                  caption_key_hook=None, drop_newlines=False,
                  segmented_time=False, stale_hour="1", channels=1,
                  deferred_schedule=False):
    caption = FakeElement("combobox", aria_label=CAPTION_NAME,
                          on_type=caption_hook, on_key_type=caption_key_hook)
    add_media = FakeElement("button", "Add photo/video")
    switch = FakeElement("switch", aria_label="Set date and time", checked=False)

    def render_time(element):
        values = [item.value for item in element.children]
        while len(values) < 3:
            values.append("")
        return "%s : %s %s" % (values[0], values[1], values[2])

    # ⚠️ 真实 composer 上**每个渠道各有一套**日期框 + 时间控件
    # （Facebook 一套、Instagram 一套，CR-73）。channels 就是模拟这一点。
    dates, groups, spin_sets = [], [], []
    for _ in range(channels):
        date = FakeElement("textbox", name="Date picker", on_type=date_hook,
                           deferred=deferred_schedule)
        labels = [None, "minutes", "meridiem"][:spinbuttons]
        spins = [FakeElement("spinbutton", aria_label=label,
                             segmented=segmented_time) for label in labels]
        if segmented_time and spins:
            # 打开定时开关后 UI 会预填一个时刻；小时那格留着旧值。
            spins[0].value = spins[0].text = stale_hour
        group = FakeElement("application", name="Time input", children=spins,
                            render=render_time, deferred=deferred_schedule)
        dates.append(date)
        groups.append(group)
        spin_sets.append(spins)

    children = [caption, add_media, switch]
    for date, group in zip(dates, groups):
        children.extend((date, group))
    root = FakeElement("none", "", children=children)
    page = FakeComposer([root], texts=list(page_texts), chooser=chooser)
    page.block_insert_drops_newlines = drop_newlines
    if deferred_schedule:
        page.deferred_elements = list(dates) + list(groups)
    return page, {"caption": caption, "add_media": add_media, "switch": switch,
                  "date": dates[0], "group": groups[0], "spins": spin_sets[0],
                  "dates": dates, "groups": groups, "spin_sets": spin_sets}


CAPTION_TEXT = ("Neuer Frühling für Straßenkatzen 🐾\n"
                "\n"
                "Größe zählt: 219,99 $ statt $219.99\n"
                "#Neakasa #Katzenklo")


async def caption_ok():
    page, parts = make_composer()
    await fill_caption(page, CAPTION_TEXT)
    return parts["caption"].text


check(asyncio.run(caption_ok()) == CAPTION_TEXT,
      "G4 填正文：换行/空行/emoji/变音/#标签/$金额 原样填进去")


# ---- CR-71：@/# 的 typeahead 只在**按键**路径上打乱正文 ----
# 2026-09-01 真机实测：`keyboard.type()` 逐字符敲键把 `@ifa.berlin` 撕成
# `@ifa.ber` + `lin` 插到了两个地方。修法是改用 `keyboard.insert_text()`
# （CDP Input.insertText，不派发按键）。下面两条把"改回去就会坏"钉住。
def _typeahead_scramble(element):
    """模拟 typeahead：一看到 @ 提及就把光标挪走，后续字符落到末尾。"""
    if "@ifa.ber" in element.text and not element.text.endswith("SCRAMBLED"):
        element.text = element.text + "SCRAMBLED"


async def caption_survives_typeahead():
    page, parts = make_composer(caption_key_hook=_typeahead_scramble)
    await fill_caption(page, "Hallo @ifa.berlin und #Neakasa")
    return parts["caption"].text


check(asyncio.run(caption_survives_typeahead()) == "Hallo @ifa.berlin und #Neakasa",
      "G4 不再走按键路径：@ 提及的 typeahead 打不乱正文了"
      "（⛔ 改回 keyboard.type 这条立刻红）")

_write_src = inspect.getsource(bs._write_caption)
# 查带 `page.` 前缀的调用形态：函数的 docstring 里**故意**写着
# "不要改回 ``keyboard.type()``"，不带前缀地查会把那句警告本身当成违规。
check("page.keyboard.type(" not in _write_src
      and "page.keyboard.insert_text(" in _write_src,
      "写正文只用 insert_text，不用 keyboard.type —— "
      "逐字符按键会招出 @/# 的自动补全（CR-71）。"
      "⚠️ 只查这一个函数：G5 往 mm/dd/yyyy 那个普通 textbox 里敲日期"
      "仍然用 keyboard.type，那里没有 typeahead")


async def caption_falls_back_per_line():
    # 整段插入时 \n 没变成换行 → 第一种写法失配，第二种（逐行 + Shift+Enter）救回来
    page, parts = make_composer(drop_newlines=True)
    await fill_caption(page, CAPTION_TEXT)
    return parts["caption"].text


check(asyncio.run(caption_falls_back_per_line()) == CAPTION_TEXT,
      "整段插入丢换行时自动回落到逐行 + Shift+Enter，两种写法都当场回读校验")


async def caption_mangled():
    # 模拟话题标签自动补全把 #Neakasa 改写成别的：回读闸必须当场拦住
    def hook(element):
        if element.text.endswith("#Katzenklo"):
            element.text = element.text[:-len("#Katzenklo")] + "#Katzenklos"
    page, _ = make_composer(caption_hook=hook)
    try:
        await fill_caption(page, CAPTION_TEXT)
    except PublishStepError as exc:
        return str(exc)
    return ""


mangled = asyncio.run(caption_mangled())
check("回读与要填的不一致" in mangled and "自动补全" in mangled,
      "G4 正文被自动补全改写时**当场停下**，并指出第一处不同")


async def schedule(when, *, zone="Europe/Berlin", **kwargs):
    page, parts = make_composer(**kwargs)
    # verify_device=False：这几条测的是**换算**，与跑测试这台机器的时区无关。
    # 设备核对本身另有专门断言（见下面 [10c]）。
    readback = await set_schedule(page, when, ui_timezone=zone,
                                  verify_device=False)
    return readback, parts


# 2026-09-08 08:00Z = 柏林夏令时 10:00（UTC+2）→ 10 AM
readback, parts = asyncio.run(schedule(
    datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)))
check(parts["date"].value == "09/08/2026",
      "G5 日期按 dump 里 placeholder 实测的 mm/dd/yyyy 填（实得 %r）"
      % parts["date"].value)
check([item.value for item in parts["spins"]] == ["10", "00", "AM"],
      "G5 时刻显式换算到 UI 时区，并按录到的 12 小时制 + meridiem 填")
check(parts["switch"].checked is True and parts["switch"].clicks == 1,
      "G5 定时开关只点一次，并回读确认真的开了")
check("Europe/Berlin" in readback and "10 : 00 AM" in readback
      and "2026-09-08T08:00:00+00:00" in readback,
      "G5 回读**同时**给出 UI 显示的时刻与目标时刻——"
      "只给一个的话，人要么核对不了屏幕、要么以为排错了（实得 %r）" % readback)

# 跨日 + 跨月：柏林时间比 UTC 早，23:30Z 已经是次日
readback_cross, parts_cross = asyncio.run(schedule(
    datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)))
check(parts_cross["date"].value == "10/01/2026"
      and [item.value for item in parts_cross["spins"]] == ["1", "30", "AM"],
      "G5 跨日跨月：UTC 09-30 23:30 → 柏林 10-01 01:30 AM（实得 %s %s）"
      % (parts_cross["date"].value,
         [item.value for item in parts_cross["spins"]]))

# 夏令时切换日：2026-03-29 柏林 02:00 跳到 03:00（UTC+1 → UTC+2）
_, parts_dst_on = asyncio.run(schedule(
    datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)))
check([item.value for item in parts_dst_on["spins"]] == ["3", "30", "AM"],
      "G5 夏令时开始日：01:30Z → 柏林 03:30（不是 02:30）")
# 2026-10-25 柏林 03:00 退回 02:00；02:30 在无 offset 的 UI 中出现两次。
async def schedule_dst_off_ambiguous():
    page, parts = make_composer()
    try:
        await set_schedule(
            page, datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc),
            ui_timezone="Europe/Berlin", verify_device=False)
    except PublishStepError as exc:
        return str(exc), parts
    return "", parts


dst_off_error, parts_dst_off = asyncio.run(schedule_dst_off_ambiguous())
check("重复的墙上时间" in dst_off_error
      and parts_dst_off["switch"].clicks == 0,
      "G5 夏令时结束日：无 offset 的 02:30 有两种绝对时刻，"
      "必须在任何 UI 操作前失败闭合")


# ---- [10c] 用户 2026-09-01 实测：UI 跟**发帖者设备的本机时间**走 ----
# 于是「受众那边几点」与「屏幕上填几点」是两个不同的钟，而且两地夏令时切换日
# **不是同一天**：本机(美西) 03-08 / 11-01，柏林 03-29 / 10-25。
# 一年因此有两段约一周的窗口，时差从 9 小时变成 8 小时。
# ⛔ 这几条钉住的就是"手算时差"必然踩的那个坑。
DEVICE_TZ = "America/Los_Angeles"
BERLIN = ZoneInfo("Europe/Berlin")

for label, target, want_date, want_time in [
        ("常规（时差 9 小时）", datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
         "09/08/2026", ["1", "00", "AM"]),
        ("⚠️ 柏林已回冬令时、本机还在夏令时（时差 8 小时）",
         datetime(2026, 10, 28, 10, 0, tzinfo=BERLIN),
         "10/28/2026", ["2", "00", "AM"]),
        ("两地都回冬令时之后（又变回 9 小时）",
         datetime(2026, 11, 3, 10, 0, tzinfo=BERLIN),
         "11/03/2026", ["1", "00", "AM"]),
        ("⚠️ 本机已进夏令时、柏林还没（时差 8 小时）",
         datetime(2027, 3, 20, 10, 0, tzinfo=BERLIN),
         "03/20/2027", ["2", "00", "AM"])]:
    _, got = asyncio.run(schedule(target, zone=DEVICE_TZ))
    check(got["date"].value == want_date
          and [item.value for item in got["spins"]] == want_time,
          "G5 %s：柏林 %s → UI 填 %s %s（实得 %s %s）"
          % (label, target.strftime("%m-%d %H:%M"), want_date,
             " ".join(want_time), got["date"].value,
             [item.value for item in got["spins"]]))


async def device_mismatch():
    # 拿一个**必然**与本机不同的时区：本机是什么都不影响这条断言。
    other = "Etc/GMT+11" if datetime.now().astimezone().utcoffset() != (
        timedelta(hours=-11)) else "Etc/GMT+3"
    page, _ = make_composer()
    try:
        await set_schedule(page, WHEN, ui_timezone=other)
    except PublishStepError as exc:
        return str(exc)
    return ""


mismatch = asyncio.run(device_mismatch())
check("与**这台机器**的时区对不上" in mismatch and "本机偏移" in mismatch,
      "G5 配置时区与本机对不上时**失败闭合**——UI 跟设备走，"
      "配置和设备不一致就等于排错时刻，而这种错没人会立刻发现")
check("手算必错" in mismatch,
      "错误信息劝住「我自己减几小时就行」——两地切换日不同，手算在那两段窗口里必错")

# 设备核对比的是**目标时刻**的偏移，不是"今天"的偏移。
# 拿今天去判，会在上面那两段窗口里把正确的配置判成错的。
_src = inspect.getsource(bs.assert_ui_timezone_is_device)
check("when.astimezone(zone).utcoffset()" in _src
      and "when.astimezone().utcoffset()" in _src
      and "datetime.now()" not in _src,
      "设备核对用目标时刻的偏移，不用当下的偏移")


async def schedule_two_spins():
    try:
        await schedule(WHEN, spinbuttons=2)
    except PublishStepError as exc:
        return str(exc)
    return ""


two = asyncio.run(schedule_two_spins())
check("composer_hours_spinbutton" in two and "不去猜哪个是小时" in two,
      "G5 时间控件个数一变就失败闭合：小时那个本来就是靠排除法定位的")


# ---- CR-72：分段时间输入上 Ctrl+A 选不中当前格 ----
# 2026-09-01 真机实测：小时格里预填着旧值，Ctrl+A 没选中它，
# 再敲 "1" 得到的是 "11" —— 帖子会排到 11:00 AM 而不是 1:00 AM。
# ⚠️ 这类错**只差一个字符，而且看起来完全正常**，正是回读闸存在的理由。
segmented_readback, segmented_parts = asyncio.run(schedule(
    datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc),
    zone="America/Los_Angeles", segmented_time=True, stale_hour="1"))
check(segmented_parts["spins"][0].value == "1",
      "G5 分段小时格：先把旧值真的清干净再写，不会把 1 敲成 11（实得 %r）"
      % segmented_parts["spins"][0].value)
check("1 : 00 AM" in segmented_readback,
      "G5 分段输入下整组回读仍然对得上（实得 %r）" % segmented_readback)

_clear_src = inspect.getsource(bs._clear_field)
check("Backspace" in _clear_src and "input_value" in inspect.getsource(bs._field_value),
      "清空字段必须**回读确认真的空了**，Ctrl+A 没生效时有退格兜底（CR-72）")

_time_src = inspect.getsource(bs._set_one_time)
check("逐字段当前值" in _time_src,
      "时刻回读失败时逐字段打出当前值 —— 只报合成串看不出是哪一格坏的")


# ---- CR-73：每个渠道各有一套排期控件，**不是一套** ----
# 真机截图：Schedule 下面 `Facebook` 是 Sep 10 11:00 AM，
# `Instagram` 还停在 Sep 1 06:23 PM（默认值）—— 只设第一组等于让 IG 立刻发。
two_ch_readback, two_ch = asyncio.run(schedule(
    datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), channels=2))
check(all(item.value == "09/08/2026" for item in two_ch["dates"]),
      "G5 **两个渠道的日期都被写上**（实得 %r）"
      % [item.value for item in two_ch["dates"]])
check(all(spins[0].value == "10" and spins[1].value == "00"
          and spins[2].value == "AM" for spins in two_ch["spin_sets"]),
      "G5 两个渠道的时刻都被写上，不是只设 Facebook 那一组")
check(two_ch_readback.count("09/08/2026") == 2,
      "回读把每一组都打出来，人能一眼看到两个渠道排的是同一时刻（实得 %r）"
      % two_ch_readback)


async def schedule_unpaired():
    """日期框和时间控件数量对不上时必须失败闭合，不去猜怎么配对。"""
    page, parts = make_composer(channels=2)
    parts["groups"].pop().role = "none"      # 弄掉一个时间控件
    try:
        await set_schedule(page, WHEN, ui_timezone="Europe/Berlin",
                           verify_device=False)
    except PublishStepError as exc:
        return str(exc)
    return ""


unpaired = asyncio.run(schedule_unpaired())
check("配不成对" in unpaired and "不去猜" in unpaired,
      "G5 排期控件配不成对时失败闭合 —— 配错的后果是某个渠道被排到别的时刻")


# ---- CR-74：排期区是**打开定时开关之后才异步渲染**的，`.all()` 不等待 ----
deferred_readback, deferred_parts = asyncio.run(schedule(
    datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc),
    channels=2, deferred_schedule=True))
check(all(item.value == "09/08/2026" for item in deferred_parts["dates"]),
      "G5 排期区晚一拍才渲染时**先等再枚举**，两组都设上了（实得 %r）"
      % [item.value for item in deferred_parts["dates"]])

_sched_src = inspect.getsource(bs.set_schedule)
check(_sched_src.index("wait_for") < _sched_src.index(".all()"),
      "⛔ `.all()` 之前必须先 wait_for —— `.all()` 不等待，"
      "排期区还没渲染出来时它返回 0 个（CR-74）")


# ---- CR-75：Planner 的"就绪信号"在日历数据还在转圈时就已经渲染好了 ----
# 真机截图：`Planner` 标题、`September 2026`、Week/Month 全在，中间一个大转圈。
# 那一刻页面上的 link 全是导航和侧栏 —— 刚提交成功的帖子被判成"回读不到"。
class LoadingPlanner:
    """前 `ticks` 次读到的只有导航 link，之后日历条目才渲染出来。"""

    def __init__(self, ticks):
        self.ticks = ticks
        self.reads = 0
        self.focused = None
        self.select_all = False

    def get_by_role(self, role, name=None, exact=True):
        self.reads += 1
        # ⚠️ 用 aria_label / text，不是第二个位置参数（那是 name）：
        # `_node_text` 读的是 inner_text 与 aria-label，读不到 name。
        found = [FakeElement("link", aria_label="Create post")]
        if self.reads > self.ticks:
            found.append(FakeElement(
                "link",
                aria_label="Probe caption September 15, 2026, 10:00 AM"))
        return FakeLocator(self, found)


_ENTRY_SPEC = selectors.EvidenceSignal(
    key="planner_scheduled_card", step="G6c", kind="semantic",
    surface="content_calendar", source_dump="fixture.json", sequences=(1,),
    breaks_when="fixture", role="link", name="Probe caption",
    attributes={"entry_role": "link",
                "datetime_regex": (r"(?P<date>[A-Z][a-z]{2,8} \d{1,2}, \d{4})"
                                   r"\D{0,10}?(?P<time>\d{1,2}:\d{2} [AaPp][Mm])")})


async def planner_waits_for_entries():
    page = LoadingPlanner(ticks=2)          # 前两次只有导航 link
    cards = await bs._entries_when_ready(page, _ENTRY_SPEC, "link", timeout=5)
    texts = [await bs._node_text(item) for item in cards]
    return page.reads, texts


reads, texts = asyncio.run(planner_waits_for_entries())
check(reads > 2 and any("September 15, 2026" in item for item in texts),
      "G6c 等到**真的能解析出时刻的条目**才读，不把「还在转圈」当成零占用"
      "（读了 %d 次）" % reads)


async def planner_empty_stays_empty():
    page = LoadingPlanner(ticks=10 ** 6)   # 永远只有导航 link = 真的空日历
    cards = await bs._entries_when_ready(page, _ENTRY_SPEC, "link", timeout=1)
    return [await bs._node_text(item) for item in cards]


check(not any("September" in item
              for item in asyncio.run(planner_empty_stays_empty())),
      "真的空日历仍然读作空：等满预算后原样返回，由调用方按内容筛出零条")

check("_PLANNER_ENTRY_BUDGET" in inspect.getsource(bs._entries_when_ready),
      "等待预算是有上界的常量，不是整个 ui_timeout —— "
      "空日历每次发布都会真的等满这一段")


async def schedule_bad_date():
    def hook(element):
        element.value = "08/09/2026"        # 模拟 UI 换成了 dd/mm/yyyy
    try:
        await schedule(datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc),
                       date_hook=hook)
    except PublishStepError as exc:
        return str(exc)
    return ""


bad_date = asyncio.run(schedule_bad_date())
check("日期回读对不上" in bad_date and "mm/dd/yyyy" in bad_date,
      "G5 日期回读对不上时点名「先怀疑日期格式变了」——静默排到别的日子是最贵的错")

check(raises(ValueError,
             lambda: asyncio.run(set_schedule(
                 UntouchablePage(), datetime(2026, 9, 8, 10, 0),
                 ui_timezone="Europe/Berlin")),
             "显式带时区"),
      "G5 naive datetime 在接触页面之前就被拒")


async def login_ok():
    page, _ = make_composer()
    return await ensure_logged_in(page, page_name="Neakasa Deutschland",
                                  instagram_account="neakasa.de")


context = asyncio.run(login_ok())
check(context.logged_in and context.page_name_seen,
      "G2 页面上出现目标主页显示名时通过")
check(context.selection_verified is False and context.notes,
      "G2 **不谎称**已确认选中：composer 上没录到主页切换器，所以只给弱结论")
check(any("neakasa.de" in note for note in context.notes),
      "G2 没看到 IG 帐号时明说，让人提交前重点看那一项")
check(any("默认全勾选" in note and "提交后" in note for note in context.notes),
      "G2 沿用默认全勾选且不点击渠道控件；自动路径改由提交后结构化卡片回读")


# ⚠️ **2026-09-01 按真实 composer 重写：提交前只核对 Facebook。**
# 实测（`docs/PROBE_FINDINGS_20260901.md`）composer 上从头到尾没有 IG 帐号名，
# 只有 `img 'Instagram'` 一个图标。FB 主页名出现在预览抬头那条 `heading h2`。
# IG 改由提交后从 Planner 详情弹窗回读证明，少了会转人工。
async def strict_login(facebook_value):
    caption = FakeElement("combobox", aria_label=CAPTION_NAME)
    heading = FakeElement("heading", aria_label=facebook_value,
                          text=facebook_value)
    root = FakeElement("none", "", children=[caption, heading])
    page = FakeComposer([root], texts=[facebook_value, "neakasa.de"])
    spec = selectors.EvidenceSignal(
        key="composer_account_context", step="G2", kind="semantic",
        surface=selectors.SURFACE_COMPOSER, source_dump="fixture.json",
        sequences=(1,), breaks_when="fixture", role="heading",
        name="Neakasa Deutschland", attributes={
            "facebook_account_token": "Neakasa Deutschland",
            "facebook_account_regex": r"@?(?P<account>.+)",
        })
    return await ensure_logged_in(
        page, page_name="Neakasa Deutschland",
        instagram_account="neakasa.de", account_spec=spec)


strict_context = asyncio.run(strict_login("Neakasa Deutschland"))
check(strict_context.selection_verified,
      "生产账号闸从 composer 预览抬头提取**完整值**后通过")
check(any("IG" in note and "回读" in note for note in strict_context.notes),
      "并且**明说** IG 在 composer 上不显示、由提交后回读证明，不是悄悄跳过")
try:
    asyncio.run(strict_login("Neakasa Deutschland Test"))
except PublishStepError:
    strict_near_collision_blocked = True
else:
    strict_near_collision_blocked = False
check(strict_near_collision_blocked,
      "生产账号闸拒绝 FB 同名前缀 Page（完整值比较，多一个词就是另一个主页）")


async def login_wrong_page():
    page, _ = make_composer(page_texts=("Neakasa Official",))
    try:
        await ensure_logged_in(page, page_name="Neakasa Deutschland")
    except PublishStepError as exc:
        return str(exc)
    return ""


wrong = asyncio.run(login_wrong_page())
check("比没登录严重" in wrong,
      "G2 找不到目标主页显示名时拦住，并说清这比没登录更严重")


async def upload(multiple, count):
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for index in range(count):
            item = Path(folder) / ("%02d.jpg" % (index + 1))
            Image.new("RGB", (64, 64), (7, 8, 9)).save(item, format="JPEG")
            paths.append(item)
        chooser = FakeChooser(multiple)
        page, _ = make_composer(chooser=chooser)
        notes = await upload_images(page, paths)
        return chooser, notes


chooser, notes = asyncio.run(upload(True, 5))
check(chooser.files is not None and len(chooser.files) == 5,
      "G3 走 file chooser 通道交 5 张图，全程没有写死 input[type=file] 选择器")
check(any("缩略图数量没有被程序核对过" in note for note in notes),
      "G3 **不假装**验过缩略图：那个容器没进 dump，如实交回给人复核")
check(raises(PublishStepError, lambda: asyncio.run(upload(False, 5)),
             "只收 1 个文件"),
      "G3 控件只收单文件却要传 5 张时停下，不默默只传一张")


async def no_chooser():
    page, _ = make_composer(chooser=None)
    try:
        await upload_images(page, [Path(__file__)])
    except PublishStepError as exc:
        return str(exc)
    return ""


no_fc = asyncio.run(no_chooser())
check("拖拽区" in no_fc and "重录一次 G1" in no_fc,
      "G3 等不到文件选择器时区分「是拖拽区」与「定位失效」两种情况")


async def hung_page():
    page, _ = make_composer()
    page.hang = True
    try:
        await bs.assert_page_usable(page, timeout=0.05)
    except PublishStepError as exc:
        return str(exc)
    return ""


hung = asyncio.run(hung_page())
check("CR-64" in hung and "F5" in hung,
      "坏页（page.evaluate 超时）当场点名 CR-64，而不是 except: 吞掉后静默什么都不做")


print("\n[11] G6b 留痕与幂等：state/published.jsonl")
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    base = dict(post_id="122123185335379375", platform="facebook",
                scheduled_at="2026-09-08T10:00:00+02:00",
                recorded_at="2026-09-01T12:00:00+02:00",
                text_de_sha256=journal.text_sha256("hallo"),
                images=("01.jpg",), image_sources=("original",))
    journal.append(state, journal.PublishRecord(
        status=journal.STATUS_PREPARED, **base))
    check(journal.scheduled_record(state, base["post_id"], "facebook") is None,
          "「已准备」不算已排期——它意味着可能留着草稿，不能当幂等跳过的理由")
    pending = journal.pending_draft_record(state, base["post_id"], "facebook")
    check(pending is not None and pending["status"] == journal.STATUS_PREPARED,
          "「已准备」会被认出来，重跑前先提醒人去看有没有草稿残留")
    journal.append(state, journal.PublishRecord(
        status=journal.STATUS_SCHEDULED, **base))
    check(journal.scheduled_record(state, base["post_id"], "facebook") is not None,
          "结转成「已排期」之后幂等生效")
    check(journal.pending_draft_record(state, base["post_id"], "facebook") is None,
          "已排期之后不再提示草稿残留")
    check(journal.scheduled_record(state, base["post_id"], "instagram") is None,
          "幂等按 post_id + 平台判，FB 排过了不影响 IG 那一路")
    check(len(journal.load(state)) == 2,
          "留痕只追加不重写：两次操作两行")
    check(raises(ValueError,
                 lambda: journal.PublishRecord(
                     status=journal.STATUS_PREPARED,
                     **{**base, "scheduled_at": "2026-09-08T10:00:00"}),
                 "必须显式带时区"),
          "排期时刻不带时区的留痕直接拒收")
    check(raises(ValueError,
                 lambda: journal.PublishRecord(
                     status="published", **base), "未知发布状态"),
          "只认发布状态机定义的五个状态，不许自造状态")


print("\n[12] 发布入口：默认准备，显式 --submit 才进入证据门禁后的 G6")
entry = (ROOT / "tools" / "publish_post.py").read_text(encoding="utf-8")
workflow_entry = (ROOT / "publish" / "workflow.py").read_text(encoding="utf-8")
check("force_utf8()" in entry,
      "入口调 force_utf8()（本机代码页 936，输出一被重定向就炸在 ß/⚠ 上）")
check("--submit" in entry and "bs.submit" in workflow_entry,
      "单帖默认仍停在提交前；只有显式 --submit 才进入 G6")
with contextlib.redirect_stdout(io.StringIO()) as captured:
    submit_gate_code = publish_entry.main([
        "--post-id", "does-not-matter", "--at", "2026-09-08T10:00",
        "--submit", "--assume-yes"])
check("ui_constraints_verified" in entry,
      "--submit 那条路径上仍然存在 ui_constraints_verified 这道闸")
check(submit_gate_code == 2 and "没有碰浏览器" in captured.getvalue(),
      "--submit 自动隐含 strict；任一硬闸不过都在碰浏览器之前失败闭合")
check("publish_debug_port" in workflow_entry and "publish_profile_dir" in workflow_entry
      and "assert_publish_chrome_isolated" in workflow_entry,
      "入口只附着发布专用 profile/端口，且启动前核对与抓取小号隔离")
check("await pw.stop()" in workflow_entry and "browser.close()" not in workflow_entry,
      "收尾只断开 Playwright，不关用户的 Chrome，也不关那个待提交的标签页")
check("--mark-scheduled" in entry and "--mark-not-scheduled" in entry
      and "manual_evidence=True" in entry,
      "模糊状态可人工结转为已排期/未排期，且明确标记为人工证据")

entry_bat = ROOT / "scripts" / "run_publish_post.bat"
raw_entry = entry_bat.read_bytes() if entry_bat.exists() else b""
check(bool(raw_entry) and all(byte <= 127 for byte in raw_entry),
      "scripts\\run_publish_post.bat 存在且纯 ASCII")
check(bool(raw_entry) and raw_entry.replace(b"\r\n", b"").count(b"\n") == 0
      and not raw_entry.startswith(b"\xef\xbb\xbf"),
      "run_publish_post.bat 只有 CRLF、无 BOM")
check(b"PYTHONIOENCODING=utf-8" in raw_entry
      and b"tools\\publish_post.py" in raw_entry,
      "run_publish_post.bat 设了 UTF-8 并只转交 Python 入口")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

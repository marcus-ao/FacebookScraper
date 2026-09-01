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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.console import force_utf8  # noqa: E402

force_utf8()

from core.config import cfg  # noqa: E402
from core.store import post_dirname  # noqa: E402
from publish.business_suite import (ProbeRequired, ensure_logged_in,  # noqa: E402
                                    fill_caption, set_schedule, submit,
                                    upload_images)
from publish.compose import (ComposeError, InstagramConstraints,  # noqa: E402
                             ScheduleWindow, compose_post)
from tools.probe_publish import ProbeRecorder, install_script  # noqa: E402
from translate import PROMPT_VERSION, source_text_sha256  # noqa: E402
import publish.compose as compose_module  # noqa: E402
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
             lambda: ScheduleWindow("", timedelta(minutes=1), timedelta(days=1)),
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
        max_ahead=timedelta(days=10))
    original_cfg = compose_module.cfg
    try:
        compose_module.cfg = lambda: VerifiedProbeConfig(
            state_dir, profile, recorder.output_path)
        post = compose_post(
            "fixture-post", WHEN, archive_root=root,
            instagram_constraints=limits, schedule_window=window, now=NOW,
            require_verified_ui_constraints=True, warning_sink=None)
    finally:
        compose_module.cfg = original_cfg
    check(len(post.image_paths) == 2,
          "审核过且数值一致的完整 probe 才能严格组装，空白可选 FB slug 不阻塞")
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
                    str(missing_dump), timedelta(hours=1), timedelta(days=10)),
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
                         timedelta(hours=1), timedelta(days=2)),
                     now=NOW, warning_sink=None),
                 "晚于"),
          "排期超出注入的 G1 UI 上限会被拒绝")
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN.replace(tzinfo=None), archive_root=root,
                     warning_sink=None),
                 "显式带时区"),
          "naive datetime 直接拒绝，不依赖本机时区")
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN, archive_root=root,
                     require_verified_ui_constraints=True,
                     warning_sink=None),
                 "G1"),
          "真正发布模式下缺 G1 约束会失败闭合")


print("\n[5] G2–G6 只有会先失败的函数骨架；G7 仍仅有任务书契约")
selector_path = ROOT / "publish" / "selectors.py"
tree = ast.parse(selector_path.read_text(encoding="utf-8"))
assignments = [node for node in ast.walk(tree)
               if isinstance(node, (ast.Assign, ast.AnnAssign))]
check(not assignments,
      "publish/selectors.py 没有任何未经 G1 验证的定位常量")


class UntouchablePage:
    def __getattr__(self, name):
        raise AssertionError("骨架不应接触 page.%s" % name)


async def skeleton_errors():
    calls = [
        ensure_logged_in(UntouchablePage()),
        upload_images(UntouchablePage(), [Path("01.jpg")]),
        fill_caption(UntouchablePage(), "äöüß"),
        set_schedule(UntouchablePage(), WHEN),
        submit(UntouchablePage()),
    ]
    messages = []
    for call in calls:
        try:
            await call
        except ProbeRequired as exc:
            messages.append(str(exc))
    return messages


messages = asyncio.run(skeleton_errors())
check(len(messages) == 5 and all("probe_publish.py" in item for item in messages),
      "G2–G6 每个签名都在接触页面前明确要求先做 G1")
check(all("不得猜选择器" in item for item in messages),
      "骨架错误信息不会让维护者误以为可以临时猜一个定位")


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
    ".click(", ".fill(", ".goto(", "setInputFiles(", "querySelector(")),
    "监听脚本没有点击/填写/导航/上传等页面驱动")
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
    check(bool(page.mask_selectors) and all("password" in value
                                           for value in page.mask_selectors),
          "每步截图都请求遮罩密码/登录类敏感输入")
    check(data["mode"] == "record-only" and data["finished_at"],
          "dump 明确标记只记录模式，并在正常收尾时写完成时间")


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

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method in self.fail_on:
            raise RuntimeError("boom:%s" % method)
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            if "typeof window." in expr and "Handlers" in expr:
                return {"result": {"value": self.verify}}
            return {"result": {"value": None}}
        if method == "Page.captureScreenshot":
            import base64 as _b64
            buf = io.BytesIO()
            Image.new("RGB", (8, 6), (4, 5, 6)).save(buf, format="PNG")
            return {"data": _b64.b64encode(buf.getvalue()).decode()}
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
      "覆盖后续文档与新建 frame")
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

# 截图：Playwright 挂住时的 CDP 回退，隐私边界必须一起带过去
with tempfile.TemporaryDirectory() as d:
    shot = Path(d) / "s.png"
    sess4 = FakeCDPSession()
    okk, det = asyncio.run(probe_module._cdp_screenshot(sess4, shot))
    exprs = [p.get("expression", "") for m, p in sess4.sent if m == "Runtime.evaluate"]
    check(okk and shot.is_file() and shot.stat().st_size > 0,
          "CDP 截图回退能真的写出 PNG")
    check(any("blur" in e for e in exprs),
          "**回退路径也要遮罩敏感输入**：Playwright 的 mask= 用不了，"
          "改成临时插一条 CSS 把密码/邮箱/OTP 模糊掉")
    check(any("remove()" in e for e in exprs), "截完把临时样式撤掉，不留痕迹")

    sess5 = FakeCDPSession(fail_on=("Runtime.evaluate",))
    shot2 = Path(d) / "s2.png"
    ok5, det5 = asyncio.run(probe_module._cdp_screenshot(sess5, shot2))
    check(not ok5 and not shot2.exists(),
          "**遮罩插不进去就不截图** —— 宁可没有截图，也不能把敏感输入拍进去")

page = FakePage()
_rec = ProbeRecorder(Path(tempfile.mkdtemp()), port=1, profile=Path("."))
asyncio.run(_rec.record(page, {"session_id": _rec.session_id, "is_trusted": True,
                               "event_type": "click", "target": {"tag": "b"}}))
check(page.screenshot_timeouts and page.screenshot_timeouts[0] is not None,
      "record() 给截图显式超时：坏页面上 Playwright 截图会一直挂着，"
      "而 record 持锁，一次挂住就把后面所有事件堵死")

check(inspect.iscoroutinefunction(probe_module.cdp_page_targets),
      "有一条直接问 CDP 要 page 目标的路 —— 用来核对 Playwright 有没有漏页")

# 监听器"不知为何就没了"是这一轮最难复现的一类：scratch 环境怎么试都正常，
# 用户真机上就是丢。与其继续猜是哪一种原因，不如让它**自己好起来**。
sessN, (_sn, okN, _dn) = asyncio.run(_install())
check(okN and "Page.frameNavigated" in sessN.handlers,
      "装完注册 Page.frameNavigated：主帧一导航就**立刻**补注入一次，"
      "不用等巡检那 2 秒")

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
      "装到一半页面跳走、page 对象本身是坏的），最多两秒就自己回来")
check("installed[page]" in run_src and "installed[id(" not in run_src
      and "installed.get(id(" not in run_src,
      "已装表用 page 对象本身做键 —— id() 会在对象回收后重用，"
      "那会让一个新页面被误当成'已经装过了'而跳过"
      "（注释里提到 id(page) 是有意的，所以只查真正的取值写法）")
check("timeout=2.0" in run_src,
      "巡检间隔 2 秒：这是用户走流程时能忍的'掉了多久会自己回来'")


print("\n[8][CR-66] 「按 Enter 停止记录」要真的停；观察项不再挡在出口")

# 用户实测：按 Enter 之后事件还在往里记（#18..#24 边问边冒），
# 而 set_observations 和 record 抢同一把锁 —— 于是问答期间每点一次就多排一个，
# 看起来就是卡死。这里钉的是"停止必须真的停"。
check('recording["on"] = False' in run_src,
      "按 Enter 之后**真的关闸**：receive 直接丢弃后续事件")
check("session.detach()" in run_src,
      "并且断开各页面的 CDP 会话，浏览器不再回送事件")
check(run_src.index("await asyncio.sleep(0.9)")
      < run_src.index('recording["on"] = False'),
      "顺序不能反：先给最后一次 input 的 700ms 去抖留时间，再关闸，否则会丢最后一条")
check(run_src.index('recording["on"] = False') < run_src.index("if collect_notes:"),
      "关闸发生在问观察项**之前** —— 这正是死锁的来源")

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
# 去掉注释行再查：说明文字里**引用**旧写法是有意的，不能当成还在用它。
rec_code = "\n".join(
    line for line in inspect.getsource(ProbeRecorder.record).splitlines()
    if not line.lstrip().startswith("#"))
check("self._counter +=" not in rec_code
      and 'len(self.data["interactions"]) + 1' in rec_code,
      "record() 不再用只增不减的计数器取号，改由已落盘条数推出")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

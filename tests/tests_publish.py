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
from unittest.mock import patch

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
from localize.text import PROMPT_VERSION, source_text_sha256  # noqa: E402
import publish.business_suite as bs  # noqa: E402
# 验证真实录制器输出满足发布数据契约。
from tools._scaffolding.probe_publish import ProbeRecorder, install_script  # noqa: E402
import tools._scaffolding.probe_publish as probe_module  # noqa: E402
import publish.compose as compose_module  # noqa: E402
import publish.journal as journal  # noqa: E402
import publish.selectors as selectors  # noqa: E402
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

    text = "Offer stays at $10.\n\n#Neakasa #P1Pro"
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
        "text_de": "Das Angebot bleibt bei $10.\n\n#Neakasa #P1Pro",
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
        # 截图超时须释放记录锁。
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
    # 夹具满足完整 v2 契约，交互与快照共用递增 evidence_order。
    last_order = max((row.get("evidence_order") or 0)
                     for row in recorder.data["interactions"])
    recorder.data["snapshots"] = [{
        "sequence": 1,
        "page_id": "page-001",
        "evidence_order": last_order + 1,
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
            ("paths", "state"): str(self.state_dir),
            ("publish", "ui_constraints_verified"): self._verified,
            ("publish", "ui_probe_dump"): str(self._dump),
            ("publish", "require_all_media_de"): False,
            # 严格月份校验使用 UI 时区。
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


print("\n[S0-1] 默认拒绝原图回退；只有显式 false 才能放行")
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    media_de = fixture["post_dir"] / "media_de"
    media_de.mkdir()
    Image.new("RGB", (101, 100), (1, 2, 3)).save(media_de / "01.png")
    settings = cfg()._d["publish"]
    settings.pop("require_all_media_de", None)
    try:
        compose_post("fixture-post", WHEN, archive_root=root, warning_sink=None)
        missing_error = ""
    except ComposeError as exc:
        missing_error = str(exc)
    check("第 2 张" in missing_error and "--media-index 1" in missing_error
          and '--account "in_neakasa.tech"' in missing_error
          and '--post-id "fixture-post"' in missing_error,
          "配置缺省时离线拒绝缺少的第2张，并给出零基下标的补图命令")
    settings["require_all_media_de"] = True
    check(raises(ComposeError, lambda: compose_post(
        "fixture-post", WHEN, archive_root=root, warning_sink=None), "德语图"),
        "显式 true 同样阻止原图回退")
    for invalid in ("false", 0, None):
        settings["require_all_media_de"] = invalid
        check(raises(ComposeError, lambda: compose_post(
            "fixture-post", WHEN, archive_root=root, warning_sink=None),
            "require_all_media_de"), "错误配置不能被当成关闭图片闸：%r" % invalid)
    settings["require_all_media_de"] = False
    allowed = compose_post("fixture-post", WHEN, archive_root=root, warning_sink=None)
    check(allowed.image_sources == ("media_de", "original")
          and any("第 2 张" in value for value in allowed.warnings),
          "显式 false 允许原图，但保留来源及警告")
    settings["require_all_media_de"] = True
    Image.new("RGB", (102, 100), (4, 5, 6)).save(media_de / "02.png")
    localized = compose_post("fixture-post", WHEN, archive_root=root, warning_sink=None)
    check(localized.image_sources == ("media_de", "media_de"),
          "所有图片均有德语版本时通过，人工放置的德语图同样有效")

# 下文历史用例专门覆盖原图回退及其它独立闸；明确选择兼容配置。
cfg()._d["publish"]["require_all_media_de"] = False
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
    check(not any("G1" in warning for warning in post.warnings),
          "未测量的 UI 边界不再制造 G1 阻塞警告")


print("\n[] compose 的用户入口：tools/compose_publish.py")
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


print("\n[] 排期时区：Windows 上必须有 tzdata，且夏令时切换日要对")
# 验证 IANA 时区可用，不以固定 UTC 偏移替代。
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


print("\n[2b][] media_de 同序号多候选：改成与 K 组一致的『人工优先』")
# 程序图与人工修订并存时，发布端也须人工优先。
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
          "同一套规则")
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
    rewrite_translation(fixture, text_de="Das Angebot kostet 10 €。\n\n#Neakasa #P1Pro")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "$10"),
          "发布前再次复用 money_preserved，金额被改动会点名原金额")

# 发布前再次核对金额与受保护标签。
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot bleibt bei $10.\n\n#Eins #P1Pro")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "标签"),
          "发布前品牌型号标签被改写会被拦下，"
          "而不是把改错的标签发到德语主页")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot bleibt bei $10.\n\n#Neakasa")
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "标签"),
          "原帖品牌或型号标签被删掉会被拦下")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    fixture = make_fixture(root)
    rewrite_translation(fixture, text_de="Das Angebot bleibt bei $10.\n\n#P1Pro #Neakasa")
    reordered = compose_post("fixture-post", WHEN, archive_root=root, warning_sink=None)
    check(reordered.text_de.endswith("#P1Pro #Neakasa"),
          "品牌型号保持原写法；分区编辑允许运营调整标签顺序")

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
ScheduleWindow("", timedelta(0), None, "America/Los_Angeles")
check(raises(ValueError,
             lambda: ScheduleWindow("  ", timedelta(minutes=1), timedelta(days=1),
                                    "America/Los_Angeles"),
             "空白"),
      "空白不能冒充录证来源；没有录证的配置窗口用空字符串")
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
        fb_post = compose_post(
                "fixture-fb-too-many", WHEN, archive_root=root, now=NOW,
                require_verified_ui_constraints=True, warning_sink=None)
    finally:
        compose_module.cfg = original_cfg
    check(len(post.image_paths) == 2,
          "审核过且数值一致的完整 probe 才能严格组装，空白可选 FB slug 不阻塞")
    check(len(auto_post.image_paths) == 2,
          "strict 模式核验控件证据，不再要求 CLI 手工注入 UI 边界")
    check(len(fb_post.image_paths) == 6,
          "独立 FB 帖子不受旧 IG 人工图片上限影响")
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
    # 月份窗口是日历边界，不是固定提前天数。
    wide = ScheduleWindow(str(recorder.output_path), timedelta(0),
                          timedelta(days=60), "America/Los_Angeles")
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
                     archive_root=root, instagram_constraints=limits,
                     schedule_window=wide, now=NOW, warning_sink=None),
                 "只能选当月"),
          "跨月排期被拒 —— 固定时长窗口放行它，日历边界这道闸不放")
    # 柏林与 UI 时区可能属于不同月份。
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
    # 显式构造未复核状态，不依赖本机配置。
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
# 核验每条定位都有可追溯来源。
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
    """排除注释与字符串常量后检查代码。"""
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
    """清空测试证据注册表，验证缺证据时不触碰浏览器。"""
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
check(all("缺少控件证据：" in item and "处理：" in item for item in gated),
      "缺证据时说明原因并提供处理入口")
check("composer_submit_button" in gated[0]
      and "composer_success_signal" in gated[0],
      "submit() 明确点名它缺的是提交按钮与成功信号两样")
with tempfile.TemporaryDirectory() as folder:
    missing_state = Path(folder)
    with patch.object(bs, "cfg", lambda: VerifiedProbeConfig(
            missing_state, missing_state / "profile",
            missing_state / "publish_probe_missing.json")):
        check(not bs.submission_evidence_ready() and not bs.readback_evidence_ready(),
              "只有登记表但本机缺少实际probe时，提交与回读闸都保持关闭")
check("ui_timezone" in gated[1] and "tools._scaffolding.probe_publish" in gated[1]
      and "--fill-notes" in gated[1],
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


print("\n[10] G2–G5 在一个仿真 composer 上的真实行为")
# 以下使用替代 UI，只验证交互逻辑。


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
                # 模拟 Ctrl+A 选不中分段输入，旧值可能与新值拼接。
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
        """模拟逐键事件，用于触发 @/# 自动补全扰动。"""
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
        """insert_text 触发输入而不触发按键，保留编辑器重写钩子。"""
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
                 on_key_type=None, segmented=False, deferred=False,
                 checked_lag=0):
        self.role = role
        self.name = name
        self.aria_label = aria_label
        self.value = value
        self.text = text
        self.checked = checked
        self.children = list(children)
        self.on_type = on_type
        # 只在**按键**路径上生效：模拟 @/# 的 typeahead 打乱正文。
        self.on_key_type = on_key_type
        # 分段输入（时/分/AM-PM）：Ctrl+A 选不中它，Delete 清不掉。
        self.segmented = segmented
        # 打开定时开关之后才异步渲染出来的元素。
        self.deferred = deferred
        # 状态变化前的读取次数；0 表示立即变化。
        self.checked_lag = checked_lag
        self.pending_checked = None
        self.render = render
        self.clicks = 0

    def accessible_name(self):
        return self.aria_label if self.aria_label is not None else self.name

    def inner_text(self):
        return self.render(self) if self.render is not None else self.text


class FakeLocator:
    """每次操作重新定位，模拟延迟渲染和动态元素。"""

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
            # checked_lag 模拟受控开关异步更新。
            if element.checked_lag > 0:
                element.pending_checked = not element.checked
            else:
                element.checked = not element.checked

    async def is_checked(self):
        element = self._one()
        if element.pending_checked is not None:
            element.checked_lag -= 1
            if element.checked_lag <= 0:
                element.checked = element.pending_checked
                element.pending_checked = None
        return bool(element.checked)

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
            # 模拟分段输入忽略 fill，避免替代 UI 掩盖失败。
            return
        element.value = value
        element.text = value
        # fill 与 type 共用编辑器重写钩子。
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
            continue                      # 还没渲染出来（形状）
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
                  deferred_schedule=False, switch_lag=0):
    caption = FakeElement("combobox", aria_label=CAPTION_NAME,
                          on_type=caption_hook, on_key_type=caption_key_hook)
    add_media = FakeElement("button", "Add photo/video")
    switch = FakeElement("switch", aria_label="Set date and time", checked=False,
                         checked_lag=switch_lag)

    def render_time(element):
        values = [item.value for item in element.children]
        while len(values) < 3:
            values.append("")
        return "%s : %s %s" % (values[0], values[1], values[2])

    # 各渠道独立渲染日期和时间控件。
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
    page.switch = switch
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


# typeahead 仅在逐键输入路径扰乱正文。
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
# 限定 page 调用，排除说明文字中的反例。
check("page.keyboard.type(" not in _write_src
      and "page.keyboard.insert_text(" in _write_src,
      "写正文只用 insert_text，不用 keyboard.type —— "
      "逐字符按键会招出 @/# 的自动补全。"
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
    # 换算测试不依赖宿主设备时区。
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


# 覆盖两地 DST 切换不同步的时间窗口。
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

# 比较目标时刻的设备偏移。
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
      "清空字段必须**回读确认真的空了**，Ctrl+A 没生效时有退格兜底")

_time_src = inspect.getsource(bs._set_one_time)
check("逐字段当前值" in _time_src,
      "时刻回读失败时逐字段打出当前值 —— 只报合成串看不出是哪一格坏的")


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


# ---- ：排期区是**打开定时开关之后才异步渲染**的，`.all()` 不等待 ----
deferred_readback, deferred_parts = asyncio.run(schedule(
    datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc),
    channels=2, deferred_schedule=True))
check(all(item.value == "09/08/2026" for item in deferred_parts["dates"]),
      "G5 排期区晚一拍才渲染时**先等再枚举**，两组都设上了（实得 %r）"
      % [item.value for item in deferred_parts["dates"]])

_sched_src = inspect.getsource(bs.set_schedule)
check(_sched_src.index("wait_for") < _sched_src.index(".all()"),
      "⛔ `.all()` 之前必须先 wait_for —— `.all()` 不等待，"
      "排期区还没渲染出来时它返回 0 个")


# ---- ：React 受控开关的 aria-checked 是异步翻的，点击返回 ≠ 已打开 ----
lag_readback, lag_parts = asyncio.run(schedule(
    datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc), switch_lag=3))
check(lag_parts["switch"].checked and lag_parts["switch"].clicks == 1,
      "G5 定时开关状态晚几拍才翻时**等它翻**，不是立刻判失败；"
      "而且只点了 %d 次 —— 连点会把已经打开的又关回去"
      % lag_parts["switch"].clicks)


async def switch_never_turns_on():
    page, parts = make_composer()
    parts["switch"].checked = False

    async def dead_click(timeout=None):
        parts["switch"].clicks += 1        # 点了，但永远不翻
    original = FakeLocator.click
    try:
        FakeLocator.click = lambda self, timeout=None: dead_click(timeout)
        await set_schedule(page, WHEN, ui_timezone="Europe/Berlin",
                           verify_device=False)
    except PublishStepError as exc:
        return str(exc), parts["switch"].clicks
    finally:
        FakeLocator.click = original
    return "", parts["switch"].clicks


dead_msg, dead_clicks = asyncio.run(switch_never_turns_on())
check("没有被打开" in dead_msg and "aria-checked" in dead_msg,
      "G5 开关真的打不开时失败闭合，并把 aria-checked 一起打出来供排查")
check(dead_clicks == 2,
      "开关打不开时**只补点一次**就放弃（实得 %d 次）——"
      "无限重试会在别的变体上把开关来回拨" % dead_clicks)

check("_SWITCH_ON_BUDGET" in inspect.getsource(bs._switch_is_on),
      "开关等待预算是有上界的常量，不是整个 ui_timeout")

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


# 此夹具只提供 FB 预览身份，IG 由详情回读核验。
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
      "发布账号闸从 composer 预览抬头提取**完整值**后通过")
check(any("IG" in note and "回读" in note for note in strict_context.notes),
      "并且**明说** IG 在 composer 上不显示、由提交后回读证明，不是悄悄跳过")
try:
    asyncio.run(strict_login("Neakasa Deutschland Test"))
except PublishStepError:
    strict_near_collision_blocked = True
else:
    strict_near_collision_blocked = False
check(strict_near_collision_blocked,
      "发布账号闸拒绝 FB 同名前缀 Page（完整值比较，多一个词就是另一个主页）")


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
check(any("尚未核对缩略图数量" in note and "media.verify_upload" in note for note in notes),
      "G3 上传入口说明尚未核验：发布 workflow 另行核对缩略图，旧调用方仍需人工复核")
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
check("" in hung and "F5" in hung,
      "坏页（page.evaluate 超时）当场点名 ，而不是 except: 吞掉后静默什么都不做")


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
with tempfile.TemporaryDirectory() as folder:
    state = Path(folder)
    with patch.object(compose_module, "cfg", lambda: VerifiedProbeConfig(
            state, state / "profile", state / "publish_probe_fixture.json",
            verified=False)), \
            patch.object(publish_entry, "prepare", side_effect=AssertionError("不得碰浏览器")), \
            contextlib.redirect_stdout(io.StringIO()) as captured:
        submit_gate_code = publish_entry.main([
            "--post-id", "does-not-matter", "--at", "2026-09-08T10:00",
            "--submit", "--assume-yes"])
check("ui_constraints_verified" in entry,
      "--submit 那条路径上仍然存在 ui_constraints_verified 这道闸")
check(submit_gate_code == 2 and "ui_constraints_verified" in captured.getvalue(),
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

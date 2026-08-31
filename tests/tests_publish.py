"""G0/G0b 与 G1 recorder 的离线测试；零浏览器、零对外发布。"""
from __future__ import annotations

import ast
import asyncio
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
    make_fixture(root, media_complete=False)
    check(raises(ComposeError,
                 lambda: compose_post("fixture-post", WHEN, archive_root=root,
                                      warning_sink=None),
                 "media_complete=False"),
          "只拿到轮播封面时不发布残缺内容")

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

limits = InstagramConstraints(
    probe_dump="state/publish_probe_fixture.json",
    min_aspect_ratio=0.5,
    max_aspect_ratio=2.0,
    max_images=5,
    max_caption_length=1000,
    caption_length_mode="codepoints",
    max_hashtags=10)
window = ScheduleWindow(
    probe_dump="state/publish_probe_fixture.json",
    min_ahead=timedelta(hours=1),
    max_ahead=timedelta(days=10))
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    make_fixture(root)
    post = compose_post(
        "fixture-post", WHEN, archive_root=root,
        instagram_constraints=limits, schedule_window=window, now=NOW,
        require_verified_ui_constraints=True, warning_sink=None)
    check(len(post.image_paths) == 2,
          "四类 IG 约束与定时窗口都有 probe 来源时可完成严格组装")
    check(not any("尚无 G1" in item or "只是 API 占位" in item
                  for item in post.warnings),
          "严格组装不再携带『约束未知』假绿警告")

with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "archive"
    make_fixture(root)
    too_few = InstagramConstraints(
        probe_dump="state/publish_probe_fixture.json",
        min_aspect_ratio=0.5, max_aspect_ratio=2.0, max_images=1,
        max_caption_length=1000, caption_length_mode="codepoints",
        max_hashtags=10)
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN, archive_root=root,
                     instagram_constraints=too_few, schedule_window=window,
                     now=NOW, require_verified_ui_constraints=True,
                     warning_sink=None),
                 "图片数"),
          "注入的 G1 图片数上限会在浏览器前生效")

    base_limits = {
        "probe_dump": "state/publish_probe_fixture.json",
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
                         require_verified_ui_constraints=True,
                         warning_sink=None),
                     expected),
              "注入的 G1 %s上限会在浏览器前生效" % expected)
    check(raises(ComposeError,
                 lambda: compose_post(
                     "fixture-post", WHEN, archive_root=root,
                     instagram_constraints=limits,
                     schedule_window=ScheduleWindow(
                         "state/publish_probe_fixture.json",
                         timedelta(hours=1), timedelta(days=2)),
                     now=NOW, require_verified_ui_constraints=True,
                     warning_sink=None),
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


print("\n[5] G2–G7 只有会先失败的契约骨架，selectors 仍为空")
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
check("className" not in script and "cssPath" not in script,
      "dump 不记录混淆 class 或脆 CSS path")
check(all(forbidden not in script for forbidden in (
    ".click(", ".fill(", ".goto(", "setInputFiles(", "querySelector(")),
    "监听脚本没有点击/填写/导航/上传等页面驱动")


class FakePage:
    url = "https://business.example.invalid/create"

    async def screenshot(self, *, path, full_page):
        Path(path).write_bytes(b"fixture-png")


async def record_fixture(state_dir: Path):
    recorder = ProbeRecorder(
        state_dir, port=9223, profile=Path("publish-profile"),
        timestamp="20260831_120000")
    await recorder.record(FakePage(), {
        "event_type": "click",
        "page_url": FakePage.url,
        "target": {
            "tag": "button", "role": "button", "aria_label": "Create",
            "data_testid": "create", "name": "", "placeholder": "",
            "visible_text": "Create post", "is_contenteditable": False,
        },
        "candidates": [],
    })
    await recorder.set_observations({"ui_timezone": "fixture timezone"})
    await recorder.finish()
    return recorder


with tempfile.TemporaryDirectory() as d:
    recorder = asyncio.run(record_fixture(Path(d) / "state"))
    data = json.loads(recorder.output_path.read_text(encoding="utf-8"))
    item = data["interactions"][0]
    check(recorder.output_path.name == "publish_probe_20260831_120000.json",
          "dump 文件名符合 state/publish_probe_<时间戳>.json 契约")
    check(len(data["interactions"]) == 1 and item["sequence"] == 1,
          "每次人工交互按顺序持久化")
    check(Path(item["screenshot"]).is_file() and item["screenshot_error"] is None,
          "每条交互都有对应截图")
    check(data["observations"]["ui_timezone"] == "fixture timezone",
          "时区/窗口等人工观察与事件 dump 存在同一份记录里")
    check(data["mode"] == "record-only" and data["finished_at"],
          "dump 明确标记只记录模式，并在正常收尾时写完成时间")


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

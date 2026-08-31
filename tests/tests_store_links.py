"""归档路径安全回归：目录/叶子链接不得把写入导向其它帖子或账号外。"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.console import force_utf8  # noqa: E402
from core.store import Archive, ArchivePathError, Media, Post  # noqa: E402

force_utf8()

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def make_post(pid, text="new text", media=None):
    return Post(post_id=pid, platform="instagram", account="acct", text=text,
                created_at="2026-08-30T12:00:00Z", owner="acct",
                media=media or [])


def make_directory_link(link, target):
    """优先 symlink；无 Windows 权限时用无需提权的 junction。"""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("无法创建测试 junction：" + result.stderr.strip())


def expect_rejected(call, label):
    try:
        call()
    except ArchivePathError as exc:
        check(any(word in str(exc) for word in ("symlink", "junction", "reparse", "hardlink")),
              label + " 明确说明链接/reparse 风险")
        return True
    except Exception as exc:
        check(False, f"{label} 抛出了错误类型 {type(exc).__name__}: {exc}")
        return False
    check(False, label + " 未被拒绝")
    return False


print("[1] expected post dir 链到 posts/ 内另一帖时不得覆写")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    victim = make_post("victim", text="VICTIM")
    arc.append(victim)
    victim_dir = arc.post_dir(victim)
    victim_post_before = (victim_dir / "post.json").read_bytes()
    victim_text_before = (victim_dir / "text.txt").read_bytes()
    manifest_before = arc.manifest.read_bytes()

    attacker = make_post("attacker", text="OVERWRITE")
    attacker_dir = arc.post_dir(attacker)
    make_directory_link(attacker_dir, victim_dir)
    check(attacker_dir.is_symlink()
          or bool(getattr(attacker_dir, "is_junction", lambda: False)()),
          "测试前置确实建立了 symlink/junction")
    expect_rejected(lambda: arc.append(attacker), "链接帖子目录")
    check((victim_dir / "post.json").read_bytes() == victim_post_before
          and (victim_dir / "text.txt").read_bytes() == victim_text_before,
          "另一帖的 post.json/text.txt 字节完全不变")
    check(arc.manifest.read_bytes() == manifest_before,
          "目录链接攻击失败后 manifest 也不变")

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rebuilt_count = arc.reindex()
    check(rebuilt_count == 1 and {row["post_id"] for row in arc.rows()} == {"victim"},
          "reindex 跳过链接目录，只保留真实直属帖子目录")
    check("不是安全的真实直属目录" in output.getvalue(),
          "reindex 对链接目录给出明确跳过告警")


print("\n[2] post.json / text.txt 叶子链接不得改写账号外 sentinel")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    post = make_post("post_leaf", text="SHOULD NOT WRITE")
    post_dir = arc.post_dir(post)
    post_dir.mkdir()
    outside_post = root / "outside_post_sentinel.json"
    outside_post.write_bytes(b"OUTSIDE POST SENTINEL")
    os.link(outside_post, post_dir / "post.json")
    check((post_dir / "post.json").stat().st_nlink > 1,
          "测试前置确实建立了 post.json hardlink")
    expect_rejected(lambda: arc.append(post), "post.json 叶子链接")
    check(outside_post.read_bytes() == b"OUTSIDE POST SENTINEL",
          "账号外 post.json sentinel 字节不变")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        check(arc.reindex() == 0, "reindex 不读取 hardlink post.json")
    check("post.json 不是普通直属文件" in output.getvalue(),
          "reindex 显式告警并跳过链接 post.json")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    post = make_post("text_leaf", text="SHOULD NOT WRITE")
    post_dir = arc.post_dir(post)
    post_dir.mkdir()
    outside_text = root / "outside_text_sentinel.txt"
    outside_text.write_bytes(b"OUTSIDE TEXT SENTINEL")
    os.link(outside_text, post_dir / "text.txt")
    expect_rejected(lambda: arc.append(post), "text.txt 叶子链接")
    check(outside_text.read_bytes() == b"OUTSIDE TEXT SENTINEL",
          "账号外 text.txt sentinel 字节不变")
    check(not (post_dir / "post.json").exists(),
          "叶子预检先全部完成，text.txt 不安全时 post.json 也未部分写入")

    (post_dir / "post.json").write_text(
        json.dumps(post.to_row(), ensure_ascii=False), encoding="utf-8")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rebuilt_count = arc.reindex()
    check(rebuilt_count == 1, "text.txt 不安全不妨碍普通 post.json 重建索引")
    check(outside_text.read_bytes() == b"OUTSIDE TEXT SENTINEL",
          "reindex 没有跟随 text.txt hardlink 写账号外")
    check("text.txt 不是普通直属文件" in output.getvalue(),
          "reindex 明确告警且不写链接 text.txt")


print("\n[3] 01.jpg 叶子链接不得把媒体下载导向账号外")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    post = make_post("media_leaf", media=[Media(url="https://cdn/1.jpg", kind="image")])
    post_dir = arc.post_dir(post)
    post_dir.mkdir()
    outside_media = root / "outside_media_sentinel.jpg"
    outside_media.write_bytes(b"OUTSIDE MEDIA SENTINEL")
    os.link(outside_media, post_dir / "01.jpg")
    expect_rejected(lambda: arc.media_path(post, 0, "image/jpeg"), "01.jpg 叶子链接")
    check(outside_media.read_bytes() == b"OUTSIDE MEDIA SENTINEL",
          "账号外媒体 sentinel 字节不变")


print("\n[4] archive 根与账号 base 自身也必须是真实直属目录")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    outside = root / "outside_root"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"ROOT SENTINEL")
    linked_root = root / "linked_archive"
    make_directory_link(linked_root, outside)
    expect_rejected(lambda: Archive(linked_root, "acct"), "archive 根目录链接")
    check((outside / "sentinel.txt").read_bytes() == b"ROOT SENTINEL",
          "archive 根链接被拒后外部目录未改写")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    archive_root = root / "archive"
    archive_root.mkdir()
    outside = root / "outside_account"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"ACCOUNT SENTINEL")
    make_directory_link(archive_root / "acct", outside)
    expect_rejected(lambda: Archive(archive_root, "acct"), "账号归档目录链接")
    check((outside / "sentinel.txt").read_bytes() == b"ACCOUNT SENTINEL",
          "账号 base 链接被拒后外部目录未改写")


print("\n[5] 五种允许的静态图片 MIME 使用显式、稳定后缀")
with tempfile.TemporaryDirectory() as temp:
    arc = Archive(Path(temp), "acct")
    post = make_post("mime_suffixes")
    mapping = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/avif": ".avif",
    }
    actual = {mime: arc.media_path(post, index, mime).suffix
              for index, mime in enumerate(mapping)}
    check(actual == mapping,
          "JPEG/PNG/WebP/GIF/AVIF 后缀不依赖系统 mimetypes 注册表")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

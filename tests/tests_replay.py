"""离线 replay 自测：旧帖子可恢复隔离，且历史媒体路径不能越界。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.console import force_utf8  # noqa: E402
from core.store import Archive, Media, Post  # noqa: E402
from tools.replay import _resolve, isolate_stale_post_dirs, relink, run  # noqa: E402

force_utf8()

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def post(pid, day):
    return Post(post_id=pid, platform="instagram", account="acct", text=pid,
                created_at=f"2026-08-{day:02d}T12:00:00Z", owner="acct")


def tree_snapshot(root):
    """目录集合与全部文件字节；用于证明 fail-closed 路径零写盘。"""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        snapshot[rel] = path.read_bytes() if path.is_file() else None
    return snapshot


def make_directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError("无法创建测试 junction：" + result.stderr.strip())


print("[1] dry-run 只列出待隔离目录，不创建也不移动")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    kept = post("keep", 1)
    kept.media = [Media(url="https://cdn/keep.jpg", kind="image")]
    stale = post("stale", 2)
    arc.append(kept)
    arc.append(stale)
    kept_dir = arc.post_dir(kept)
    stale_dir = arc.post_dir(stale)

    broken_dir = arc.posts_dir / "broken_old_dir"
    broken_dir.mkdir()
    (broken_dir / "post.json").write_text("{bad json", encoding="utf-8")

    # 同 ID 的 dated/undated 旧目录只保留预期事实源。
    duplicate_dir = arc.posts_dir / "undated_keep"
    duplicate_dir.mkdir()
    duplicate_media = duplicate_dir / "01.jpg"
    duplicate_media.write_bytes(b"kept image")
    duplicate_row = kept.to_row()
    duplicate_row["created_at"] = ""
    duplicate_row["media"][0]["local_path"] = "posts/undated_keep/01.jpg"
    (duplicate_dir / "post.json").write_text(
        json.dumps(duplicate_row), encoding="utf-8")

    plan = isolate_stale_post_dirs(arc.base, [kept], move=False)
    planned_names = {source.name for source, _ in plan}
    check(planned_names == {stale_dir.name, broken_dir.name, duplicate_dir.name},
          "stale、无效目录及 kept 的重复 truth dir 都列入隔离")
    check(kept_dir.exists() and stale_dir.exists() and broken_dir.exists()
          and duplicate_dir.exists(), "dry-run 后所有源目录均未移动")
    check(not (arc.base / "_orphan_posts").exists(),
          "dry-run 不创建 _orphan_posts 目录")

    print("\n[2] 实际隔离可恢复、不覆盖，并阻止 reindex 复活")
    linked, recovered, missing = relink(
        [kept], {("keep", "https://cdn/keep.jpg"):
                 "posts/undated_keep/01.jpg"}, arc.base, arc, move=True)
    check((linked, recovered, missing) == (1, 0, 0)
          and (kept_dir / "01.jpg").read_bytes() == b"kept image",
          "隔离重复 truth dir 前，先把 kept 媒体重连到唯一预期目录")
    moved = isolate_stale_post_dirs(arc.base, [kept], move=True)
    moved_by_name = {source.name: destination for source, destination in moved}
    stale_orphan = moved_by_name[stale_dir.name]
    check(kept_dir.exists(), "本轮 kept 的帖子目录原位保留")
    check(not stale_dir.exists() and not broken_dir.exists() and not duplicate_dir.exists(),
          "非预期旧目录（含同 ID 重复 truth dir）已离开 posts/")
    check(stale_orphan.parent == arc.base / "_orphan_posts" and stale_orphan.exists(),
          "旧帖子移到同账号 _orphan_posts/，没有删除")
    saved = json.loads((stale_orphan / "post.json").read_text(encoding="utf-8"))
    check(saved["post_id"] == "stale", "隔离目录中的 post.json 原样可恢复")

    rebuilt = Archive(root, "acct")
    check(rebuilt.reindex() == 1
          and {row["post_id"] for row in rebuilt.rows()} == {"keep"},
          "reindex 只重建 kept，旧 stale 帖不会复活")

    shutil.copytree(stale_orphan, stale_dir)
    moved_again = isolate_stale_post_dirs(arc.base, [kept], move=True)
    second_stale = next(destination for source, destination in moved_again
                        if source.name == stale_dir.name)
    check(stale_orphan.exists() and second_stale.exists() and second_stale != stale_orphan,
          "同名旧目录再次隔离时使用后缀，两份都保留不覆盖")

    print("\n[3] replay 读取历史 local_path 时也执行 containment")
    media = arc.base / "posts" / "media_owner" / "01.jpg"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"image")
    check(_resolve(arc.base, "posts/media_owner/01.jpg") == media,
          "账号归档内的正常历史媒体路径仍能解析")
    outside = arc.base.parent / "outside.jpg"
    outside.write_bytes(b"do not touch")
    check(_resolve(arc.base, "../outside.jpg") is None,
          "越过账号目录的历史 local_path 被拒绝")
    check(outside.read_bytes() == b"do not touch", "账号外文件未被读取后搬移或改写")


print("\n[4] 空/错形/零候选/零 kept capture 必须 fail-closed")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "in_acct")
    existing = Post(post_id="existing", platform="instagram", account="acct",
                    text="must survive", created_at="2026-08-01T00:00:00Z",
                    owner="acct")
    arc.append(existing)

    wrong = arc.base / "capture_wrong.json"
    empty = arc.base / "capture_empty.json"
    zero = arc.base / "capture_zero.json"
    rejected = arc.base / "capture_rejected.json"
    wrong.write_text('{"valid_json":"but_not_a_list"}', encoding="utf-8")
    empty.write_text("[]", encoding="utf-8")
    zero.write_text('[{"unrelated":"shape"}]', encoding="utf-8")
    rejected.write_text('[{"synthetic":true}]', encoding="utf-8")

    class FakeConfig(dict):
        def __init__(self, archive_dir):
            super().__init__(targets={"instagram": "acct", "facebook": "page"})
            self.archive_dir = archive_dir

    before_manifest = arc.manifest.read_bytes()
    before_tree = tree_snapshot(arc.base)
    foreign = Post(post_id="foreign", platform="instagram", account="acct",
                   text="not ours", created_at="2026-08-02T00:00:00Z",
                   owner="someone_else")
    with patch("tools.replay.cfg", return_value=FakeConfig(root)):
        rc_wrong = run("instagram", wrong, dry_run=False)
        rc_empty = run("instagram", empty, dry_run=False)
        rc_zero = run("instagram", zero, dry_run=False)
        with patch("tools.replay.extract", return_value=[foreign]):
            rc_rejected = run("instagram", rejected, dry_run=False)

    check([rc_wrong, rc_empty, rc_zero, rc_rejected] == [1, 1, 1, 1],
          "四种危险 capture 都返回非零，不提供清空归档的隐式路径")
    check(arc.manifest.read_bytes() == before_manifest,
          "fail-closed 后 manifest 字节完全不变")
    check(tree_snapshot(arc.base) == before_tree,
          "fail-closed 后 posts、capture 及全部目录/文件字节集合完全不变")


print("\n[5] replay 入口、kept 目标与 known 媒体链接均在写盘前关闭")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    outside = root / "outside_archive"
    (outside / "in_acct").mkdir(parents=True)
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"ROOT LINK SENTINEL")
    linked_root = root / "linked_archive"
    make_directory_link(linked_root, outside)
    with patch("tools.replay.cfg", return_value=FakeConfig(linked_root)):
        rc_root_link = run("instagram", None, dry_run=False)
    check(rc_root_link == 1 and sentinel.read_bytes() == b"ROOT LINK SENTINEL",
          "archive root junction 被入口预检拒绝，账号外未写")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    archive_root = root / "archive"
    archive_root.mkdir()
    outside = root / "outside_account"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"ACCOUNT LINK SENTINEL")
    make_directory_link(archive_root / "in_acct", outside)
    with patch("tools.replay.cfg", return_value=FakeConfig(archive_root)):
        rc_account_link = run("instagram", None, dry_run=False)
    check(rc_account_link == 1 and sentinel.read_bytes() == b"ACCOUNT LINK SENTINEL",
          "account base junction 被入口预检拒绝，账号外未写")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "in_acct")
    victim = post("victim", 1)
    arc.append(victim)
    kept = post("kept_link", 2)
    kept_target = arc.post_dir(kept)
    make_directory_link(kept_target, arc.post_dir(victim))
    capture = arc.base / "capture_kept_link.json"
    capture.write_text('[{"synthetic":true}]', encoding="utf-8")
    before = tree_snapshot(arc.base)
    with patch("tools.replay.cfg", return_value=FakeConfig(root)), \
            patch("tools.replay.extract", return_value=[kept]):
        rc_kept_link = run("instagram", capture, dry_run=False)
    check(rc_kept_link == 1 and tree_snapshot(arc.base) == before,
          "kept 期望目录 junction 在备份/重连/隔离前失败，归档树不变")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "in_acct")
    kept = post("known_link", 3)
    kept.media = [Media(url="https://cdn/known.jpg", kind="image")]
    media_dir = arc.post_dir(kept)
    media_dir.mkdir(parents=True)
    outside_media = root / "outside_known_media.jpg"
    outside_media.write_bytes(b"KNOWN MEDIA SENTINEL")
    linked_media = media_dir / "01.jpg"
    linked_media.write_bytes(b"ORIGINAL LOCAL MEDIA")
    kept.media[0].local_path = str(linked_media.relative_to(arc.base)).replace("\\", "/")
    arc.append(kept)
    # 归档入口已拒绝 hardlink；在正常入档后模拟外部替换，验证 replay 也会拒绝。
    linked_media.unlink()
    os.link(outside_media, linked_media)
    capture = arc.base / "capture_known_link.json"
    capture.write_text('[{"synthetic":true}]', encoding="utf-8")
    before = tree_snapshot(arc.base)
    manifest_before = arc.manifest.read_bytes()
    with patch("tools.replay.cfg", return_value=FakeConfig(root)), \
            patch("tools.replay.extract", return_value=[kept]):
        rc_known_link = run("instagram", capture, dry_run=False)
    check(rc_known_link == 1, "known 媒体 hardlink 使 replay 明确失败")
    check(arc.manifest.read_bytes() == manifest_before and tree_snapshot(arc.base) == before
          and outside_media.read_bytes() == b"KNOWN MEDIA SENTINEL",
          "known 媒体链接失败发生在任何备份/移动/索引改写前")


print("\n[6] kept 真相叶、账号内源链接与既有媒体冲突均整体 fail-closed")
for unsafe_leaf in ("post.json", "text.txt"):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        arc = Archive(root, "in_acct")
        kept = post("kept_truth_leaf", 4)
        target = arc.post_dir(kept)
        target.mkdir(parents=True)
        outside_leaf = root / ("outside_" + unsafe_leaf.replace(".", "_"))
        outside_leaf.write_bytes(("TRUTH LEAF " + unsafe_leaf).encode())
        os.link(outside_leaf, target / unsafe_leaf)
        capture = arc.base / ("capture_" + unsafe_leaf.replace(".", "_") + ".json")
        capture.write_text('[{"synthetic":true}]', encoding="utf-8")
        before = tree_snapshot(arc.base)
        with patch("tools.replay.cfg", return_value=FakeConfig(root)), \
                patch("tools.replay.extract", return_value=[kept]):
            rc = run("instagram", capture, dry_run=False)
        check(rc == 1 and tree_snapshot(arc.base) == before,
              f"kept {unsafe_leaf} hardlink 在任何备份/移动/写索引前失败")
        check(outside_leaf.read_bytes() == ("TRUTH LEAF " + unsafe_leaf).encode()
              and not list(arc.base.glob("manifest.jsonl.*.bak")),
              f"kept {unsafe_leaf} hardlink 的账号外 sentinel 与归档备份集合不变")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "in_acct")
    kept = post("source_dir_link", 5)
    kept.media = [Media(url="https://cdn/source-link.jpg", kind="image")]
    real_source_dir = arc.base / "media-real"
    real_source_dir.mkdir()
    real_source = real_source_dir / "source.jpg"
    real_source.write_bytes(b"SOURCE LINK SENTINEL")
    linked_source_dir = arc.base / "media-linked"
    make_directory_link(linked_source_dir, real_source_dir)
    row = kept.to_row()
    row["media"][0]["local_path"] = "media-linked/source.jpg"
    arc.manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    capture = arc.base / "capture_source_dir_link.json"
    capture.write_text('[{"synthetic":true}]', encoding="utf-8")
    before = tree_snapshot(arc.base)
    with patch("tools.replay.cfg", return_value=FakeConfig(root)), \
            patch("tools.replay.extract", return_value=[kept]):
        rc = run("instagram", capture, dry_run=False)
    check(rc == 1 and tree_snapshot(arc.base) == before
          and real_source.read_bytes() == b"SOURCE LINK SENTINEL",
          "resolve 后仍在账号内的媒体源 symlink/junction 也在写盘前拒绝")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "in_acct")
    kept = post("existing_media", 6)
    kept.media = [Media(url="https://cdn/existing.jpg", kind="image")]
    source_dir = arc.base / "media"
    source_dir.mkdir()
    source = source_dir / "source.jpg"
    source.write_bytes(b"ORIGINAL SOURCE")
    target = arc.post_dir(kept)
    target.mkdir(parents=True)
    destination = target / "01.jpg"
    destination.write_bytes(b"EXISTING MEDIA SENTINEL")
    row = kept.to_row()
    row["media"][0]["local_path"] = "media/source.jpg"
    arc.manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    capture = arc.base / "capture_existing_media.json"
    capture.write_text('[{"synthetic":true}]', encoding="utf-8")
    before = tree_snapshot(arc.base)
    with patch("tools.replay.cfg", return_value=FakeConfig(root)), \
            patch("tools.replay.extract", return_value=[kept]):
        rc = run("instagram", capture, dry_run=False)
    check(rc == 1 and tree_snapshot(arc.base) == before,
          "kept 目标已有普通媒体时 replay 整体失败且归档树字节不变")
    check(source.read_bytes() == b"ORIGINAL SOURCE"
          and destination.read_bytes() == b"EXISTING MEDIA SENTINEL"
          and not list(arc.base.glob("manifest.jsonl.*.bak")),
          "replay 不覆盖既有媒体、不搬源，也不先产生 manifest 备份")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

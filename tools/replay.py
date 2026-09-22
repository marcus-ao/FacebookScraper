"""从 capture 离线重建归档并重连已有媒体；不重新下载，未关联文件移入可恢复的孤儿目录。"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import maintenance
from core import integrity                                   # noqa: E402
from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from core.parse import extract, partition_by_owner           # noqa: E402
from core.store import (Archive, ArchivePathError, Post,      # noqa: E402
                        assert_physical_direct_path, assert_post_directory,
                        iter_post_dirs, planned_post_directory)

PREFIX = {"facebook": "fa", "instagram": "in"}


def newest_capture(base: Path) -> Path | None:
    """取最新回填转储，排除仅含近期内容的增量转储。"""
    caps = sorted(p for p in base.glob("_capture_*.json")
                  if not p.name.startswith("_capture_delta_"))
    return caps[-1] if caps else None


def old_local_paths(base: Path) -> dict[tuple[str, str], str]:
    """按 (post_id, URL) 重连媒体；依次读历史备份与当前 manifest，后者优先。"""
    out: dict[tuple[str, str], str] = {}
    live = base / "manifest.jsonl"
    backups = sorted(p for p in base.glob("manifest.jsonl*") if p != live)
    for path in backups + [live]:
        assert_physical_direct_path(
            base, path, kind="file", label=f"历史 manifest {path.name}")
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                for m in row.get("media") or []:
                    if m.get("local_path") and m.get("url"):
                        out[(row["post_id"], m["url"])] = m["local_path"]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return out


def _contained(path: Path, root: Path) -> bool:
    """``path`` resolve 后是否仍在 ``root`` 内（包括 root 自身）。"""
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _resolve_relative_file(base: Path, rel: str) -> Path | None:
    """沿每一级真实直属目录解析账号内相对文件；不安全路径直接抛错。"""
    try:
        rel_path = Path(rel)
    except TypeError as exc:
        raise ArchivePathError(f"历史媒体路径不是字符串：{rel!r}") from exc
    if rel_path.is_absolute() or rel_path.drive or ".." in rel_path.parts:
        raise ArchivePathError(f"历史媒体路径不得是绝对路径或包含 ..：{rel}")
    if not rel_path.parts:
        raise ArchivePathError("历史媒体路径为空")

    parent = base
    for part in rel_path.parts[:-1]:
        directory = parent / part
        assert_physical_direct_path(
            parent, directory, kind="directory", label="历史媒体父目录")
        if not directory.exists():
            return None
        parent = directory
    leaf = parent / rel_path.parts[-1]
    assert_physical_direct_path(
        parent, leaf, kind="file", label="历史媒体文件")
    return leaf if leaf.exists() else None


def _resolve(base: Path, rel: str, *, fail_unsafe: bool = False) -> Path | None:
    """在当前布局、旧媒体目录及 _orphan_media 中寻找已有文件。"""
    try:
        direct = _resolve_relative_file(base, rel)
        if direct is not None:
            return direct
        return _resolve_relative_file(
            base, str(Path("_orphan_media") / Path(rel).name))
    except ArchivePathError:
        if fail_unsafe:
            raise
        return None


def preflight_replay_paths(base: Path, posts: list[Post],
                           known: dict[tuple[str, str], str]) -> None:
    """写入前核验全部目标与历史媒体路径，含无媒体帖；链接或冲突导致整批拒绝。"""
    posts_root = assert_physical_direct_path(
        base, base / "posts", kind="directory", label="posts 根目录")
    if not posts_root.exists():
        raise ArchivePathError("posts 根目录不存在")

    planned_media: dict[Path, Path] = {}
    for post in posts:
        target = planned_post_directory(base, post)
        assert_physical_direct_path(
            target, target / "post.json", kind="file",
            label=f"kept post.json {post.post_id}")
        assert_physical_direct_path(
            target, target / "text.txt", kind="file",
            label=f"kept text.txt {post.post_id}")
        for index, media in enumerate(post.media):
            if media.kind == "video":
                continue
            rel = known.get((post.post_id, media.url))
            if not rel:
                continue
            source = _resolve(base, rel, fail_unsafe=True)
            if source is None:
                continue
            suffix = source.suffix or ".jpg"
            destination = assert_physical_direct_path(
                target, source if source.parent == target else target / f"{index + 1:02d}{suffix}", kind="file",
                label=f"kept 媒体目标 {post.post_id}[{index}]")
            # 相同路径无需移动；其它既有文件一律视为冲突，不覆盖。
            if (destination.exists()
                    and source.resolve(strict=True) != destination.resolve(strict=True)):
                raise ArchivePathError(
                    f"kept 媒体目标已存在，拒绝覆盖：{destination}")
            previous_source = planned_media.get(destination)
            if (previous_source is not None
                    and previous_source.resolve(strict=True)
                    != source.resolve(strict=True)):
                raise ArchivePathError(
                    f"多个历史媒体会写入同一 kept 目标，拒绝覆盖：{destination}")
            planned_media[destination] = source


def _available_orphan_path(root: Path, name: str) -> Path:
    """返回不覆盖既有隔离数据的目标路径。"""
    candidate = root / name
    suffix = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = root / f"{name}.{suffix}"
        suffix += 1
    return candidate


def isolate_stale_post_dirs(base: Path, posts: list[Post],
                            move: bool) -> list[tuple[Path, Path]]:
    """将非预期帖子目录移入 _orphan_posts，保持同 ID 唯一事实源；move=False 只返回计划。"""
    posts_root = base / "posts"
    if not posts_root.exists():
        return []
    if not _contained(posts_root, base):
        raise ValueError("posts 越过账号归档目录边界")

    kept_dirs = {planned_post_directory(base, post) for post in posts}
    orphan_root = base / "_orphan_posts"
    if not _contained(orphan_root, base):
        raise ValueError("_orphan_posts 越过账号归档目录边界")

    planned: list[tuple[Path, Path]] = []
    for directory in iter_post_dirs(base):
        if directory in kept_dirs:
            continue

        destination = _available_orphan_path(orphan_root, directory.name)
        if not _contained(destination, orphan_root):
            raise ValueError("隔离目标越过 _orphan_posts 边界")
        planned.append((directory, destination))

        if move:
            orphan_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(directory), str(destination))
    return planned


def relink(posts: list[Post], known: dict[tuple[str, str], str],
           base: Path, arc: Archive, move: bool) -> tuple[int, int, int]:
    """返回重连数、孤儿目录恢复数及缺失数；move=False 仅统计。"""
    linked = recovered = missing = 0
    for post in posts:
        for i, m in enumerate(post.media):
            if m.kind == "video":
                continue                      # 视频从不下载，本来就没有本地文件
            rel = known.get((post.post_id, m.url))
            if not rel:
                continue
            src = _resolve(base, rel, fail_unsafe=True)
            if src is None:
                missing += 1
                continue
            want = None
            if move:
                target = planned_post_directory(base, post)
                target.mkdir(parents=True, exist_ok=True)
                assert_post_directory(base, target)
                want = assert_physical_direct_path(
                    target, src if src.parent == target else target / f"{i + 1:02d}{src.suffix or '.jpg'}",
                    kind="file", label="媒体重连目标")
            if want is not None and src.resolve() != want.resolve():
                # 保留真实扩展名。
                if want.exists():
                    raise ArchivePathError(f"媒体重连目标已存在，拒绝覆盖：{want}")
                shutil.move(str(src), str(want))
                assert_physical_direct_path(
                    want.parent, want, kind="file", label="媒体重连目标")
                if not want.exists() or src.exists():
                    raise ArchivePathError(
                        f"媒体搬移后的物理状态不符合预期：{src} -> {want}")
                if "_orphan_media" in str(src):
                    recovered += 1
                src = want
            elif "_orphan_media" in str(src):
                recovered += 1
            m.local_path = str(src.relative_to(base)).replace("\\", "/") \
                if move else rel
            linked += 1
    return linked, recovered, missing


def run(platform: str, capture: Path | None, dry_run: bool) -> int:
    c = cfg()
    account = c["targets"][platform]
    archive_root = Path(c.archive_dir)
    base = archive_root / f"{PREFIX[platform]}_{account}"
    try:
        assert_physical_direct_path(
            archive_root.parent, archive_root, kind="directory", label="archive 根目录")
        assert_physical_direct_path(
            archive_root, base, kind="directory", label="账号归档目录")
    except ArchivePathError as exc:
        print("[!] 归档入口路径不安全，本次 replay 未执行：%s" % exc)
        return 1
    if not base.exists():
        print("[!] 找不到归档目录 %s —— 先跑一次回填" % base)
        return 1

    cap = capture or newest_capture(base)
    if cap is None or not cap.exists():
        print("[!] 找不到 capture 文件。回填时应该在 %s 下留了 _capture_*.json" % base)
        return 1

    print("平台     : %s / %s" % (platform, account))
    print("capture  : %s  (%d KB)" % (cap.name, cap.stat().st_size // 1024))

    payloads = json.loads(cap.read_text(encoding="utf-8"))
    if not isinstance(payloads, list) or not payloads:
        shape = "空数组" if isinstance(payloads, list) else type(payloads).__name__
        print("[!] capture 顶层必须是非空 JSON 数组，实得 %s。"
              "为防止误清空归档，本次未写盘。" % shape)
        return 1
    print("响应段数 : %d" % len(payloads))

    posts = extract(payloads, platform, account, route="backfill")
    if not posts:
        print("[!] capture 未解析出任何候选帖子。可能是 capture 内容不对或解析器回归；"
              "为防止误清空归档，本次未写盘。")
        return 1
    kept, rejected = partition_by_owner(posts, account)
    if not kept:
        print("[!] 候选帖子全部被归属校验丢弃（候选 %d / kept 0）。"
              "为防止误隔离整个 posts/ 并写空 manifest，本次未写盘。" % len(posts))
        return 1

    manifest = base / "manifest.jsonl"
    try:
        known = old_local_paths(base)
        preflight_replay_paths(base, kept, known)
    except ArchivePathError as exc:
        print("[!] replay 路径预检失败，本次未备份、移动或改写任何文件：%s" % exc)
        return 1
    before = sum(1 for line in manifest.read_text(encoding="utf-8").splitlines()
                 if line.strip()) if manifest.exists() else 0

    target = (account or "").strip().lower()
    authored = [p for p in kept if p.owner == target]
    collab = [p for p in kept if p.owner != target]
    imgs = sum(1 for p in kept for m in p.media if m.kind == "image")
    vids = sum(1 for p in kept for m in p.media if m.kind == "video")
    others = sorted({r["owner_name"] or r["owner"] or "(归属未知)" for r in rejected})

    print()
    print("解析出候选   : %d" % len(posts))
    print("本账号保留   : %d   （旧 manifest 有 %d 行，差 %+d）"
          % (len(kept), before, len(kept) - before))
    print("  其中原创   : %d" % len(authored))
    print("  其中合作帖 : %d   （别人发布、本账号是 coauthor，同样在本账号主页上）"
          % len(collab))
    print("丢弃         : %d   来自 %d 个其它账号/未知归属" % (len(rejected), len(others)))
    if others:
        print("               %s%s" % (", ".join(others[:6]),
                                       " …" if len(others) > 6 else ""))
    suspect = integrity.check_dropped_partners(
        rejected, integrity.known_partners([p.to_row() for p in kept], account))
    authorized, third_party = integrity.split_suspect_sources(suspect, platform)
    if third_party:
        print("[!] 丢弃的里面有 %d 篇来自**已知合作方**（%s）—— "
              "合作帖判定可能漏判了，这几篇很可能就在本账号主页上"
              % (len(third_party), integrity.name_suspect_owners(third_party, 6)))
    if authorized:
        print("[!] 丢弃的里面有 %d 篇来自**已授权来源**（%s）—— "
              "多半是本品牌另一账号重发同一批文案，核对 post_id 与正文是否重复"
              % (len(authorized), integrity.name_suspect_owners(authorized, 6)))
    print("媒体         : 图片 %d / 视频 %d（视频只记元数据，不下载）" % (imgs, vids))
    print("正文为空     : %d" % sum(1 for p in kept if not (p.text or "").strip()))
    print("无日期       : %d" % sum(1 for p in kept if not p.created_at))

    stale_plan = isolate_stale_post_dirs(base, kept, move=False)
    print("旧帖子待隔离 : %d 个目录" % len(stale_plan))
    for source, destination in stale_plan:
        print("  - posts/%s -> _orphan_posts/%s" % (source.name, destination.name))

    if dry_run:
        linked, recovered, missing = relink(kept, known, base, None, move=False)
        print("媒体可关联   : %d 个（其中 %d 个要从 _orphan_media/ 捞回来）；"
              "%d 个记录里有路径但文件已不在" % (linked, recovered, missing))
        print("\n--dry-run：什么都没写。去掉这个参数才会真正重建。")
        return 0

    # 首次写入前再次完整核验，防止计划生成后目标被替换。
    try:
        preflight_replay_paths(base, kept, known)
    except ArchivePathError as exc:
        print("[!] replay 路径在执行前变得不安全，本次未备份、移动或改写任何文件：%s"
              % exc)
        return 1

    # 先备份并重连媒体，再隔离旧目录，最后重建；中断时原索引与备份仍可恢复。
    if manifest.exists():
        # 时间戳备份不覆盖历史记录，保留重连媒体所需路径。
        bak = base / ("manifest.jsonl.%d.bak" % int(time.time()))
        bak = _available_orphan_path(base, bak.name)
        shutil.copy2(manifest, bak)
        print("\n旧 manifest 已备份 → %s" % bak.name)

    # 重建拒绝记录，避免已修正的归属仍显示为被丢弃。
    rej_path = base / "_rejected.jsonl"
    if rej_path.exists():
        rej_bak = base / ("_rejected.jsonl.%d.bak" % int(time.time()))
        rej_bak = _available_orphan_path(base, rej_bak.name)
        shutil.copy2(rej_path, rej_bak)
        print("旧 _rejected.jsonl 已备份")

    path_arc = Archive(base.parent, base.name)  # 仅用于算本轮唯一的目标媒体路径
    linked, recovered, missing = relink(kept, known, base, path_arc, move=True)
    print("媒体重新关联 : %d 个文件挂上（其中 %d 个从 _orphan_media/ 捞回）；"
          "%d 个记录里有路径但文件已不在" % (linked, recovered, missing))

    isolated = isolate_stale_post_dirs(base, kept, move=True)
    if isolated:
        print("旧帖子已隔离 : %d 个目录（可从 _orphan_posts/ 恢复）" % len(isolated))

    if manifest.exists():
        manifest.unlink()
    if rej_path.exists():
        rej_path.unlink()

    arc = Archive(base.parent, base.name)      # 重新加载（此刻 manifest 为空）
    for post in kept:
        arc.append(post)
    written = arc.reindex()
    print("写入 manifest : %d 条" % written)

    n_rej = arc.record_rejected(rejected)
    print("写入 _rejected.jsonl : %d 条" % n_rej)

    left = list((base / "_orphan_media").glob("*")) if (base / "_orphan_media").exists() else []
    print("_orphan_media/ 剩余 : %d 个（**从来只是移动，没有删除**）" % len(left))

    stale_raw = [f for f in (base / "raw").glob("*.json")
                 if f.stem not in {p.post_id for p in kept}]
    if stale_raw:
        print("提示：raw/ 下有 %d 个已不对应任何帖子的文件，未动。"
              "它们只是 to_row() 的副本，可以整个删掉。" % len(stale_raw))
    return 0


@maintenance.guarded('replay_cli')
def main(argv=None) -> int:
    force_utf8()
    ap = argparse.ArgumentParser(
        description="用已保存的 capture 离线重建归档（B7）。不重新下载媒体。")
    ap.add_argument("platform", choices=sorted(PREFIX))
    ap.add_argument("--capture", type=Path, default=None,
                    help="指定 capture 文件；不给则取该账号最新的一份")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写盘")
    args = ap.parse_args(argv)
    return run(args.platform, args.capture, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

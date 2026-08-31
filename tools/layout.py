r"""归档布局工具。对应实施计划的 J 组。

三个子命令：

    python -m tools.layout migrate  <facebook|instagram>   扁平布局 → 每帖一个文件夹
    python -m tools.layout reindex  <facebook|instagram>   从 posts/ 重建 manifest.jsonl
    python -m tools.layout index    <facebook|instagram>   生成 index.html 总览

`migrate` 只需要跑一次（J1 的迁移）。`reindex` 是"文件夹与索引冲突时以文件夹
为准"那条规则的执行者，随时可跑。`index` 是给业务同事看的那份，随时可重生成。

全部支持 `--dry-run`。
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from core.store import (                                     # noqa: E402
    Archive, ArchivePathError, assert_physical_direct_path, post_dirname,
)

PREFIX = {"facebook": "fa", "instagram": "in"}


def archive_base(platform: str):
    c = cfg()
    account = c["targets"][platform]
    return c.archive_dir / f"{PREFIX[platform]}_{account}", account


def _resolve_migration_source(base: Path, rel: str) -> Path | None:
    """只接受账号目录内逐级真实、叶子为单链接普通文件的相对路径。"""
    try:
        relative = Path(rel)
    except TypeError as exc:
        raise ArchivePathError(f"媒体路径不是字符串：{rel!r}") from exc
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ArchivePathError(f"媒体路径不得是绝对路径或包含 ..：{rel}")
    if not relative.parts:
        raise ArchivePathError("媒体路径为空")

    parent = base
    for part in relative.parts[:-1]:
        directory = assert_physical_direct_path(
            parent, parent / part, kind="directory", label="媒体源父目录")
        if not directory.exists():
            return None
        parent = directory
    source = assert_physical_direct_path(
        parent, parent / relative.parts[-1], kind="file", label="媒体源文件")
    return source if source.exists() else None


# --------------------------------------------------------------------------
# migrate
# --------------------------------------------------------------------------

def migrate(base: Path, dry_run: bool) -> int:
    """把 `media/<post_id>_<n>.ext` 搬进 `posts/<日期>_<时分>_<post_id>/NN.ext`。

    **搬运用 move 而不是 copy**：归档已经上 GB，复制一份纯属浪费；
    而且真出问题时"文件到底在哪一份里"会变成新的麻烦。
    迁移前会把 manifest 备份成 `manifest.jsonl.premigrate`，可回退。
    """
    base = Path(base)
    manifest = base / "manifest.jsonl"
    try:
        assert_physical_direct_path(
            base.parent, base, kind="directory", label="账号归档目录")
        assert_physical_direct_path(
            base, manifest, kind="file", label="manifest.jsonl")
    except ArchivePathError as exc:
        print("[!] manifest 路径不安全，本次迁移未执行：%s" % exc)
        return 1
    if not manifest.exists():
        print("[!] 没有 manifest.jsonl，先跑回填或 tools.replay")
        return 1

    rows = [json.loads(line) for line in
            manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    posts_dir = base / "posts"
    print("帖子 %d 篇 → %s" % (len(rows), posts_dir))

    moved = missing = unmigratable_sources = unsafe_targets = undated = 0
    backup = base / "manifest.jsonl.premigrate"
    plan: list[tuple[Path, Path, dict, int, str]] = []
    row_targets: list[tuple[dict, Path]] = []
    planned_destinations: dict[Path, Path] = {}

    posts_safe = True
    for target, kind, label in (
            (posts_dir, "directory", "posts 根目录"),
            (backup, "file", "迁移前 manifest 备份")):
        try:
            assert_physical_direct_path(base, target, kind=kind, label=label)
        except ArchivePathError as exc:
            unsafe_targets += 1
            print("    ! 迁移目标路径不安全，未搬运：%s" % exc)
            if target == posts_dir:
                posts_safe = False

    for row in rows:
        dirname = post_dirname(row["post_id"], row.get("created_at"))
        if dirname.startswith("undated_"):
            undated += 1
        post_target = posts_dir / dirname
        if not posts_safe:
            continue
        try:
            assert_physical_direct_path(
                posts_dir, post_target, kind="directory", label="帖子目标目录")
            assert_physical_direct_path(
                post_target, post_target / "post.json", kind="file", label="post.json")
            assert_physical_direct_path(
                post_target, post_target / "text.txt", kind="file", label="text.txt")
        except ArchivePathError as exc:
            unsafe_targets += 1
            print("    ! 迁移目标路径不安全，未搬运帖子 %s：%s"
                  % (row["post_id"], exc))
            continue
        row_targets.append((row, post_target))

        for idx, m in enumerate(row.get("media") or []):
            rel = m.get("local_path")
            if not rel:
                continue
            try:
                # resolve 后仍在账号内并不足够：账号内的 symlink/junction 或
                # hardlink 也不能作为可搬源。每一级与叶子都做物理检查。
                src = _resolve_migration_source(base, rel)
            except (ArchivePathError, OSError, RuntimeError, TypeError, ValueError):
                unmigratable_sources += 1
                print("    ! 媒体路径无法解析或越过账号归档目录，"
                      "或不是独立真实文件，未搬运：%s" % rel)
                continue
            if src is None:
                missing += 1
                continue
            dst = post_target / ("%02d%s" % (idx + 1, src.suffix))
            try:
                assert_physical_direct_path(
                    post_target, dst, kind="file", label="媒体目标文件")
            except ArchivePathError as exc:
                unsafe_targets += 1
                print("    ! 迁移目标路径不安全，未搬运媒体 %s：%s" % (rel, exc))
                continue
            if dst.exists() and src.resolve(strict=True) != dst.resolve(strict=True):
                unsafe_targets += 1
                print("    ! 媒体目标已存在，拒绝覆盖，未搬运媒体 %s：%s" % (rel, dst))
                continue
            previous_source = planned_destinations.get(dst)
            if (previous_source is not None
                    and previous_source.resolve(strict=True) != src.resolve(strict=True)):
                unsafe_targets += 1
                print("    ! 多个媒体源会写入同一目标，拒绝覆盖：%s" % dst)
                continue
            planned_destinations[dst] = src
            if dst.exists():
                # 源和目标就是同一个已迁移文件，无需再次 move。
                m["local_path"] = str(dst.relative_to(base)).replace("\\", "/")
                continue
            plan.append((src, dst, m, idx, rel))

    print("待搬运媒体 %d 个；记录里有路径但文件不在的 %d 个；"
            "源路径无法解析、越界或不是独立真实文件、无法迁移的 %d 个；"
          "目标路径不安全的 %d 个；无日期帖子 %d 篇"
          % (len(plan), missing, unmigratable_sources, unsafe_targets, undated))
    if unsafe_targets:
        print("[!] 检测到不安全的迁移目标；为避免账号外写入或部分迁移，"
              "本次整体拒绝执行，未移动或写入任何文件。")
        return 1
    if dry_run:
        for src, dst, _, _, _ in plan[:3]:
            print("   %s  ->  %s" % (src.name, dst.relative_to(base)))
        print("\n--dry-run：什么都没动。")
        return 0

    try:
        # 规划和执行各校验一次；若两者之间路径被替换成链接，执行阶段仍关闭。
        manifest = assert_physical_direct_path(
            base, manifest, kind="file", label="manifest.jsonl")
        backup = assert_physical_direct_path(
            base, backup, kind="file", label="迁移前 manifest 备份")
        posts_dir = assert_physical_direct_path(
            base, posts_dir, kind="directory", label="posts 根目录")
        posts_dir.mkdir(parents=True, exist_ok=True)
        assert_physical_direct_path(
            base, posts_dir, kind="directory", label="posts 根目录")

        # 备份也属于写盘。所有会搬的源/目标在 copy2 之前统一复核，目标冲突
        # 不得等到已经产生备份或搬了一半才暴露。
        for planned_src, planned_dst, _, _, rel in plan:
            current_src = _resolve_migration_source(base, rel)
            if (current_src is None
                    or current_src.resolve(strict=True) != planned_src.resolve(strict=True)):
                raise ArchivePathError(f"媒体源在执行前发生变化：{rel}")
            assert_physical_direct_path(
                posts_dir, planned_dst.parent, kind="directory", label="帖子目标目录")
            assert_physical_direct_path(
                planned_dst.parent, planned_dst, kind="file", label="媒体目标文件")
            if planned_dst.exists():
                raise ArchivePathError(f"媒体目标在执行前已存在，拒绝覆盖：{planned_dst}")
        shutil.copy2(manifest, backup)

        for src, dst, m, _, rel in plan:
            current_src = _resolve_migration_source(base, rel)
            if (current_src is None
                    or current_src.resolve(strict=True) != src.resolve(strict=True)):
                raise ArchivePathError(f"媒体源在搬移前发生变化：{rel}")
            src = current_src
            post_target = assert_physical_direct_path(
                posts_dir, dst.parent, kind="directory", label="帖子目标目录")
            post_target.mkdir(parents=True, exist_ok=True)
            assert_physical_direct_path(
                posts_dir, post_target, kind="directory", label="帖子目标目录")
            dst = assert_physical_direct_path(
                post_target, dst, kind="file", label="媒体目标文件")
            if dst.exists():
                raise ArchivePathError(f"媒体目标已存在，拒绝覆盖：{dst}")
            shutil.move(str(src), str(dst))
            assert_physical_direct_path(
                post_target, dst, kind="file", label="媒体目标文件")
            if not dst.exists() or src.exists():
                raise ArchivePathError(f"媒体搬移后的物理状态不符合预期：{src} -> {dst}")
            m["local_path"] = str(dst.relative_to(base)).replace("\\", "/")
            moved += 1

        # 写 post.json / text.txt，再重建索引。两个叶子先全部校验后才写。
        for row, post_target in row_targets:
            post_target = assert_physical_direct_path(
                posts_dir, post_target, kind="directory", label="帖子目标目录")
            post_target.mkdir(parents=True, exist_ok=True)
            assert_physical_direct_path(
                posts_dir, post_target, kind="directory", label="帖子目标目录")
            post_json = assert_physical_direct_path(
                post_target, post_target / "post.json", kind="file", label="post.json")
            text_file = assert_physical_direct_path(
                post_target, post_target / "text.txt", kind="file", label="text.txt")
            post_json.write_text(
                json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            text_file.write_text(row.get("text") or "", encoding="utf-8")
    except ArchivePathError as exc:
        print("[!] 迁移目标在执行前变得不安全，已停止后续操作：%s" % exc)
        return 1

    n = Archive(base.parent, base.name).reindex()
    print("搬运 %d 个媒体，写出 %d 个帖子文件夹，重建索引 %d 条" % (moved, len(rows), n))

    for stale in ("media", "raw"):
        p = base / stale
        if p.exists() and not any(p.iterdir()):
            p.rmdir()
            print("空目录 %s/ 已删除" % stale)
        elif p.exists():
            left = len(list(p.iterdir()))
            print("⚠️ %s/ 还剩 %d 个文件，**未删**。确认无用后由人清理。" % (stale, left))
    print("旧 manifest 备份在 manifest.jsonl.premigrate")
    return 0


# --------------------------------------------------------------------------
# index.html
# --------------------------------------------------------------------------

PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>{title}</title>
<style>
 body{{font:15px/1.6 -apple-system,"Segoe UI",sans-serif;margin:0;background:#f5f5f7;color:#1d1d1f}}
 header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #d2d2d7;padding:14px 20px;z-index:9}}
 h1{{margin:0;font-size:17px}} .sub{{color:#6e6e73;font-size:13px;margin-top:3px}}
 .wrap{{max-width:860px;margin:0 auto;padding:18px 20px 60px}}
 .card{{background:#fff;border:1px solid #e3e3e6;border-radius:12px;padding:16px;margin-bottom:14px}}
 .meta{{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}}
 .date{{font-weight:600}} .pid{{color:#86868b;font-size:12px;font-family:ui-monospace,monospace}}
 .tag{{font-size:11px;padding:2px 7px;border-radius:20px;background:#eef;color:#3a3aa0}}
 .tag.v{{background:#fdeeee;color:#a03a3a}} .tag.w{{background:#fff4e0;color:#8a5a00}}
 .tag.c{{background:#e8f5ea;color:#1f6b33}}
 .txt{{white-space:pre-wrap;margin:8px 0 12px}}
 .imgs{{display:flex;gap:8px;flex-wrap:wrap}}
 .imgs img{{max-height:190px;border-radius:8px;border:1px solid #e3e3e6}}
 a{{color:#0066cc}} .empty{{color:#86868b;font-style:italic}}
</style>
<header><h1>{title}</h1><div class="sub">{sub}</div></header>
<div class="wrap">{cards}</div>
</html>"""


def build_index(base: Path, account: str, dry_run: bool) -> int:
    arc = Archive(base.parent, base.name)
    rows = sorted(arc.rows(), key=lambda r: r.get("created_at") or "", reverse=True)
    if not rows:
        print("[!] 归档是空的")
        return 1

    cards = []
    for r in rows:
        media = r.get("media") or []
        imgs = [m for m in media if m.get("kind") == "image" and m.get("local_path")]
        n_vid = sum(1 for m in media if m.get("kind") == "video")
        tags = []
        # 合作帖：别人发布、本账号是 coauthor，但同样在本账号主页上。
        # 标出来是因为**它的内容著作权在原作者手里**，二次使用要看授权。
        if (r.get("owner") or "") != account.lower():
            tags.append('<span class="tag c">合作 · @%s</span>'
                        % html.escape(r.get("owner") or "?"))
        if n_vid:
            tags.append('<span class="tag v">视频 ×%d</span>' % n_vid)
        if not r.get("media_complete", True):
            tags.append('<span class="tag w">媒体不全</span>')
        if not media:
            tags.append('<span class="tag">无配图</span>')
        link = ('<a href="%s" target="_blank">原帖</a>' % html.escape(r["permalink"])
                if r.get("permalink") else "")
        text = html.escape(r.get("text") or "")
        cards.append(
            '<div class="card"><div class="meta">'
            '<span class="date">%s</span><span class="pid">%s</span>%s %s</div>'
            '<div class="txt">%s</div><div class="imgs">%s</div></div>' % (
                html.escape((r.get("created_at") or "无日期")[:16].replace("T", " ")),
                html.escape(r["post_id"]), " ".join(tags), link,
                text or '<span class="empty">（无正文）</span>',
                "".join('<img loading="lazy" src="%s" alt="">'
                        % html.escape(m["local_path"]) for m in imgs)))

    with_text = sum(1 for r in rows if (r.get("text") or "").strip())
    n_collab = sum(1 for r in rows if (r.get("owner") or "") != account.lower())
    collab = " · 其中 %d 篇是合作帖（原作者不是本账号）" % n_collab if n_collab else ""
    sub = ("%d 篇 · %s ~ %s · %d 篇有正文%s · 按时间倒序" % (
        len(rows), (rows[-1].get("created_at") or "?")[:10],
        (rows[0].get("created_at") or "?")[:10], with_text, collab))
    page = PAGE.format(title=html.escape(account), sub=html.escape(sub),
                       cards="".join(cards))
    # 原创/合作的拆分**打到 stdout**，不只是埋在 HTML 副标题里。
    # 人工核对合作帖修复效果时看的就是这个数（实测 1019 = 756 + 263），
    # 而"要双击 HTML 才看得到"意味着它没法被复制回报、也没法在终端里比对。
    print("总计 %d 篇：原创 %d · **合作 %d**（别人发布、本账号是 coauthor，"
          "同样在本账号主页上）" % (len(rows), len(rows) - n_collab, n_collab))
    out = base / "index.html"
    if dry_run:
        print("--dry-run：会写 %s（%d 篇，%d KB）" % (out, len(rows), len(page) // 1024))
        return 0
    out.write_text(page, encoding="utf-8")
    print("已生成 %s  （%d 篇，%d KB）" % (out, len(rows), out.stat().st_size // 1024))
    print("双击它就能在浏览器里看完整个账号的历史。")
    return 0


def main(argv=None) -> int:
    force_utf8()
    ap = argparse.ArgumentParser(description="归档布局工具（J 组）")
    ap.add_argument("command", choices=("migrate", "reindex", "index"))
    ap.add_argument("platform", choices=sorted(PREFIX))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    base, account = archive_base(args.platform)
    if not base.exists():
        print("[!] 找不到归档目录 %s" % base)
        return 1

    if args.command == "migrate":
        return migrate(base, args.dry_run)
    if args.command == "index":
        return build_index(base, account, args.dry_run)
    if args.dry_run:
        print("reindex 没有 dry-run 的意义（它就是把 posts/ 的事实抄进索引）")
        return 1
    print("从 posts/ 重建索引：%d 条" % Archive(base.parent, base.name).reindex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

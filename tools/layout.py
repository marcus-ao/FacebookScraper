"""重建归档索引、生成总览或迁移布局；各子命令支持 --dry-run。"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import sys
from dataclasses import fields
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from core import index_db, paid_model                       # noqa: E402
from core.store import (                                     # noqa: E402
    Archive, ArchivePathError, Post, _atomic_write_text, _new_folder_name,
    archive_write_lock, assert_physical_direct_path, assert_post_directory,
    infer_tags, iter_post_dirs, primary_tag_folder,
)

PREFIX = {"facebook": "fa", "instagram": "in"}


def archive_base(platform: str):
    c = cfg()
    account = c["targets"][platform]
    return c.archive_dir / f"{PREFIX[platform]}_{account}", account


def _migration_events(base: Path) -> dict[str, dict]:
    path = assert_physical_direct_path(base, base / "layout_migrations.jsonl", kind="file", label="布局迁移记录")
    latest = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                latest[row["post_id"]] = row
    return latest


def _checked_tree(base: Path, directory: Path) -> None:
    assert_post_directory(base, directory)
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories:
            assert_physical_direct_path(Path(parent), Path(parent) / name, kind="directory", label="迁移子目录")
        for name in files:
            assert_physical_direct_path(Path(parent), Path(parent) / name, kind="file", label="迁移源文件")


def _migration_plan(base: Path) -> list[dict]:
    pending = {pid: event for pid, event in _migration_events(base).items() if event["status"] == "started"}
    plans = []
    for directory in iter_post_dirs(base):
        source_path = assert_physical_direct_path(directory, directory / "post.json", kind="file", label="迁移源帖")
        if not source_path.exists():
            continue
        row = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(row, dict) or not isinstance(row.get("post_id"), str) or not isinstance(row.get("text"), str):
            raise ArchivePathError("迁移源 post.json schema 无效：%s" % source_path)
        saved = pending.get(row["post_id"])
        if saved:
            plan = dict(saved, row=row)
            target = base / plan["target"]
            assert_post_directory(base, target)
        else:
            values = {key: value for key, value in row.items() if key in {field.name for field in fields(Post)}}
            post = Post(**values)
            name = row.get("folder_name") or _new_folder_name(post)
            updated = dict(row, folder_name=name, tags=row.get("tags") if isinstance(row.get("tags"), list) else infer_tags(row["text"]))
            # 规划目标强制月份 + 主 tag 层级；post_directory 的旧平铺兼容仅用于日常读取。
            month = name[:7] if name[:4].isdigit() else "undated"
            target = assert_post_directory(
                base, base / "posts" / month / primary_tag_folder(updated) / name)
            if directory == target and updated == row:
                continue
            plan = {"post_id": row["post_id"], "status": "started", "source": directory.relative_to(base).as_posix(),
                    "target": target.relative_to(base).as_posix(), "folder_name": name,
                    "backup": "_layout_backups/" + uuid4().hex, "row": updated}
        _checked_tree(base, directory)
        if target.exists() and target != directory:
            raise ArchivePathError("迁移目标已存在，拒绝覆盖：%s" % target)
        plan["current"] = directory.relative_to(base).as_posix()
        plans.append(plan)
    ids = [plan["post_id"] for plan in plans]
    if len(ids) != len(set(ids)):
        raise ArchivePathError("同一 post_id 有多个真相目录，请先核对再迁移")
    return plans


def _remap_paths(value, old_prefix: str, new_prefix: str, *, path_value: bool = False):
    if isinstance(value, dict):
        return {key: _remap_paths(item, old_prefix, new_prefix,
                                 path_value=key in {"local_path", "out_path", "source_rel"})
                for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_paths(item, old_prefix, new_prefix, path_value=path_value) for item in value]
    if path_value and isinstance(value, str):
        normalized = value.replace("\\", "/")
        if normalized.startswith(old_prefix + "/"):
            return new_prefix + normalized[len(old_prefix):]
    return value


def _migrate_one(base: Path, plan: dict) -> None:
    current, target = base / plan["current"], base / plan["target"]
    ledger = base / "layout_migrations.jsonl"
    def guard(path):
        return assert_physical_direct_path(path.parent, path, kind="file", label="迁移账本")
    event = {key: value for key, value in plan.items() if key not in {"row", "current"}}
    paid_model.append_jsonl(ledger, event, guard=guard)
    backup = base / plan["backup"]
    assert_physical_direct_path(base, backup.parent, kind="directory", label="布局恢复区")
    assert_physical_direct_path(backup.parent, backup, kind="directory", label="迁移备份")
    backup.parent.mkdir(parents=True, exist_ok=True)
    marker = backup / ".complete"
    if not marker.exists():
        shutil.copytree(current, backup, dirs_exist_ok=True)
        _atomic_write_text(marker, "complete\n", label="迁移备份完成标记")
    if current != target:
        assert_post_directory(base, current)
        assert_post_directory(base, target)
        if not current.resolve().is_relative_to(base.resolve()) or not target.resolve().is_relative_to(base.resolve()):
            raise ArchivePathError("迁移目标超出账号归档目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        current.rename(target)
    row = _remap_paths(plan["row"], plan["source"], plan["target"])
    row["folder_name"] = plan["folder_name"]
    if not isinstance(row.get("tags"), list):
        row["tags"] = infer_tags(row["text"])
    _atomic_write_text(target / "post.json", json.dumps(row, ensure_ascii=False, indent=2), label="post.json")
    history = target / "source_history.jsonl"
    if history.exists():
        assert_physical_direct_path(target, history, kind="file", label="源版本历史")
        rewritten = [json.dumps(_remap_paths(json.loads(line), plan["source"], plan["target"]), ensure_ascii=False)
                     for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
        _atomic_write_text(history, "\n".join(rewritten) + "\n", label="源版本历史路径迁移")
    images = assert_physical_direct_path(base, base / "images_de.jsonl", kind="file", label="图片账本")
    if images.exists():
        entries = [json.loads(line) for line in images.read_text(encoding="utf-8").splitlines() if line.strip()]
        seen = {json.dumps(entry, sort_keys=True) for entry in entries}
        for entry in entries:
            rewritten = _remap_paths(entry, plan["source"], plan["target"])
            signature = json.dumps(rewritten, sort_keys=True)
            if rewritten != entry and signature not in seen:
                paid_model.append_jsonl(images, rewritten, guard=guard)
                seen.add(signature)
    paid_model.append_jsonl(ledger, dict(event, status="completed"), guard=guard)


def migrate(base: Path, *, dry_run: bool = True) -> int:
    """显式迁移旧平铺目录；先备份、记录恢复计划，保留人工文件与图片所有权。"""
    base = Path(base)
    if base.name == "in_neakasa.tech":
        raise ArchivePathError("in_neakasa.tech 已冻结，保持现有布局，不执行迁移")
    plans = _migration_plan(base)
    for plan in plans:
        print("  %s -> %s" % (plan["source"], plan["target"]))
    if dry_run:
        return len(plans)
    with archive_write_lock(base):
        plans = _migration_plan(base)
        for plan in plans:
            _migrate_one(base, plan)
        Archive(base.parent, base.name).reindex()
    return len(plans)


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
        # 标出合作作者，供审核二次使用授权。
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
    ap.add_argument("command", choices=("reindex", "index", "reindex-db", "migrate"))
    ap.add_argument("platform", choices=sorted(PREFIX), nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "reindex-db":
        database = cfg().state_dir / "index.sqlite"
        scope = "、".join(cfg().active_accounts())
        if args.dry_run:
            print("--dry-run：将从帖子真相源重建展示索引 %s（只含 %s），当前未写盘"
                  % (database, scope))
            return 0
        count = index_db.rebuild_index(cfg().archive_dir, database, state_dir=cfg().state_dir)
        # 范围必须与 Web 侧一致（index_db.display_account_dirs），否则两边来回重建。
        print("展示索引已从文件重建：%d 篇（只含 %s）；%s" % (count, scope, database))
        return 0
    if args.platform is None:
        ap.error("此命令需要指定 facebook 或 instagram")

    base, account = archive_base(args.platform)
    if not base.exists():
        print("[!] 找不到归档目录 %s" % base)
        return 1

    if args.command == "index":
        return build_index(base, account, args.dry_run)
    if args.command == "migrate":
        count = migrate(base, dry_run=args.dry_run)
        print("%s %d 个帖子目录" % ("将迁移" if args.dry_run else "已迁移", count))
        return 0
    if args.dry_run:
        print("reindex 没有 dry-run 的意义（它就是把 posts/ 的事实抄进索引）")
        return 1
    print("从 posts/ 重建索引：%d 条" % Archive(base.parent, base.name).reindex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

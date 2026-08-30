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
from core.store import Archive, post_dirname                 # noqa: E402

PREFIX = {"facebook": "fa", "instagram": "in"}


def archive_base(platform: str):
    c = cfg()
    account = c["targets"][platform]
    return c.archive_dir / f"{PREFIX[platform]}_{account}", account


# --------------------------------------------------------------------------
# migrate
# --------------------------------------------------------------------------

def migrate(base: Path, dry_run: bool) -> int:
    """把 `media/<post_id>_<n>.ext` 搬进 `posts/<日期>_<时分>_<post_id>/NN.ext`。

    **搬运用 move 而不是 copy**：归档已经上 GB，复制一份纯属浪费；
    而且真出问题时"文件到底在哪一份里"会变成新的麻烦。
    迁移前会把 manifest 备份成 `manifest.jsonl.premigrate`，可回退。
    """
    manifest = base / "manifest.jsonl"
    if not manifest.exists():
        print("[!] 没有 manifest.jsonl，先跑回填或 tools.replay")
        return 1

    rows = [json.loads(line) for line in
            manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    posts_dir = base / "posts"
    print("帖子 %d 篇 → %s" % (len(rows), posts_dir))

    moved = missing = undated = 0
    plan: list[tuple[Path, Path, dict, int]] = []
    for row in rows:
        dirname = post_dirname(row["post_id"], row.get("created_at"))
        if dirname.startswith("undated_"):
            undated += 1
        for idx, m in enumerate(row.get("media") or []):
            rel = m.get("local_path")
            if not rel:
                continue
            src = base / rel
            if not src.exists():
                missing += 1
                continue
            plan.append((src, posts_dir / dirname / ("%02d%s" % (idx + 1, src.suffix)),
                         m, idx))

    print("待搬运媒体 %d 个；记录里有路径但文件不在的 %d 个；无日期帖子 %d 篇"
          % (len(plan), missing, undated))
    if dry_run:
        for src, dst, _, _ in plan[:3]:
            print("   %s  ->  %s" % (src.name, dst.relative_to(base)))
        print("\n--dry-run：什么都没动。")
        return 0

    shutil.copy2(manifest, base / "manifest.jsonl.premigrate")
    for src, dst, m, _ in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        m["local_path"] = str(dst.relative_to(base)).replace("\\", "/")
        moved += 1

    # 写 post.json / text.txt，再重建索引
    for row in rows:
        d = posts_dir / post_dirname(row["post_id"], row.get("created_at"))
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "text.txt").write_text(row.get("text") or "", encoding="utf-8")

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

r"""用已保存的 capture 离线重建归档。对应实施计划的 B7。

**这个工具存在的理由**：2026-08-30 的首次真实回填暴露了三个解析器缺陷
（跨账号污染、轮播子项被当成帖子、FB 视频帖被当成抓取失败），
产出的 manifest 数字是错的。修完解析器之后需要重建归档——
但**不能让用户再滚一次 40 分钟**。

`routes/backfill.py` 在解析**之前**无条件转储了原始响应
（`_capture_<时间戳>.json`），所以重建完全可以离线做。
那条兜底设计就是为这一刻准备的，第一次兑现价值。

用法（项目根目录，已激活 venv）：

    python -m tools.replay facebook            # 用 config.toml 的账号，取最新 capture
    python -m tools.replay instagram --dry-run # 只看结果，不写盘
    python -m tools.replay facebook --capture archive/fa_x/_capture_123.json

**媒体一律不重新下载。** CDN 签名 URL 早已过期，重下必然 403
（计划第 1 节：媒体 URL 带签名且有时效）。已经在盘上的文件按 URL 重新关联，
关联不上的移进 `_orphan_media/` —— **移动不是删除**，确认无误后再由人清理。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from core.parse import extract, partition_by_owner           # noqa: E402
from core.store import Archive, Post                         # noqa: E402

PREFIX = {"facebook": "fa", "instagram": "in"}


def newest_capture(base: Path) -> Path | None:
    """最新的一份 capture。多次回填会留下多份，默认取时间戳最大的那份。"""
    caps = sorted(base.glob("_capture_*.json"))
    return caps[-1] if caps else None


def old_local_paths(manifest: Path) -> dict[tuple[str, str], str]:
    """从旧 manifest 建 `(post_id, media_url) -> local_path` 的映射。

    **按 URL 关联而不是按下标**：修复后媒体的排列顺序可能变
    （FB 现在会在图片后面追加视频），下标关联会张冠李戴。
    而 URL 来自同一份 capture，两次解析必然逐字符相同，是可靠的键。
    """
    out: dict[tuple[str, str], str] = {}
    if not manifest.exists():
        return out
    for line in manifest.read_text(encoding="utf-8").splitlines():
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


def relink(posts: list[Post], known: dict[tuple[str, str], str],
           base: Path) -> tuple[int, int]:
    """把盘上已有的媒体文件重新挂到新解析出的帖子上。

    返回 `(挂上的, 记录里有 local_path 但文件已不在的)`。
    第二个数字不为零说明有人手工删过文件，值得知道。
    """
    linked = missing = 0
    for post in posts:
        for m in post.media:
            if m.kind == "video":
                continue                      # 视频从不下载，本来就没有本地文件
            rel = known.get((post.post_id, m.url))
            if not rel:
                continue
            if (base / rel).exists():
                m.local_path = rel
                linked += 1
            else:
                missing += 1
    return linked, missing


def run(platform: str, capture: Path | None, dry_run: bool) -> int:
    c = cfg()
    account = c["targets"][platform]
    base = c.archive_dir / f"{PREFIX[platform]}_{account}"
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
    print("响应段数 : %d" % len(payloads))

    posts = extract(payloads, platform, account, route="backfill")
    kept, rejected = partition_by_owner(posts, account)

    manifest = base / "manifest.jsonl"
    before = sum(1 for line in manifest.read_text(encoding="utf-8").splitlines()
                 if line.strip()) if manifest.exists() else 0

    known = old_local_paths(manifest)
    linked, missing = relink(kept, known, base)

    imgs = sum(1 for p in kept for m in p.media if m.kind == "image")
    vids = sum(1 for p in kept for m in p.media if m.kind == "video")
    others = sorted({r["owner_name"] or r["owner"] or "(归属未知)" for r in rejected})

    print()
    print("解析出候选   : %d" % len(posts))
    print("本账号保留   : %d   （旧 manifest 有 %d 行，差 %+d）"
          % (len(kept), before, len(kept) - before))
    print("丢弃         : %d   来自 %d 个其它账号/未知归属" % (len(rejected), len(others)))
    if others:
        print("               %s%s" % (", ".join(others[:6]),
                                       " …" if len(others) > 6 else ""))
    print("媒体         : 图片 %d / 视频 %d（视频只记元数据，不下载）" % (imgs, vids))
    print("媒体重新关联 : %d 个文件挂上；%d 个记录里有路径但文件已不在" % (linked, missing))
    print("正文为空     : %d" % sum(1 for p in kept if not (p.text or "").strip()))
    print("无日期       : %d" % sum(1 for p in kept if not p.created_at))

    # 盘上有、但没有任何保留帖子认领的媒体文件 = 孤儿
    claimed = {m.local_path for p in kept for m in p.media if m.local_path}
    media_dir = base / "media"
    orphans = [f for f in sorted(media_dir.glob("*"))
               if f.is_file() and str(f.relative_to(base)).replace("\\", "/") not in
               {str(Path(c_).as_posix()) for c_ in claimed}]
    print("孤儿媒体     : %d 个（轮播子项与他人帖子留下的重复文件）" % len(orphans))

    if dry_run:
        print("\n--dry-run：什么都没写。去掉这个参数才会真正重建。")
        return 0

    # ---- 写盘。顺序很重要：先备份，再写新的，最后动文件 ----
    if manifest.exists():
        bak = base / "manifest.jsonl.bak"
        shutil.copy2(manifest, bak)
        print("\n旧 manifest 已备份 → %s" % bak.name)
        manifest.unlink()

    arc = Archive(base.parent, base.name)      # 重新加载（此刻 manifest 为空）
    written = sum(1 for p in kept if arc.append(p))
    print("写入 manifest : %d 条" % written)

    n_rej = arc.record_rejected(rejected)
    print("写入 _rejected.jsonl : %d 条（本次新增）" % n_rej)

    if orphans:
        orphan_dir = base / "_orphan_media"
        orphan_dir.mkdir(exist_ok=True)
        for f in orphans:
            shutil.move(str(f), str(orphan_dir / f.name))
        print("孤儿媒体移入 %s ：%d 个（**移动不是删除**，确认后再由人清理）"
              % (orphan_dir.name, len(orphans)))

    stale_raw = [f for f in (base / "raw").glob("*.json")
                 if f.stem not in {p.post_id for p in kept}]
    if stale_raw:
        print("提示：raw/ 下有 %d 个已不对应任何帖子的文件，未动。"
              "它们只是 to_row() 的副本，J 组重构布局时一并处理。" % len(stale_raw))
    return 0


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

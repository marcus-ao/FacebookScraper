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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import cfg                                  # noqa: E402
from core.console import force_utf8                          # noqa: E402
from core.parse import extract, partition_by_owner           # noqa: E402
from core.store import Archive, Post                         # noqa: E402

PREFIX = {"facebook": "fa", "instagram": "in"}


def newest_capture(base: Path) -> Path | None:
    """最新的一份**回填** capture。多次回填会留下多份，取时间戳最大的那份。

    ⚠️ **必须排除 `_capture_delta_*.json`。** 增量每天也会转储，而且名字排序
    永远排在回填那份后面——不排除的话，"重建归档"会拿一份只有几十条的
    增量快照去重建整个归档，把 700 多篇冲成几十篇。
    """
    caps = sorted(p for p in base.glob("_capture_*.json")
                  if not p.name.startswith("_capture_delta_"))
    return caps[-1] if caps else None


def old_local_paths(base: Path) -> dict[tuple[str, str], str]:
    """从**所有**历史 manifest 建 `(post_id, media_url) -> 记录里的路径` 映射。

    **按 URL 关联而不是按下标**：修复后媒体的排列顺序可能变
    （FB 会在图片后面追加视频），下标关联会张冠李戴。
    而 URL 来自同一份 capture，两次解析必然逐字符相同，是可靠的键。

    **为什么要读全部备份而不只读当前 manifest**：
    上一次重建把 263 篇合作帖当成他人帖丢掉了，它们的媒体记录只存在于
    更早的那几份备份里。只读当前 manifest 的话，这些帖子加回来也没有图。
    读取顺序是"所有备份（按名字）→ 当前 manifest"，**新的覆盖旧的**。
    """
    out: dict[tuple[str, str], str] = {}
    live = base / "manifest.jsonl"
    backups = sorted(p for p in base.glob("manifest.jsonl*") if p != live)
    for path in backups + [live]:
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


def _resolve(base: Path, rel: str) -> Path | None:
    """记录里的相对路径 → 盘上真实存在的文件。

    历史记录里的路径有两代（旧扁平布局 `media/<id>_<n>.jpg`、
    J 组之后的 `posts/<文件夹>/01.jpg`），而**文件可能已经被上一次重建
    移进了 `_orphan_media/`**。三个地方都找一遍，找不到才算丢。
    """
    direct = base / rel
    if direct.exists():
        return direct
    orphan = base / "_orphan_media" / Path(rel).name
    if orphan.exists():
        return orphan
    return None


def relink(posts: list[Post], known: dict[tuple[str, str], str],
           base: Path, arc: Archive, move: bool) -> tuple[int, int, int]:
    """把盘上已有的媒体文件重新挂到新解析出的帖子上。

    返回 `(挂上的, 从 _orphan_media 捞回来的, 记录里有路径但文件已不在的)`。
    最后一个数字不为零说明有人手工删过文件，值得知道。

    `move=False` 时只做统计不动文件（`--dry-run`）。
    """
    linked = recovered = missing = 0
    for post in posts:
        for i, m in enumerate(post.media):
            if m.kind == "video":
                continue                      # 视频从不下载，本来就没有本地文件
            rel = known.get((post.post_id, m.url))
            if not rel:
                continue
            src = _resolve(base, rel)
            if src is None:
                missing += 1
                continue
            want = arc.media_path(post, i, None) if move else None
            if want is not None and src.resolve() != want.resolve():
                # 文件在孤儿区、或在按旧编号命名的位置：搬到这篇帖子该在的地方。
                # ⚠️ 用真实后缀，别让 .png 变成 .jpg
                want = want.with_suffix(src.suffix or want.suffix)
                shutil.move(str(src), str(want))
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

    known = old_local_paths(base)
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
    print("媒体         : 图片 %d / 视频 %d（视频只记元数据，不下载）" % (imgs, vids))
    print("正文为空     : %d" % sum(1 for p in kept if not (p.text or "").strip()))
    print("无日期       : %d" % sum(1 for p in kept if not p.created_at))

    if dry_run:
        linked, recovered, missing = relink(kept, known, base, None, move=False)
        print("媒体可关联   : %d 个（其中 %d 个要从 _orphan_media/ 捞回来）；"
              "%d 个记录里有路径但文件已不在" % (linked, recovered, missing))
        print("\n--dry-run：什么都没写。去掉这个参数才会真正重建。")
        return 0

    # ---- 写盘。顺序很重要：先备份，再重建索引，最后动文件 ----
    if manifest.exists():
        # ⚠️ 备份名带时间戳，**不覆盖上一次的备份**。
        # 上一轮的 `manifest.jsonl.bak` 是唯一还记着那 263 篇合作帖媒体路径的
        # 地方；固定名字的备份会把它冲掉，而那时候图片已经在 _orphan_media/ 里，
        # 没有这份记录就再也对不上号了。
        bak = base / ("manifest.jsonl.%d.bak" % int(time.time()))
        shutil.copy2(manifest, bak)
        print("\n旧 manifest 已备份 → %s" % bak.name)
        manifest.unlink()

    # `_rejected.jsonl` 也要重来：它是"这次解析丢了什么"的记录，
    # 而 record_rejected 按 post_id 去重、只追加。上一轮误丢的 263 篇
    # 若不清掉，会永远留在里面，看起来像还在被丢弃。
    rej_path = base / "_rejected.jsonl"
    if rej_path.exists():
        shutil.copy2(rej_path, base / ("_rejected.jsonl.%d.bak" % int(time.time())))
        rej_path.unlink()
        print("旧 _rejected.jsonl 已备份")

    arc = Archive(base.parent, base.name)      # 重新加载（此刻 manifest 为空）
    linked, recovered, missing = relink(kept, known, base, arc, move=True)
    print("媒体重新关联 : %d 个文件挂上（其中 %d 个从 _orphan_media/ 捞回）；"
          "%d 个记录里有路径但文件已不在" % (linked, recovered, missing))

    written = sum(1 for p in kept if arc.append(p))
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

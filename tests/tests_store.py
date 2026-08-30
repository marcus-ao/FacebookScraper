"""归档层自测：去重、断点续传、以及"增量写了残缺帖后回填能否补全"。"""
import sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.store import Archive, Post, Media

fails = []
def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

with tempfile.TemporaryDirectory() as d:
    print("[1] 基本去重与续传")
    a = Archive(d, "acct")
    p = Post(post_id="p1", platform="instagram", account="x", text="hello",
             created_at="2026-08-01T00:00:00Z",
             media=[Media(url="u1", kind="image")], source_route="delta")
    check(a.append(p) is True,  "首次写入返回 True")
    check(a.append(p) is False, "重复写入返回 False（幂等重跑）")
    check(len(a.rows()) == 1,   f"有效记录 1 条，实得 {len(a.rows())}")
    check(Archive(d, "acct").has("p1"), "重建实例能从磁盘恢复")

    print("\n[2] 残缺 -> 补全（这是修掉的那个缺陷）")
    inc = Post(post_id="p2", platform="instagram", account="x", text="carousel",
               created_at="2026-08-02T00:00:00Z",
               media=[Media(url="cover", kind="image")],
               source_route="delta", media_complete=False)
    a.append(inc)
    check(len(a.needs_media()) == 1, "残缺帖进入待补清单")

    full = Post(post_id="p2", platform="instagram", account="x", text="carousel",
                created_at="2026-08-02T00:00:00Z",
                media=[Media(url="c1", kind="image"), Media(url="c2", kind="image"),
                       Media(url="c3", kind="image")],
                source_route="backfill", media_complete=True)
    check(a.should_append(full) is True,
          "下载前判定允许补全版通过（不能被 has(post_id) 直接跳过）")
    check(a.append(full) is True, "补全版被接受（旧代码这里会静默丢弃）")
    check(len(a.rows()) == 2, f"去重后仍是 2 篇，实得 {len(a.rows())}")
    got = {r["post_id"]: r for r in a.rows()}["p2"]
    check(len(got["media"]) == 3, f"读取时后写胜出，媒体 3 个，实得 {len(got['media'])}")
    check(got["media_complete"] is True, "media_complete 已更新为 True")
    check(len(a.needs_media()) == 0, "待补清单已清空")

    print("\n[3] 完整的帖子不会被降级覆写")
    downgrade = Post(post_id="p2", platform="instagram", account="x", text="carousel",
                     created_at="2026-08-02T00:00:00Z",
                     media=[Media(url="cover", kind="image")],
                     source_route="delta", media_complete=False)
    check(a.should_append(downgrade) is False, "下载前判定拒绝完整帖被降级")
    check(a.append(downgrade) is False, "已完整的帖子拒绝被残缺版覆写")
    check(len({r["post_id"]: r for r in a.rows()}["p2"]["media"]) if False else
          len({r["post_id"]: r for r in a.rows()}["p2"]["media"]) == 3,
          "媒体仍是 3 个，未被降级")

    print("\n[4] 磁盘上确实是追加写（多行同 id），读取时收敛")
    raw_lines = [l for l in open(a.manifest, encoding="utf-8") if l.strip()]
    check(len(raw_lines) == 3, f"物理 3 行（p1, p2残缺, p2完整），实得 {len(raw_lines)}")
    check(len(a.rows()) == 2, "逻辑 2 条")

    print("\n[5] manifest 里的坏行被跳过，不中断整批加载")
    with open(a.manifest, "a", encoding="utf-8") as f:
        # 第一行是合法 JSON 但不是对象 —— 曾经会抛未捕获的 TypeError
        f.write('[1,2]\n')
        f.write('不是 json\n')
        f.write('{"no_id": 1}\n')
    reloaded = Archive(d, "acct")
    check(len(reloaded.rows()) == 2,
          f"三种坏行都被跳过，仍是 2 条，实得 {len(reloaded.rows())}")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

"""归档层自测：去重、断点续传、以及"增量写了残缺帖后回填能否补全"。"""
import sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉
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

print("\n[J 组] 每帖一个文件夹")
from core.store import post_dirname   # noqa: E402

check(post_dirname("123", "2026-08-25T14:23:20Z") == "2026-08-25_1423_123",
      "文件夹名是 <日期>_<时分>_<post_id>")
check(post_dirname("123", "2026-08-25T14:23:20Z")
      < post_dirname("456", "2026-08-26T09:00:00Z"),
      "按名称排序就是按时间排序 —— 这正是这个命名要解决的问题")
check(post_dirname("123", "") == "undated_123", "没有时间的进 undated_")
check(post_dirname("123", None) == "undated_123", "created_at 为 None 也不崩")
check(post_dirname("123", "去年夏天") == "undated_123",
      "不认识的时间格式一律 undated —— **不猜**，猜出来的日期看起来是真的")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(d, "acct")
    p = Post(post_id="900", platform="instagram", account="acct",
             text="Sommer\nSale", created_at="2026-08-25T14:23:20Z",
             owner="acct",
             media=[Media(url="https://cdn/a.jpg", kind="image"),
                    Media(url="https://cdn/b.mp4", kind="video")])
    # 下载发生在 append 之前：media_path 必须能在此时就建好文件夹
    m0 = arc.media_path(p, 0, "image/jpeg")
    check(m0.name == "01.jpg", "帖内第 1 张图叫 01.jpg（1 起、补零）")
    check(m0.parent.name == "2026-08-25_1423_900", "图片落在该帖自己的文件夹里")
    check(m0.parent.exists(), "media_path 顺手把文件夹建好了（下载先于 append）")
    check(arc.media_path(p, 1, "video/mp4").name == "02.mp4",
          "编号跟的是帖内位置，不是「第几张图」—— 顺序信息比连号更值钱")
    m0.write_bytes(b"x")
    p.media[0].local_path = str(m0.relative_to(arc.base)).replace("\\", "/")

    arc.append(p)
    pd = arc.post_dir(p)
    check((pd / "post.json").exists(), "post.json 落地（真相源）")
    check((pd / "text.txt").read_text(encoding="utf-8") == "Sommer\nSale",
          "text.txt 是正文的纯文本副本，给人和设计同事看")
    saved = json.loads((pd / "post.json").read_text(encoding="utf-8"))
    check(saved["owner"] == "acct" and saved["post_id"] == "900",
          "post.json 里字段齐全")

    # reindex：文件夹是真相，索引由它重建
    arc.manifest.write_text("这是一份被写坏的索引\n", encoding="utf-8")
    n = Archive(d, "acct").reindex()
    check(n == 1, "reindex 从 posts/ 重建出 1 条")
    rebuilt = Archive(d, "acct").rows()
    check(len(rebuilt) == 1 and rebuilt[0]["post_id"] == "900",
          "重建后的索引与文件夹一致 —— 冲突时以文件夹为准")

    # 人删掉一个文件夹：reindex 之后索引里也应该没有它
    import shutil as _sh
    _sh.rmtree(pd)
    check(Archive(d, "acct").reindex() == 0,
          "文件夹被删掉后，reindex 把索引里对应的记录也去掉（方向永远是 posts→索引）")

    (arc.posts_dir / "2026-01-01_0000_bad").mkdir(parents=True)
    (arc.posts_dir / "2026-01-01_0000_bad" / "post.json").write_text(
        "{不是 json", encoding="utf-8")
    check(Archive(d, "acct").reindex() == 0, "坏掉的 post.json 被跳过，不中断重建")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(d, "acct")
    p = Post(post_id="901", platform="facebook", account="acct", text="",
             created_at="不是时间", owner="acct")
    arc.append(p)
    check(arc.post_dir(p).name == "undated_901", "无法解析时间的帖子进 undated_ 文件夹")
    check(Archive(d, "acct").reindex() == 1, "undated 的帖子照样进索引")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

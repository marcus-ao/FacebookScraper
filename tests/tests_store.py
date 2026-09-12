"""归档层自测：去重、断点续传、以及"增量写了残缺帖后回填能否补全"。"""
import contextlib, io, json, os, shutil, sys, tempfile
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

    print("\n[3b] 残缺重试的部分进展也必须落盘")
    partial_old = Post(
        post_id="p3", platform="instagram", account="x", text="partial",
        created_at="2026-08-03T00:00:00Z",
        media=[Media(url="u31", kind="image"), Media(url="u32", kind="image")],
        source_route="delta", media_complete=False)
    check(a.append(partial_old) is True, "先保存两张都未下载成功的残缺帖")
    partial_new = Post(
        post_id="p3", platform="instagram", account="x", text="partial",
        created_at="2026-08-03T00:00:00Z",
        media=[Media(url="u31", kind="image", local_path="posts/p3/01.jpg"),
               Media(url="u32", kind="image")],
        source_route="delta", media_complete=False)
    check(a.append(partial_new) is True,
          "媒体项数相同但多落盘一张时也算升级，不能丢掉恢复进展")
    p3 = {r["post_id"]: r for r in a.rows()}["p3"]
    check(p3["media"][0]["local_path"] and not p3["media_complete"],
          "真相源记住已恢复路径，同时仍保留待补状态")
    check(a.append(partial_new) is False,
          "同样的部分进展重跑保持幂等，不反复追加")
    more_but_lost = Post(
        post_id="p3", platform="instagram", account="x", text="partial",
        created_at="2026-08-03T00:00:00Z",
        media=[Media(url=f"u3{i}", kind="image") for i in range(1, 4)],
        source_route="delta", media_complete=False)
    check(a.append(more_but_lost) is False,
          "发现更多媒体但丢失旧落盘路径时拒绝倒退，不用项数覆盖恢复成果")
    more_and_kept = Post(
        post_id="p3", platform="instagram", account="x", text="partial",
        created_at="2026-08-03T00:00:00Z",
        media=[Media(url="u31", kind="image", local_path="posts/p3/01.jpg"),
               Media(url="u32", kind="image"), Media(url="u33", kind="image")],
        source_route="delta", media_complete=False)
    check(a.append(more_and_kept) is True,
          "保住旧落盘路径且发现更多媒体时允许单调升级")

    print("\n[4] 磁盘上确实是追加写（多行同 id），读取时收敛")
    raw_lines = [l for l in open(a.manifest, encoding="utf-8") if l.strip()]
    check(len(raw_lines) == 6,
          f"物理 6 行（另含 p3 的两次单调升级），实得 {len(raw_lines)}")
    check(len(a.rows()) == 3, "逻辑 3 条")

    print("\n[5] manifest 里的坏行被跳过，不中断整批加载")
    with open(a.manifest, "a", encoding="utf-8") as f:
        # 第一行是合法 JSON 但不是对象 —— 曾经会抛未捕获的 TypeError
        f.write('[1,2]\n')
        f.write('不是 json\n')
        f.write('{"no_id": 1}\n')
    reloaded = Archive(d, "acct")
    check(len(reloaded.rows()) == 3,
          f"三种坏行都被跳过，仍是 3 条，实得 {len(reloaded.rows())}")

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
check(post_dirname("pfbid_ABC-123.9", "2026-08-25T14:23:20Z")
      == "2026-08-25_1423_pfbid_ABC-123.9",
      "正常 ASCII post_id 原样保留，兼容已有目录名")

hostile_id = "x/../../../escaped"
hostile_name = post_dirname(hostile_id, "2026-08-25T14:23:20Z")
check(hostile_name == post_dirname(hostile_id, "2026-08-25T14:23:20Z"),
      "异常 post_id 的安全目录名是确定性的")
check(Path(hostile_name).name == hostile_name
      and "/" not in hostile_name and "\\" not in hostile_name,
      "异常 post_id 不会把路径分隔符带进目录名")
check(post_dirname("CON", None).startswith("undated_~id_"),
      "Windows 保留设备名也转成安全目录名")
check(post_dirname("normal.", None).startswith("undated_~id_")
      and post_dirname("normal.", None) != post_dirname("normal", None),
      "尾随点不会被 Windows 正规化后与正常 ID 目录碰撞")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(d, "acct")
    hostile = Post(post_id=hostile_id, platform="instagram", account="acct",
                   text="safe", created_at="2026-08-25T14:23:20Z", owner="acct")
    check(arc.append(hostile) is True, "异常 post_id 仍可归档，不丢业务数据")
    hostile_dir = arc.post_dir(hostile)
    check(hostile_dir.resolve().parent.parent == arc.posts_dir.resolve(),
          "异常 post_id 的目录 resolve 后仍严格位于 posts/月份 下一层")
    hostile_row = json.loads((hostile_dir / "post.json").read_text(encoding="utf-8"))
    check(hostile_row["post_id"] == hostile_id,
          "安全目录名只影响路径，post.json 保留远端原始 ID")
    check(not (Path(d) / "escaped").exists(), "账号目录外没有被路径穿越创建文件")

    # 即使未来目录名生成器发生回归，Archive.post_dir 的独立 containment
    # 仍必须拒绝越界路径。
    from unittest.mock import patch
    with patch("core.store._new_folder_name", return_value="../escape"):
        try:
            arc.post_dir(Post("fresh", "instagram", "acct", "new", "2026-09-10T00:00:00Z"))
            contained = False
        except ValueError:
            contained = True
    check(contained, "resolve containment 独立拦截目录名生成器的越界回归")

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
    check(m0.parent.name == "2026-08-25_1423_sommer-sale_900", "图片落在该帖自己的文件夹里")
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

print("\n[J2] created_at 改变时只留一个 truth dir，旧目录可恢复隔离")
with tempfile.TemporaryDirectory() as d:
    arc = Archive(d, "acct")
    old = Post(post_id="move_forward", platform="instagram", account="acct",
               text="old", created_at="", owner="acct",
               media=[Media(url="cover", kind="image")], media_complete=False)
    arc.append(old)
    old_dir = arc.post_dir(old)
    new = Post(post_id="move_forward", platform="instagram", account="acct",
               text="new", created_at="2026-08-29T12:30:00Z", owner="acct",
               media=[Media(url="one", kind="image"), Media(url="two", kind="image")],
               media_complete=True)
    check(arc.append(new) is True, "undated 残缺帖可升级成 dated 完整帖")
    check(arc.post_dir(new) == old_dir and old_dir.exists(),
          "补出源发布时间也保留创建时固定的 folder_name")
    history = json.loads((old_dir / "source_history.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    check(history["source"]["text"] == "old", "源版本历史保留旧正文，可人工恢复")
    check(Archive(d, "acct").reindex() == 1,
          "created_at 从无到有后 reindex 仍只有一条 truth")

with tempfile.TemporaryDirectory() as d:
    arc = Archive(d, "acct")
    old = Post(post_id="move_reverse", platform="instagram", account="acct",
               text="old incomplete", created_at="2026-08-29T12:30:00Z", owner="acct",
               media=[Media(url="cover", kind="image")], media_complete=False)
    arc.append(old)
    old_dir = arc.post_dir(old)
    new = Post(post_id="move_reverse", platform="instagram", account="acct",
               text="new complete", created_at="", owner="acct",
               media=[Media(url="one", kind="image"), Media(url="two", kind="image")],
               media_complete=True)
    check(arc.append(new) is True, "dated 残缺帖也可升级成 undated 完整帖")
    new_dir = arc.post_dir(new)
    check(new_dir == old_dir and old_dir.exists(),
          "created_at 反向纠正保留固定目录且只保留一个当前真相")

    # 模拟修复前已经遗留的双 truth dirs。旧实现先按 created_at 排序再用
    # post_id 后写胜出，会让 dated 旧残缺记录覆盖 undated 新完整记录。
    duplicate = arc.posts_dir / "2026-08-29_1230_move_reverse"
    duplicate.mkdir()
    (duplicate / "post.json").write_text(json.dumps(old.to_row()), encoding="utf-8")
    output = io.StringIO()
    rebuilt = Archive(d, "acct")
    with contextlib.redirect_stdout(output):
        count = rebuilt.reindex()
    row = rebuilt.rows()[0]
    manifest_lines = [line for line in rebuilt.manifest.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
    check(count == 1 and len(manifest_lines) == 1,
          "双 truth dirs 重建时按 post_id 收敛为一条 manifest")
    check(row["text"] == "new complete" and row["media_complete"] is True,
          "质量 rank 让 undated 新完整记录胜出，不被 dated 旧残缺记录回退")
    check("有 2 个 truth dirs" in output.getvalue()
          and "按质量等级选择" in output.getvalue(),
          "reindex 对重复 truth dirs 显式告警并说明选择依据")

# ==========================================================================
# [J migrate] / [J migrate target] / [J migrate conflict] 三段已删除（2026-09-03）。
# 它们测的是 tools/layout.py::migrate 的越界防护，而那段一次性迁移代码已随
# 归档完成迁移而删除。防护本身（core/store.py::assert_physical_direct_path）
# 没有动，tests_store_links.py 在仍然活着的路径上覆盖同一个威胁模型。
# ==========================================================================

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

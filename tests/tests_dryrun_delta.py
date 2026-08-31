"""`tools/dryrun_delta.py` 自测：离线重放能不能替代一次真实增量。

这套测试盯的是**这个工具本身可不可信**——它的用途是"改完解析器不用再露面一次
就能知道对不对"，所以它自己给出的结论必须和真实路径一致：

  1. 真的走完 `delta_once()`（不是另写一套简化流程），
  2. **一次网络请求都不发**（发了就等于把"离线自检"变成了又一次露面），
  3. `--break-coauthors` 自检真的能把 CR-19 那种退化照出来。

样本按**真实响应结构**构造：`coauthor_producers` 与 `invited_coauthor_producers`
两个键在每个节点上都有。CR-19 的教训就是测试样本比真实数据干净。
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

from core.store import Archive, Post                       # noqa: E402
import tools.dryrun_delta as dd                            # noqa: E402

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def ig_node(pk, owner, coauthors=(), ts=1756000000, text="hello"):
    """照抄真实 iphone_struct 形态：两个 coauthor 键都在，多数为空数组。"""
    return {"pk": pk, "code": "c%s" % pk, "taken_at": ts,
            "product_type": "clips",
            "user": {"username": owner, "full_name": owner.title()},
            "caption": {"text": text},
            "coauthor_producers": [{"pk": "9%d" % i, "username": u}
                                   for i, u in enumerate(coauthors)],
            "invited_coauthor_producers": [],
            "image_versions2": {"candidates": [
                {"url": "https://cdn.example.com/%s.jpg" % pk,
                 "width": 1080, "height": 1080}]}}


class StubCfg:
    """只提供 dryrun_delta.run() 用到的两样：targets 与 archive_dir。"""

    def __init__(self, root: Path, account: str) -> None:
        self.archive_dir = root
        self._targets = {"instagram": account, "facebook": account}

    def __getitem__(self, key):
        if key == "targets":
            return self._targets
        raise KeyError(key)


def build(root: Path, account: str, payloads: list) -> Path:
    """写一份增量转储 + 一个已有归档，返回转储路径。"""
    arc = Archive(root, "in_%s" % account)
    # 归档里先有 4 篇，min_own_posts 的下限 min(配置值, 已有篇数) 才不会被拉到 0
    for i in range(4):
        arc.append(Post(post_id="old%d" % i, platform="instagram",
                        account=account, text="old", owner="brand.x",
                        coauthors=[account],
                        created_at="2026-07-%02dT00:00:00Z" % (10 + i)))
    cap = arc.base / "_capture_delta_1700000000.json"
    cap.write_text(json.dumps(payloads, ensure_ascii=False), encoding="utf-8")
    return cap


# ==========================================================================
print("[1] 正常形态：走完真实的 delta_once()，合作帖被算进本账号")

with tempfile.TemporaryDirectory() as d:
    root, account = Path(d), "acme.us"
    payloads = [{"data": {"items": [
        ig_node("1", account),                              # 自己发的
        ig_node("2", "brand.x", coauthors=[account]),       # 合作帖
        ig_node("3", "brand.x", coauthors=[account]),       # 合作帖
        ig_node("4", "chicagofire"),                        # 纯推荐位
    ]}}]
    cap = build(root, account, payloads)
    dd.cfg = lambda: StubCfg(root, account)                 # noqa: E731
    rc = dd.run("instagram", cap, break_coauthors=False)
    check(rc == 0, "正常形态退出码 0")

# 直接拿 delta_once 的返回值断言数字（run() 只打印，不返回 ScanResult）
with tempfile.TemporaryDirectory() as d:
    root, account = Path(d), "acme.us"
    payloads = [{"data": {"items": [
        ig_node("1", account),
        ig_node("2", "brand.x", coauthors=[account]),
        ig_node("3", "brand.x", coauthors=[account]),
        ig_node("4", "chicagofire"),
    ]}}]
    cap = build(root, account, payloads)
    arc = Archive(root, "in_%s" % account)
    ctx = dd.FakeCtx(dd.FakePage(json.loads(cap.read_text(encoding="utf-8")),
                                 dd.FAKE_URL["instagram"]))
    res = asyncio.run(dd.delta_once(ctx, "instagram", account, arc,
                                    dd.offline_cfg(), dry_run=True))
    check(res.own == 3 and res.authored == 1 and res.collab == 2,
          "本账号 3 篇 = 原创 1 + 合作 2（合作帖没有被当成他人帖丢掉）")
    check(res.rejected == 1, "陌生账号的推荐位照常丢弃")
    check(res.suspect == [], "丢弃的不是合作方 → 哨兵不响（正常情况必须安静）")
    check("原创 1" in res.summary() and "合作 2" in res.summary(),
          "摘要把原创与合作拆开 —— 判定漂移时一眼可见")
    check(res.new == 3, "四篇里三篇属于本账号且归档里没有 → 判为新增（dry-run 不写盘）")
    check(arc.rows() and len(arc.rows()) == 4,
          "**dry-run 真的没写盘**：归档还是原来那 4 篇")


# ==========================================================================
print("\n[2] 一次网络请求都不发 —— 否则'离线自检'就成了又一次真实露面")

with tempfile.TemporaryDirectory() as d:
    root, account = Path(d), "acme.us"
    cap = build(root, account, [{"data": {"items": [
        ig_node(str(10 + i), "brand.x", coauthors=[account]) for i in range(4)]}}])
    ctx = dd.FakeCtx(dd.FakePage(json.loads(cap.read_text(encoding="utf-8")),
                                 dd.FAKE_URL["instagram"]))
    asyncio.run(dd.delta_once(ctx, "instagram", account,
                              Archive(root, "in_%s" % account),
                              dd.offline_cfg(), dry_run=True))
    check(True, "跑完全程没有触发 FakeRequest 的断言（dry-run 不下载媒体）")

    raised = False
    try:
        asyncio.run(ctx.request.get("https://cdn.example.com/x.jpg"))
    except AssertionError:
        raised = True
    check(raised, "真去发请求会当场炸 —— 这道断言是防'哪天顺手加了下载'")


# ==========================================================================
print("\n[3] --break-coauthors 自检：判定退回旧实现时必须被照出来")

with tempfile.TemporaryDirectory() as d:
    root, account = Path(d), "acme.us"
    # 真实比例：本账号自己发的只有 1 篇，其余全是合作帖
    payloads = [{"data": {"items": [ig_node("1", account)] + [
        ig_node(str(20 + i), "brand.x", coauthors=[account]) for i in range(5)]}}]
    cap = build(root, account, payloads)
    dd.cfg = lambda: StubCfg(root, account)                 # noqa: E731
    rc = dd.run("instagram", cap, break_coauthors=True)
    check(rc == 0, "退化时 run() 返回 0 —— 中止是**预期结果**，不是工具坏了")

    # 退化后的实际判定：只剩 1 篇自家的，闸必须拦
    import core.parse as parse
    original = parse.on_timeline_of
    parse.on_timeline_of = lambda post, target: (
        post.owner == (target or "").strip().lower())
    try:
        ctx = dd.FakeCtx(dd.FakePage(json.loads(cap.read_text(encoding="utf-8")),
                                     dd.FAKE_URL["instagram"]))
        blocked = ""
        try:
            asyncio.run(dd.delta_once(ctx, "instagram", account,
                                      Archive(root, "in_%s" % account),
                                      dd.offline_cfg(), dry_run=True))
        except dd.DeltaBlocked as e:
            blocked = str(e)
    finally:
        parse.on_timeline_of = original
    check("没有拿到时间线" in blocked, "退化时 min_own_posts 那道闸拦住了本次")
    check("已知合作方" in blocked and "brand.x" in blocked,
          "中止理由直接点名合作帖判定 —— 而不是让人先去怀疑被拦（2026-08-30 就猜错过）")


# ==========================================================================
print("\n[4] 找不到转储时给可操作的提示，而不是崩掉")

with tempfile.TemporaryDirectory() as d:
    root, account = Path(d), "acme.us"
    Archive(root, "in_%s" % account)                  # 建目录但不放转储
    dd.cfg = lambda: StubCfg(root, account)           # noqa: E731
    check(dd.run("instagram", None, break_coauthors=False) == 1,
          "没有增量转储 → 返回 1 并提示先跑一次增量")
    check(dd.newest_delta_capture(root / ("in_%s" % account)) is None,
          "newest_delta_capture 在没有转储时返回 None")

with tempfile.TemporaryDirectory() as d:
    root, account = Path(d), "acme.us"
    arc = Archive(root, "in_%s" % account)
    # 回填转储不是增量转储：不能被误取（取错会拿一份几十条的快照当增量看）
    (arc.base / "_capture_1700000000.json").write_text("[]", encoding="utf-8")
    check(dd.newest_delta_capture(arc.base) is None,
          "**回填的 _capture_*.json 不会被当成增量转储** —— 两者语义完全不同")

print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
raise SystemExit(1 if fails else 0)

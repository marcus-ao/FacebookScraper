"""登录态回填主干自测：响应收尾、超时等待与媒体失败重试语义。"""
import asyncio
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

from core.store import Archive, Media, Post
import routes.backfill as backfill
from routes.backfill import (
    STALL_HINT_SECONDS, Collector, ScrollProgress, _download, _stdin_waiter)


fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("[1] 收尾会等待尚未读完的响应体")


async def collector_case():
    release = asyncio.Event()

    class Response:
        url = "https://www.facebook.com/api/graphql"
        status = 200

        async def text(self):
            await release.wait()
            return '{"data":{"post_id":"p1"}}\n非 JSON 行'

    collector = Collector()
    collector.submit(Response())
    await asyncio.sleep(0)
    check(collector.payloads == [], "响应体未完成时还没有被误算为已收集")
    release.set()
    await collector.drain()
    check(collector.hits == 1, "响应体完成后命中数正确")
    check(collector.payloads == [{"data": {"post_id": "p1"}}],
          "drain 后最后一段 JSON 已进入 capture 数据")
    check(not collector._tasks, "后台任务已清空且异常已被消费")


asyncio.run(collector_case())


print("\n[2] 等 Enter 的线程不会阻止最长会话超时退出")
gate = threading.Event()
done, thread = _stdin_waiter(lambda: gate.wait(2))
check(thread.daemon, "stdin 等待线程是守护线程，不参与进程退出等待")
check(not done.wait(0.05), "输入仍阻塞时不会误报已按 Enter")
gate.set()
check(done.wait(0.5), "输入结束后会通知主循环")
thread.join(timeout=0.5)


print("\n[3] 图片下载失败会留下可重试的 media_complete=False")


class Response:
    def __init__(self, data, ok=True, status=200, content_type="image/jpeg"):
        self._data = data
        self.ok = ok
        self.status = status
        self.headers = {"content-type": content_type}

    async def body(self):
        return self._data


class Request:
    def __init__(self):
        self.urls = []

    async def get(self, url, headers=None):
        self.urls.append((url, headers))
        if url.endswith("good.jpg"):
            return Response(b"image-bytes")
        return Response(b"")


class Context:
    def __init__(self):
        self.request = Request()


async def download_case(base):
    archive = Archive(base, "acct")
    post = Post(
        post_id="p2", platform="instagram", account="acct", text="caption",
        created_at="2026-08-29T00:00:00Z", media_complete=True,
        media=[Media(url="https://cdn/good.jpg", kind="image"),
               Media(url="https://cdn/empty.jpg", kind="image"),
               Media(url="https://cdn/video.mp4", kind="video")],
    )
    context = Context()
    await _download(context, archive, post, "https://www.instagram.com/acct/")
    return archive, post, context


with tempfile.TemporaryDirectory() as d:
    arc, post, ctx = asyncio.run(download_case(d))
    check(post.media[0].local_path is not None, "成功图片写入本地并记录相对路径")
    saved = arc.base / post.media[0].local_path
    check(saved.read_bytes() == b"image-bytes", "落盘内容与响应体一致且非零字节")
    check(post.media[1].local_path is None, "空响应没有被伪装成已下载媒体")
    check(post.media_complete is False, "任一已知图片失败都会标记媒体不完整")
    check([u for u, _ in ctx.request.urls] ==
          ["https://cdn/good.jpg", "https://cdn/empty.jpg"],
          "视频只留元数据，没有被下载")


print("\n[4] 回填主流程不会用 has(post_id) 跳过残缺帖的补全")


class Page:
    def on(self, _event, _handler):
        pass

    def remove_listener(self, _event, _handler):
        pass

    async def goto(self, _url, wait_until=None):
        pass

    async def close(self):
        pass


class RunContext:
    async def new_page(self):
        return Page()


class Closable:
    async def close(self):
        pass


class Playwright:
    async def stop(self):
        pass


with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    archive = Archive(root, "fa_acme")
    archive.append(Post(
        post_id="p3", platform="facebook", account="acme", text="caption",
        created_at="2026-08-29T00:00:00Z", media_complete=False,
        media=[Media(url="https://cdn/cover.jpg", kind="image")],
    ))
    complete = Post(
        post_id="p3", platform="facebook", account="acme", text="caption",
        created_at="2026-08-29T00:00:00Z", media_complete=True,
        media=[Media(url="https://cdn/one.jpg", kind="image"),
               Media(url="https://cdn/two.jpg", kind="image")],
        owner="acme",   # 主流程现在按 owner 过滤，不带归属的会被丢掉
    )
    # 同一批里混一条别人的帖子——这正是真实回填里发生的事
    foreign = Post(
        post_id="x9", platform="facebook", account="acme", text="someone else",
        created_at="2026-08-29T00:00:00Z",
        media=[Media(url="https://cdn/other.jpg", kind="image")],
        owner="somebodyelse", owner_name="Somebody Else",
    )

    class Config:
        archive_dir = root

        def __getitem__(self, key):
            if key == "targets":
                return {"facebook": "acme", "instagram": "acme"}
            raise KeyError(key)

        def get(self, _section, _key, default=None):
            return default

    done = threading.Event()
    done.set()
    downloaded = []

    async def fake_attach():
        return Playwright(), Closable(), RunContext()

    async def fake_download(_ctx, _arc, post, _referer):
        downloaded.append(post.post_id)

    original = (backfill.cfg, backfill.attach, backfill.extract,
                backfill._download, backfill._stdin_waiter)
    try:
        backfill.cfg = lambda: Config()
        backfill.attach = fake_attach
        backfill.extract = lambda *_a, **_k: [complete, foreign]
        backfill._download = fake_download
        backfill._stdin_waiter = lambda: (done, None)
        added = asyncio.run(backfill.run("facebook"))
    finally:
        (backfill.cfg, backfill.attach, backfill.extract,
         backfill._download, backfill._stdin_waiter) = original

    rebuilt = Archive(root, "fa_acme")
    rows = {row["post_id"]: row for row in rebuilt.rows()}
    current = rows["p3"]
    check(downloaded == ["p3"], "已存在但残缺的帖子仍进入媒体补全")
    check(added == 1, "补全版被计入本次新增/升级结果")
    check(current["media_complete"] is True and len(current["media"]) == 2,
          "归档最终由残缺封面升级为完整两图记录")

    # ↓ 2026-08-30 新增：真实回填混进了 266 条别人的帖子，主流程必须拦住
    check("x9" not in rows, "别人账号的帖子没有进 manifest")
    check("x9" not in downloaded,
          "别人账号的帖子连媒体都不下载（省流量，更省下游的麻烦）")
    rejected_path = rebuilt.base / "_rejected.jsonl"
    check(rejected_path.exists(), "被丢弃的帖子写进了 _rejected.jsonl（不得静默丢弃）")
    rej = [json.loads(line) for line in
           rejected_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    check(len(rej) == 1 and rej[0]["post_id"] == "x9", "_rejected.jsonl 里正是那一条")
    check(rej[0]["reason"] == "owner_mismatch" and rej[0]["owner"] == "somebodyelse",
          "丢弃原因与真实归属都记下来了")


print("\n[5] 滚动进度显示（B8）—— 让人知道自己滚到哪一年了")


def ig_node(pk, code, ts, owner="acme_us"):
    return {"pk": pk, "code": code, "taken_at": ts,
            "user": {"username": owner, "full_name": owner},
            "caption": {"text": "hi"},
            "image_versions2": {"candidates": [
                {"url": "https://cdn/%s.jpg" % code, "width": 1080, "height": 1080}]}}


prog = ScrollProgress("instagram", "acme_us")
payloads = [ig_node("1", "AAA", 1756000000)]          # 2025-08-24
prog.update(payloads)
check(len(prog.ids) == 1, "第一批解析出 1 篇")
first_earliest = prog.earliest

payloads.append(ig_node("2", "BBB", 1600000000))      # 更早：2020-09-13
prog.update(payloads)
check(len(prog.ids) == 2, "增量只处理新到的那段，累计 2 篇")
check(prog.earliest < first_earliest, "最早日期随着往下滚不断前移")
check(prog.earliest.startswith("2020-09"), "最早日期取的是最小值")

payloads.append(ig_node("1", "AAA", 1756000000))      # 同一帖再次出现
prog.update(payloads)
check(len(prog.ids) == 2, "同一帖在多个响应里重复出现只算一篇")

payloads.append(ig_node("9", "ZZZ", 1750000000, owner="someone_else"))
prog.update(payloads)
check(len(prog.ids) == 2, "别人账号的帖子不计入进度（否则数字会虚高）")

line = prog.line(0)
check("2 篇" in line and "acme_us" in line, "进度行里有篇数和账号名")
check("2020-09" in line, "进度行里有最早日期 —— 这才是判断到没到底的依据")
check("秒没有新内容" not in line, "刚有新内容时不提示收尾")
check("秒没有新内容" in prog.line(STALL_HINT_SECONDS + 1), "长时间没新内容时提示可以收尾")
check("—" in ScrollProgress("instagram", "x").line(0), "一篇都还没抓到时不显示假日期")

broken = ScrollProgress("instagram", "acme_us")
broken.update([{"pk": "1", "code": "A", "taken_at": "not-a-number",
                "user": "这里本该是个对象"}])
check(True, "脏响应不会让进度显示把整场抓取带崩")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

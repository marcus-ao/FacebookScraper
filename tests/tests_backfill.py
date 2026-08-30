"""登录态回填主干自测：响应收尾、超时等待与媒体失败重试语义。"""
import asyncio
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.store import Archive, Media, Post
import routes.backfill as backfill
from routes.backfill import Collector, _download, _stdin_waiter


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
        backfill.extract = lambda *_a, **_k: [complete]
        backfill._download = fake_download
        backfill._stdin_waiter = lambda: (done, None)
        added = asyncio.run(backfill.run("facebook"))
    finally:
        (backfill.cfg, backfill.attach, backfill.extract,
         backfill._download, backfill._stdin_waiter) = original

    current = {row["post_id"]: row for row in Archive(root, "fa_acme").rows()}["p3"]
    check(downloaded == ["p3"], "已存在但残缺的帖子仍进入媒体补全")
    check(added == 1, "补全版被计入本次新增/升级结果")
    check(current["media_complete"] is True and len(current["media"]) == 2,
          "归档最终由残缺封面升级为完整两图记录")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

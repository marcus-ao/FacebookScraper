r"""Day 1 · 一次性历史回填（登录态 + 人工滚动 + 响应拦截）

形态：脚本不驱动滚动，你自己滚。
    因此没有任何可识别的自动化行为特征 —— 滚动的确实是人。
    脚本只做三件事：挂响应监听、等你滚完、把捞到的 JSON 落盘。

前置：
    1. 双击 scripts\start_chrome.bat 起专用 Chrome
    2. 在那个窗口里登录抓取用的小号
    3. 保持窗口开着，运行本脚本

跑一次就够。跑完这个账号的历史内容就全在 archive/ 里了，
之后的新帖由 routes/delta.py 每天登出增量接手。
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

from core.chrome import attach
from core.config import cfg
from core.parse import extract
from core.store import Archive, Post

# 只收这些接口的响应，其余（埋点、字体、图片本体）直接跳过
INTEREST = ("/api/graphql", "/graphql/query", "/api/v1/feed",
            "/api/v1/users/", "/api/v1/media")


class Collector:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.hits = 0
        self._tasks: set[asyncio.Task] = set()

    def submit(self, response) -> None:
        """登记响应处理任务，收尾时统一等待，避免最后几段 JSON 还没读完就落盘。"""
        self._tasks.add(asyncio.create_task(self.on_response(response)))

    async def drain(self) -> None:
        """等待所有已登记的响应体读取完成，并消费后台任务异常。"""
        if not self._tasks:
            return
        tasks, self._tasks = tuple(self._tasks), set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                print(f"    ! 响应处理失败：{type(result).__name__}: {result}")

    async def on_response(self, response) -> None:
        if not any(k in response.url for k in INTEREST):
            return
        if response.status != 200:
            return
        try:
            body = await response.text()
        except Exception:
            return
        self.hits += 1
        # FB 的 GraphQL 有时一个响应里多段 JSON，按行拆
        for chunk in body.splitlines():
            chunk = chunk.strip()
            if not chunk.startswith("{"):
                continue
            try:
                self.payloads.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue


def _stdin_waiter(readline=None) -> tuple[threading.Event, threading.Thread]:
    """在守护线程里等 Enter；超时退出时不会被阻塞的 ``readline`` 拖住进程。"""
    done = threading.Event()
    read = readline or sys.stdin.readline

    def wait() -> None:
        try:
            read()
        except Exception:
            # stdin 被关闭也等价于“结束人工等待”；不要让守护线程打印无关 traceback。
            pass
        finally:
            done.set()

    thread = threading.Thread(target=wait, name="backfill-stdin", daemon=True)
    thread.start()
    return done, thread


async def run(platform: str) -> int:
    c = cfg()
    account = c["targets"][platform]
    url = ({"instagram": f"https://www.instagram.com/{account}/",
            "facebook": f"https://www.facebook.com/{account}/"})[platform]

    col = Collector()
    pw, browser, ctx = await attach()
    page = None
    response_handler = col.submit

    try:
        page = await ctx.new_page()
        page.on("response", response_handler)
        await page.goto(url, wait_until="domcontentloaded")
        print(f"\n已打开 {url}")
        print("=" * 62)
        print("  现在请在那个 Chrome 窗口里手工向下滚动，直到看见最早的帖子。")
        print("  慢慢滚，让每屏内容都加载出来（看到图片显示出来再继续）。")
        print("  滚完之后回到这里按 Enter。")
        print("=" * 62)

        deadline = time.monotonic() + c.get("backfill", "max_session_seconds", 1800)
        waiter, _ = _stdin_waiter()
        while not waiter.is_set():
            await asyncio.sleep(2)
            if time.monotonic() > deadline:
                print("\n达到最长会话时间，自动收尾。")
                break
            print(f"\r  已捕获 {col.hits} 个响应 / {len(col.payloads)} 段 JSON",
                  end="", flush=True)
        print()

        # 停止接收新事件，再等已经到达的响应体读完；否则用户按 Enter 时仍在处理的
        # 最后几段 JSON 会被无声漏出 capture 文件。
        page.remove_listener("response", response_handler)
        await col.drain()

        # 无条件转储原始响应，且必须在解析之前。
        # 解析器是按已知响应结构写的，真实结构一旦不同就会解析出 0 篇 ——
        # 没有这份转储，你滚了 20 分钟的成果会全部丢失，且无从排查。
        arc = Archive(c.archive_dir, f"{platform[:2]}_{account}")
        dump = arc.base / f"_capture_{int(time.time())}.json"
        dump.write_text(json.dumps(col.payloads, ensure_ascii=False), encoding="utf-8")
        print(f"原始响应已转储 → {dump}  ({dump.stat().st_size // 1024} KB)")

        posts = extract(col.payloads, platform, account, route="backfill")
        print(f"解析出 {len(posts)} 篇帖子")
        if not posts and col.payloads:
            print("\n[!] 捕获到响应但一篇都没解析出来 —— 说明响应结构与解析器不符。")
            print(f"    原始数据在 {dump.name}，把它发给我，我按真实结构改解析器。")
            print("    不用重滚。")

        n = 0
        for post in posts:
            if not arc.should_append(post):
                continue
            await _download(ctx, arc, post, url)
            arc.save_raw(post.post_id, post.to_row())
            if not arc.append(post):
                print(f"    ! {post.post_id} 媒体仍未补全，保留原归档并留待下次重试")
                continue
            n += 1
            imgs = sum(1 for m in post.media if m.kind == "image")
            vids = sum(1 for m in post.media if m.kind == "video")
            print(f"  + {post.post_id}  {imgs}图/{vids}视频  "
                  f"{post.text[:38].replace(chr(10), ' ')}")
        return n
    finally:
        try:
            if page is not None:
                page.remove_listener("response", response_handler)
                await col.drain()
                await page.close()
        finally:
            try:
                await browser.close()
            finally:
                await pw.stop()


async def _download(ctx, arc: Archive, post: Post, referer: str) -> None:
    """用浏览器自己的请求栈下载，复用其 cookie 与 TLS 指纹。

    视频不下载（范围外），但保留 URL 与元数据，
    否则连续性检查会把视频帖误报成缺口。
    """
    downloads_complete = True
    for i, m in enumerate(post.media):
        if m.kind == "video":
            continue
        try:
            resp = await ctx.request.get(m.url, headers={"Referer": referer})
            if not resp.ok:
                print(f"    ! 媒体 {resp.status} {post.post_id}[{i}]")
                downloads_complete = False
                continue
            data = await resp.body()
        except Exception as e:
            print(f"    ! 媒体失败 {post.post_id}[{i}]: {e}")
            downloads_complete = False
            continue
        if not data:
            print(f"    ! 媒体为空 {post.post_id}[{i}]")
            downloads_complete = False
            continue
        p = arc.media_path(post.post_id, i, resp.headers.get("content-type"))
        p.write_bytes(data)
        m.local_path = str(p.relative_to(arc.base))
    # ``media_complete`` 同时表示源响应是否给全、以及已知图片是否均已落盘。
    # 下载失败必须留下 False，下一次回填才会被 Archive.should_append 接受并重试。
    post.media_complete = post.media_complete and downloads_complete


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("facebook", "instagram"):
        # 提示里给 .bat 的用法：实际跑这个脚本的人多半是双击 .bat 进来的，
        # 告诉他一条他敲不出来的命令没有意义
        raise SystemExit(
            "用法：scripts\\run_backfill.bat <facebook|instagram>\n"
            "      （等价命令：.venv\\Scripts\\python.exe -m routes.backfill facebook）")
    print(f"\n新增 {asyncio.run(run(sys.argv[1]))} 篇")

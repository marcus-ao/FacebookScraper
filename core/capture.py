r"""浏览器响应捕获与媒体下载 —— 回填与增量共用的那一半。

**为什么单独成模块**：`routes/backfill.py`（人工滚动）和 `routes/delta.py`
（每日增量）在"身份"和"节奏"上完全不同，但"怎么把浏览器发出的接口响应捞下来"
和"怎么下载媒体"这两件事逐字相同。抄一份过去的代价不是重复代码，而是
**语义漂移**：CR-04 确立的「下载失败必须留下 media_complete=False，
下次才会被 should_append 接受并重试」这条规则一旦只在一处生效，
另一条路径就会安静地把失败记成完整，且要几周后才会被人发现。

本模块**不含任何驱动页面的动作**。滚动策略是两条路径各自的事：
回填由人滚（这是它的全部价值），增量按 C7 的深度上限自己滚几屏。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.store import Archive, Post

# 只收这些接口的响应，其余（埋点、字体、图片本体）直接跳过
INTEREST = ("/api/graphql", "/graphql/query", "/api/v1/feed",
            "/api/v1/users/", "/api/v1/media")


class Collector:
    """把页面发出的接口响应体收集成一批 JSON payload。

    注意 :meth:`submit` 与 :meth:`drain` 的配对：响应体是异步读的，
    停止监听之后必须 drain，否则最后到达的几段 JSON 会在页面关闭时被取消，
    形成**无提示的数据缺口**（CR-05）。
    """

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.hits = 0
        self.statuses: list[tuple[int, str]] = []   # 非 200 的 (状态码, URL)
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
            # 401/403/429 是增量最需要看见的信号：会话失效与限流都长这样。
            # 回填有人盯着，增量没有——所以状态码必须留痕，不能只看有没有 JSON。
            self.statuses.append((response.status, response.url))
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

    def blocked_status(self) -> tuple[int, str] | None:
        """捕获期间有没有出现"被拦"的状态码。有就返回第一个。

        401/403 = 会话失效或权限没了；429 = 限流。三者的处理办法都是
        **当次立即停止**（C7「异常即停」），区别只在给人的提示文案。
        """
        for status, url in self.statuses:
            if status in (401, 403, 429):
                return status, url
        return None


async def download_media(ctx, arc: Archive, post: Post, referer: str) -> None:
    """用浏览器自己的请求栈下载，复用其 cookie 与 TLS 指纹。

    视频不下载（范围外），但保留 URL 与元数据，
    否则连续性检查会把视频帖误报成缺口。

    ⚠️ 媒体 CDN URL 带签名且有时效，必须在拿到响应的**同一次运行内**下完，
    不能先存 URL 事后再取（存了就是 403）。
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
        p = arc.media_path(post, i, resp.headers.get("content-type"))
        p.write_bytes(data)
        m.local_path = str(p.relative_to(arc.base))
    # ``media_complete`` 同时表示源响应是否给全、以及已知图片是否均已落盘。
    # 下载失败必须留下 False，下一次抓取才会被 Archive.should_append 接受并重试。
    post.media_complete = post.media_complete and downloads_complete


def prune_captures(base: Path, keep: int, prefix: str = "_capture_delta_") -> int:
    """只保留最近 ``keep`` 份增量转储，返回删掉的份数。

    **回填的 `_capture_*.json` 不在此列**（前缀不同，且那两份是离线重放的唯一
    输入，删了就要用户重滚 40 分钟）。这里裁的是增量每天产生的那份。

    为什么增量也要转储：解析器悄悄失效时，"每天抓到 0 篇"和"这几天确实没发帖"
    在日志里长得一样，没有原始响应就无从分辨。
    为什么要裁：每天一份 MB 级 JSON，一年就是几个 GB。
    """
    if keep < 0:
        return 0
    files = sorted(base.glob(prefix + "*.json"))
    stale = files[:-keep] if keep else files
    for f in stale:
        try:
            f.unlink()
        except OSError:
            continue
    return len(stale)

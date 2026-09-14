"""回填与增量共用的响应捕获和媒体下载；页面操作由调用方负责。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.store import Archive, Post
from core import paid_model

# 只收这些接口的响应，其余（埋点、字体、图片本体）直接跳过
INTEREST = ("/api/graphql", "/graphql/query", "/api/v1/feed",
            "/api/v1/users/", "/api/v1/media")


_IMAGE_MIME_ALIASES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
    "image/avif": "image/avif",
}


def normalized_image_content_type(value: str | None) -> str | None:
    """只接受归档和人工审校链路能安全处理的静态光栅图片类型。"""
    if not value:
        return None
    return _IMAGE_MIME_ALIASES.get(value.split(";", 1)[0].strip().lower())


def _detected_image_content_type(data: bytes) -> str | None:
    """用文件签名识别允许的图片，避免 CDN/登录墙伪造 Content-Type。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    # AVIF 是 ISO-BMFF：ftyp box 的 major/compatible brand 必须含 avif/avis。
    if len(data) >= 16 and data[4:8] == b"ftyp":
        box_size = int.from_bytes(data[:4], "big")
        end = min(len(data), box_size if box_size >= 16 else len(data))
        brands = [data[i:i + 4] for i in range(8, end - 3, 4) if i != 12]
        if any(brand in {b"avif", b"avis"} for brand in brands):
            return "image/avif"
    return None


def validated_image_content_type(value: str | None, data: bytes) -> str | None:
    """MIME 与文件签名一致时返回规范 MIME，否则拒绝。"""
    declared = normalized_image_content_type(value)
    return declared if declared and _detected_image_content_type(data) == declared else None


def atomic_write_json(path: Path, value) -> None:
    """在同目录完整落盘后原子替换 capture JSON。"""
    paid_model.atomic_write_json(path, value)


class Collector:
    """异步收集响应；停止监听后须 drain，避免丢失尚未读完的响应。"""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.hits = 0
        self.statuses: list[tuple[int, str]] = []   # 非 200 的 (状态码, URL)
        self.sources: list[str] = []                # 出过 payload 的接口，供排查
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
            # 保留会话失效和限流信号，即使响应没有 JSON。
            self.statuses.append((response.status, response.url))
            return
        try:
            body = await response.text()
        except Exception:
            return
        self.hits += 1
        # FB 的 GraphQL 有时一个响应里多段 JSON，按行拆
        got = 0
        for chunk in body.splitlines():
            chunk = chunk.strip()
            if not chunk.startswith("{"):
                continue
            try:
                self.payloads.append(json.loads(chunk))
                got += 1
            except json.JSONDecodeError:
                continue
        if got:
            self.sources.append(response.url.split("?")[0])

    def blocked_status(self) -> tuple[int, str] | None:
        """返回首个 401/403/429，供调用方立即停止会话。"""
        for status, url in self.statuses:
            if status in (401, 403, 429):
                return status, url
        return None


async def download_media(ctx, arc: Archive, post: Post, referer: str) -> None:
    """复用浏览器请求栈下载静态媒体；签名 URL 须在当前运行内使用，视频只留元数据。"""
    downloads_complete = True
    for i, m in enumerate(post.media):
        if m.kind == "video":
            continue
        reusable = arc.reusable_media_path(post, m.url)
        if reusable is not None:
            try:
                existing = reusable.read_bytes()
            except OSError:
                existing = b""
            if existing and _detected_image_content_type(existing):
                m.local_path = str(reusable.relative_to(arc.base)).replace("\\", "/")
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
        content_type = validated_image_content_type(
            resp.headers.get("content-type"), data)
        if not content_type:
            print(f"    ! 媒体类型或文件签名异常 {post.post_id}[{i}]: "
                  f"{resp.headers.get('content-type') or '缺少 Content-Type'}")
            downloads_complete = False
            continue
        p = arc.media_path(post, i, content_type)
        p.write_bytes(data)
        m.local_path = str(p.relative_to(arc.base))
    # 源响应或下载不完整时保留 False，让后续抓取继续补齐。
    post.media_complete = post.media_complete and downloads_complete


def prune_captures(base: Path, keep: int, prefix: str = "_capture_delta_") -> int:
    """保留最近 keep 份增量转储；不清理回填转储。"""
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


def prune_captures_days(base: Path, keep_days: int, *, now: datetime | None = None) -> int:
    """按捕获日期清理增量转储；保留未知文件、链接和回填文件。"""
    if keep_days < 1:
        raise ValueError("keep_captures_days 至少为 1")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("capture 清理时刻必须包含时区")
    base = Path(base)
    if base.is_symlink() or base.resolve() != base.absolute():
        raise ValueError("capture 清理目录必须为实际目录，不能经过链接")
    cutoff = (now - timedelta(days=keep_days)).timestamp()
    removed = 0
    for path in base.glob("_capture_delta_*.json"):
        stamp = path.stem.removeprefix("_capture_delta_")
        if (not stamp.isdecimal() or int(stamp) >= cutoff or path.is_symlink()
                or not path.is_file() or path.resolve().parent != base.resolve()):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed

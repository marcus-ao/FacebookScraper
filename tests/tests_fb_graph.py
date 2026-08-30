"""保留的 Graph API 只读抓取自测：视频边界与图片失败状态。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉

import httpx

from core.store import Archive, Media, Post
from routes.fb_graph import _download_images


fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Client:
    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("good.jpg"):
            return httpx.Response(
                200, content=b"image-data", headers={"content-type": "image/jpeg"},
                request=request)
        if url.endswith("empty.jpg"):
            return httpx.Response(
                200, content=b"", headers={"content-type": "image/jpeg"},
                request=request)
        return httpx.Response(403, content=b"forbidden", request=request)


print("[1] 只读 Graph 路线严格保持‘视频只记元数据’边界")
with tempfile.TemporaryDirectory() as d:
    archive = Archive(d, "ig_acme")
    post = Post(
        post_id="g1", platform="instagram", account="acme", text="caption",
        created_at="2026-08-29T00:00:00Z", media_complete=True,
        media=[
            Media(url="https://cdn/good.jpg", kind="image"),
            Media(url="https://cdn/empty.jpg", kind="image"),
            Media(url="https://cdn/forbidden.jpg", kind="image"),
            Media(url="https://cdn/video.mp4", kind="video"),
        ],
    )
    client = Client()
    _download_images(client, archive, post)

    check("https://cdn/video.mp4" not in client.urls, "视频 URL 没有发起下载请求")
    check(post.media[3].local_path is None, "视频只保留 URL 与 kind 元数据")
    check(post.media[0].local_path is not None, "成功图片记录本地相对路径")
    check((archive.base / post.media[0].local_path).read_bytes() == b"image-data",
          "成功图片非空落盘")
    check(post.media[1].local_path is None, "空响应不写 0 字节文件")
    check(post.media[2].local_path is None, "HTTP 失败不伪装成已下载")
    check(post.media_complete is False, "任一图片失败会留下可重试的不完整状态")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

"""路线 C-2：浏览器驱动 + 网络层拦截（Facebook / Instagram 通用）

核心取舍：不解析 DOM，只拦截 XHR/GraphQL 响应。

    FB 和 IG 都是 React SPA，页面上的 class name 是构建期混淆的，
    每周都可能变；靠 CSS 选择器抓取的脚本必然持续失修。
    而喂给页面的 JSON 结构由后端接口决定，变更频率低一个数量级。

    所以这里让真实浏览器去滚动加载（它自己会带上正确的 doc_id、
    fb_dtsg、X-FB-LSD 等一堆参数，你不需要逆向任何一个），
    我们只在 page.on("response") 里把 JSON 捞出来。

    附带好处：想拿当前有效的 doc_id 时，从这里捞就行，
    不用去猜它这周轮换成了什么。

前置条件：先跑 python -m core.session login <platform>
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any, Callable

from core.session import SAFARI_UA, state_path
from core.store import Archive, Media, Post
from routes.ig_v1_feed import _parse_item

# 命中这些片段的响应才收集，其余（埋点、图片、字体）直接跳过
INTEREST = ("/api/graphql", "/graphql/query", "/api/v1/feed", "/api/v1/media")


class Collector:
    """把拦截到的 JSON 响应堆起来，滚动结束后统一解析。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def on_response(self, response) -> None:
        url = response.url
        if not any(k in url for k in INTEREST):
            return
        if response.status != 200:
            return
        try:
            body = await response.text()
        except Exception:
            return
        # FB 的 GraphQL 有时返回多段 JSON（每行一段），逐行尝试
        for chunk in body.splitlines():
            chunk = chunk.strip()
            if not chunk.startswith("{"):
                continue
            try:
                self.payloads.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue


async def harvest(
    platform: str,
    profile_url: str,
    scrolls: int = 15,
    headless: bool = False,
) -> list[dict]:
    """打开目标主页，滚动 N 次，返回拦截到的所有 JSON 响应。

    headless 默认 False：有头模式的指纹面明显更干净，
    个人自用没有必要为了省一个窗口去换检测风险。
    """
    from playwright.async_api import async_playwright

    sp = state_path(platform)
    if not sp.exists():
        raise SystemExit(f"缺少会话 {sp}，先跑 python -m core.session login {platform}")

    col = Collector()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            storage_state=str(sp),
            user_agent=SAFARI_UA,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()
        page.on("response", lambda r: asyncio.create_task(col.on_response(r)))

        await page.goto(profile_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(2500, 4500))

        for i in range(scrolls):
            await page.mouse.wheel(0, random.randint(1800, 3200))
            # 随机化滚动节奏。等距滚动是最容易被识别的行为特征之一。
            await page.wait_for_timeout(random.randint(1800, 3600))
            if (i + 1) % 5 == 0:
                print(f"  滚动 {i + 1}/{scrolls}，已捕获 {len(col.payloads)} 段响应")

        await ctx.storage_state(path=str(sp))   # 回写刷新后的 cookie
        await browser.close()

    return col.payloads


def walk(node: Any, pred: Callable[[dict], bool]):
    """在任意深度的 JSON 里找出满足 pred 的所有 dict。

    FB/IG 的响应嵌套很深且结构随版本漂移，写死路径不如全树搜索稳。
    """
    if isinstance(node, dict):
        if pred(node):
            yield node
        for v in node.values():
            yield from walk(v, pred)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, pred)


def extract_ig_posts(payloads: list[dict], account: str) -> list[Post]:
    """从拦截结果里挑出 IG 帖子节点。

    判据：同时具备 pk/code 和 taken_at 的 dict 就是一条 media。
    比匹配某个具体的 GraphQL 字段路径更抗结构变化。
    """
    seen: set[str] = set()
    out: list[Post] = []
    for payload in payloads:
        for node in walk(payload, lambda d: "code" in d and "taken_at" in d and "pk" in d):
            pid = str(node["pk"])
            if pid in seen:
                continue
            seen.add(pid)
            try:
                out.append(_parse_item(node, account))
            except Exception:
                continue
    return out


def extract_fb_posts(payloads: list[dict], account: str) -> list[Post]:
    """从拦截结果里挑出 FB 帖子节点。

    FB 的 story 节点判据：有 post_id，且带 message 或 attachments。
    FB 的 GraphQL schema 比 IG 乱得多，这里只做尽力而为的提取，
    真正稳的 FB 路径是官方 Graph API（见 routes/fb_graph.py）。
    """
    seen: set[str] = set()
    out: list[Post] = []
    for payload in payloads:
        for node in walk(payload, lambda d: "post_id" in d and (
                "message" in d or "attachments" in d)):
            pid = str(node["post_id"])
            if pid in seen:
                continue
            seen.add(pid)
            msg = node.get("message")
            text = msg.get("text", "") if isinstance(msg, dict) else (msg or "")
            media: list[Media] = []
            for img in walk(node.get("attachments", []),
                            lambda d: "uri" in d and "height" in d and "width" in d):
                media.append(Media(url=img["uri"], kind="image",
                                   width=img.get("width"), height=img.get("height")))
            out.append(Post(
                post_id=pid, platform="facebook", account=account,
                text=text, created_at="", permalink=None,
                media=media, source_route="intercept",
            ))
    return out


async def run(platform: str, account: str, scrolls: int,
              archive_root: str | Path = "archive") -> int:
    url = ({"instagram": f"https://www.instagram.com/{account}/",
            "facebook": f"https://www.facebook.com/{account}/"})[platform]

    payloads = await harvest(platform, url, scrolls=scrolls)
    print(f"捕获 {len(payloads)} 段 JSON 响应")

    posts = (extract_ig_posts if platform == "instagram" else extract_fb_posts)(
        payloads, account)
    print(f"解析出 {len(posts)} 篇帖子")

    arc = Archive(archive_root, f"{platform[:2]}_{account}")
    import httpx
    n = 0
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for post in posts:
            if arc.has(post.post_id):
                continue
            for i, m in enumerate(post.media):
                try:
                    resp = client.get(m.url, headers={"User-Agent": SAFARI_UA,
                                                      "Referer": url})
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                p = arc.media_path(post.post_id, i, resp.headers.get("content-type"))
                p.write_bytes(resp.content)
                m.local_path = str(p.relative_to(arc.base))
            arc.append(post)
            n += 1
    return n


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        raise SystemExit(
            "用法：python -m routes.intercept <facebook|instagram> <account> [scrolls]")
    s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    print(f"新增 {asyncio.run(run(sys.argv[1], sys.argv[2], s))} 篇")

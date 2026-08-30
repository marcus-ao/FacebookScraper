"""路线 C-1：Instagram 直连 legacy REST 端点 /api/v1/feed/user/

为什么是这条而不是 GraphQL：
    Instagram 的 web GraphQL 端点要求一个硬编码的 doc_id，而这个 doc_id
    大约每 2-4 周轮换一次。轮换后旧 doc_id 返回 401，且错误文案是
    "Please wait a few minutes before you try again" —— 这个提示是误导的，
    它不是限流，等多久都不会恢复。instaloader 的 Profile.get_posts() 周期性
    失效就是这个原因（instaloader issue #2689，截至 2026-08 仍未合并修复）。

    /api/v1/feed/user/ 是 web 客户端翻页时仍在使用的老 REST 端点，不依赖
    doc_id，因此稳定得多。返回的是 iphone_struct 结构。

前置条件：
    1. 先跑 python -m core.session login instagram 存一次会话
    2. 从住宅 IP 运行。数据中心 IP 在首次请求就会被拦，云服务器上跑不通。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from core.session import SAFARI_UA, Pacer, cookies_for
from core.store import Archive, Media, Post

IG = "https://www.instagram.com"
# 这两个是 web 客户端的公开常量，不是密钥
APP_ID = "936619743392459"
ASBD_ID = "198387"


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": SAFARI_UA,
        "X-IG-App-ID": APP_ID,
        "X-ASBD-ID": ASBD_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def resolve_user_id(client: httpx.Client, username: str) -> str:
    """username → numeric user_id。feed 端点只认数字 id。"""
    r = client.get(
        f"{IG}/api/v1/users/web_profile_info/",
        params={"username": username},
        headers=_headers(f"{IG}/{username}/"),
    )
    r.raise_for_status()
    return r.json()["data"]["user"]["id"]


def _parse_item(item: dict, account: str) -> Post:
    """iphone_struct → 统一 schema。

    轮播帖的子项在 carousel_media 里；单图帖没有这个字段，
    图片直接在 image_versions2.candidates（第一个是最大尺寸）。
    """
    code = item.get("code", "")
    caption = (item.get("caption") or {}).get("text", "") or ""

    def best_image(node: dict) -> dict | None:
        cands = (node.get("image_versions2") or {}).get("candidates") or []
        return cands[0] if cands else None

    media: list[Media] = []
    children = item.get("carousel_media") or [item]
    for child in children:
        if child.get("video_versions"):
            v = child["video_versions"][0]
            media.append(Media(url=v["url"], kind="video",
                               width=v.get("width"), height=v.get("height")))
        else:
            img = best_image(child)
            if img:
                media.append(Media(url=img["url"], kind="image",
                                   width=img.get("width"), height=img.get("height")))

    return Post(
        post_id=str(item.get("pk") or item.get("id") or code),
        platform="instagram",
        account=account,
        text=caption,
        created_at=time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(item.get("taken_at", 0))
        ),
        permalink=f"{IG}/p/{code}/" if code else None,
        media=media,
        source_route="v1_feed",
    )


def scrape(username: str, archive_root: str | Path = "archive",
           max_posts: int | None = None, page_size: int = 12) -> int:
    cookies = cookies_for("instagram", "instagram.com")
    if "sessionid" not in cookies:
        raise SystemExit("会话里没有 sessionid，请重新执行 login")

    arc = Archive(archive_root, f"ig_{username}")
    pacer = Pacer()
    referer = f"{IG}/{username}/"
    n_new = 0

    with httpx.Client(cookies=cookies, timeout=30.0, follow_redirects=True) as client:
        user_id = resolve_user_id(client, username)
        print(f"{username} → user_id={user_id}")

        max_id: str | None = None
        while True:
            pacer.wait()
            params: dict[str, str | int] = {"count": page_size}
            if max_id:
                params["max_id"] = max_id

            r = client.get(f"{IG}/api/v1/feed/user/{user_id}/",
                           params=params, headers=_headers(referer))

            if r.status_code == 401:
                # 这里的 401 通常意味着会话失效或该端点也被收紧，退避无效
                raise SystemExit(
                    "401：会话可能已失效。重新执行 login；若仍 401，"
                    "说明该端点被收紧，改走官方 API 或浏览器拦截路线。"
                )
            if r.status_code == 429:
                pacer.backoff(1)
                continue
            r.raise_for_status()

            payload = r.json()
            items = payload.get("items") or []
            if not items:
                break

            for item in items:
                post = _parse_item(item, username)
                if arc.has(post.post_id):
                    continue
                arc.save_raw(post.post_id, item)
                _download_media(client, arc, post, referer)
                arc.append(post)
                n_new += 1
                print(f"  + {post.post_id}  {len(post.media)} 媒体  "
                      f"{post.text[:40].replace(chr(10), ' ')}")
                if max_posts and n_new >= max_posts:
                    return n_new

            if not payload.get("more_available"):
                break
            max_id = payload.get("next_max_id")
            if not max_id:
                break

    return n_new


def _download_media(client: httpx.Client, arc: Archive, post: Post,
                    referer: str) -> None:
    """媒体 CDN URL 带签名且有时效，必须在拿到响应后立刻下载。"""
    for i, m in enumerate(post.media):
        try:
            resp = client.get(m.url, headers={"User-Agent": SAFARI_UA,
                                              "Referer": referer})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"    ! 媒体下载失败 {post.post_id}[{i}]: {e}")
            continue
        path = arc.media_path(post.post_id, i, resp.headers.get("content-type"))
        path.write_bytes(resp.content)
        m.local_path = str(path.relative_to(arc.base))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法：python -m routes.ig_v1_feed <username> [max_posts]")
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    total = scrape(sys.argv[1], max_posts=limit)
    print(f"\n新增 {total} 篇")

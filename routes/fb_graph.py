"""通过官方 Graph API 读取授权账号；使用 published_posts 排除访客和被标记内容。"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from core.media import image_facts
from core.store import Archive, Media, Post

GRAPH = "https://graph.facebook.com/v21.0"

POST_FIELDS = ",".join([
    "id", "message", "created_time", "permalink_url",
    "full_picture",
    "attachments{media_type,media,url,subattachments{media_type,media,url}}",
])


def _media_from_attachments(att: dict) -> list[Media]:
    out: list[Media] = []
    subs = (att.get("subattachments") or {}).get("data") or [att]
    for s in subs:
        media = s.get("media") or {}
        img = media.get("image") or {}
        if img.get("src"):
            kind = "video" if s.get("media_type") == "video" else "image"
            out.append(Media(url=img["src"], kind=kind,
                             width=img.get("width"), height=img.get("height")))
    return out


def _download_images(client, arc: Archive, post: Post) -> None:
    """只下载图片；视频保留元数据。失败时留下可补全的归档状态。"""
    downloads_complete = True
    if post.source_media_complete is None:
        post.source_media_complete = post.media_complete
    for i, media in enumerate(post.media):
        if media.kind == "video":
            continue
        try:
            response = client.get(media.url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            print(f"    ! 媒体失败 {post.post_id}[{i}]: {e}")
            downloads_complete = False
            continue
        if not response.content:
            print(f"    ! 媒体为空 {post.post_id}[{i}]")
            downloads_complete = False
            continue
        facts = image_facts(response.content, response.headers.get("content-type") or "")
        if not facts:
            print(f"    ! 媒体类型或文件签名异常 {post.post_id}[{i}]: "
                  f"{response.headers.get('content-type') or '缺少 Content-Type'}")
            downloads_complete = False
            continue
        try:
            arc.save_media(post, i, response.content, facts)
        except OSError:
            print(f"    ! 媒体写入失败 {post.post_id}[{i}]，保留已保存内容")
            downloads_complete = False
    post.media_complete = post.source_media_complete and downloads_complete


def scrape_page(page_id: str, token: str, archive_root: str | Path = "archive",
                limit: int = 100) -> int:
    arc = Archive(archive_root, f"fb_{page_id}")
    url = f"{GRAPH}/{page_id}/published_posts"
    params = {"fields": POST_FIELDS, "limit": limit, "access_token": token}
    n = 0

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        while url:
            r = client.get(url, params=params)
            if r.status_code != 200:
                raise SystemExit(f"Graph API {r.status_code}: {r.text[:400]}")
            body = r.json()

            for item in body.get("data", []):
                pid = item["id"]
                media: list[Media] = []
                for att in (item.get("attachments") or {}).get("data", []):
                    media.extend(_media_from_attachments(att))
                if not media and item.get("full_picture"):
                    media.append(Media(url=item["full_picture"], kind="image"))

                post = Post(
                    post_id=pid, platform="facebook", account=page_id,
                    text=item.get("message", "") or "",
                    created_at=item.get("created_time", ""),
                    permalink=item.get("permalink_url"),
                    media=media, source_route="graph_api",
                    # 显式记录路径确定的 owner，供下游统一归属检查。
                    owner=str(page_id).strip().lower(),
                )
                if not arc.should_append(post):
                    continue
                _download_images(client, arc, post)
                if not arc.append(post):
                    continue
                n += 1
                print(f"  + {pid}  {len(media)} 媒体  {post.text[:40]}")

            url = (body.get("paging") or {}).get("next")
            params = {}      # next 链接自带全部参数

    return n


def scrape_ig_professional(ig_user_id: str, token: str,
                           archive_root: str | Path = "archive") -> int:
    """通过 Instagram Login API 读取 Professional 账号。"""
    arc = Archive(archive_root, f"ig_{ig_user_id}")
    fields = "id,caption,media_type,media_url,permalink,timestamp,children{media_url,media_type}"
    url = f"{GRAPH}/{ig_user_id}/media"
    params = {"fields": fields, "limit": 100, "access_token": token}
    n = 0

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        while url:
            r = client.get(url, params=params)
            if r.status_code != 200:
                raise SystemExit(f"IG API {r.status_code}: {r.text[:400]}")
            body = r.json()

            for item in body.get("data", []):
                pid = item["id"]
                kids = (item.get("children") or {}).get("data") or [item]
                media = [
                    Media(url=k["media_url"],
                          kind="video" if k.get("media_type") == "VIDEO" else "image")
                    for k in kids if k.get("media_url")
                ]
                post = Post(
                    post_id=pid, platform="instagram", account=ig_user_id,
                    text=item.get("caption", "") or "",
                    created_at=item.get("timestamp", ""),
                    permalink=item.get("permalink"),
                    media=media, source_route="graph_api",
                    owner=str(ig_user_id).strip().lower(),   # 同上
                )
                if not arc.should_append(post):
                    continue
                _download_images(client, arc, post)
                if not arc.append(post):
                    continue
                n += 1

            url = (body.get("paging") or {}).get("next")
            params = {}

    return n


if __name__ == "__main__":
    import sys

    tok = os.environ.get("META_TOKEN")
    if not tok:
        raise SystemExit("需要环境变量 META_TOKEN")
    if len(sys.argv) < 3:
        raise SystemExit("用法：META_TOKEN=... python -m routes.fb_graph <fb|ig> <id>")
    fn = scrape_page if sys.argv[1] == "fb" else scrape_ig_professional
    print(f"新增 {fn(sys.argv[2], tok)} 篇")

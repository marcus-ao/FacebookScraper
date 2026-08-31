"""路线 B：官方 Graph API（Facebook Page / Instagram Professional）

自有账号首选。零封号风险、可增量、可无人值守，且不需要 App Review
（Business 类型应用自动获得 Standard Access，只要 Page 管理员本人
在该应用上有 admin/developer/tester 角色即可）。

两个必须记住的坑：
  - 读取用 /published_posts 而不是 /feed。/feed 会混进访客发帖和
    本 Page 被标记的帖子，那些不是你的内容。
  - /{page-id}/videos 边只写不读，视频要从 published_posts 的
    attachments 里取。
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from core.capture import validated_image_content_type
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
        content_type = validated_image_content_type(
            response.headers.get("content-type"), response.content)
        if not content_type:
            print(f"    ! 媒体类型或文件签名异常 {post.post_id}[{i}]: "
                  f"{response.headers.get('content-type') or '缺少 Content-Type'}")
            downloads_complete = False
            continue
        path = arc.media_path(post, i, content_type)
        path.write_bytes(response.content)
        media.local_path = str(path.relative_to(arc.base))
    post.media_complete = post.media_complete and downloads_complete


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
                    # Graph API 只会返回该 page 自己的帖子，归属是路径固有的，
                    # 不像爬取路径那样会混进别人的内容。仍然显式写上：
                    # 留 None 的话，将来任何按 owner 过滤的地方都会把它们全丢掉。
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
    """Instagram API with Instagram Login。

    注意 Basic Display API 已废弃，个人账号已无任何官方 API 通道。
    需要 Professional 账号；其中 Creator 账号不必绑定 Facebook 主页，
    是最轻量的官方路径。
    """
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

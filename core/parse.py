"""将 IG iphone_struct、GraphQL media 与 FB story 归一为 Post；缺轮播子项时标记媒体不全。"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from core.store import Media, Post

IG = "https://www.instagram.com"


def iso(ts: int | float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))


def walk(node: Any, pred: Callable[[dict], bool]) -> Iterator[dict]:
    """遍历嵌套 JSON 中满足 pred 的字典。"""
    if isinstance(node, dict):
        if pred(node):
            yield node
        for v in node.values():
            yield from walk(v, pred)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, pred)


# IG iphone_struct

def is_iphone_struct(d: dict) -> bool:
    """要求有效 code 并排除 carousel_item，避免将轮播子图识别成帖子。"""
    return (bool(d.get("pk")) and bool(d.get("code")) and bool(d.get("taken_at"))
            and d.get("product_type") != "carousel_item")


def ig_coauthors(node: dict) -> list[str]:
    """提取已接受的 coauthor_producers；邀请未接受者不算合作作者。"""
    out: list[str] = []
    for c in node.get("coauthor_producers") or []:
        if isinstance(c, dict):
            name = (c.get("username") or "").strip().lower()
        elif isinstance(c, str):
            name = c.strip().lower()
        else:
            continue
        if name and name not in out:
            out.append(name)
    return out


def from_iphone_struct(item: dict, account: str, route: str) -> Post:
    code = item.get("code", "")
    caption = (item.get("caption") or {}).get("text", "") or ""
    # 兼容 user/owner 两种归属字段。
    ig_user = item.get("user") or item.get("owner") or {}
    owner = (ig_user.get("username") or "").strip().lower() or None if isinstance(ig_user, dict) else None
    owner_name = ig_user.get("full_name") if isinstance(ig_user, dict) else None

    def best_image(node: dict) -> dict | None:
        cands = (node.get("image_versions2") or {}).get("candidates") or []
        # candidates 按尺寸降序，取第 0 个即最大尺寸
        if not isinstance(cands, list):
            return None
        return next((c for c in cands
                     if isinstance(c, dict) and c.get("url")), None)

    media: list[Media] = []
    children = item.get("carousel_media") or [item]
    media_complete = True
    if not isinstance(children, list):
        children = [item]
        media_complete = False
    for child in children:
        if not isinstance(child, dict):
            media_complete = False
            continue
        versions = child.get("video_versions") or []
        has_video = bool(versions)
        video = (next((v for v in versions
                       if isinstance(v, dict) and v.get("url")), None)
                 if isinstance(versions, list) else None)
        if has_video:
            if video:
                # 视频不下载，但记录存在，否则连续性检查会误报缺口
                media.append(Media(url=video["url"], kind="video",
                                   width=video.get("width"), height=video.get("height")))
            else:
                # 已明确是视频却缺视频 URL，不能退回缩略图并伪装成完整图片项。
                media_complete = False
            continue
        img = best_image(child)
        if img:
            media.append(Media(url=img["url"], kind="image",
                               width=img.get("width"), height=img.get("height")))
        else:
            # 局部结构异常保留父帖并标残缺，供后续补齐。
            media_complete = False

    return Post(
        post_id=str(item.get("pk") or item.get("id") or code),
        platform="instagram", account=account,
        text=caption, created_at=iso(item.get("taken_at")),
        permalink=f"{IG}/p/{code}/" if code else None,
        media=media, source_route=route, media_complete=media_complete,
        owner=owner, owner_name=owner_name, coauthors=ig_coauthors(item),
    )


# IG GraphQL timeline

def is_graphql_node(d: dict) -> bool:
    # 检查值有效性，避免空字段的子项误命中。
    return bool(d.get("shortcode")) and bool(d.get("taken_at_timestamp"))


def from_graphql_node(node: dict, account: str, route: str) -> Post:
    code = node.get("shortcode", "")
    ig_owner = node.get("owner") or node.get("user") or {}
    owner = ((ig_owner.get("username") or "").strip().lower() or None
             if isinstance(ig_owner, dict) else None)
    owner_name = ig_owner.get("full_name") if isinstance(ig_owner, dict) else None
    caps = (node.get("edge_media_to_caption") or {}).get("edges") or []
    text = caps[0]["node"]["text"] if caps else ""

    typename = node.get("__typename", "")
    is_video = bool(node.get("is_video")) or typename == "GraphVideo"
    is_carousel = typename == "GraphSidecar"

    dims = node.get("dimensions") or {}
    media: list[Media] = []
    if node.get("display_url"):
        media.append(Media(
            url=node["display_url"],
            kind="video" if is_video else "image",
            width=dims.get("width"), height=dims.get("height"),
        ))

    # 轮播帖在这个端点只给封面，子项拿不到
    children = (node.get("edge_sidecar_to_children") or {}).get("edges") or []
    for e in children:
        n = e.get("node") or {}
        if n.get("display_url"):
            d2 = n.get("dimensions") or {}
            media.append(Media(
                url=n["display_url"],
                kind="video" if n.get("is_video") else "image",
                width=d2.get("width"), height=d2.get("height"),
            ))

    complete = not (is_carousel and not children)

    return Post(
        post_id=str(node.get("id") or code),
        platform="instagram", account=account,
        text=text, created_at=iso(node.get("taken_at_timestamp")),
        permalink=f"{IG}/p/{code}/" if code else None,
        media=media, source_route=route, media_complete=complete,
        owner=owner, owner_name=owner_name, coauthors=ig_coauthors(node),
    )


# Facebook story

def is_fb_story(d: dict) -> bool:
    return bool(d.get("post_id")) and ("message" in d or "attachments" in d)


FB_WATCH = "https://www.facebook.com/watch/?v="


def _fb_slug(url: str | None) -> str | None:
    """从 FB URL 提取小写 slug；无自定义名称的主页返回 id:<数字>。"""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if tail.startswith("profile.php"):
        _, _, qs = tail.partition("?")
        for part in qs.split("&"):
            key, _, value = part.partition("=")
            if key == "id" and value:
                return "id:" + value
        return None
    return tail.lower() or None


def _fb_actor(node: dict) -> tuple[str | None, str | None]:
    """从 actors[0] 取归属；比较 URL 账号名，不比较显示名。"""
    actors = node.get("actors")
    if not (isinstance(actors, list) and actors and isinstance(actors[0], dict)):
        return None, None
    a = actors[0]
    slug = _fb_slug(a.get("url"))
    if slug is None and a.get("id"):
        slug = "id:" + str(a["id"])
    return slug, a.get("name")


def _fb_videos(node: dict) -> list[Media]:
    """仅记录顶层附件视频元数据；watch 链接由 ID 构造，不作为下载地址。"""
    out: list[Media] = []
    seen: set[str] = set()

    def collect(container) -> None:
        if not isinstance(container, list):
            return
        for att in container:
            if not isinstance(att, dict):
                continue
            m = att.get("media")
            if isinstance(m, dict) and m.get("__typename") == "Video" and m.get("id"):
                vid = str(m["id"])
                if vid not in seen:
                    seen.add(vid)
                    out.append(Media(url=FB_WATCH + vid, kind="video"))
            sub = att.get("all_subattachments")
            if isinstance(sub, dict):
                collect(sub.get("nodes"))

    collect(node.get("attachments"))
    return out


def from_fb_story(node: dict, account: str, route: str) -> Post:
    msg = node.get("message")
    text = msg.get("text", "") if isinstance(msg, dict) else (msg or "")

    media: list[Media] = []
    seen_uris: set[str] = set()
    for img in walk(node.get("attachments", []),
                    lambda d: "uri" in d and "width" in d and "height" in d):
        uri = img["uri"]
        if uri in seen_uris:
            continue
        seen_uris.add(uri)
        media.append(Media(url=uri, kind="image",
                           width=img.get("width"), height=img.get("height")))
    # 同一张图常有多个尺寸变体，按面积降序保留最大的那些
    media.sort(key=lambda m: (m.width or 0) * (m.height or 0), reverse=True)
    media.extend(_fb_videos(node))

    owner, owner_name = _fb_actor(node)
    ts = node.get("creation_time") or node.get("created_time")
    return Post(
        post_id=str(node["post_id"]), platform="facebook", account=account,
        text=text, created_at=iso(ts) if isinstance(ts, (int, float)) else (ts or ""),
        permalink=node.get("url") or node.get("permalink_url"),
        media=media, source_route=route,
        # 纯文字或视频不等于媒体残缺；下载失败由下载层标记。
        media_complete=True,
        owner=owner, owner_name=owner_name,
    )


def _merge_post(current: Post, candidate: Post) -> Post:
    """合并同帖响应：优先完整媒体，并补齐正文、时间、链接及作者信息。"""
    def rank(post: Post) -> tuple[int, bool, bool, bool, bool]:
        return (len(post.media), post.media_complete, bool(post.text),
                bool(post.created_at), bool(post.permalink))

    winner, other = ((candidate, current)
                     if rank(candidate) > rank(current)
                     else (current, candidate))
    # 合并合作作者集合，避免分片响应遗漏目标账号。
    merged = list(winner.coauthors or [])
    for name in (other.coauthors or []):
        if name not in merged:
            merged.append(name)
    winner.coauthors = merged
    # 补齐另一份响应中的归属，避免误判为未知作者。
    for attr in ("text", "created_at", "permalink", "owner", "owner_name"):
        if not getattr(winner, attr) and getattr(other, attr):
            setattr(winner, attr, getattr(other, attr))
    if len(winner.media) == len(other.media):
        winner.media_complete = winner.media_complete or other.media_complete
    return winner

def extract(payloads: list[dict], platform: str, account: str,
            route: str) -> list[Post]:
    """从一批 JSON 响应里抽出全部帖子，按 post_id 去重。"""
    matchers = {
        "instagram": [(is_iphone_struct, from_iphone_struct),
                      (is_graphql_node, from_graphql_node)],
        "facebook": [(is_fb_story, from_fb_story)],
    }[platform]

    out: dict[str, Post] = {}
    for payload in payloads:
        for pred, build in matchers:
            for node in walk(payload, pred):
                try:
                    post = build(node, account, route)
                except Exception:
                    continue
                if not post.post_id:
                    continue
                prev = out.get(post.post_id)
                # 同一帖可能在多个响应里出现：保留媒体更全者，同时补齐正文等字段。
                out[post.post_id] = post if prev is None else _merge_post(prev, post)
    return list(out.values())


def on_timeline_of(post: Post, target: str) -> bool:
    """目标为 owner 或已接受的 coauthor 即属于该时间线；账号名忽略大小写。"""
    who = (target or "").strip().lower()
    return post.owner == who or who in (post.coauthors or [])


def partition_by_owner(posts: list[Post], account: str) -> tuple[list[Post], list[dict]]:
    """返回 (kept, rejected)，保留目标及合作帖；调用方须落盘拒绝原因。"""
    target = (account or "").strip().lower()
    kept: list[Post] = []
    rejected: list[dict] = []
    for post in posts:
        if on_timeline_of(post, target):
            kept.append(post)
            continue
        rejected.append({
            "post_id": post.post_id,
            "platform": post.platform,
            "owner": post.owner,
            "owner_name": post.owner_name,
            "coauthors": post.coauthors,
            "created_at": post.created_at,
            "permalink": post.permalink,
            "text_head": (post.text or "")[:80],
            "reason": "owner_unknown" if post.owner is None else "owner_mismatch",
            "expected_owner": target,
        })
    return kept, rejected

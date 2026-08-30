"""把各路来源的 JSON 收敛成统一的 Post。

三种输入形态，结构互不相同：
  1. iphone_struct    —— 回填时拦截到的 /api/v1/ 响应，字段最全
  2. GraphQL node     —— 登出 web_profile_info 的 timeline media，字段较少
  3. FB story node    —— Facebook GraphQL，结构最乱

形态 2 的重要限制：timeline media 里**不含轮播子项**，只有封面图
(display_url)。所以登出增量能发现新帖、拿到正文和封面，但拿不到
轮播帖的全部图片 —— 这类帖会被标记 media_complete=False，
由完整性检查汇总，留给人工或下一次登录态回填补齐。
"""
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
    """在任意深度的 JSON 里找出满足 pred 的所有 dict。

    FB/IG 的响应嵌套深且随版本漂移，全树搜索比写死字段路径抗变化。
    """
    if isinstance(node, dict):
        if pred(node):
            yield node
        for v in node.values():
            yield from walk(v, pred)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, pred)


# --------------------------------------------------------------------------
# 形态 1：iphone_struct（回填拦截）
# --------------------------------------------------------------------------

def is_iphone_struct(d: dict) -> bool:
    return "pk" in d and "code" in d and "taken_at" in d


def from_iphone_struct(item: dict, account: str, route: str) -> Post:
    code = item.get("code", "")
    caption = (item.get("caption") or {}).get("text", "") or ""

    def best_image(node: dict) -> dict | None:
        cands = (node.get("image_versions2") or {}).get("candidates") or []
        # candidates 按尺寸降序，取第 0 个即最大尺寸
        return cands[0] if cands else None

    media: list[Media] = []
    children = item.get("carousel_media") or [item]
    for child in children:
        if child.get("video_versions"):
            # 视频不下载，但记录存在，否则连续性检查会误报缺口
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
        platform="instagram", account=account,
        text=caption, created_at=iso(item.get("taken_at")),
        permalink=f"{IG}/p/{code}/" if code else None,
        media=media, source_route=route, media_complete=True,
    )


# --------------------------------------------------------------------------
# 形态 2：GraphQL timeline node（登出 web_profile_info）
# --------------------------------------------------------------------------

def is_graphql_node(d: dict) -> bool:
    return "shortcode" in d and "taken_at_timestamp" in d


def from_graphql_node(node: dict, account: str, route: str) -> Post:
    code = node.get("shortcode", "")
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
    )


# --------------------------------------------------------------------------
# 形态 3：Facebook story node
# --------------------------------------------------------------------------

def is_fb_story(d: dict) -> bool:
    return "post_id" in d and ("message" in d or "attachments" in d)


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

    ts = node.get("creation_time") or node.get("created_time")
    return Post(
        post_id=str(node["post_id"]), platform="facebook", account=account,
        text=text, created_at=iso(ts) if isinstance(ts, (int, float)) else (ts or ""),
        permalink=node.get("url") or node.get("permalink_url"),
        media=media, source_route=route, media_complete=bool(media),
    )


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

def _merge_post(current: Post, candidate: Post) -> Post:
    """合并同一帖的两份响应，优先媒体更全者并补回非媒体字段。

    同一篇帖子可能同时出现在 GraphQL 与 ``iphone_struct`` 响应里。只比较媒体
    数量会在数量相同时保留先到的空正文；而只整条替换又可能让媒体更全的版本
    丢掉另一份响应里的正文。这里先按媒体完整度选主记录，再从另一份补齐正文、
    时间与链接。
    """
    def rank(post: Post) -> tuple[int, bool, bool, bool, bool]:
        return (len(post.media), post.media_complete, bool(post.text),
                bool(post.created_at), bool(post.permalink))

    winner, other = ((candidate, current)
                     if rank(candidate) > rank(current)
                     else (current, candidate))
    for attr in ("text", "created_at", "permalink"):
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

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
    """判定必须看**值**，不是看键在不在。

    2026-08-30 实测：轮播帖的 `carousel_media` 子项里 `code` 这个**键确实存在，
    但值是 None**，`pk` 和 `taken_at` 也都在。旧判定写的是 `"code" in d`，
    于是 478 个子项全部命中，每个都被造成一篇独立的、没有正文的"帖子"——
    一篇 3 图轮播被记成 1 篇父帖 + 3 篇空帖。

    `product_type != "carousel_item"` 是第二道闸：值判空是根因修复，
    这一条是针对已知形态的显式拦截。两条都要——万一哪天子项开始带 code 了，
    第二条还能挡住。
    """
    return (bool(d.get("pk")) and bool(d.get("code")) and bool(d.get("taken_at"))
            and d.get("product_type") != "carousel_item")


def from_iphone_struct(item: dict, account: str, route: str) -> Post:
    code = item.get("code", "")
    caption = (item.get("caption") or {}).get("text", "") or ""
    # 实测 1022 个有效节点里 1022 个都用 user.username，owner.username 一个没有；
    # 但两个都收着，成本为零，端点换形态时不至于整批丢归属。
    ig_user = item.get("user") or item.get("owner") or {}
    owner = (ig_user.get("username") or "").strip().lower() or None if isinstance(ig_user, dict) else None
    owner_name = ig_user.get("full_name") if isinstance(ig_user, dict) else None

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
        owner=owner, owner_name=owner_name,
    )


# --------------------------------------------------------------------------
# 形态 2：GraphQL timeline node（登出 web_profile_info）
# --------------------------------------------------------------------------

def is_graphql_node(d: dict) -> bool:
    # 同 is_iphone_struct：判值不判键。这里没有真实数据证实过子项会误命中，
    # 但 iphone_struct 那边已经付过一次代价了，同一类判定不该留两种写法。
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
        owner=owner, owner_name=owner_name,
    )


# --------------------------------------------------------------------------
# 形态 3：Facebook story node
# --------------------------------------------------------------------------

def is_fb_story(d: dict) -> bool:
    return bool(d.get("post_id")) and ("message" in d or "attachments" in d)


FB_WATCH = "https://www.facebook.com/watch/?v="


def _fb_slug(url: str | None) -> str | None:
    """从 actors[0].url 里取出账号名段，归一化成可与 config 比对的形式。

    实测两种形态：
        https://www.facebook.com/neakasaofficial       -> "neakasaofficial"
        https://www.facebook.com/profile.php?id=1000…  -> "id:1000…"
    后者是没有自定义用户名的主页，取不到 slug，用数字 ID 兜底并加前缀，
    免得哪天真有个账号就叫 "profile.php" 时撞上。
    """
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
    """归属。FB 把它放在 actors[0]，形如
    `{"__typename": "User", "id": "615…", "name": "Neakasa Official",
      "url": "https://www.facebook.com/neakasaofficial"}`。

    ⚠️ **`name` 是展示名，与 URL 里的账号名不相等**（"Neakasa Official"
    vs "neakasaofficial"），所以判等必须用 url 里的 slug，不能用 name。
    这一点和 Instagram 不同——IG 的 username 与 config 里的值直接相等。
    """
    actors = node.get("actors")
    if not (isinstance(actors, list) and actors and isinstance(actors[0], dict)):
        return None, None
    a = actors[0]
    slug = _fb_slug(a.get("url"))
    if slug is None and a.get("id"):
        slug = "id:" + str(a["id"])
    return slug, a.get("name")


def _fb_videos(node: dict) -> list[Media]:
    """视频附件。**只记元数据不下载**（计划固化的边界）。

    2026-08-30 之前这个函数不存在，`from_fb_story` 只抽图片，于是 47 篇里
    20 篇视频帖得到 `media=[]` → `media_complete=False`，被当成"图片抓取失败"，
    永久挂在 `Archive.needs_media()` 的待补清单上、每次回填都重试一遍。

    取的是 `attachments[i].media` 而不是对整棵 attachments 子树做 walk：
    实测有 1 篇图文帖的 attachments 深处也埋着 Video 节点（推荐位之类），
    walk 会把它当成这篇帖子的视频。`attachments[i].media` 才是"这篇帖子附的东西"。

    ⚠️ 我们捕获到的这版响应里，`media` 只给了 `{__typename, id}`，
    没有 `progressive_url`（它挂在更深的、没有 __typename 的渲染器节点上，
    路径不稳定）。所以 URL 是**用 video id 拼出来的 watch 链接**，
    不是响应里给的。因为视频从不下载，拼错的成本仅限于这条链接点不开。
    """
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
        # 解析层一律给 True：它表达的是"响应给了什么我们就收了什么"。
        # 旧写法 `bool(media)` 把**纯文字帖**和**视频帖**都判成不完整，
        # 而它们本来就没有图片可下。真正的 False 由下载环节在失败时写入
        # （见 CR-04），职责不该混在这里。
        media_complete=True,
        owner=owner, owner_name=owner_name,
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
    # owner 也要补：同一帖的多份响应里，往往只有一份带 actors/user，
    # 漏补的话这篇会因为"归属未知"被 partition_by_owner 丢掉 —— 丢的是真帖子。
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


def partition_by_owner(posts: list[Post], account: str) -> tuple[list[Post], list[dict]]:
    """按归属把解析结果切成「本账号的」和「要丢弃的」两份。

    **为什么是独立函数而不是塞进 extract()**：丢弃是一个业务判断，
    不是解析判断。把它显式摆在调用点上，调用方就不可能"忘了处理被丢的那批"——
    返回值里就摆着，签名逼你接住。

    **为什么必须丢**：`extract()` 走 `walk()` 全树搜索，抗字段路径漂移，
    但代价是任何"看起来像帖子"的节点都会被捞进来。人工滚动时页面会加载推荐内容
    和被 @ 的 UGC。2026-08-30 实测，一次 Instagram 回填混进 266 条来自另外
    195 个账号的帖子。这些帖子若流到下游，`translate.py` 会翻译他人文案、
    发布环节会把第三方 UGC 当自家内容发到 DE Page —— 这是法务风险，不只是数据脏。

    **归属未知的一律丢弃**（`owner is None`）。宁可漏一篇自家的，
    不可混进一篇别人的：漏的那篇下次回填还能补回来，
    发出去的那篇收不回来。丢弃原因会区分记录，便于事后判断是不是丢多了。

    返回 `(kept, rejected)`。`rejected` 的每一项是可直接写进 `_rejected.jsonl`
    的 dict，**必须落盘**——静默丢数据正是本项目最忌讳的事。
    """
    target = (account or "").strip().lower()
    kept: list[Post] = []
    rejected: list[dict] = []
    for post in posts:
        if post.owner == target:
            kept.append(post)
            continue
        rejected.append({
            "post_id": post.post_id,
            "platform": post.platform,
            "owner": post.owner,
            "owner_name": post.owner_name,
            "created_at": post.created_at,
            "permalink": post.permalink,
            "text_head": (post.text or "")[:80],
            "reason": "owner_unknown" if post.owner is None else "owner_mismatch",
            "expected_owner": target,
        })
    return kept, rejected

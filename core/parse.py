"""将 IG iphone_struct、GraphQL media 与 FB story 归一为 Post；缺轮播子项时标记媒体不全。"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit

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


def _media_count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


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
    # XDTMediaDict 单图也带 carousel_media: null/[]；字段存在不代表轮播。
    # 非空子项或异常值仍须匹配轮播类型，不能把矛盾响应放行为完整单图。
    carousel = item.get('media_type') == 8 or item.get('carousel_media') not in (None, [])
    declared = _media_count(item.get('carousel_media_count'))
    children = item.get("carousel_media") or [item]
    media_complete = (item.get('media_type') in (1, 2) if not carousel
                      else item.get('media_type') == 8 and bool(item.get('carousel_media')))
    if not isinstance(children, list):
        children = [item]
        media_complete = False
    for child in children:
        if not isinstance(child, dict):
            media_complete = False
            continue
        versions = child.get("video_versions") or []
        has_video = bool(versions) or child.get('media_type') == 2 or child.get('product_type') in {'clips', 'igtv'}
        video = (next((v for v in versions
                       if isinstance(v, dict) and v.get("url")), None)
                 if isinstance(versions, list) else None)
        if has_video:
            if video:
                # 视频不下载，但记录存在，否则连续性检查会误报缺口
                media.append(Media(url=video["url"], kind="video",
                                   width=video.get("width"), height=video.get("height"),
                                   source_media_id=str(child.get('pk') or child.get('id') or '') or None))
            else:
                # 已明确是视频却缺视频 URL，不能退回缩略图并伪装成完整图片项。
                media_complete = False
                media.append(Media(url=f'{IG}/p/{code}/' if code else '', kind='video',
                                   source_media_id=str(child.get('pk') or child.get('id') or '') or None))
            continue
        img = best_image(child)
        if img:
            media.append(Media(url=img["url"], kind="image",
                               width=img.get("width"), height=img.get("height"),
                               source_media_id=str(child.get('pk') or child.get('id') or '') or None))
        else:
            # 局部结构异常保留父帖并标残缺，供后续补齐。
            media_complete = False

    if declared is not None and declared != len(media):
        media_complete = False
    return Post(
        post_id=str(item.get("pk") or item.get("id") or code),
        platform="instagram", account=account,
        text=caption, created_at=iso(item.get("taken_at")),
        permalink=f"{IG}/p/{code}/" if code else None,
        media=media, source_route=route, media_complete=media_complete,
        source_media_complete=media_complete,
        source_media_count=declared if declared is not None else (len(media) if media_complete else None),
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

    media: list[Media] = []
    container = node.get('edge_sidecar_to_children') or {}
    children = container.get('edges') or []
    complete = typename in ('GraphImage', 'GraphVideo') if not is_carousel else bool(children)
    if not isinstance(children, list):
        children, complete = [], False
    # 有子项时封面不是额外一张图；缺子项时保留可获得封面并明确不完整。
    nodes = [(edge.get('node') or {}) for edge in children if isinstance(edge, dict)] if children else [node]
    if len(nodes) != len(children) and children:
        complete = False
    for child in nodes:
        video = bool(child.get('is_video')) or child.get('__typename') == 'GraphVideo'
        url = (child.get('video_url') or child.get('display_url')) if video else child.get('display_url')
        if not url:
            complete = False
            if not video:
                continue
            url = f'{IG}/p/{code}/' if code else ''
        dimensions = child.get('dimensions') or {}
        media.append(Media(url=url, kind='video' if video else 'image',
            width=dimensions.get('width'), height=dimensions.get('height'),
            source_media_id=str(child.get('id') or '') or None))
    declared = _media_count(container.get('count'))
    if ((declared is not None and declared != len(media))
            or (container.get('page_info') or {}).get('has_next_page')):
        complete = False

    return Post(
        post_id=str(node.get("id") or code),
        platform="instagram", account=account,
        text=text, created_at=iso(node.get("taken_at_timestamp")),
        permalink=f"{IG}/p/{code}/" if code else None,
        media=media, source_route=route, media_complete=complete,
        source_media_complete=complete,
        source_media_count=declared if declared is not None else (len(media) if complete else None),
        owner=owner, owner_name=owner_name, coauthors=ig_coauthors(node),
    )


# Facebook story

def is_fb_story(d: dict) -> bool:
    return bool(d.get("post_id")) and ("message" in d or "attachments" in d)


FB_WATCH = "https://www.facebook.com/watch/?v="


def _fb_slug(url: str | None, *, message_action: bool = False) -> str | None:
    """只接受 Facebook 主页 URL；查询串不是用户名，帖子链接不是作者证据。"""
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme not in {'http', 'https'}
            or parsed.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com'}
            or parsed.username or parsed.password):
        return None
    tail = parsed.path.strip('/')
    if message_action:
        parts = tail.split('/')
        if len(parts) != 3 or parts[:2] != ['messages', 't']:
            return None
        tail = parts[2]
    if tail == 'profile.php':
        ids = parse_qs(parsed.query).get('id', [])
        return 'id:' + ids[0] if len(ids) == 1 and re.fullmatch(r'[0-9]+', ids[0]) else None
    if not re.fullmatch(r'[A-Za-z0-9_.]+', tail) or tail.lower() in {
            'watch', 'reel', 'reels', 'posts', 'photo.php', 'photos', 'groups', 'pages', 'share', 'login.php'}:
        return None
    return 'id:' + tail if tail.isdigit() else tail.lower()


def _fb_actor(node: dict) -> dict:
    """只取主作者。⚠️ 其它 actors 不是已接受合作者，不能改回从这里取 coauthors。"""
    actors = node.get("actors")
    if not (isinstance(actors, list) and actors and isinstance(actors[0], dict)):
        return {}
    return actors[0]


def _actor_dicts(value) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get('nodes'), list):
        return [item for item in value['nodes'] if isinstance(item, dict)]
    return []


def fb_collaborator_evidence(node: dict) -> list[dict[str, str]]:
    """已接受的 Facebook 合作者，只留 ID 与主页 URL。

    直接的 `collaborators`，以及 Comet 标题路径
    `comet_sections.context_layout.story.comet_sections.title.story.collaborators`。
    显示名、其它 actors、提及和邀请都不算。
    """
    if not isinstance(node, dict):
        return []
    raw = _actor_dicts(node.get('collaborators'))
    titled = node.get('comet_sections')
    for key in ('context_layout', 'story', 'comet_sections', 'title', 'story', 'collaborators'):
        titled = titled.get(key) if isinstance(titled, dict) else None
    for item in _actor_dicts(titled):
        if item not in raw:
            raw.append(item)
    evidence: list[dict[str, str]] = []
    for item in raw:
        found = _fb_evidence(item)
        if found and found not in evidence:
            evidence.append(found)
    return evidence


def _fb_resolved_label(item: dict, index: dict[str, list[dict[str, str]]]) -> str | None:
    """把一个合作者证据归一成账号名；多个名字或只有显示名时不猜。"""
    roots = _fb_identity_tokens(item)
    if not roots:
        return None
    pending = [min(roots)]
    reached: set[str] = set()
    while pending:
        token = pending.pop()
        if token in reached:
            continue
        reached.add(token)
        for linked in index.get(token, []):
            pending.extend(_fb_identity_tokens(linked) - reached)
    if not roots.issubset(reached):
        return None
    ids = {token for token in reached if token.startswith('id:')}
    names = reached - ids
    if len(names) == 1:
        return min(names)
    if names or len(ids) != 1:
        return None
    return min(ids)


def _resolve_fb_coauthors(post: Post, index=None) -> Post:
    if not post.coauthor_evidence:
        return post
    index = _fb_identity_index(post.coauthor_evidence) if index is None else index
    names: list[str] = []
    for item in post.coauthor_evidence:
        label = _fb_resolved_label(item, index)
        if label and label != post.owner and label not in names:
            names.append(label)
    post.coauthors = names
    return post


def _fb_evidence(actor: dict) -> dict[str, str]:
    return {key: str(actor[key]) for key in ('id', 'url')
            if isinstance(actor.get(key), (str, int)) and actor[key]}


def _fb_identity_tokens(evidence: dict[str, str]) -> set[str]:
    tokens = set()
    identifier = evidence.get('id', '')
    if re.fullmatch(r'[0-9]+', identifier):
        tokens.add('id:' + identifier)
    slug = _fb_slug(evidence.get('url'), message_action=evidence.get('source') == 'profile_message')
    if slug:
        tokens.add(slug)
    return tokens


def _fb_identity_index(evidence: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    for item in evidence:
        for token in _fb_identity_tokens(item):
            bucket = index.setdefault(token, [])
            if item not in bucket:
                bucket.append(item)
    return index


def _resolve_fb_owner(post: Post, index=None) -> Post:
    """沿同一作者对象的 ID/URL 关联取规范名；分离或冲突的证据不能互相覆盖。"""
    evidence = list(post.owner_evidence)
    index = _fb_identity_index(evidence) if index is None else index
    roots = set().union(*(_fb_identity_tokens(item) for item in evidence))
    pending = [min(roots)] if roots else []
    reached: set[str] = set()
    while pending:
        token = pending.pop()
        if token in reached:
            continue
        reached.add(token)
        for item in index.get(token, []):
            if item not in evidence:
                evidence.append(item)
            pending.extend(_fb_identity_tokens(item) - reached)
    ids = {token for token in reached if token.startswith('id:')}
    names = reached - ids
    post.owner_conflict = not roots.issubset(reached) or len(ids) > 1 or len(names) > 1
    post.owner = None if post.owner_conflict else next(iter(names or ids), None)
    post.owner_evidence = sorted(evidence, key=lambda item: (item.get('id', ''), item.get('url', '')))
    return post


def _fb_media(node: dict) -> tuple[list[Media], bool]:
    """按附件顺序识别媒体；只在同一个媒体内部选择最大图像尺寸。"""
    out: list[Media] = []
    seen: set[str] = set()
    complete = True

    def collect(container) -> None:
        nonlocal complete
        if not isinstance(container, list):
            complete = False
            return
        for att in container:
            if not isinstance(att, dict):
                complete = False
                continue
            style = att.get('styles')
            if (isinstance(style, dict) and style.get('__typename') in {
                    'StoryAttachmentPhotoStyleRenderer', 'StoryAttachmentAlbumStyleRenderer'}
                    and isinstance(style.get('attachment'), dict)):
                # Comet 外层 media 只是 ID 占位；实际图片/有序相册在 renderer 内。
                # 相册外层的封面不能另算一张，单图外层空 subattachments 也不表示缺图。
                att = style['attachment']
            sub = att.get('all_subattachments') or att.get('subattachments')
            children = None
            if isinstance(sub, dict):
                children = sub.get('nodes') or sub.get('data') or []
                count = _media_count(sub.get('count'))
                if not children or (count is not None and count != len(children)) or (sub.get('page_info') or {}).get('has_next_page'):
                    complete = False
            m = att.get("media")
            if not isinstance(m, dict):
                if children is not None:
                    collect(children)
                    continue
                # 无媒体身份的图片片段仍保留素材，但不能据此证明原帖列表已给齐。
                complete = False
                if isinstance(att.get('image'), dict):
                    m = {'image': att['image']}
                else:
                    continue
            if m.get('__typename') not in ('Photo', 'Video'):
                complete = False
            identifier = str(m.get('id') or '') or None
            if m.get('__typename') == 'Video':
                key = 'video:' + (identifier or str(len(out)))
                if key not in seen:
                    seen.add(key)
                    out.append(Media(url=FB_WATCH + identifier if identifier else '', kind='video', source_media_id=identifier))
                if children is not None:
                    collect(children)
                continue
            images = list(walk(m, lambda value: isinstance(value.get('uri'), str) and 'width' in value and 'height' in value))
            if not images:
                if (att.get('styles') or {}).get('__typename') != 'StoryAttachmentProfileMediaStyleRenderer':
                    complete = False
                if children is not None:
                    collect(children)
                continue
            image = max(images, key=lambda value: (value.get('width') or 0) * (value.get('height') or 0))
            key = 'image:' + (identifier or image['uri'])
            if key not in seen:
                seen.add(key)
                out.append(Media(url=image['uri'], kind='image', width=image.get('width'),
                                 height=image.get('height'), source_media_id=identifier))
            if children is not None:
                collect(children)

    # 缺附件字段只是响应片段；不能当成明确的空列表，否则会压掉别段的图片。
    collect(node.get('attachments'))
    return out, complete


def from_fb_story(node: dict, account: str, route: str) -> Post:
    msg = node.get("message")
    text = msg.get("text", "") if isinstance(msg, dict) else (msg or "")

    media, complete = _fb_media(node)

    actor = _fb_actor(node)
    evidence = _fb_evidence(actor)
    ts = node.get("creation_time") or node.get("created_time")
    post = _resolve_fb_owner(Post(
        post_id=str(node["post_id"]), platform="facebook", account=account,
        text=text, created_at=iso(ts) if isinstance(ts, (int, float)) else (ts or ""),
        permalink=node.get("url") or node.get("permalink_url"),
        media=media, source_route=route,
        # 纯文字或视频不等于媒体残缺；下载失败由下载层标记。
        media_complete=complete, source_media_complete=complete,
        source_media_count=len(media) if complete else None,
        owner_name=actor.get('name'), owner_evidence=[evidence] if evidence else [],
        coauthor_evidence=fb_collaborator_evidence(node),
    ))
    return _resolve_fb_coauthors(post)


def merge_post(current: Post, candidate: Post) -> Post:
    """合并同帖响应：FB 已知媒体优先于空片段，再按完整性选取并补齐其它字段。"""
    def rank(post: Post) -> tuple[bool, bool, int, bool, bool, bool]:
        return (bool(post.media) if post.platform == 'facebook' else True,
                bool(post.source_media_complete), len(post.media), bool(post.text),
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
    if winner.platform == 'facebook' and (winner.owner_evidence or other.owner_evidence):
        winner.owner_evidence = winner.owner_evidence + [
            item for item in other.owner_evidence if item not in winner.owner_evidence]
        _resolve_fb_owner(winner)
    if winner.platform == 'facebook' and other.coauthor_evidence:
        winner.coauthor_evidence = list(winner.coauthor_evidence)
        winner.coauthor_evidence.extend(
            item for item in other.coauthor_evidence if item not in winner.coauthor_evidence)
        _resolve_fb_coauthors(winner)
    if (winner.source_media_count is None and other.source_media_count is not None
            and (winner.platform != 'facebook' or other.source_media_count >= len(winner.media))):
        winner.source_media_count = other.source_media_count
        if winner.source_media_count != len(winner.media):
            winner.source_media_complete = winner.media_complete = False
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
                out[post.post_id] = post if prev is None else merge_post(prev, post)
    if platform == 'facebook':
        evidence = [item for post in out.values() for item in [*post.owner_evidence, *post.coauthor_evidence]]
        # 主页对象/消息动作显式绑定账号 ID；不能从任意链接或帖子 permalink 猜作者。
        for payload in payloads:
            for actor in walk(payload, lambda node: node.get('__typename') in ('Page', 'User', 'ProfileActionMessage')):
                if actor.get('__typename') == 'ProfileActionMessage':
                    owner = actor.get('profile_owner')
                    if isinstance(owner, dict):
                        item = _fb_evidence({'id': owner.get('id'), 'url': actor.get('uri')})
                        if item.get('id') and item.get('url'):
                            evidence.append(dict(item, source='profile_message'))
                else:
                    evidence.append(_fb_evidence(actor))
        index = _fb_identity_index(evidence)
        for post in out.values():
            _resolve_fb_owner(post, index)
            _resolve_fb_coauthors(post, index)
    return list(out.values())


def on_timeline_of(post: Post, target: str) -> bool:
    """目标为 owner 或已接受的 coauthor 即属于该时间线；账号名忽略大小写。"""
    who = (target or "").strip().lower()
    return not post.owner_conflict and (post.owner == who or who in (post.coauthors or []))


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
            "owner_evidence": post.owner_evidence,
            "coauthors": post.coauthors,
            "created_at": post.created_at,
            "permalink": post.permalink,
            "text_head": (post.text or "")[:80],
            "reason": ("owner_conflict" if post.owner_conflict else
                       "owner_unknown" if post.owner is None else "owner_mismatch"),
            "expected_owner": target,
        })
    return kept, rejected

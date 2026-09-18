"""解析层自测。用贴近真实响应结构的样本，验证三种形态都能正确收敛。"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
from core.console import force_utf8   # noqa: E402

force_utf8()   # 输出被重定向到文件/管道时，cp936 编不出 ß/⚠ 会让整套测试崩掉
from core.parse import extract

# --- 形态 2：登出 web_profile_info 的 timeline node ---
graphql_single = {
    "id": "3001", "shortcode": "AAA", "__typename": "GraphImage",
    "display_url": "https://cdn/aaa_1080.jpg", "is_video": False,
    "taken_at_timestamp": 1756000000,
    "dimensions": {"width": 1080, "height": 1350},
    "edge_media_to_caption": {"edges": [{"node": {"text": "Free shipping on all orders"}}]},
}
graphql_carousel_coveronly = {   # 轮播帖，端点只给封面 —— 应标 incomplete
    "id": "3002", "shortcode": "BBB", "__typename": "GraphSidecar",
    "display_url": "https://cdn/bbb_cover.jpg", "is_video": False,
    "taken_at_timestamp": 1756100000,
    "dimensions": {"width": 1080, "height": 1080},
    "edge_media_to_caption": {"edges": [{"node": {"text": "New drop"}}]},
}
graphql_video = {
    "id": "3003", "shortcode": "CCC", "__typename": "GraphVideo",
    "display_url": "https://cdn/ccc_thumb.jpg", "is_video": True,
    "taken_at_timestamp": 1756200000,
    "dimensions": {"width": 1080, "height": 1920},
    "edge_media_to_caption": {"edges": []},      # 无文案
}
web_profile_info = {"data": {"user": {"id": "999", "username": "acme",
    "edge_owner_to_timeline_media": {"edges": [
        {"node": graphql_single}, {"node": graphql_carousel_coveronly},
        {"node": graphql_video}]}}}}

# --- 形态 1：回填拦截到的 iphone_struct，同一批里也有 3002（轮播，这次字段全） ---
iphone_carousel = {
    "pk": "3002", "code": "BBB", "taken_at": 1756100000, "media_type": 8,
    "caption": {"text": "New drop"},
    "carousel_media": [
        {"image_versions2": {"candidates": [
            {"url": "https://cdn/bbb_1.jpg", "width": 1080, "height": 1080},
            {"url": "https://cdn/bbb_1_small.jpg", "width": 320, "height": 320}]}},
        {"image_versions2": {"candidates": [
            {"url": "https://cdn/bbb_2.jpg", "width": 1080, "height": 1080}]}},
        {"video_versions": [{"url": "https://cdn/bbb_3.mp4", "width": 720, "height": 1280}]},
    ],
}
feed_resp = {"items": [iphone_carousel], "more_available": False}

# --- 同帖多响应的字段互补：媒体数相同但一份正文为空；媒体更多的一份正文为空 ---
graphql_empty_caption = {
    "id": "4001", "shortcode": "DDD", "__typename": "GraphImage",
    "display_url": "https://cdn/ddd.jpg", "taken_at_timestamp": 1756201000,
    "dimensions": {"width": 1080, "height": 1080},
    "edge_media_to_caption": {"edges": []},
}
iphone_with_caption = {
    "pk": "4001", "code": "DDD", "taken_at": 1756201000, "media_type": 1,
    "caption": {"text": "Caption recovered from the richer response"},
    "image_versions2": {"candidates": [
        {"url": "https://cdn/ddd.jpg", "width": 1080, "height": 1080}]},
}
graphql_caption_cover = {
    "id": "4002", "shortcode": "EEE", "__typename": "GraphSidecar",
    "display_url": "https://cdn/eee_cover.jpg", "taken_at_timestamp": 1756202000,
    "dimensions": {"width": 1080, "height": 1080},
    "edge_media_to_caption": {"edges": [{"node": {"text": "Keep this caption"}}]},
}
iphone_more_media_no_caption = {
    "pk": "4002", "code": "EEE", "taken_at": 1756202000, "caption": None, "media_type": 8,
    "carousel_media": [
        {"image_versions2": {"candidates": [
            {"url": "https://cdn/eee_1.jpg", "width": 1080, "height": 1080}]}},
        {"image_versions2": {"candidates": [
            {"url": "https://cdn/eee_2.jpg", "width": 1080, "height": 1080}]}},
    ],
}

# --- 形态 3：FB story，含同图多尺寸变体 ---
fb_resp = {"data": {"node": {"timeline_list_feed_units": {"edges": [{"node": {
    "post_id": "pfbid123", "creation_time": 1756300000,
    "message": {"text": "Summer sale starts now"},
    "url": "https://www.facebook.com/acme/posts/pfbid123",
    "attachments": [{"media": {
        "image": {"uri": "https://scontent/x_720.jpg", "width": 720, "height": 720},
        "photo_image": {"uri": "https://scontent/x_2048.jpg", "width": 2048, "height": 2048},
    }}],
}}]}}}}

fails = []
def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

print("[1] 登出 GraphQL 形态")
posts = {p.post_id: p for p in extract([web_profile_info], "instagram", "acme", "delta")}
check(len(posts) == 3, f"抽出 3 篇，实得 {len(posts)}")
check(posts["3001"].text == "Free shipping on all orders", "单图帖正文正确")
check(posts["3001"].media_complete is True, "单图帖 media_complete=True")
check(posts["3002"].media_complete is False, "轮播帖只有封面 -> media_complete=False")
check(posts["3003"].media[0].kind == "video", "视频帖 kind=video")
check(posts["3003"].text == "", "无文案帖不报错，text 为空")
check(posts["3001"].permalink == "https://www.instagram.com/p/AAA/", "permalink 正确")

print("\n[2] iphone_struct 形态（回填）")
posts2 = {p.post_id: p for p in extract([feed_resp], "instagram", "acme", "backfill")}
p = posts2["3002"]
check(len(p.media) == 3, f"轮播 3 个子项，实得 {len(p.media)}")
check(p.media[0].url.endswith("bbb_1.jpg"), "取 candidates[0] 即最大尺寸，非缩略图")
check(p.media[2].kind == "video", "轮播里的视频子项识别为 video")
check(p.media_complete is True, "回填形态 media_complete=True")

iphone_partial_carousel = {
    "pk": "3004", "code": "BADCHILD", "taken_at": 1756100100,
    "caption": {"text": "Keep the parent even if one child drifts"},
    "carousel_media": [
        {"image_versions2": {"candidates": [
            {"url": "https://cdn/valid.jpg", "width": 1080, "height": 1080}]}},
        {"video_versions": [{}], "image_versions2": {"candidates": [
            {"url": "https://cdn/video-thumbnail.jpg", "width": 1080, "height": 1080}]}},
    ],
}
partial = extract([iphone_partial_carousel], "instagram", "acme", "backfill")
check(len(partial) == 1 and partial[0].post_id == "3004",
      "一个轮播子项字段漂移时保留父帖，不让 extract 静默丢整篇")
check(len(partial[0].media) == 2 and partial[0].media[0].url.endswith("valid.jpg")
      and partial[0].media[1].kind == 'video',
      "字段正常的兄弟子项保留，缺 URL 的视频不被缩略图伪装成图片")
check(partial[0].media_complete is False,
      "无法解析的子项把父帖标为媒体残缺，供下次回填重试")

print("\n[3] 同一帖跨形态合并（增量先见封面，回填后见全量）")
merged = {x.post_id: x for x in extract(
    [web_profile_info, feed_resp], "instagram", "acme", "mixed")}
check(len(merged["3002"].media) == 3, f"保留媒体更全的那份，实得 {len(merged['3002'].media)}")

merged_fields = {x.post_id: x for x in extract(
    [graphql_empty_caption, iphone_with_caption,
     graphql_caption_cover, iphone_more_media_no_caption],
    "instagram", "acme", "mixed")}
check(merged_fields["4001"].text == "Caption recovered from the richer response",
      "媒体数相同时不会让先到的空正文压掉后到正文")
check(len(merged_fields["4002"].media) == 2 and
      merged_fields["4002"].text == "Keep this caption",
      "选择媒体更多的响应后，会从另一份响应补回正文")

print("\n[4] Facebook story 形态")
fb = extract([fb_resp], "facebook", "acme", "backfill")
check(len(fb) == 1, f"抽出 1 篇，实得 {len(fb)}")
check(fb[0].text == "Summer sale starts now", "正文正确")
check(fb[0].media[0].width == 2048, f"多尺寸变体取最大，实得 {fb[0].media[0].width}")
check(fb[0].created_at.startswith("2025-") or fb[0].created_at.startswith("2026-"),
      f"时间戳转 ISO：{fb[0].created_at}")

print("\n[5] 脏输入不崩")
junk = [{"a": [1, 2, {"b": None}]}, {"shortcode": "X"}, [], {"post_id": "y"}]
try:
    extract(junk, "instagram", "acme", "t"); extract(junk, "facebook", "acme", "t")
    check(True, "缺字段/异常结构被跳过而非抛异常")
except Exception as e:
    check(False, f"崩了: {e}")

# 脱敏响应结构回归样本。
from core.parse import _fb_slug, on_timeline_of, partition_by_owner   # noqa: E402

print("\n[真实结构 1] 轮播子项不得成为独立帖子")
# 轮播子项保留 code=None 及 carousel_item，避免夹具过度简化。
ig_carousel_real = {
    "pk": "3712000000000000001", "code": "DXpArent", "taken_at": 1756300000,
    "media_type": 8, "product_type": "carousel_container",
    "user": {"username": "neakasa.tech", "full_name": "Neakasa"},
    "caption": {"text": "Three ways to keep the litter box fresh"},
    "carousel_media": [
        {"pk": "3712000000000000002", "code": None, "taken_at": 1756300000,
         "product_type": "carousel_item",
         "image_versions2": {"candidates": [{"url": "https://cdn/c1.jpg",
                                             "width": 1080, "height": 1080}]}},
        {"pk": "3712000000000000003", "code": None, "taken_at": 1756300000,
         "product_type": "carousel_item",
         "image_versions2": {"candidates": [{"url": "https://cdn/c2.jpg",
                                             "width": 1080, "height": 1080}]}},
        {"pk": "3712000000000000004", "code": None, "taken_at": 1756300000,
         "product_type": "carousel_item",
         "video_versions": [{"url": "https://cdn/c3.mp4", "width": 720, "height": 1280}]},
    ],
}
posts = extract([ig_carousel_real], "instagram", "neakasa.tech", route="backfill")
check(len(posts) == 1, "一篇 3 项的轮播只产出 1 篇帖子（旧代码产出 4 篇）")
check(posts[0].post_id == "3712000000000000001", "留下的是父帖不是子项")
check(len(posts[0].media) == 3, "父帖收全了 3 个子项的媒体")
check(sum(1 for m in posts[0].media if m.kind == "video") == 1, "子项里的视频被标成 video")
check(posts[0].text.startswith("Three ways"), "正文来自父帖的 caption")
check(posts[0].owner == "neakasa.tech", "归属取自 user.username")

print("\n[真实结构 2] Facebook 归属：name 与 URL 里的账号名并不相等")
def fb_story(pid, actor_name, actor_url, attachments, text="hi", ts=1756400000):
    actor_id = "61591381265280" if actor_name == "Neakasa Official" else "90000000000001"
    return {"post_id": pid, "creation_time": ts,
            "message": {"text": text},
            "actors": [{"__typename": "User", "id": actor_id,
                        "name": actor_name, "url": actor_url}],
            "attachments": attachments,
            "url": "https://www.facebook.com/neakasaofficial/posts/%s" % pid}

photo_att = [{"__typename": "StoryAttachment",
              "media": {"__typename": "Photo", "id": "p1",
                        "image": {"uri": "https://cdn/fb1.jpg",
                                  "width": 1440, "height": 1440}},
              "all_subattachments": None}]
p = extract([fb_story("122100000000000001", "Neakasa Official",
                      "https://www.facebook.com/neakasaofficial", photo_att)],
            "facebook", "neakasaofficial", route="backfill")[0]
check(p.owner == "neakasaofficial", "owner 归一化成 URL 里的账号名段")
check(p.owner_name == "Neakasa Official", "展示名单独留着，给人看")
check(p.owner != p.owner_name, "两者确实不相等 —— 所以判等不能用 name")
check(len(p.media) == 1 and p.media[0].kind == "image", "图片照常抽出")

check(_fb_slug("https://www.facebook.com/neakasaofficial") == "neakasaofficial",
      "_fb_slug 取普通主页的账号名")
check(_fb_slug("https://www.facebook.com/neakasaofficial/") == "neakasaofficial",
      "_fb_slug 容忍结尾的斜杠")
check(_fb_slug("https://www.facebook.com/profile.php?id=100064709675058")
      == "id:100064709675058", "没有自定义用户名的主页退回数字 ID")
check(_fb_slug(None) is None and _fb_slug("") is None, "空 URL 不崩")

print("\n[真实结构 3] Facebook 视频帖必须被认成视频，而不是抓取失败")
video_att = [{"__typename": "StoryAttachment",
              "media": {"__typename": "Video", "__isNode": "Video",
                        "id": "2895895450769921"},
              "all_subattachments": None}]
v = extract([fb_story("122100000000000002", "Neakasa Official",
                      "https://www.facebook.com/neakasaofficial", video_att,
                      text="Quiet protection. Real efficiency.")],
            "facebook", "neakasaofficial", route="backfill")[0]
check(len(v.media) == 1 and v.media[0].kind == "video", "视频附件被记成一条 video 媒体")
check(v.media[0].url.endswith("2895895450769921"), "URL 由 video id 拼出，可定位")
check(v.media_complete is True,
      "视频帖是完整的 —— 旧代码判 False，让它永久挂在待补清单上")

# 真实形态：相册帖，attachments[0].all_subattachments.nodes 里混着图和视频
album_att = [{"__typename": "StoryAttachment",
              "media": {"__typename": "Photo", "id": "p9",
                        "image": {"uri": "https://cdn/a1.jpg",
                                  "width": 1440, "height": 1440}},
              "all_subattachments": {"nodes": [
                  {"media": {"__typename": "Photo", "id": "p10",
                             "image": {"uri": "https://cdn/a2.jpg",
                                       "width": 1440, "height": 1440}}},
                  {"media": {"__typename": "Video", "id": "778899"}},
              ]}}]
a = extract([fb_story("122100000000000003", "Neakasa Official",
                      "https://www.facebook.com/neakasaofficial", album_att,
                      text="Production Update")],
            "facebook", "neakasaofficial", route="backfill")[0]
check(sum(1 for m in a.media if m.kind == "image") == 2, "相册里两张图都抽到")
check(sum(1 for m in a.media if m.kind == "video") == 1,
      "相册里夹带的视频也记下来（真实数据里有这么一篇 9图+1视频）")

# 真实形态：头像/封面更新帖 —— Photo 只有 id，没有 uri，正文为空
avatar_att = [{"__typename": "StoryAttachment",
               "media": {"__typename": "Photo", "__isNode": "Photo",
                         "id": "122098419195379375"},
               "styles": {"__typename": "StoryAttachmentProfileMediaStyleRenderer"}}]
av = extract([fb_story("122098419195379375", "Neakasa Official",
                       "https://www.facebook.com/neakasaofficial", avatar_att, text="")],
             "facebook", "neakasaofficial", route="backfill")[0]
check(av.media == [], "头像更新帖抽不到媒体（响应里本来就没有 uri）")
check(av.media_complete is True,
      "但它是完整的 —— 没有图可下不等于下载失败，否则会永远重试")

print("\n[真实结构 4] 归属过滤：混进来的别人的帖子必须被拦下")
mixed = extract([
    fb_story("122100000000000004", "Neakasa Official",
             "https://www.facebook.com/neakasaofficial", photo_att),
    fb_story("1514468247386817", "The Garden State Cat Club",
             "https://www.facebook.com/thegardenstatecatclub", photo_att,
             text="THE WINNERS OF THE NEAKASA M1"),
], "facebook", "neakasaofficial", route="backfill")
kept, rejected = partition_by_owner(mixed, "neakasaofficial")
check(len(kept) == 1 and kept[0].post_id == "122100000000000004", "只留本账号的")
check(len(rejected) == 1, "另一条被丢弃")
check(rejected[0]["owner"] == "thegardenstatecatclub"
      and rejected[0]["owner_name"] == "The Garden State Cat Club",
      "丢弃记录同时留下归一化归属和展示名")
check(rejected[0]["reason"] == "owner_mismatch", "原因是归属不符")
check(rejected[0]["text_head"].startswith("THE WINNERS"),
      "留了正文开头，人能一眼看出丢的是什么")

no_owner = extract([{"post_id": "999", "message": {"text": "no actor here"},
                     "attachments": [], "creation_time": 1756400000}],
                   "facebook", "neakasaofficial", route="backfill")
kept2, rejected2 = partition_by_owner(no_owner, "neakasaofficial")
check(kept2 == [] and rejected2[0]["reason"] == "owner_unknown",
      "归属未知的一律丢弃，原因与「归属不符」分开记")

kept3, _ = partition_by_owner(mixed, "NeakasaOfficial")
check(len(kept3) == 1, "目标账号大小写不敏感（config 里怎么写都能匹配上）")

print("\n[真实结构 5] 合作帖（collab）必须算本账号的")

# 保留已接受与待接受合作字段，只有前者决定归属。
def ig_node(pk, owner, coauthors=(), invited=(), ts=1756000000, text="collab"):
    return {"pk": pk, "code": "c%s" % pk, "taken_at": ts,
            "product_type": "clips",
            "user": {"username": owner, "full_name": owner.title()},
            "caption": {"text": text},
            "coauthor_producers": [{"pk": "9%d" % i, "username": u}
                                   for i, u in enumerate(coauthors)],
            "invited_coauthor_producers": [{"pk": "8%d" % i, "username": u}
                                           for i, u in enumerate(invited)],
            "image_versions2": {"candidates": [
                {"url": "https://cdn.example.com/%s.jpg" % pk,
                 "width": 1080, "height": 1080}]}}


timeline = extract([{"items": [
    ig_node("1", "neakasa.tech"),                                  # 自己发的
    ig_node("2", "neakasa.global", coauthors=["neakasa.tech"]),    # 自家兄弟账号合作
    ig_node("3", "ruka.bsh", coauthors=["neakasa.tech"]),          # 第三方创作者合作
    ig_node("4", "some.brand", invited=["neakasa.tech"]),          # 只是被邀请，没接受
    ig_node("5", "chicago.fire"),                                  # 纯推荐位
]}], "instagram", "neakasa.tech", route="backfill")
kept, rejected = partition_by_owner(timeline, "neakasa.tech")
kept_ids = sorted(p.post_id for p in kept)
check(kept_ids == ["1", "2", "3"],
      "自己发的 + 两种合作帖都留下（实测漏判会丢掉 263 篇自己主页上的帖子）")
check(sorted(r["post_id"] for r in rejected) == ["4", "5"],
      "只被邀请、没接受的不算；纯推荐位也不算")
check([p.owner for p in kept if p.post_id == "3"] == ["ruka.bsh"],
      "owner 仍然是**真实作者**，没有被改写成目标账号")
check([p.coauthors for p in kept if p.post_id == "3"] == [["neakasa.tech"]],
      "coauthors 记下了合作关系，下游据此区分原创与合作")
check([p.coauthors for p in kept if p.post_id == "1"] == [[]],
      "自己原创的帖子 coauthors 为空")

upper = extract([{"items": [ig_node("6", "x.brand", coauthors=["Neakasa.Tech"])]}],
                "instagram", "neakasa.tech", route="backfill")
check(partition_by_owner(upper, "neakasa.tech")[0], "coauthor 的大小写不影响判定")

# 合并：同一帖的两份响应里只有一份带 coauthor_producers 时不能丢
half = extract([{"items": [ig_node("7", "brand.a", coauthors=["neakasa.tech"])]},
                {"items": [{"pk": "7", "code": "c7", "taken_at": 1756000000,
                            "user": {"username": "brand.a"},
                            "caption": {"text": "同一帖的另一份响应"},
                            "image_versions2": {"candidates": [
                                {"url": "https://cdn.example.com/7.jpg",
                                 "width": 1440, "height": 1440}]}}]}],
               "instagram", "neakasa.tech", route="backfill")
check(len(half) == 1 and half[0].coauthors == ["neakasa.tech"],
      "跨响应合并时 coauthors 会被补齐 —— 漏补就会把真帖子丢掉")

# 分片 coauthors 须取并集。
subsets = extract([{"items": [ig_node("8", "brand.b", coauthors=["other.brand"])]},
                   {"items": [ig_node("8", "brand.b",
                                      coauthors=["neakasa.tech"])]}],
                  "instagram", "neakasa.tech", route="backfill")
check(len(subsets) == 1
      and sorted(subsets[0].coauthors) == ["neakasa.tech", "other.brand"],
      "两份响应各带一部分 coauthor 时取并集，不是『空了才补』")
check(partition_by_owner(subsets, "neakasa.tech")[0],
      "并集之后这篇留得下来 —— 只补空的话它会被丢掉")

# 同时覆盖对象与裸字符串作者项。
strs = extract([{"items": [dict(ig_node("9", "brand.c"),
                                coauthor_producers=["Neakasa.Tech", "", None])]}],
               "instagram", "neakasa.tech", route="backfill")
check(strs[0].coauthors == ["neakasa.tech"],
      "coauthor 条目是裸字符串也能认出来，空值/None 跳过")

check(on_timeline_of(strs[0], "NeakasaOfficial".replace("Official", ".Tech")),
      "on_timeline_of 自己归一化 target —— 调用方传了带大写的账号名也不会静默丢光")

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

"""解析层自测。用贴近真实响应结构的样本，验证三种形态都能正确收敛。"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # tests/ 在子目录，得把项目根加进来
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
    "pk": "3002", "code": "BBB", "taken_at": 1756100000,
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
    "pk": "4001", "code": "DDD", "taken_at": 1756201000,
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
    "pk": "4002", "code": "EEE", "taken_at": 1756202000, "caption": None,
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

print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

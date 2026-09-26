"""Classify archived source posts independently of local preview availability."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


PostType = Literal[
    "static_image_text", "image_only", "video", "image_video", "text_only", "pending",
]
POST_TYPES: frozenset[str] = frozenset((
    "static_image_text", "image_only", "video", "image_video", "text_only", "pending",
))


def classify_post(row: Mapping[str, Any]) -> PostType:
    media = row.get("media")
    source_complete = row.get("source_media_complete")
    if source_complete is None:
        source_complete = row.get("media_complete", True)
    if source_complete is not True or not isinstance(media, list):
        return "pending"
    expected = row.get("source_media_count")
    if expected is not None and (type(expected) is not int
                                 or expected < 0 or expected != len(media)):
        return "pending"
    kinds = [item.get("kind") for item in media if isinstance(item, Mapping)]
    if len(kinds) != len(media) or any(kind not in {"image", "video"} for kind in kinds):
        return "pending"
    if "image" in kinds and "video" in kinds:
        return "image_video"
    if "video" in kinds:
        return "video"
    if "image" in kinds:
        return "static_image_text" if str(row.get("text") or "").strip() else "image_only"
    return "text_only" if str(row.get("text") or "").strip() else "pending"

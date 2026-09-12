"""离线发布测试的 G1 数据；不读取用户 probe、会话或归档。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from PIL import Image


def verified_probe_config(real, state_dir: Path):
    """保留业务配置，给真实的 G1 校验器提供临时 v2 记录及截图。"""
    config = copy.copy(real)
    config._d = copy.deepcopy(real._d)
    state_dir.mkdir(parents=True, exist_ok=True)
    profile = state_dir.parent / "publish-profile"
    config._d.setdefault("paths", {})["state"] = str(state_dir)
    config._d["publish"].update({
        "profile_dir": str(profile), "debug_port": 9223,
        "ui_probe_dump": "publish_probe_fixture.json",
        "ui_constraints_verified": True,
        "ui_timezone": "America/Los_Angeles",
    })
    screenshots = state_dir / "publish_probe_fixture_screenshots"
    screenshots.mkdir(exist_ok=True)
    shot = screenshots / "masked.png"
    Image.new("RGB", (24, 16), (1, 2, 3)).save(shot)
    stamp = "2026-09-01T12:00:00+00:00"
    common = {"page_id": "fixture-page", "recorded_at": stamp,
              "screenshot": str(shot), "screenshot_error": None}
    data = {
        "schema_version": 2, "mode": "record-and-passive-evidence",
        "session_id": "offline-fixture", "started_at": stamp,
        "finished_at": stamp, "cdp_port": 9223, "profile_dir": str(profile),
        "interactions": [dict(common, sequence=index, evidence_order=index,
                              is_trusted=True, target={"role": "textbox"},
                              event_type="click" if index % 2 else "input")
                         for index in range(1, 8)],
        "snapshots": [dict(common, sequence=1, evidence_order=8,
                           reason="final", semantic_items=[])],
        "observations": {
            "business_suite_entry_url": "https://business.example.invalid/create",
            "ui_timezone": "America/Los_Angeles",
            "schedule_min_ahead": "1 hour", "schedule_max_ahead": "30 days",
            "schedule_min_ahead_seconds": "3600",
            "schedule_max_ahead_seconds": "2592000",
            "schedule_input_behavior": "direct input read back",
            "success_signal": "scheduled",
            "instagram_min_aspect_ratio": "0.5",
            "instagram_max_aspect_ratio": "2.0",
            "instagram_max_images": "5",
            "instagram_max_caption_length": "2200",
            "instagram_caption_length_mode": "codepoints",
            "instagram_max_hashtags": "30",
            "instagram_aspect_ratio_rejection": "rejected 0.49 and 2.01",
            "instagram_image_count_rejection": "rejected 6",
            "instagram_caption_length_rejection": "rejected 2201",
            "instagram_hashtag_rejection": "rejected 31",
        },
    }
    (state_dir / "publish_probe_fixture.json").write_text(
        json.dumps(data), encoding="utf-8")
    return config

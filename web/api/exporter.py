"""为人工接管准备本地资源包；内容全部校验成功后才允许记录接管状态。"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import localize_images
from core import translated
from core.review import ReviewConflict
from core.store import assert_physical_direct_path, read_post_truth


def package_post(account_dir: Path, indexed: dict) -> tuple[bytes, str]:
    source, post_dir = read_post_truth(account_dir, indexed)
    machine = translated.load_translated(account_dir / "translated.jsonl").get(source["post_id"])
    human = translated.load_human_translated(account_dir / "translated_human.jsonl").get(source["post_id"])
    effective = translated.effective_translation(source, machine, human)
    if not effective or not translated.translation_is_current(source, effective):
        raise ReviewConflict("请先保存并复核当前德语文案，再下载交由人工处理")
    image_entry = translated.image_translation(source, machine, human)
    pairs = {pair.media_index: pair for pair in localize_images.review_image_pairs(
        account_dir, source, image_entry)} if image_entry else {}
    media = source.get("media")
    if not isinstance(media, list) or not media or any(item.get("kind") != "image" for item in media):
        raise ReviewConflict("源帖不是完整图文帖，无法准备资源包")
    metadata = {key: source.get(key) for key in (
        "post_id", "platform", "account", "owner", "coauthors", "created_at", "permalink", "tags")}
    metadata.update({"exported_at": datetime.now(timezone.utc).isoformat(),
                     "source_text_sha256": translated.source_text_sha256(source["text"]),
                     "human_revision": human["revision"] if human else None, "images": []})
    buffer = io.BytesIO()
    notes = ["这份资源包已交由人工处理，系统不会代为发布。", "文案：text_de.txt；元信息：metadata.json。"]
    with ZipFile(buffer, "w", ZIP_DEFLATED) as package:
        package.writestr("text_de.txt", effective["text_de"].encode("utf-8"))
        total = 0
        for index, media_item in enumerate(media):
            original, _ = localize_images._source_from_manifest(account_dir, source, media_item)
            pair = pairs.get(index)
            used_original = not (pair and pair.localized_rel)
            path = original if used_original else account_dir / pair.localized_rel
            parent = post_dir if used_original else assert_physical_direct_path(
                post_dir, post_dir / "media_de", kind="directory", label="德语图片目录")
            assert_physical_direct_path(parent, path, kind="file", label="导出图片")
            total += path.stat().st_size
            if total > 256 * 1024 * 1024:
                raise ReviewConflict("资源超过 256 MB，请分开从本地归档取用")
            suffix = path.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                raise ReviewConflict("存在无法识别的图片格式，请检查归档")
            name = "images/%02d_%s%s" % (index + 1, "original" if used_original else "de", suffix)
            data = path.read_bytes()
            if not data:
                raise ReviewConflict("图片文件为空，请补齐素材后再下载")
            package.writestr(name, data)
            metadata["images"].append({"index": index, "file": name, "used_original": used_original})
            if used_original:
                notes.append("第 %d 张缺少当前德语图，包内使用原图，请人工处理后再发布。" % (index + 1))
        package.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))
        package.writestr("README.txt", "\n".join(notes).encode("utf-8"))
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", source["post_id"])[:100]
    return buffer.getvalue(), "%s_%s_de.zip" % (source["platform"], safe_id)

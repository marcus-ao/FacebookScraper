"""统一归档层。

三条获取路线（官方导出 / 官方 API / 浏览器拦截）产出格式各不相同，
在这里收敛为同一个 schema，下游的翻译与图像管线只认这个 schema。

归档布局：
    archive/<account>/manifest.jsonl     每行一个 Post（见 Post.to_row）
    archive/<account>/media/<post_id>_<n>.<ext>
    archive/<account>/raw/<post_id>.json 原始响应，保留以便 schema 变更后重放
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Media:
    url: str
    kind: str                 # "image" | "video"
    local_path: str | None = None
    width: int | None = None
    height: int | None = None
    # 图内文字，留给下游 OCR 阶段回填
    ocr_text: str | None = None


@dataclass
class Post:
    post_id: str
    platform: str             # "facebook" | "instagram"
    account: str
    text: str                 # 正文 / caption
    created_at: str           # ISO 8601
    permalink: str | None = None
    media: list[Media] = field(default_factory=list)
    source_route: str = ""    # "backfill" | "delta" | "graph_api"
    # 登出增量拿不到轮播帖的子项（只有封面），这类帖标 False，
    # 由完整性检查汇总，留给人工或下一次登录态回填补齐。
    media_complete: bool = True

    def to_row(self) -> dict:
        d = asdict(self)
        return d


class Archive:
    def __init__(self, root: Path | str, account: str):
        self.base = Path(root) / account
        self.media_dir = self.base / "media"
        self.raw_dir = self.base / "raw"
        for d in (self.media_dir, self.raw_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.manifest = self.base / "manifest.jsonl"
        self._rows = self._load_rows()

    def _load_rows(self) -> dict[str, dict]:
        """读取 manifest，同一 post_id 后写胜出。

        manifest 是追加写的，一个 post_id 可能出现多次（增量先写了
        只有封面的轮播帖，回填后又写了完整版）。读取时取最后一条。
        """
        rows: dict[str, dict] = {}
        if not self.manifest.exists():
            return rows
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    rows[r["post_id"]] = r
                except (json.JSONDecodeError, KeyError, TypeError):
                    # TypeError：整行是合法 JSON 但不是对象（如数组），下标取不到
                    continue
        return rows

    def has(self, post_id: str) -> bool:
        """增量抓取的依据。断点续传直接靠它，不需要额外状态文件。"""
        return post_id in self._rows

    def rows(self) -> list[dict]:
        """当前有效记录（已按后写胜出去重），供完整性检查与下游消费。"""
        return list(self._rows.values())

    def needs_media(self) -> list[dict]:
        """媒体不全的帖子。登出增量拿不到轮播子项，这里汇总待补清单。"""
        return [r for r in self._rows.values() if not r.get("media_complete", True)]

    def save_raw(self, post_id: str, payload: dict) -> None:
        (self.raw_dir / f"{post_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def media_path(self, post_id: str, idx: int, content_type: str | None) -> Path:
        ext = ".jpg"
        if content_type:
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed:
                ext = ".jpg" if guessed == ".jpe" else guessed
        return self.media_dir / f"{post_id}_{idx}{ext}"

    def append(self, post: Post) -> bool:
        """写入 manifest，返回是否实际写了。

        重复的 post_id 默认忽略，让脚本可以反复重跑而不产生副作用。
        唯一的例外是升级：已存记录媒体不全（登出增量只拿到轮播封面），
        而新记录更全 —— 这时追加一条新的，读取时后写胜出。
        没有这个例外，media_complete 标记就没有意义，补全永远写不进去。
        """
        if not self.should_append(post):
            return False
        row = post.to_row()
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._rows[post.post_id] = row
        return True

    def should_append(self, post: Post) -> bool:
        """返回这条帖子是否会被 :meth:`append` 接受。

        下载媒体前需要先做这个判断，避免幂等重跑时重复请求 CDN；同时不能只用
        ``has(post_id)``，否则登出增量留下的残缺轮播帖永远无法被登录态回填补全。
        """
        old = self._rows.get(post.post_id)
        return old is None or self._is_upgrade(old, post)

    @staticmethod
    def _is_upgrade(old: dict, new: Post) -> bool:
        was_incomplete = not old.get("media_complete", True)
        more_media = len(new.media) > len(old.get("media", []))
        return was_incomplete and (new.media_complete or more_media)

    @staticmethod
    def fingerprint(data: bytes) -> str:
        """媒体去重用。US/DE 两站同一张图只需处理一次。"""
        return hashlib.sha256(data).hexdigest()[:16]

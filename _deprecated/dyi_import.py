"""路线 A：官方数据导出（Download Your Information）解析

自有账号一次性回填的最优解，很多人不知道有这条路。
零代码获取、零封号风险、拿到的是全量原始媒体文件而不是 CDN 压缩图。

导出步骤（网页端手工操作一次）：
    Accounts Center → Your information and permissions
    → Export your information → Create export
    → 选账号 → Export to device
    → 格式选 JSON（不是 HTML）、媒体质量选 High
    → 勾选 Posts / Photos and videos
提交后等邮件通知，下载 ZIP，解压后把目录传给本脚本。

局限（决定了它不能是唯一路线）：
    - 只能导出自己的账号
    - 手工触发，不能增量、不能定时
    - 从提交到可下载有延迟，通常几小时，数据量大时更久
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.store import Archive, Media, Post

# 导出包里帖子 JSON 的位置随版本变动，这里做多路径探测
POST_GLOBS = (
    "your_facebook_activity/posts/your_posts*.json",
    "posts/your_posts*.json",
    "your_instagram_activity/content/posts*.json",
    "content/posts*.json",
)


def _fix_mojibake(s: str) -> str:
    """Meta 的导出把 UTF-8 字节当单字节编码又写了一遍，非 ASCII 会变成乱码。

    这是导出包一个长期存在的编码缺陷：'é' 变成 'Ã©'，'ü' 变成 'Ã¼'。
    德语的 ä/ö/ü/ß 全部中招，所以这一步对本项目不是可选的。

    要试两种编码：多数情况是 latin-1，但当原字节落在 0x80-0x9F 区间时
    （'ß' 的 UTF-8 第二字节 0x9F 就在其中），Meta 有时按 cp1252 渲染，
    此时只试 latin-1 会失败并把乱码原样留下。
    """
    for enc in ("latin-1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        return fixed
    return s


def _iter_post_files(root: Path):
    for pattern in POST_GLOBS:
        yield from root.glob(pattern)


def import_export(export_dir: str | Path, account: str,
                  platform: str = "facebook",
                  archive_root: str | Path = "archive") -> int:
    root = Path(export_dir)
    if not root.is_dir():
        raise SystemExit(f"目录不存在：{root}")

    files = sorted(set(_iter_post_files(root)))
    if not files:
        raise SystemExit(
            f"在 {root} 下没找到帖子 JSON。\n"
            f"确认导出时格式选的是 JSON 而不是 HTML，且勾选了 Posts。"
        )
    print(f"找到 {len(files)} 个帖子文件")

    arc = Archive(archive_root, f"{platform[:2]}_{account}")
    n = 0

    for fp in files:
        raw = json.loads(fp.read_text(encoding="utf-8"))
        entries = raw if isinstance(raw, list) else raw.get("posts", [])

        for entry in entries:
            ts = entry.get("timestamp", 0)
            pid = str(entry.get("post_id") or f"dyi_{ts}_{n}")
            if arc.has(pid):
                continue

            # 正文可能在 data[].post，也可能在 title
            text = ""
            for d in entry.get("data", []) or []:
                if "post" in d:
                    text = _fix_mojibake(d["post"])
                    break
            if not text and entry.get("title"):
                text = _fix_mojibake(entry["title"])

            media: list[Media] = []
            for att in entry.get("attachments", []) or []:
                for item in att.get("data", []) or []:
                    m = item.get("media")
                    if not m or not m.get("uri"):
                        continue
                    src = root / m["uri"]           # 导出包内的相对路径
                    if not src.exists():
                        continue
                    idx = len(media)
                    dst = arc.media_dir / f"{pid}_{idx}{src.suffix}"
                    shutil.copy2(src, dst)          # 本地复制，无网络请求
                    media.append(Media(
                        url=f"file://{src}", kind="image",
                        local_path=str(dst.relative_to(arc.base)),
                    ))

            import time as _t
            arc.save_raw(pid, entry)
            arc.append(Post(
                post_id=pid, platform=platform, account=account,
                text=text,
                created_at=_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts)) if ts else "",
                permalink=None, media=media, source_route="dyi",
            ))
            n += 1

    return n


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        raise SystemExit(
            "用法：python -m routes.dyi_import <解压后的导出目录> <account> [platform]")
    plat = sys.argv[3] if len(sys.argv) > 3 else "facebook"
    print(f"导入 {import_export(sys.argv[1], sys.argv[2], plat)} 篇")

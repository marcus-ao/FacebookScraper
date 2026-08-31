"""归档读取/原子真相源边界：坏 schema 不入库，替换失败不截断旧 post.json。"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.console import force_utf8  # noqa: E402
from core.store import Archive, Media, Post  # noqa: E402

force_utf8()

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("[1] manifest 读取只接受非空字符串 post_id 与字符串 text")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    valid = Post(post_id="valid", platform="instagram", account="acct", text="",
                 created_at="2026-08-30T00:00:00Z").to_row()
    dirty = [
        valid,
        dict(valid, post_id=None),
        dict(valid, post_id=123),
        dict(valid, post_id=["unhashable"]),
        {key: value for key, value in valid.items() if key != "text"},
        dict(valid, post_id="null_text", text=None),
        dict(valid, post_id="list_text", text=["not", "text"]),
        ["legal JSON", "not an object"],
    ]
    arc.manifest.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in dirty) + "\n",
        encoding="utf-8")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        loaded = Archive(root, "acct")
    check([row["post_id"] for row in loaded.rows()] == ["valid"],
          "null/数字/不可哈希 ID 与缺失/null/非字符串 text 全部跳过")
    check("schema 无效" in output.getvalue()
          and "post_id 不是非空字符串" in output.getvalue()
          and "text 缺失或不是字符串" in output.getvalue(),
          "每类无效 schema 都有明确告警，不静默吞掉")


print("\n[2] post.json 原子替换异常时旧真相源与派生文件均不截断")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    arc = Archive(root, "acct")
    old = Post(post_id="atomic", platform="instagram", account="acct",
               text="OLD TRUTH", created_at="2026-08-30T00:00:00Z",
               media=[Media(url="cover", kind="image")], media_complete=False)
    arc.append(old)
    post_dir = arc.post_dir(old)
    post_before = (post_dir / "post.json").read_bytes()
    text_before = (post_dir / "text.txt").read_bytes()
    manifest_before = arc.manifest.read_bytes()

    upgraded = Post(post_id="atomic", platform="instagram", account="acct",
                    text="NEW TRUTH", created_at="2026-08-30T00:00:00Z",
                    media=[Media(url="one", kind="image"),
                           Media(url="two", kind="image")], media_complete=True)
    try:
        with patch("core.store.os.replace", side_effect=OSError("simulated replace failure")):
            arc.append(upgraded)
        failed_explicitly = False
    except OSError as exc:
        failed_explicitly = "simulated replace failure" in str(exc)
    check(failed_explicitly, "原子 replace 失败明确上抛，调用方不会误报升级成功")
    check((post_dir / "post.json").read_bytes() == post_before,
          "replace 失败后旧 post.json 字节完全不变")
    check((post_dir / "text.txt").read_bytes() == text_before,
          "post.json 未提交时 text.txt 也不提前改写")
    check(arc.manifest.read_bytes() == manifest_before,
          "真相源提交失败时 manifest 不追加升级记录")
    check(not list(post_dir.glob(".post.json.*.tmp")),
          "失败后同目录临时文件已清理")
    check(json.loads((post_dir / "post.json").read_text(encoding="utf-8"))["text"]
          == "OLD TRUTH", "旧 post.json 仍是可解析的完整 JSON")


print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败"))
sys.exit(1 if fails else 0)

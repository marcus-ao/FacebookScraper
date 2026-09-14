"""把 state/audit-probe/ 的真实响应脱敏成可入库的 shape 夹具。

为什么不直接入库真实响应：那些 JSON 里有整篇真实帖子正文、永久链接和真实
内容哈希。类型测试要的是**形状**（键、类型、枚举值、数组长度、null 与否），
不需要业务内容。

为什么不手写夹具：手写的形状会和后端漂移，而这正是要防的事。所以做法是
按真实响应机械脱敏，脚本入库，产物可复现。

脱敏规则（只动**值**，一个键都不改）：

* 64 位十六进制串   → 由 `sha256:<原串前8位>` 派生的合成 64 位十六进制
* UUID              → 由原串派生的合成 UUID
* http(s) URL       → https://example.invalid/<路径长度>
* 超过 40 字的字符串 → `『文本 <码点数> 字』`（保留非空与"是字符串"这两件事）
* 其它字符串        → 原样保留（枚举、状态词、时区名、平台名、短标签全靠它）
* 数字 / 布尔 / null → 原样保留
* 数组长度、嵌套结构 → 原样保留

⚠️ 脱敏后 `en_span` / `de_span` 这类下标**不再指向被截短的正文**。夹具只用于
形状断言；标记定位的测试用的是手写输入（`src/lib/marks.test.ts`），不用这里。

用法：

    scripts\\run_python.bat docs/ui-refactor/tools/redact_probe.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "state" / "audit-probe"
TARGET = ROOT / "web" / "ui-next" / "src" / "types" / "__fixtures__"

# 真实响应 → 夹具文件名。键名就是 shape 测试里引用的名字。
FILES = {
    "tasks.json": "review-list.json",
    "history.json": "history-list.json",
    "detail_fb.json": "task-detail-active.json",
    "detail_frozen.json": "task-detail-frozen.json",
    "calendar.json": "calendar.json",
    "settings.json": "settings.json",
    "runtime.json": "runtime.json",
    "approval_options.json": "approval-options.json",
    "initial_capabilities.json": "initial-capabilities.json",
    "refinement_capabilities.json": "refinement-capabilities.json",
    "template_text.json": "template-text.json",
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
LONG_TEXT = 40


def _derive(value: str, length: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    while len(digest) < length:
        digest += hashlib.sha256(digest.encode("utf-8")).hexdigest()
    return digest[:length]


def _redact_string(value: str) -> str:
    if SHA256_RE.match(value):
        return _derive("sha:" + value, 64)
    if UUID_RE.match(value):
        raw = _derive("uuid:" + value, 32)
        return "%s-%s-%s-%s-%s" % (raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:32])
    if value.startswith(("http://", "https://")):
        path = urlsplit(value).path
        return "https://example.invalid/%d" % max(1, len(path))
    # 本机 API 路径是结构而不是内容（前端要按它拼图片请求），原样保留。
    # 里面的 post_id 是公开帖子 ID，文档里本来就有。
    if value.startswith("/api/"):
        return value
    if len(value) > LONG_TEXT:
        return "『文本 %d 字』" % len(value)
    return value


def redact(node):
    if isinstance(node, dict):
        return {key: redact(item) for key, item in node.items()}
    if isinstance(node, list):
        return [redact(item) for item in node]
    if isinstance(node, str):
        return _redact_string(node)
    return node


def main() -> int:
    if not SOURCE.is_dir():
        print("找不到 %s —— 先用 README 里的只读 GET 重新取证" % SOURCE)
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)
    written = 0
    for source_name, target_name in FILES.items():
        path = SOURCE / source_name
        if not path.is_file():
            print("跳过（未捕获）：%s" % source_name)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        (TARGET / target_name).write_text(
            json.dumps(redact(data), ensure_ascii=False, indent=1, sort_keys=False) + "\n",
            encoding="utf-8")
        print("%-32s -> %s" % (source_name, target_name))
        written += 1
    print("\n%d 份夹具写到 %s" % (written, TARGET))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

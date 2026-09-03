r"""工作区卫生检查：把三个**已经犯过**的错变成会让测试变红的机器检查。

2026-09-02 的架构审查发现，这个仓库的臃肿不是"有一堆死代码堆在那儿"——
726 个定义里只有 9 个从来没被引用过。真正的病因是三条好习惯被无差别地
执行到了极限、而且**从不回收**：

1. **每个决定都把完整论证写在代码/配置里。** 于是 `config.toml` 613 行里
   387 行是注释，真正的键只有 78 个；而其中两个键
   （`schedule_min_minutes_ahead` / `schedule_max_days_ahead`）在全部
   Python 代码里出现 **0 次**——改了不生效的旋钮，比没有更糟。
2. **每次失败都留下防御，永不回收。** 登出增量方案 8/30 就被自己证伪，
   720 行仍在维护；`harvest_embedded` 的理由被自己推翻后默认关闭、代码留着；
   `_deprecated/` 三个文件里两个连 import 都过不了。
3. **方案变更时并行新增，不删旧的。** 同一个文件锁类被逐字节复制了 5 遍，
   两个 dHash 用不同的重采样滤波器给同一张图不同的值，
   `_is_fatal_api_error` 在两个模块里语义分叉——CR-53 的修复只落到了一半，
   同一次 429 让翻译整批崩、调图只算单条失败，持续两天没人发现。

下面三条检查分别对着这三条。**它们会红，而且应该红**——红的时候不是去
放宽阈值，是去回收那段代码；确实要保留的，写进各自的豁免表并说明理由。
"""
from __future__ import annotations

import ast
import collections
import hashlib
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.console import force_utf8   # noqa: E402

force_utf8()

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# 生产代码（不含测试、不含一次性脚手架）
PROD_DIRS = ("core", "routes", "publish")
PROD_ROOT_FILES = ("pipeline.py", "pipeline_assisted.py",
                   "translate.py", "localize_images.py")


def python_files(*, include_tests: bool, include_scaffolding: bool) -> list[Path]:
    out: list[Path] = []
    for name in PROD_ROOT_FILES:
        out.append(ROOT / name)
    for folder in PROD_DIRS:
        out += [p for p in (ROOT / folder).rglob("*.py")
                if "__pycache__" not in p.parts]
    for p in (ROOT / "tools").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if "_scaffolding" in p.parts and not include_scaffolding:
            continue
        out.append(p)
    if include_tests:
        out += [p for p in (ROOT / "tests").rglob("*.py")
                if "__pycache__" not in p.parts]
    return sorted(set(out))


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# ==========================================================================
print("[1] 病因 1 · 没有改了不生效的旋钮")
# --------------------------------------------------------------------------
# config.toml 里的每一个键都必须真的被某处 Python 代码读取。
# `runs_per_day` 曾因为这个问题被删过一次，schedule_*_ahead 是同一个坑的复发。

CONFIG_KEY_EXEMPT = {
    # [targets] 的值是账号名，键名就是平台名，由 cfg()["targets"][platform]
    # 动态索引，grep 不到字面量。
    "targets.facebook", "targets.instagram",
}

#: **整表消费**的数据表：键是业务数据（术语、价格、型号），不是旋钮。
#: 对它们要检查的是"这张表被读了没"，而不是"每个词条被 grep 到没"——
#: 后者永远会红，而红得没有信息量的检查会被人关掉。
CONFIG_DATA_TABLES = {
    "translate.glossary",       # 英→德术语表，render_glossary 整表渲染
    "publish.price_map",        # 美元→欧元定价表，apply_money_mapping 整表查
    "publish.trusted_owners",   # 合作方白名单
    "image.keep_verbatim",      # 图内不翻译的型号名
}


def config_keys() -> list[str]:
    data = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    out: list[str] = []

    def walk(node, prefix):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, path)
            else:
                out.append(path)
    walk(data, "")
    return out


sources = {p: p.read_text(encoding="utf-8")
           for p in python_files(include_tests=True, include_scaffolding=True)}
all_python = "\n".join(sources.values())

def is_read(token: str) -> bool:
    """键名以字符串字面量出现即算被读到（``cfg().get("delta", "max_scrolls")``）。"""
    return bool(re.search(r'["\']%s["\']' % re.escape(token), all_python))


unread = []
seen_tables = set()
for dotted in config_keys():
    if dotted in CONFIG_KEY_EXEMPT:
        continue
    table = next((t for t in CONFIG_DATA_TABLES if dotted.startswith(t + ".")), None)
    if table is not None:
        if table not in seen_tables:
            seen_tables.add(table)
            if not is_read(table.rsplit(".", 1)[-1]):
                unread.append("%s（整张数据表都没人读）" % table)
        continue
    if not is_read(dotted.rsplit(".", 1)[-1]):
        unread.append(dotted)

check(not unread,
      "config.toml 的每个键都被代码读取，实得未读取 %d 个：%s"
      % (len(unread), "、".join(unread) or "无"))


# ==========================================================================
print("\n[2] 病因 2 · 没有零引用的定义")
# --------------------------------------------------------------------------
# 每个函数/类都必须至少被引用一次（测试和脚手架都算）。
# 留一个没人调的函数，下一个人会以为它有用途。

SYMBOL_EXEMPT = {
    # CLI 入口点由 argparse / __main__ 分派，或供外部按名调用
    "main", "_cli",
}

definitions: dict[str, list[tuple[str, int]]] = {}
for path in python_files(include_tests=False, include_scaffolding=False):
    try:
        tree = ast.parse(sources[path])
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.setdefault(node.name, []).append((rel(path), node.lineno))

references = collections.Counter()
for text in sources.values():
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text):
        references[match.group()] += 1

orphans = []
for name, places in definitions.items():
    if name.startswith("__") or name in SYMBOL_EXEMPT:
        continue
    if references[name] <= len(places):        # 只有定义本身，没有任何引用
        orphans.append("%s（%s）" % (name, places[0][0] + ":" + str(places[0][1])))

check(not orphans,
      "生产代码里没有零引用定义，实得 %d 个：%s"
      % (len(orphans), "、".join(sorted(orphans)) or "无"))


# ==========================================================================
print("\n[3] 病因 3 · 同一段逻辑不许在两个文件里各写一遍")
# --------------------------------------------------------------------------
# 滑动窗口比对：忽略空行、注释与字符串字面量后，连续 WINDOW 行完全相同
# 且出现在两个不同文件里，就算重复实现。
#
# 这条检查如果早就在，CR-53 那次分叉不会发生：文件锁 5 份、dHash 2 份、
# _is_fatal_api_error 2 份、原子写 6 份，当场就会红。

WINDOW = 8

DUPLICATE_EXEMPT = {
    # 目前没有豁免。要加的话写成 frozenset({"文件A", "文件B"}) 并在这里
    # 说明为什么这两处**必须**各写一遍——"改起来麻烦"不是理由。
}

#: CLI 入口不参与重复检测。
#:
#: 归一化会把字符串字面量换成 S —— 那是抓到文件锁 5 份复制的关键（那 5 份的
#: 差异只有类名和文案）。但同一招也会把两个 ``main()``（argparse 接线 +
#: 诊断输出 + ``return 0/1``）判成重复，而那里的"相同"只是形状。
#:
#: 更要紧的是：那种内容差异往往是**承重的**。``start_chrome_publish.py`` 的
#: 排查清单必须与 ``start_chrome.py`` 的不同——CR-63 正是因为它复用了抓取
#: Chrome 的清单，让用户照着一份**全是错的**清单去排查了一整轮。
#:
#: 真正的逻辑不在 main 里。所以按函数名整段排除，比枚举各种句式的正则稳。
CLI_ENTRY_NAMES = {"main", "_cli"}


def cli_line_ranges(tree: ast.AST) -> list[range]:
    """``main`` / ``_cli`` 函数体，以及 ``if __name__ == "__main__":`` 块。"""
    spans = []
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in CLI_ENTRY_NAMES):
            spans.append(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        elif isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                spans.append(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return spans


def normalized(path: Path) -> list[tuple[int, str]]:
    try:
        skip = {n for span in cli_line_ranges(ast.parse(sources[path]))
                for n in span}
    except SyntaxError:
        skip = set()
    out = []
    for number, line in enumerate(sources[path].split("\n"), 1):
        if number in skip:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = re.sub(r'"""[\s\S]*?"""|"[^"]*"|\'[^\']*\'', "S", stripped)
        if stripped in {"S", "S,", "S)", ")", "(", "]", "["}:
            continue
        out.append((number, stripped))
    return out


index: dict[str, list[tuple[str, int, int]]] = collections.defaultdict(list)
for path in python_files(include_tests=False, include_scaffolding=False):
    rows = normalized(path)
    for start in range(len(rows) - WINDOW):
        window = rows[start:start + WINDOW]
        digest = hashlib.md5(
            "\n".join(text for _, text in window).encode("utf-8")).hexdigest()
        index[digest].append((rel(path), window[0][0], window[-1][0]))

clashes = {}
for places in index.values():
    files = {name for name, _, _ in places}
    if len(files) < 2 or frozenset(files) in DUPLICATE_EXEMPT:
        continue
    clashes.setdefault(frozenset(files), []).append(places[0])

if clashes:
    for files, samples in sorted(clashes.items(), key=lambda kv: -len(kv[1])):
        first = samples[0]
        print("       %d 处窗口重复：%s" % (len(samples), " ↔ ".join(sorted(files))))
        print("         例如 %s:%d-%d" % first)
check(not clashes,
      "生产代码里没有跨文件重复实现，实得 %d 组" % len(clashes))


# ==========================================================================
print("\n[4] 文档引用必须指向真实存在的文件")
# --------------------------------------------------------------------------
# 删文档时最容易留下的尾巴：代码注释还指着一个已经不存在的路径。

missing = set()
for path, text in sources.items():
    for match in re.finditer(r"docs/[A-Za-z0-9_\-]+\.md", text):
        if not (ROOT / match.group()).is_file():
            missing.add("%s → %s" % (rel(path), match.group()))
for name in ("README.md",):
    for match in re.finditer(r"\(docs/[A-Za-z0-9_\-]+\.md\)",
                             (ROOT / name).read_text(encoding="utf-8")):
        target = match.group()[1:-1]
        if not (ROOT / target).is_file():
            missing.add("%s → %s" % (name, target))

check(not missing,
      "没有指向不存在文档的引用，实得 %d 处：%s"
      % (len(missing), "、".join(sorted(missing)) or "无"))


# ==========================================================================
print("\n[5] .bat 必须是 CRLF（裸 LF 会让 cmd 误解析整行）")
# --------------------------------------------------------------------------
# CR-62：切分支之后 .bat 会带着裸 LF，而 git status 看不见。

bad_bats = []
for path in sorted((ROOT / "scripts").glob("*.bat")):
    data = path.read_bytes()
    if data.replace(b"\r\n", b"") .count(b"\n"):
        bad_bats.append(path.name)
check(not bad_bats,
      "scripts/ 下的 .bat 全是 CRLF，实得裸 LF：%s" % ("、".join(bad_bats) or "无"))


print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
sys.exit(1 if fails else 0)

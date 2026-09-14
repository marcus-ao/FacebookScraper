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


# 受检代码（不含测试、不含一次性脚手架）
PROD_DIRS = ("core", "routes", "publish", "pipeline")
PROD_ROOT_FILES = ("translate.py", "localize_images.py")


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

# `web/` 不进模块图、不进重复检测 —— 它是叶子，而且 reader/writer 本来就该薄。
# 但**配置键的读取方可以住在那里**：`[review].snooze_default_days` 就只被
# `web/api/reader.py` 与 `writer.py` 读。只扫 core/routes/publish/pipeline 的话，
# 这条检查会把一个真在生效的键报成「改了不生效的旋钮」，而下一个人面前只有
# 两条错路：删掉键，或者加一条豁免。于是这个键干脆**从来没写进 config.toml**，
# 代码里三处默认值各自写了个 3 —— 这条检查本来就是为了防住这种事。
# 只扩 `is_read` 的文本语料；`sources` 不动，另外几条检查的边界照旧。
WEB_SOURCES = [p for p in (ROOT / "web").rglob("*.py") if "__pycache__" not in p.parts]
all_python = "\n".join([*sources.values(),
                        *(p.read_text(encoding="utf-8") for p in WEB_SOURCES)])

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
# Web 是实际调用者。引用统计纳入叶子入口，定义扫描/依赖图边界不变。
for text in [*sources.values(), *(path.read_text(encoding='utf-8') for path in WEB_SOURCES)]:
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text):
        references[match.group()] += 1

orphans = []
for name, places in definitions.items():
    if name.startswith("__") or name in SYMBOL_EXEMPT:
        continue
    if references[name] <= len(places):        # 只有定义本身，没有任何引用
        orphans.append("%s（%s）" % (name, places[0][0] + ":" + str(places[0][1])))

check(not orphans,
      "受检代码里没有零引用定义，实得 %d 个：%s"
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
      "受检代码里没有跨文件重复实现，实得 %d 组" % len(clashes))


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


# ==========================================================================
print("\n[6] 模块图必须是 DAG，依赖只许向下")
# --------------------------------------------------------------------------
# 2026-09-03 的架构复查：`core/{config,store,chrome,...}` 之上的 13 个模块
# 是**一个强连通分量**——不是"有几处循环"，是整个应用层根本没有分层，
# 它是一个模块套着 13 个文件名。任何一块都不能单独读、单独测、单独换。
#
# 而它是**一次一行**长出来的：每次都是"就这一次，在函数体里 import 一下"。
# 现场留下 28 处函数内导入，其中 6 处注释明写着"延迟导入，避免模块初始化环"。
# 那些注释是唯一的记录——而注释不会让测试变红。
#
# 所以这里立两条：
#
#   6a  **模块级**导入图无环。这是会在 import 期直接炸的那一类。
#   6b  **全部**导入（含函数体内）在排除组装根之后仍然无环。
#       这一条抓的是"用延迟导入把环藏起来"——Python 不炸，但模块之间
#       依旧互为前提。
#
# 组装根豁免（``main`` / ``_cli``）：进程入口的职责就是把对象图装起来，
# 它**可以**向上够。`translate.main()` 要一个住在 pipeline_assisted 的预算
# 策略，那是依赖注入，不是环。豁免只给这两个名字，且只给函数体。

COMPOSITION_ROOTS = {"main", "_cli"}


def internal_imports(tree: ast.AST, own: set[str], *,
                     toplevel_only: bool = False,
                     skip_composition_roots: bool = False) -> set[str]:
    if toplevel_only:
        nodes: list[ast.AST] = []

        def walk(body):
            # if/try 里的模块级导入照样在 import 期执行，要算进来
            for node in body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    nodes.append(node)
                elif isinstance(node, (ast.If, ast.Try)):
                    walk(node.body)
                    walk(getattr(node, "orelse", []))
                    walk(getattr(node, "finalbody", []))
                    for handler in getattr(node, "handlers", []):
                        walk(handler.body)
        walk(tree.body)
    else:
        skipped: set[int] = set()
        if skip_composition_roots:
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name in COMPOSITION_ROOTS):
                    skipped |= {id(d) for d in ast.walk(node)}
        nodes = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.Import, ast.ImportFrom))
                 and id(n) not in skipped]

    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            # `from publish import evidence` 的目标是 publish.evidence，
            # 不是 publish —— 两者分层不同，混为一谈会漏报也会误报。
            names = [node.module] + [f"{node.module}.{a.name}"
                                     for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        found |= {n for n in names if n in own}
    return found


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts
                    ).removesuffix(".__init__")


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 强连通分量；返回所有大小 >1 的分量。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    counter = [0]
    out: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w == v:
                continue
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                out.append(sorted(component))

    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 10_000))
    try:
        for v in sorted(graph):
            if v not in index:
                strongconnect(v)
    finally:
        sys.setrecursionlimit(old_limit)
    return out


graph_files = python_files(include_tests=False, include_scaffolding=False)
own_modules = {module_name(p) for p in graph_files}
trees = {module_name(p): ast.parse(p.read_text(encoding="utf-8"))
         for p in graph_files}

for label, kwargs in (
        ("6a 模块级导入图", dict(toplevel_only=True)),
        ("6b 含函数体（排除组装根）", dict(skip_composition_roots=True))):
    graph = {name: internal_imports(tree, own_modules, **kwargs) - {name}
             for name, tree in trees.items()}
    cycles = find_cycles(graph)
    check(not cycles,
          "%s 无环，实得 %d 个：%s" % (
              label, len(cycles),
              " ｜ ".join(" <-> ".join(c) for c in cycles) or "无"))

# 组装根豁免不是"随便什么函数都能藏环"。函数体内导入本仓库模块的，
# 只允许出现在 main/_cli 里，或者带一句说明它为什么必须晚绑定。
#
# 目前允许的两类理由：
#   - 组装根（main/_cli）；
#   - 明写 `# 延迟导入：`  开头的注释，说明晚绑定的**代价原因**
#     （例如 pipeline/cli.py 不想为了看一眼积压就把 Pillow 拉起来）。
LAZY_REASON = re.compile(r"#\s*延迟导入[：:]")


def module_scope_import_ids(tree: ast.Module) -> set[int]:
    """在 import 期执行的导入：模块级，含 `if __name__` / `try` 包着的那些。"""
    out: set[int] = set()

    def walk(body):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                out.add(id(node))
            elif isinstance(node, (ast.If, ast.Try)):
                walk(node.body)
                walk(getattr(node, "orelse", []))
                walk(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)
    walk(tree.body)
    return out


undocumented: list[str] = []
for path in graph_files:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = trees[module_name(path)]
    exempt = module_scope_import_ids(tree)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in COMPOSITION_ROOTS):
            exempt |= {id(d) for d in ast.walk(node)}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if id(node) in exempt:
            continue
        if not internal_imports(ast.Module(body=[node], type_ignores=[]),
                                own_modules):
            continue
        # 前三行（成段说明）或本行行尾（`import x  # 延迟导入：...`）都算
        window = "\n".join(lines[max(0, node.lineno - 4):node.end_lineno])
        if not LAZY_REASON.search(window):
            undocumented.append("%s:%d" % (rel(path), node.lineno))

check(not undocumented,
      "函数体内导入本仓库模块的，都在组装根里或写了「延迟导入：」理由，"
      "实得 %d 处无说明：%s"
      % (len(undocumented), "、".join(undocumented) or "无"))


print("\n[7] SQLite 只做展示索引，业务模块不依赖数据库")
database_consumers = []
for name, tree in trees.items():
    if name == "core.index_db":
        continue
    if name.split(".")[0] not in {"core", "routes", "pipeline", "publish", "translate", "localize_images"}:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules = [node.module or ""] + ["%s.%s" % (node.module, alias.name) for alias in node.names]
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        else:
            continue
        if any(module in {"core.index_db", "sqlite3"} or module.startswith("sqlite3.") for module in modules):
            database_consumers.append("%s:%d" % (name, node.lineno))
check(not database_consumers,
      "core/routes/pipeline/publish 及付费执行器不读 DB 做决策，实得：%s"
      % ("、".join(database_consumers) or "无"))

print("\n[8] 云盘镜像没有下载或反向恢复接口")
mirror_tree = trees.get("core.mirror")
reverse_mirror = []
if mirror_tree is not None:
    for node in ast.walk(mirror_tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"download", "download_file", "download_all", "restore", "sync_from_cloud"}:
            reverse_mirror.append(str(node.lineno))
        if (node.func.attr == "get" and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "http"):
            permitted = (node.args and isinstance(node.args[0], ast.Constant)
                         and node.args[0].value == "https://open.feishu.cn/open-apis/drive/v1/files/task_check")
            if not permitted:
                reverse_mirror.append(str(node.lineno))
check(not reverse_mirror, "镜像仅可读取异步任务状态元数据，无云内容反向入口，实得：%s"
      % ("、".join(reverse_mirror) or "无"))

print("\n[9] 同名源文版本字段只使用统一摘要")
source_hash_errors = []
def source_version_field(node):
    return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value == 'source_text_sha256') or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
        and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'source_text_sha256')

def canonical_source_call(node):
    # Accessing an already-bound token is a transfer, not a hash calculation.
    return not isinstance(node, ast.Call) or source_version_field(node) or (
        isinstance(node.func, ast.Name) and node.func.id == 'source_text_sha256') or (
        isinstance(node.func, ast.Attribute) and node.func.attr == 'source_text_sha256')

for path in graph_files:
    for node in ast.walk(trees[module_name(path)]):
        pairs = []
        if isinstance(node, ast.Dict):
            pairs = [(key.value, value) for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant)]
        elif isinstance(node, ast.Call):
            pairs = [(key.arg, key.value) for key in node.keywords]
        for key, value in pairs:
            if key == 'source_text_sha256' and not canonical_source_call(value):
                source_hash_errors.append('%s:%s' % (rel(path), value.lineno))
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(source_version_field(value) for value in operands):
                for value in operands:
                    if not canonical_source_call(value):
                        source_hash_errors.append('%s:%s' % (rel(path), value.lineno))
check(not source_hash_errors, '含 Web 的来源哈希写入/比较统一，实得：' + ('、'.join(source_hash_errors) or '无'))

print("\n" + ("全部通过" if not fails else "%d 项失败" % len(fails)))
sys.exit(1 if fails else 0)

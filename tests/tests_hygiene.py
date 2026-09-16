"""检查配置消费、无用定义、重复实现、文档链接、脚本换行及依赖边界。"""
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
PROD_DIRS = ("core", "routes", "localize", "publish", "pipeline", "deployment")


def python_files(*, include_tests: bool, include_scaffolding: bool) -> list[Path]:
    out: list[Path] = []
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
# 配置键须有实际读取方。

CONFIG_KEY_EXEMPT = {
    # targets 通过平台名动态索引，不能只查字面量。
    "targets.facebook", "targets.instagram",
}

# 业务数据表检查整表消费，不逐一检索词条。
CONFIG_DATA_TABLES = {
    "translate.glossary",       # 英→德术语表，render_glossary 整表渲染
    "publish.price_map",        # 美元→欧元定价表，apply_money_mapping 整表查
    "publish.trusted_owners",   # 合作方白名单
    "image.keep_verbatim",      # 图内不翻译的型号名
    "storage.product_aliases", # 首次归档按完整别名整表分类
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

# Web 可读取配置；仅扩展键消费检查，不改变模块分层范围。
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
# 检查函数和类是否有消费者。

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
# 忽略注释和字面量后，比较跨文件的连续代码窗口。

WINDOW = 8

DUPLICATE_EXEMPT = {
    # 豁免以文件对登记，并注明必要理由。
}

# 排除 CLI 接线函数，避免字面量归一化将不同命令误判为重复逻辑。
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
        # 纯字符串映射也是数据；不同文案表归一化后均为 S: S,，不代表重复逻辑。
        if stripped in {"S", "S,", "S)", ")", "(", "]", "[", "S: S,", "S:S,"}:
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
# 检查失效文档引用。

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
# 检查工作文件的实际 CRLF，git status 不反映此差异。

bad_bats = []
for path in sorted((ROOT / "scripts").glob("*.bat")):
    data = path.read_bytes()
    if data.replace(b"\r\n", b"") .count(b"\n"):
        bad_bats.append(path.name)
check(not bad_bats,
      "scripts/ 下的 .bat 全是 CRLF，实得裸 LF：%s" % ("、".join(bad_bats) or "无"))


# ==========================================================================
print("\n[6] 模块图必须是 DAG，依赖只许向下")
# 检查模块级和函数内导入环；仅 main/_cli 组装根允许注入上层策略。

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
            # from package import submodule 应映射到子模块。
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

# 非组装根的晚绑定须在导入附近注明“延迟导入：”理由。
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
    if name.split(".")[0] not in set(PROD_DIRS):
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
                         and node.args[0].value in {
                             "https://open.feishu.cn/open-apis/drive/v1/files/task_check",
                             "https://open.feishu.cn/open-apis/drive/v1/files",
                         })
            if not permitted:
                reverse_mirror.append(str(node.lineno))
check(not reverse_mirror, "镜像仅可读取目录及异步任务元数据，无云内容反向入口，实得：%s"
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

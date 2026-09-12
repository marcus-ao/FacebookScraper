r"""统一归档层。

三条获取路线（官方导出 / 官方 API / 浏览器拦截）产出格式各不相同，
在这里收敛为同一个 schema，下游的翻译与图像管线只认这个 schema。

归档布局（旧平铺目录继续可读，新帖创建月份目录）::

    archive/<平台前缀>_<账号>/
      index.html                        全账号总览（派生，可重建）
      manifest.jsonl                    **派生索引**，可从 posts/ 重建
      _rejected.jsonl                   被丢弃的节点及原因
      _orphan_media/                    重建后无主的媒体文件
      _orphan_posts/                    被升级/replay 隔离的旧帖子目录（可恢复）
      posts/
        2026-08/2026-08-25_1423_<slug>_<post_id>/
          post.json                     **真相源**
          text.txt                      正文纯文本（派生，给人和设计同事看）
          text_de.txt                   德语译文副本（派生）
          01.jpg  02.jpg                原图，按帖内顺序
          source_history.jsonl          源帖更新前的完整版本与媒体路径
          media_de/                     设计同事回填的德文版图
        undated/undated_<slug>_<post_id>/ 时间解析不出来的进这里，不猜
      _capture_*.json                   回填时的原始响应转储

**为什么是每帖一个文件夹**：帖子一多，扁平的 `media/<post_id>_<n>.jpg`
就分不清哪些图和哪段文字是一篇、哪天发的。但决定性的理由不是"好看"，
而是**下游有人要动这些文件**——计划固化了「图内英文文字本期走人工处理」，
设计同事会把替换好德文的图交回来。存在人工编辑环节的数据，
就该按人能操作的粒度组织。

**三条必须守住的规则**：

1. **`post.json` 是真相，`manifest.jsonl` 是派生索引。** 冲突时以文件夹为准，
   跑 `Archive.reindex()` 重建索引——**永远不反过来**。方向必须单一，
   否则会退化成"两个都不可信"。
2. **译文的真相源仍是账号级的 `translated.jsonl`**（守住既有决策：重跑抓取
   不得冲掉花钱买来的译文）。文件夹里的 `text_de.txt` 是派生副本。
3. **被丢弃的节点必须留痕**（`_rejected.jsonl`），不得静默丢弃。

历史说明：旧布局有个 `raw/<post_id>.json`，docstring 声称是"原始响应，
保留以便 schema 变更后重放"，**但代码实际写进去的是 `post.to_row()`**——
和 manifest 逐字段相同，不是原始响应。真正的原始响应一直在 `_capture_*.json` 里。
`post.json` 落地后它成了纯重复，已移除。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os  # noqa: F401  兼容现有故障注入测试对 core.store.os.replace 的补丁
import re
import shutil
import stat
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from core import paid_model
from core.config import cfg


@dataclass
class Media:
    url: str
    kind: str                 # "image" | "video"
    local_path: str | None = None
    width: int | None = None
    height: int | None = None


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
    # ⚠️ owner 是**节点自己声明的归属**，account 是**我们要抓的目标**。
    # 两者必须分开：人工滚动时页面会加载推荐内容和被 @ 的 UGC，
    # 它们和目标账号的帖子混在同一批响应里。2026-08-30 实测，
    # Instagram 一次回填混进了 266 条来自另外 195 个账号的帖子，
    # 全被标成目标账号 —— 下游会翻译并发布他人内容。
    # 归一化形式：IG = user.username；FB = actors[0].url 里的账号名段。
    # 都转小写，便于与 config.toml 的 [targets] 直接比对。
    owner: str | None = None
    # 展示名，只给人看（_rejected.jsonl 里 "The Garden State Cat Club"
    # 比一串 slug 有用得多）。不参与任何判等。
    owner_name: str | None = None
    # 合作帖的其他作者（Instagram 的 collab）。**这个字段决定一篇帖子在不在
    # 本账号主页上**：collab 帖会同时出现在双方主页，但 `user.username`
    # 只记原始发布者。2026-08-30 实测，只看 owner 的话 neakasa.tech 有
    # **263 篇自己主页上的帖子被当成他人帖丢掉**，其中 2026-08 那个月
    # 21 篇里绝大多数都是这种——账号看起来"一个多月没发帖"，其实一直在更。
    # 归一化为小写 username，便于与 [targets] 直接比对。
    coauthors: list[str] = field(default_factory=list)
    # 源响应只给轮播封面拿不到子项时（IG 的 web_profile_info 就是这样），标 False，
    # 由完整性检查汇总，留给人工或下一次登录态回填补齐。
    media_complete: bool = True
    folder_name: str | None = None  # 创建时固定；修改正文/标签不重命名
    tags: list[str] | None = None   # None 尚未预填；[] 是人工明确清空

    def to_row(self) -> dict:
        d = asdict(self)
        return d


_SAFE_POST_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}\Z")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


def _safe_post_id_component(post_id: str) -> str:
    """把外部 ``post_id`` 变成单个、跨平台安全的目录名组件。

    Facebook/Instagram 的正常 ID 只含 ASCII 字母、数字、点、横线或下划线，
    这类值原样保留，兼容已有归档。其余值（路径分隔符、控制字符、Windows
    保留名、过长 ID 等）使用原值的 SHA-256 摘要；同一个异常 ID 永远得到
    同一个目录名。这里只改变磁盘路径，``post.json`` / manifest 仍保存原 ID。
    """
    raw = str(post_id)
    reserved_stem = raw.split(".", 1)[0].upper()
    if (_SAFE_POST_ID.fullmatch(raw)
            and not raw.endswith(".")
            and reserved_stem not in _WINDOWS_RESERVED):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]
    # "~" 不在正常 ID 白名单内，因此生成名不可能与一个原样保留的正常 ID
    # 撞名；若远端真的给出同形字符串，它本身会再次走哈希分支。
    return f"~id_{digest}"


def _post_quality_parts(post: "Post | dict") -> tuple[bool, int, int]:
    """返回 ``(是否完整, 已知媒体数, 已落盘媒体数)``。"""
    if isinstance(post, Post):
        complete = post.media_complete
        media = post.media
    else:
        complete = post.get("media_complete", True)
        media = post.get("media") or []
    media_count = len(media) if isinstance(media, (list, tuple)) else 0
    if isinstance(post, Post):
        local_count = sum(1 for item in media if item.local_path)
    else:
        local_count = sum(
            1 for item in media
            if isinstance(item, dict) and item.get("local_path"))
    return bool(complete), media_count, local_count


def _post_quality_rank(post: "Post | dict") -> tuple[int, int, int]:
    """与增量升级规则共用的质量等级。

    完整记录永远胜过残缺记录；两条都残缺时，已实际落盘的媒体更多者胜出，
    其次才看已知媒体项数。落盘数优先很重要：一次重试可能只
    补回两张失败图片中的一张，虽然仍是 ``media_complete=False``，这部分
    进展也必须写回真相源，不能在下一次重试时重新下载。

    两条都完整时仍视为同等级，因为 :meth:`Archive._is_upgrade` 本来就不允许
    完整记录之间互相覆盖。把规则集中在一处，避免 append 与 reindex 各自
    发明胜负。
    """
    complete, media_count, local_count = _post_quality_parts(post)
    if complete:
        return (1, 0, 0)
    return (0, local_count, media_count)


class ArchivePathError(ValueError):
    """归档路径不是预期的真实直属目录/普通文件。"""


class ArchiveRevisionConflict(ArchivePathError):
    """源帖或标签在页面打开之后已更新，拒绝覆盖。"""


_UNSET = object()


def archive_write_lock(account_dir: Path):
    path = assert_physical_direct_path(Path(account_dir), Path(account_dir) / "archive_write.lock",
                                        kind="file", label="源帖写入锁")
    return paid_model.FileLock(path, busy_message="归档正在写入，请稍后重试")


def _is_link_or_reparse(path: Path) -> bool:
    """拒绝符号链接、Windows junction 及其它 reparse point。"""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        if path.exists():
            attrs = getattr(path.lstat(), "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if reparse_flag and attrs & reparse_flag:
                return True
        return False
    except OSError as exc:
        raise ArchivePathError(f"无法确认路径是否为链接/reparse point：{path}") from exc


def assert_physical_direct_path(parent: Path, path: Path, *,
                                kind: str, label: str) -> Path:
    """确认 ``path`` 是 ``parent`` 下真实的直属目录或普通文件。

    不存在的目标可以通过（供安全创建）；存在时拒绝 symlink、junction、其它
    reparse point，以及文件 hardlink。resolve 后必须精确等于物理父目录加当前
    文件名，不能只比较 ``resolved.parent``，否则链接到同一父目录的兄弟项仍会
    被误放行。
    """
    parent = Path(parent)
    path = Path(path)
    if kind not in {"directory", "file"}:
        raise ValueError("kind 必须是 directory 或 file")
    if path.parent != parent:
        raise ArchivePathError(f"{label} 不是预期父目录的直属子项：{path}")
    if _is_link_or_reparse(path):
        raise ArchivePathError(f"{label} 不得是 symlink/junction/reparse point：{path}")

    expected = parent.resolve(strict=False) / path.name
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ArchivePathError(f"无法解析 {label} 的物理路径：{path}") from exc
    if resolved != expected:
        raise ArchivePathError(
            f"{label} 的物理路径不是父目录下同名直属子项：{path} -> {resolved}")

    if path.exists():
        if kind == "directory" and not path.is_dir():
            raise ArchivePathError(f"{label} 应为目录，实为其它类型：{path}")
        if kind == "file":
            if not path.is_file():
                raise ArchivePathError(f"{label} 应为普通文件，实为其它类型：{path}")
            try:
                if path.stat(follow_symlinks=False).st_nlink > 1:
                    raise ArchivePathError(f"{label} 不得是 hardlink：{path}")
            except OSError as exc:
                raise ArchivePathError(f"无法确认 {label} 的文件属性：{path}") from exc
    return path


def _archive_row_error(row: object) -> str | None:
    """manifest/post.json 行的最小 schema 错误；``None`` 表示可接受。"""
    if not isinstance(row, dict):
        return "JSON 顶层不是对象"
    if not isinstance(row.get("post_id"), str) or not row["post_id"]:
        return "post_id 不是非空字符串"
    # Post.text 是必填字符串；无正文用空串表达，缺失/None 不是另一种空值。
    if not isinstance(row.get("text"), str):
        return "text 缺失或不是字符串"
    return None


def _atomic_write_text(path: Path, text: str, *, label: str) -> None:
    """同目录临时文件 flush+fsync 后原子替换，异常时保留旧文件。

    实现在 core/paid_model；路径安全断言通过 guard 传进去，包括**替换前
    再验一次目标**（防目标在写临时文件期间被换成链接）。
    """
    def guard(candidate: Path, role: str) -> None:
        assert_physical_direct_path(
            candidate.parent, candidate, kind="file",
            label=label if role == "target" else "%s 临时文件" % label)

    paid_model.atomic_write_text(path, text, guard=guard, newline="")


def post_dirname(post_id: str, created_at: str | None) -> str:
    """每帖文件夹的名字：`<YYYY-MM-DD>_<HHMM>_<post_id>`。

    **日期在前**，所以在资源管理器里按名称排序就是按发布时间排序——
    这正是"分不清哪篇是哪天发的"那个问题的解法。
    **post_id 在后**，所以判断"这篇抓过没有"不用打开任何文件。

    时间解析不出来的进 `undated_<post_id>`。**不猜时间**——
    猜一个日期塞进文件夹名，比没有日期更糟：它看起来是真的。
    真实数据里确实有这种帖子（FB 有一条 `created_at` 为空）。
    """
    ts = created_at or ""
    safe_id = _safe_post_id_component(post_id)
    # 只认 core.parse.iso() 产出的 "%Y-%m-%dT%H:%M:%SZ"。宽松匹配会把
    # from_fb_story 原样透传的怪字符串也放进来，那才是真正难查的问题。
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", ts):
        return f"{ts[:10]}_{ts[11:13]}{ts[14:16]}_{safe_id}"
    return f"undated_{safe_id}"


def account_dirs(archive_root: Path, only: str | None = None) -> list[Path]:
    """archive/ 下所有含 manifest.jsonl 的账号目录。

    从 `translate.py` 搬来的：翻译、调图、发布、流水线四路都要遍历账号目录，
    却只有翻译那边有这个函数，于是另外三路都得 import 翻译执行器。
    **archive/ 的目录布局是本模块的事。**
    """
    if not archive_root.exists():
        return []
    dirs = sorted(p for p in archive_root.iterdir()
                  if p.is_dir() and (p / "manifest.jsonl").exists())
    if only:
        dirs = [p for p in dirs if p.name == only]
    return dirs


def _folder_month(folder_name: str) -> str:
    return folder_name[:7] if re.match(r"^\d{4}-\d{2}-\d{2}_", folder_name) else "undated"


def _safe_folder_name(value: object) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._~\-]+", value)
            or value.endswith(".") or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
        raise ArchivePathError("folder_name 必须是安全的单个目录名")
    return value


def _stable_post_id_component(post_id: str) -> str:
    safe_id = _safe_post_id_component(post_id)
    if len(safe_id) > 48:
        return "~id_" + hashlib.sha256(str(post_id).encode("utf-8", errors="surrogatepass")).hexdigest()[:24]
    return safe_id


def post_folder_matches_id(folder_name: str, post_id: str) -> bool:
    """目录 ID 后缀归属检查；兼容旧完整 ID 与新布局缩短的长 ID。"""
    try:
        name = _safe_folder_name(folder_name)
    except ArchivePathError:
        return False
    return any(name.endswith("_" + component) for component in {
        _safe_post_id_component(post_id), _stable_post_id_component(post_id)})


def _new_folder_name(post: Post) -> str:
    legacy = _safe_folder_name(post_dirname(post.post_id, post.created_at))
    safe_id = _stable_post_id_component(post.post_id)
    text = unicodedata.normalize("NFKD", post.text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30].rstrip("-")
    prefix = legacy[:15] if not legacy.startswith("undated_") else "undated"
    return _safe_folder_name("_".join(part for part in (prefix, slug, safe_id) if part))


def infer_tags(text: str, models: list[str] | None = None) -> list[str]:
    """只按维护中的型号表预填；长型号优先，字母数字边界避免 M1 命中 M10。"""
    if models is None:
        models = cfg().get("image", "keep_verbatim", {}).get("models", [])
    choices = sorted({model for model in models if isinstance(model, str) and model.strip()},
                     key=lambda model: (-len(model), model))
    if not choices:
        return []
    canonical = {model.casefold(): model for model in choices}
    pattern = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, choices))
                         + r")(?![A-Za-z0-9])", re.IGNORECASE)
    return list(dict.fromkeys(canonical[match.group().casefold()] for match in pattern.finditer(text)))


def assert_post_directory(account_dir: Path, directory: Path) -> Path:
    """校验旧平铺或新月份层级；逐层拒绝链接，不能仅校验最末一层。"""
    account_dir, directory = Path(account_dir), Path(directory)
    assert_physical_direct_path(account_dir.parent, account_dir, kind="directory", label="账号归档目录")
    posts = assert_physical_direct_path(account_dir, account_dir / "posts", kind="directory", label="posts 根目录")
    if directory.parent != posts:
        month = directory.parent
        if month.parent != posts or not re.fullmatch(r"\d{4}-\d{2}|undated", month.name):
            raise ArchivePathError("帖子目录必须位于 posts/ 或 posts/<月份>/ 内")
        assert_physical_direct_path(posts, month, kind="directory", label="归档月份目录")
    return assert_physical_direct_path(directory.parent, directory, kind="directory", label="帖子目录")


def iter_post_dirs(account_dir: Path):
    """只遍历有效的两层布局，不递归进入恢复区或任意嵌套目录。"""
    account_dir = Path(account_dir)
    posts = assert_physical_direct_path(account_dir, account_dir / "posts", kind="directory", label="posts 根目录")
    if not posts.exists():
        return
    for child in sorted(posts.iterdir()):
        try:
            assert_physical_direct_path(posts, child, kind="directory", label="帖子或月份目录")
            if re.fullmatch(r"\d{4}-\d{2}|undated", child.name):
                for directory in sorted(child.iterdir()):
                    try:
                        yield assert_post_directory(account_dir, directory)
                    except ArchivePathError as exc:
                        print("    ! 跳过不安全的帖子目录：%s" % exc)
            else:
                yield child
        except ArchivePathError as exc:
            print("    ! 跳过不安全的归档目录：%s" % exc)


def post_directory(account_dir: Path, indexed: dict) -> Path:
    """统一定位稳定 folder_name/月目录与旧平铺目录；只读，不创建目录。"""
    account_dir = Path(account_dir)
    posts = account_dir / "posts"
    folder_name = indexed.get("folder_name")
    if folder_name is not None:
        name = _safe_folder_name(folder_name)
        monthly = assert_post_directory(account_dir, posts / _folder_month(name) / name)
        legacy = assert_post_directory(account_dir, posts / name)
        if monthly.exists() and legacy.exists():
            raise ArchivePathError("同一 folder_name 同时存在新旧目录，请先核对归档")
        return legacy if legacy.exists() else monthly
    name = _safe_folder_name(post_dirname(str(indexed.get("post_id") or ""), indexed.get("created_at")))
    legacy = assert_post_directory(account_dir, posts / name)
    if legacy.exists():
        return legacy
    # 迁移中断或索引落后时，按稳定 ID 后缀寻找真实目录，不能重算正文摘要。
    matches = [directory for directory in iter_post_dirs(account_dir)
               if post_folder_matches_id(directory.name, str(indexed.get("post_id") or ""))]
    if len(matches) > 1:
        raise ArchivePathError("同一 post_id 有多个帖子目录，请先重建并核对索引")
    return matches[0] if matches else legacy


def planned_post_directory(account_dir: Path, post: Post) -> Path:
    """新帖/重放的无写盘路径规划；固定对象元信息后执行阶段使用同一路径。"""
    if post.folder_name is None:
        existing = post_directory(account_dir, post.to_row())
        post.folder_name = existing.name if existing.exists() else _new_folder_name(post)
        truth = existing / "post.json"
        if truth.exists():
            row, _directory = read_post_truth(account_dir, post.to_row())
            if isinstance(row.get("tags"), list):
                post.tags = list(row["tags"])
    return post_directory(account_dir, post.to_row())


def read_post_truth(account_dir: Path, indexed: dict) -> tuple[dict, Path]:
    """按索引定位后只读实际 post.json；统一审校、图片与发布的原文边界。"""
    try:
        assert_physical_direct_path(account_dir.parent, account_dir,
                                    kind="directory", label="账号归档目录")
        post_dir = post_directory(account_dir, indexed)
        path = assert_physical_direct_path(post_dir, post_dir / "post.json",
                                           kind="file", label="post.json")
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArchivePathError("post.json 无法安全读取：%s" % exc) from exc
    error = _archive_row_error(source)
    if error:
        raise ArchivePathError("post.json schema 无效：%s" % error)
    for key in ("post_id", "platform", "account"):
        if source.get(key) != indexed.get(key):
            raise ArchivePathError("post.json 的 %s 与 manifest 不一致" % key)
    return source, post_dir


def update_post_tags(account_dir: Path, indexed: dict, tags: list[str], *,
                      expected_tags=_UNSET, expected_source_sha256: str | None = None) -> dict:
    """人工标签只写源帖；与抓取共用锁，并在锁内核对页面看到的版本。"""
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("tags 必须是非空字符串的数组；不分类时可保存空数组")
    normalized = list(dict.fromkeys(tag.strip() for tag in tags))
    with archive_write_lock(account_dir):
        current, directory = read_post_truth(account_dir, indexed)
        if expected_tags is not _UNSET and (current.get("tags") or []) != expected_tags:
            raise ArchiveRevisionConflict("标签已有新版本，请重新载入后保存")
        source_hash = hashlib.sha256(current["text"].strip().encode("utf-8")).hexdigest()
        if expected_source_sha256 is not None and source_hash != expected_source_sha256:
            raise ArchiveRevisionConflict("源帖已更新，请重新载入后保存标签")
        row = dict(current, tags=normalized)
        _atomic_write_text(directory / "post.json", json.dumps(row, ensure_ascii=False, indent=2), label="post.json")
        paid_model.append_jsonl(Path(account_dir) / "manifest.jsonl", row,
                                guard=lambda path: assert_physical_direct_path(
                                    path.parent, path, kind="file", label="manifest.jsonl"))
    return row


class Archive:
    def __init__(self, root: Path | str, account: str):
        self.root = Path(root)
        assert_physical_direct_path(
            self.root.parent, self.root, kind="directory", label="archive 根目录")
        self.base = self.root / account
        assert_physical_direct_path(
            self.root, self.base, kind="directory", label="账号归档目录")
        self.posts_dir = self.base / "posts"
        assert_physical_direct_path(
            self.base, self.posts_dir, kind="directory", label="posts 根目录")
        self.posts_dir.mkdir(parents=True, exist_ok=True)
        assert_physical_direct_path(
            self.root.parent, self.root, kind="directory", label="archive 根目录")
        assert_physical_direct_path(
            self.root, self.base, kind="directory", label="账号归档目录")
        assert_physical_direct_path(
            self.base, self.posts_dir, kind="directory", label="posts 根目录")
        self.manifest = self.base / "manifest.jsonl"
        self._rows = self._load_rows()

    # ---- 每帖文件夹 ----

    def post_dir(self, post: "Post") -> Path:
        previous = self._rows.get(post.post_id)
        if previous is not None:
            candidate = post_directory(self.base, previous)
            post.folder_name = previous.get("folder_name") or candidate.name
            return candidate
        return planned_post_directory(self.base, post)

    def _current_row(self, post: Post) -> dict | None:
        indexed = self._rows.get(post.post_id)
        if indexed is not None:
            return read_post_truth(self.base, indexed)[0]
        candidate = post_directory(self.base, post.to_row())
        if (candidate / "post.json").exists():
            return read_post_truth(self.base, post.to_row())[0]
        return None

    def _write_post_files(self, post: "Post") -> None:
        """写 `post.json`（真相源）与 `text.txt`（给人看的派生副本）。

        text.txt 与 post.json 里的 text 重复，是有意的：设计同事和审校人
        双击就能看，不必去读 JSON。post.json 永远是真相，
        text.txt 由 reindex 从它重新生成。
        """
        d = self.post_dir(post)
        d.mkdir(parents=True, exist_ok=True)
        assert_post_directory(self.base, d)
        post_json = assert_physical_direct_path(
            d, d / "post.json", kind="file", label="post.json")
        text_file = assert_physical_direct_path(
            d, d / "text.txt", kind="file", label="text.txt")
        # 两个叶子必须先全部通过，再写任何一个，避免 text.txt 有链接时
        # post.json 已经被部分更新。
        _atomic_write_text(
            post_json, json.dumps(post.to_row(), ensure_ascii=False, indent=2),
            label="post.json")
        text_file.write_text(post.text or "", encoding="utf-8")

    def _load_rows(self) -> dict[str, dict]:
        """读取 manifest，同一 post_id 后写胜出。

        manifest 是追加写的，一个 post_id 可能出现多次（增量先写了
        只有封面的轮播帖，回填后又写了完整版）。读取时取最后一条。
        """
        rows: dict[str, dict] = {}
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        if not self.manifest.exists():
            return rows
        with self.manifest.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    print(f"    ! manifest 第 {line_no} 行不是合法 JSON，跳过")
                    continue
                error = _archive_row_error(r)
                if error:
                    print(f"    ! manifest 第 {line_no} 行 schema 无效（{error}），跳过")
                    continue
                rows[r["post_id"]] = r
        return rows

    def has(self, post_id: str) -> bool:
        """增量抓取的依据。断点续传直接靠它，不需要额外状态文件。"""
        return post_id in self._rows

    def rows(self) -> list[dict]:
        """当前有效记录（已按后写胜出去重），供完整性检查与下游消费。"""
        return list(self._rows.values())

    def needs_media(self) -> list[dict]:
        """媒体不全的帖子。源响应给不全、或图片没下成功的，都在这里汇总。"""
        return [r for r in self._rows.values() if not r.get("media_complete", True)]

    def record_rejected(self, rows: list[dict]) -> int:
        """把被丢弃的节点追加进 `_rejected.jsonl`，返回实际新增条数。

        **静默丢数据是本项目最忌讳的事。** `partition_by_owner` 每次都会丢掉
        一批不属于目标账号的帖子（2026-08-30 实测 Instagram 一次回填丢 266 条），
        如果只是 `continue` 过去，就没人能回答"到底丢了什么、丢多了没有"。

        按 post_id 去重，重跑不会把同一条记录写很多遍。
        """
        if not rows:
            return 0
        path = self.base / "_rejected.jsonl"
        assert_physical_direct_path(
            self.base, path, kind="file", label="_rejected.jsonl")
        seen: set[str] = set()
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(json.loads(line)["post_id"])
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        added = 0
        assert_physical_direct_path(
            self.base, path, kind="file", label="_rejected.jsonl")
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                pid = row.get("post_id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                added += 1
        return added

    def media_path(self, post: "Post", idx: int, content_type: str | None) -> Path:
        """媒体落在该帖自己的文件夹里，命名 `01.jpg` / `02.jpg`（1 起，补零）。

        ⚠️ 签名收的是 **Post 而不是 post_id**：文件夹名要用到发布时间，
        光有 id 拼不出来。调用方只有下载环节，改动面很小。

        下载发生在 append 之前，所以这里要保证文件夹已存在。
        """
        ext = ".jpg"
        if content_type:
            mime = content_type.split(";")[0].strip().lower()
            guessed = _IMAGE_EXTENSIONS.get(mime) or mimetypes.guess_extension(mime)
            if guessed:
                ext = ".jpg" if guessed == ".jpe" else guessed
        d = self.post_dir(post)
        d.mkdir(parents=True, exist_ok=True)
        assert_post_directory(self.base, d)
        media = d / f"{idx + 1:02d}{ext}"
        assert_physical_direct_path(d, media, kind="file", label=f"媒体文件 {media.name}")
        previous = self._current_row(post)
        if media.exists() and previous is not None and idx < len(post.media):
            old_media = previous.get("media") or []
            old_url = old_media[idx].get("url") if idx < len(old_media) else None
            if old_url != post.media[idx].url:
                # 原图是留证；源帖换图时另存新文件，历史 source.local_path 继续有效。
                digest = hashlib.sha256(post.media[idx].url.encode("utf-8")).hexdigest()[:12]
                media = d / f"{idx + 1:02d}_{digest}{ext}"
                revision = 1
                while media.exists() or _is_link_or_reparse(media):
                    media = d / f"{idx + 1:02d}_{digest}_{revision}{ext}"
                    revision += 1
        return assert_physical_direct_path(
            d, media, kind="file", label=f"媒体文件 {media.name}")

    def reusable_media_path(self, post: "Post", url: str) -> Path | None:
        """返回同帖、同 URL 已安全落盘的媒体路径；没有则返回 ``None``。

        残缺帖重试会重新解析出一个全新的 :class:`Post`，其中没有旧记录的
        ``local_path``。若不在下载边界把已成功的部分接回来，每次重试都会
        重复请求这些 CDN URL。只复用当前帖子预期 truth dir 下的真实普通文件；
        时间被纠正导致目录变化时不跨目录借用，避免升级隔离旧目录后留下悬空引用。
        """
        old = self._current_row(post)
        if not isinstance(old, dict):
            return None
        expected_dir = self.post_dir(post)
        for item in old.get("media") or []:
            if not isinstance(item, dict) or item.get("url") != url:
                continue
            local = item.get("local_path")
            if not isinstance(local, str) or not local.strip():
                continue
            rel = Path(local.replace("\\", "/"))
            candidate = self.base / rel
            try:
                parent = assert_post_directory(self.base, candidate.parent)
                if parent.resolve() != expected_dir.resolve():
                    continue
                assert_physical_direct_path(
                    parent, candidate, kind="file", label="已归档媒体")
            except (ArchivePathError, OSError, RuntimeError):
                continue
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _available_recovery_path(root: Path, name: str) -> Path:
        """在恢复区生成不覆盖既有数据的目标路径。"""
        candidate = root / name
        suffix = 1
        while candidate.exists() or _is_link_or_reparse(candidate):
            candidate = root / f"{name}.{suffix}"
            suffix += 1
        return candidate

    def _isolate_other_truth_dirs(self, post: Post, keep: Path) -> list[Path]:
        """把同 ID 的其它 truth dir 移入 ``_orphan_posts/``，从不删除。

        created_at 被补出或纠正时，同一 post_id 的目标目录名会改变。若旧目录
        仍留在 ``posts/``，它就和新目录同时成为真相源，后续 reindex 可能选回
        旧的残缺记录。调用方必须先成功写完 ``keep/post.json`` 再调用这里。
        """
        orphan_root = self.base / "_orphan_posts"
        assert_physical_direct_path(
            self.base, orphan_root, kind="directory", label="_orphan_posts 恢复区")

        isolated: list[Path] = []
        target_id = str(post.post_id)
        for directory in iter_post_dirs(self.base):
            if directory == keep:
                continue
            try:
                assert_post_directory(self.base, directory)
            except ArchivePathError as exc:
                print(f"    ! {directory.name} 不是安全的真实帖子目录，跳过隔离：{exc}")
                continue
            truth = directory / "post.json"
            if not truth.exists():
                continue
            try:
                assert_physical_direct_path(
                    directory, truth, kind="file", label="旧 post.json")
                row = json.loads(truth.read_text(encoding="utf-8"))
            except (ArchivePathError, OSError, UnicodeError, json.JSONDecodeError):
                continue
            stored_id = row.get("post_id") if isinstance(row, dict) else None
            if stored_id is None or str(stored_id) != target_id:
                continue

            destination = self._available_recovery_path(orphan_root, directory.name)
            assert_physical_direct_path(
                orphan_root, destination, kind="directory", label="旧帖子隔离目标")
            orphan_root.mkdir(parents=True, exist_ok=True)
            assert_physical_direct_path(
                self.base, orphan_root, kind="directory", label="_orphan_posts 恢复区")
            shutil.move(str(directory), str(destination))
            assert_physical_direct_path(
                orphan_root, destination, kind="directory", label="旧帖子隔离目标")
            isolated.append(destination)
            print("    ! 同 post_id 的旧 truth dir 已隔离：%s -> _orphan_posts/%s"
                  % (directory.name, destination.name))
        return isolated

    def reindex(self) -> int:
        """从 `posts/*/post.json` 重建 `manifest.jsonl`，返回条数。

        **这是"文件夹与索引冲突时以文件夹为准"那条规则的执行者。**
        人删了一个文件夹、或者哪次运行崩在中途，索引就会和现实脱节；
        重建的方向永远是 posts/ → manifest，绝不反过来。

        同一 post_id 若意外存在多个 truth dir，按与 append 升级相同的质量等级
        只选一个写入索引并显式告警；目录本身不在 reindex 中删除或移动。
        顺带把 `text.txt` 也重新生成一遍（它是派生的）。
        """
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        selected: dict[str, tuple[tuple[int, int], dict, Path]] = {}
        duplicate_dirs: dict[str, list[Path]] = {}
        for d in iter_post_dirs(self.base):
            try:
                assert_post_directory(self.base, d)
            except ArchivePathError as exc:
                print(f"    ! {d.name} 不是安全的真实直属目录，跳过：{exc}")
                continue
            f = d / "post.json"
            try:
                assert_physical_direct_path(
                    d, f, kind="file", label="reindex post.json")
            except ArchivePathError as exc:
                print(f"    ! {d.name}/post.json 不是普通直属文件，跳过：{exc}")
                continue
            if not f.exists():
                continue
            try:
                row = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                print(f"    ! {d.name}/post.json 不是合法 JSON，跳过")
                continue
            error = _archive_row_error(row)
            if error:
                print(f"    ! {d.name}/post.json schema 无效（{error}），跳过")
                continue
            text_file = d / "text.txt"
            try:
                assert_physical_direct_path(
                    d, text_file, kind="file", label="reindex text.txt")
            except ArchivePathError as exc:
                print(f"    ! {d.name}/text.txt 不是普通直属文件，不写派生副本：{exc}")
            else:
                text_file.write_text(row.get("text") or "", encoding="utf-8")

            pid = row["post_id"]
            rank = _post_quality_rank(row)
            previous = selected.get(pid)
            if previous is None:
                selected[pid] = (rank, row, d)
                continue

            duplicate_dirs.setdefault(pid, [previous[2]]).append(d)
            if rank > previous[0]:
                selected[pid] = (rank, row, d)

        for pid, directories in duplicate_dirs.items():
            rank, _row, winner = selected[pid]
            quality = "完整" if rank[0] else "残缺/%d 个媒体项" % rank[1]
            losers = ", ".join(d.name for d in directories if d != winner)
            print("    ! post_id %s 有 %d 个 truth dirs；"
                  "按质量等级选择 %s [%s]。其余目录未删除，仍在 posts/：%s"
                  % (pid, len(directories), winner.name, quality, losers))

        rows = [entry[1] for entry in selected.values()]

        # 按时间正序落盘：manifest 本来不保证顺序，但重建时顺手排一下，
        # 人 `tail` 它的时候看到的就是最新的几条。
        rows.sort(key=lambda r: (r.get("created_at") or "", r.get("post_id") or ""))
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        _atomic_write_text(self.manifest, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                           label="manifest.jsonl")
        self._rows = {r["post_id"]: r for r in rows}
        return len(rows)

    def append(self, post: Post) -> bool:
        """写入 manifest，返回是否实际写了。

        重复的 post_id 默认忽略，让脚本可以反复重跑而不产生副作用。
        唯一的例外是升级：已存记录媒体不全（源响应只给了封面，或图片没下全），
        而新记录更全 —— 这时追加一条新的，读取时后写胜出。
        没有这个例外，media_complete 标记就没有意义，补全永远写不进去。
        """
        with archive_write_lock(self.base):
            self._rows = self._load_rows()
            previous = self._current_row(post)
            if previous is not None:
                self._rows[post.post_id] = previous
            if previous is not None and not (self._is_upgrade(previous, post) or self._source_changed(previous, post)):
                return False
            return self._append_locked(post, previous)

    def _append_locked(self, post: Post, previous: dict | None) -> bool:
        self.post_dir(post)
        if previous is not None and isinstance(previous.get("tags"), list):
            post.tags = list(previous["tags"])
        elif post.tags is None:
            post.tags = infer_tags(post.text)
        if previous is not None and self._source_changed(previous, post):
            old_media = previous.get("media") or []
            if previous.get("media_complete") is True and not post.media_complete:
                post.media = [Media(**item) for item in old_media]
                post.media_complete = True
            else:
                by_url = {item.get("url"): item for item in old_media if isinstance(item, dict)}
                for media in post.media:
                    if not media.local_path and media.url in by_url:
                        media.local_path = by_url[media.url].get("local_path")
        row = post.to_row()
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        # 顺序要紧：**先写文件夹，再写索引**。反过来的话，中途崩溃会留下
        # 一条指向不存在文件夹的索引记录 —— 而规则是"以文件夹为准"，
        # 那条记录会在下次 reindex 时凭空消失，且没人知道发生过什么。
        if previous is not None and self._source_changed(previous, post):
            history = self.post_dir(post) / "source_history.jsonl"
            paid_model.append_jsonl(history, {"recorded_at": datetime.now(timezone.utc).isoformat(),
                                               "source": previous},
                                    guard=lambda path: assert_physical_direct_path(
                                        path.parent, path, kind="file", label="source_history.jsonl"))
        self._write_post_files(post)
        # created_at 被补出/纠正会改变目录名。新 truth dir 已完整写成后，
        # 可恢复地隔离同 ID 的旧目录，保证 posts/ 内仍只有一个真相源。
        if previous is not None:
            self._isolate_other_truth_dirs(post, self.post_dir(post))
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        paid_model.append_jsonl(self.manifest, row, guard=lambda path: assert_physical_direct_path(
            path.parent, path, kind="file", label="manifest.jsonl"))
        self._rows[post.post_id] = row
        return True

    def should_append(self, post: Post) -> bool:
        """返回这条帖子是否会被 :meth:`append` 接受。

        下载媒体前需要先做这个判断，避免幂等重跑时重复请求 CDN；同时不能只用
        ``has(post_id)``，否则先前留下的残缺帖永远无法被后续更全的抓取补上。
        """
        old = self._current_row(post)
        return old is None or self._is_upgrade(old, post) or self._source_changed(old, post)

    @staticmethod
    def _source_changed(old: dict, new: Post) -> bool:
        if any(old.get(key) != getattr(new, key) for key in ("text", "created_at", "owner", "coauthors", "permalink")):
            return True
        if not new.media_complete:
            return False
        before = [(item.get("kind"), item.get("url")) for item in (old.get("media") or []) if isinstance(item, dict)]
        return before != [(item.kind, item.url) for item in new.media]

    @staticmethod
    def _is_upgrade(old: dict, new: Post) -> bool:
        was_incomplete = not old.get("media_complete", True)
        if not was_incomplete:
            return False
        new_complete, new_media, new_local = _post_quality_parts(new)
        if new_complete:
            return True
        _, old_media, old_local = _post_quality_parts(old)
        # 残缺 -> 残缺只能单调改进。否则“新响应多发现一张、但本次全部下载
        # 失败”会以 media_count 更大为由覆盖旧 local_path，反而丢掉恢复成果。
        return (new_media >= old_media and new_local >= old_local
                and (new_media > old_media or new_local > old_local))

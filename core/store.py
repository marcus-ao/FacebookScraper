r"""统一归档层。

三条获取路线（官方导出 / 官方 API / 浏览器拦截）产出格式各不相同，
在这里收敛为同一个 schema，下游的翻译与图像管线只认这个 schema。

归档布局（2026-08-30 起，对应实施计划的 J 组）::

    archive/<平台前缀>_<账号>/
      index.html                        全账号总览（派生，可重建）
      manifest.jsonl                    **派生索引**，可从 posts/ 重建
      _rejected.jsonl                   被丢弃的节点及原因
      _orphan_media/                    重建后无主的媒体文件
      _orphan_posts/                    被升级/replay 隔离的旧帖子目录（可恢复）
      posts/
        2026-08-25_1423_<post_id>/
          post.json                     **真相源**
          text.txt                      正文纯文本（派生，给人和设计同事看）
          text_de.txt                   德语译文副本（派生）
          01.jpg  02.jpg                原图，按帖内顺序
          media_de/                     设计同事回填的德文版图
        undated_<post_id>/              时间解析不出来的进这里，不猜
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
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from core import paid_model


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
        candidate = self.posts_dir / post_dirname(post.post_id, post.created_at)
        return assert_physical_direct_path(
            self.posts_dir, candidate, kind="directory", label="帖子目录")

    def _write_post_files(self, post: "Post") -> None:
        """写 `post.json`（真相源）与 `text.txt`（给人看的派生副本）。

        text.txt 与 post.json 里的 text 重复，是有意的：设计同事和审校人
        双击就能看，不必去读 JSON。post.json 永远是真相，
        text.txt 由 reindex 从它重新生成。
        """
        d = self.post_dir(post)
        d.mkdir(parents=True, exist_ok=True)
        assert_physical_direct_path(
            self.posts_dir, d, kind="directory", label="帖子目录")
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
        assert_physical_direct_path(
            self.posts_dir, d, kind="directory", label="帖子目录")
        media = d / f"{idx + 1:02d}{ext}"
        return assert_physical_direct_path(
            d, media, kind="file", label=f"媒体文件 {media.name}")

    def reusable_media_path(self, post: "Post", url: str) -> Path | None:
        """返回同帖、同 URL 已安全落盘的媒体路径；没有则返回 ``None``。

        残缺帖重试会重新解析出一个全新的 :class:`Post`，其中没有旧记录的
        ``local_path``。若不在下载边界把已成功的部分接回来，每次重试都会
        重复请求这些 CDN URL。只复用当前帖子预期 truth dir 下的真实普通文件；
        时间被纠正导致目录变化时不跨目录借用，避免升级隔离旧目录后留下悬空引用。
        """
        old = self._rows.get(post.post_id)
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
                parent = assert_physical_direct_path(
                    self.posts_dir, candidate.parent, kind="directory",
                    label="已归档媒体的帖子目录")
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
        for directory in sorted(self.posts_dir.iterdir(), key=lambda p: p.name):
            if directory == keep:
                continue
            try:
                assert_physical_direct_path(
                    self.posts_dir, directory, kind="directory", label="旧帖子目录")
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
        for d in sorted(self.posts_dir.iterdir()):
            try:
                assert_physical_direct_path(
                    self.posts_dir, d, kind="directory", label="reindex 帖子目录")
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
            except json.JSONDecodeError:
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
        with self.manifest.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._rows = {r["post_id"]: r for r in rows}
        return len(rows)

    def append(self, post: Post) -> bool:
        """写入 manifest，返回是否实际写了。

        重复的 post_id 默认忽略，让脚本可以反复重跑而不产生副作用。
        唯一的例外是升级：已存记录媒体不全（源响应只给了封面，或图片没下全），
        而新记录更全 —— 这时追加一条新的，读取时后写胜出。
        没有这个例外，media_complete 标记就没有意义，补全永远写不进去。
        """
        previous = self._rows.get(post.post_id)
        if not self.should_append(post):
            return False
        row = post.to_row()
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        # 顺序要紧：**先写文件夹，再写索引**。反过来的话，中途崩溃会留下
        # 一条指向不存在文件夹的索引记录 —— 而规则是"以文件夹为准"，
        # 那条记录会在下次 reindex 时凭空消失，且没人知道发生过什么。
        self._write_post_files(post)
        # created_at 被补出/纠正会改变目录名。新 truth dir 已完整写成后，
        # 可恢复地隔离同 ID 的旧目录，保证 posts/ 内仍只有一个真相源。
        if previous is not None:
            self._isolate_other_truth_dirs(post, self.post_dir(post))
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._rows[post.post_id] = row
        return True

    def should_append(self, post: Post) -> bool:
        """返回这条帖子是否会被 :meth:`append` 接受。

        下载媒体前需要先做这个判断，避免幂等重跑时重复请求 CDN；同时不能只用
        ``has(post_id)``，否则先前留下的残缺帖永远无法被后续更全的抓取补上。
        """
        old = self._rows.get(post.post_id)
        return old is None or self._is_upgrade(old, post)

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


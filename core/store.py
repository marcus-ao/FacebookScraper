"""按帖保存来源与媒体；post.json 为事实源，manifest 和文本副本从源文件派生。"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os  # noqa: F401  兼容现有故障注入测试对 core.store.os.replace 的补丁
import re
import shutil
import stat
import tempfile
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo
from uuid import uuid4
from urllib.parse import parse_qsl, urlsplit
from core import paid_model
from core.config import cfg
from core.media import image_facts


@dataclass
class Media:
    url: str
    kind: str                 # "image" | "video"
    local_path: str | None = None
    width: int | None = None
    height: int | None = None
    content_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    source_media_id: str | None = None


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
    # owner 是响应中的实际作者，account 是抓取目标；归属使用小写账号名。
    owner: str | None = None
    # 仅供展示，不参与归属判等。
    owner_name: str | None = None
    # 已接受的合作作者，同样决定帖子是否属于目标时间线。
    coauthors: list[str] = field(default_factory=list)
    # 源响应未提供全部媒体时标 False，供后续补齐。
    media_complete: bool = True
    source_media_complete: bool | None = None  # 平台是否给齐媒体列表，独立于下载结果
    source_media_count: int | None = None  # 只有来源明确声明总数或列表完整时才填写
    folder_name: str | None = None  # 创建时固定；修改正文/标签不重命名
    tags: list[str] | None = None   # None 尚未预填；[] 是人工明确清空
    tags_origin: str | None = None
    archived_at: str | None = None
    # Facebook 作者对象中的 ID/主页 URL；包含用于归一化的同身份响应证据。
    owner_evidence: list[dict[str, str]] = field(default_factory=list)
    owner_conflict: bool = False

    def to_row(self) -> dict:
        d = asdict(self)
        return d


def same_media_locator(old: dict, new: Media) -> bool:
    """只有同一媒体的签名刷新可复用；路径、变换参数或媒体身份变化仍需核验。"""
    if old.get('kind') != new.kind:
        return False
    if old.get('source_media_id') and new.source_media_id and old['source_media_id'] != new.source_media_id:
        return False
    if old.get('url') == new.url:
        return True
    if not new.source_media_id or old.get('source_media_id') != new.source_media_id:
        return False
    def locator(url):
        parts = urlsplit(url or '')
        query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k not in ('oh', 'oe') and not k.startswith('_nc_'))
        return parts.scheme, parts.netloc, parts.path, query
    return locator(old.get('url')) == locator(new.url)


def signed_url_expiry(url: str | None) -> datetime | None:
    """Meta CDN 图片地址的失效时刻；没有 `oe` 参数时返回 None（失效时间未知）。

    ⚠️ `oe` 是**十六进制**的 Unix 秒。按十进制解会得到四千年后的日期，于是任何过期地址
    都被判成"还没过期"，归档里那张下不回来的图就永远说不清为什么下不回来。
    参数名区分大小写，签名改一个字符就失效，所以不能靠改 `oe` 续命，只能重新取地址。
    """
    if not url:
        return None
    raw = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True)).get('oe')
    try:
        return datetime.fromtimestamp(int(raw, 16), timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def refresh_signed_media_urls(post: "Post", fresh: "Post") -> int:
    """用详情里的新地址替换同一张媒体已过期的签名地址；返回换掉的张数。

    只认逐位对齐的同一张：数量、顺序、kind、source_media_id 和**文件名段**都要一致。
    ⚠️ 不能整条 URL 比——Meta 的边缘主机和 oh/oe/_nc_* 每次都变，稳定的只有文件名；
    也不能放宽到文件名之外，`stp=` 这类尺寸/裁剪参数变了就是另一张派生图，换过去等于
    悄悄把归档内容换成别的尺寸。对不齐就整篇返回 0，宁可这次恢复失败。
    """
    if len(fresh.media) != len(post.media):
        return 0

    def identity(url):
        parts = urlsplit(url or '')
        return (PurePosixPath(parts.path).name,
                sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                       if key not in ('oh', 'oe') and not key.startswith('_nc_')))

    for old, new in zip(post.media, fresh.media):
        if old.kind != new.kind or not new.url or identity(old.url) != identity(new.url):
            return 0
        if old.source_media_id and new.source_media_id and old.source_media_id != new.source_media_id:
            return 0
    changed = 0
    for old, new in zip(post.media, fresh.media):
        if old.url != new.url:
            old.url = new.url
            changed += 1
    return changed


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
    """将异常 post_id 转为稳定 SHA-256 目录名；业务记录仍保留原 ID。"""
    raw = str(post_id)
    reserved_stem = raw.split(".", 1)[0].upper()
    if (_SAFE_POST_ID.fullmatch(raw)
            and not raw.endswith(".")
            and reserved_stem not in _WINDOWS_RESERVED):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]
    # 生成名以正常 ID 白名单之外的 ~ 开头，避免命名碰撞。
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
    """完整记录优先；残缺记录按已落盘媒体数、已知媒体数排序，保证补齐进度单调。"""
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
    """核验真实直属路径；拒绝 symlink、junction、reparse point 和文件硬链接，允许待创建路径。"""
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
    """同目录原子写入；替换前再次核验目标，避免被换成链接。"""
    def guard(candidate: Path, role: str) -> None:
        assert_physical_direct_path(
            candidate.parent, candidate, kind="file",
            label=label if role == "target" else "%s 临时文件" % label)

    paid_model.atomic_write_text(path, text, guard=guard, newline="")


def post_dirname(post_id: str, created_at: str | None) -> str:
    """生成日期在前的帖子目录名；无有效时间时使用 undated。"""
    ts = created_at or ""
    safe_id = _safe_post_id_component(post_id)
    # 只接受归一化时间格式；未知时间保留 undated。
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", ts):
        return f"{ts[:10]}_{ts[11:13]}{ts[14:16]}_{safe_id}"
    return f"undated_{safe_id}"


def account_dirs(archive_root: Path, only: str | None = None) -> list[Path]:
    """枚举包含 manifest.jsonl 的账号目录。"""
    if not archive_root.exists():
        return []
    dirs = sorted(p for p in archive_root.iterdir()
                  if p.is_dir() and (p / "manifest.jsonl").exists())
    if only:
        dirs = [p for p in dirs if p.name == only]
    return dirs


def source_text_digest(text: str) -> str:
    """统一来源文字指纹：strip 后 UTF-8 的 SHA-256。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _folder_month(folder_name: str) -> str:
    return folder_name[:7] if re.match(r"^\d{4}-\d{2}-\d{2}_", folder_name) else "undated"


def _archive_time(created_at: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
        if moment.tzinfo is not None and moment.utcoffset() is not None:
            return moment.astimezone(ZoneInfo('Asia/Shanghai'))
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def archive_month(source: dict | str | None) -> str:
    """已有目录月份保持稳定；新原帖按北京时间归档，未知日期不猜测。"""
    if isinstance(source, dict):
        name = source.get('folder_name')
        if isinstance(name, str) and name:
            return _folder_month(name)
        source = source.get('created_at')
    moment = _archive_time(source)
    return moment.strftime('%Y-%m') if moment else 'undated'


def _safe_folder_name(value: object) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._~\-]+", value)
            or value.endswith(".") or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
        raise ArchivePathError("folder_name 必须是安全的单个目录名")
    return value


UNCATEGORISED = "未分类"
# tag 母目录名上限。月份 7 + tag 20 + folder_name 最长 ~60，整条路径仍在 Windows 260 以内。
TAG_COMPONENT_MAX = 20


def tag_component(value: object) -> str:
    """主 tag 的目录名。与云盘同名规则，但另守本地的保留名与长度上限。

    ⚠️ 不能复用 `_safe_folder_name()`：它只允许 ASCII，而 `未分类` 和业务自定义 tag 都可能不是。
    """
    cleaned = re.sub(r'[\s/\\<>:"|?*\x00-\x1f]+', "-", str(value or "")).strip(" .-")
    if (not cleaned or cleaned in {".", ".."}
            or cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
        return UNCATEGORISED
    if len(cleaned) > TAG_COMPONENT_MAX:
        return cleaned[:TAG_COMPONENT_MAX - 7] + "-" + hashlib.sha256(
            cleaned.encode("utf-8")).hexdigest()[:6]
    return cleaned


def primary_tag_folder(indexed: object) -> str:
    """落点只由主 tag 决定；目录树表达不了一篇帖归两个产品，其余 tag 留在字段里。"""
    tags = indexed.get("tags") if isinstance(indexed, dict) else None
    if not isinstance(tags, list):
        return UNCATEGORISED
    first = next((tag for tag in tags if isinstance(tag, str) and tag.strip()), None)
    return tag_component(first) if first else UNCATEGORISED


def _stable_post_id_component(post_id: str) -> str:
    safe_id = _safe_post_id_component(post_id)
    if len(safe_id) > 48:
        return "~id_" + hashlib.sha256(str(post_id).encode("utf-8", errors="surrogatepass")).hexdigest()[:24]
    return safe_id


def post_folder_matches_id(folder_name: str, post_id: str) -> bool:
    """目录 ID 后缀归属检查；只有 2026-09-15 之前建的目录才把 ID 写进名字。"""
    try:
        name = _safe_folder_name(folder_name)
    except ArchivePathError:
        return False
    return any(name.endswith("_" + component) for component in {
        _safe_post_id_component(post_id), _stable_post_id_component(post_id)})


def post_folder_matches(folder_name: str, row: object) -> bool:
    """目录归属检查。新名字不含 post_id，按建档时固定的 `folder_name` 比；
    旧目录没有这个字段，回退到 ID 后缀，迁移中断时两种都要认。"""
    recorded = row.get("folder_name") if isinstance(row, dict) else None
    if isinstance(recorded, str) and recorded and folder_name == recorded:
        return True
    return post_folder_matches_id(
        folder_name, str((row.get("post_id") if isinstance(row, dict) else "") or ""))


_PLATFORM_COMPONENT = {"instagram": "IG", "facebook": "FB"}
SLUG_MAX = 30
# 文件夹名里的产品段上限。父目录已经是完整主 tag，这里只求业务一眼认得出是哪个产品。
PRODUCT_COMPONENT_MAX = 12


def _name_component(value: object, limit: int, *, lower: bool = False) -> str:
    """转成文件夹名可用的 ASCII 段。中文 tag 和纯 emoji 正文转不出东西，返回空串由调用方省略。"""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", text.lower() if lower else text).strip("-")[:limit].rstrip("-")


def _directory_post_id(directory: Path) -> str:
    """只读出身份用于恢复定位；坏的或读不出的目录跳过，由调用方按找不到处理。"""
    try:
        row = json.loads((directory / "post.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(row.get("post_id") or "") if isinstance(row, dict) else ""


def _folder_name_taken(account_dir: Path, name: str) -> bool:
    posts = Path(account_dir) / "posts"
    month = posts / _folder_month(name)
    return ((posts / name).exists() or (month / name).exists()
            or bool(_existing_tagged_dirs(Path(account_dir), month, name)))


def _new_folder_name(post: Post, account_dir: Path | None = None) -> str:
    """业务可读的四段名：时间戳 · 平台 · 产品 · 摘要。

    ⚠️ 日期必须留在最前面。`_folder_month()`、`tools/layout.py` 和 `localize/images.py`
    都从名字开头反推月份，把平台提到最前会同时打断这三处，而且不报错。

    产品段记的是**建档时**的主 tag。人工改 tag 只移动目录、不改名字——重命名会让
    每个媒体的 `local_path` 和已发出的引用一起失效（F2-2）。所以改过分类的帖子，
    名字里的产品会和父目录不一致，以父目录为准。
    """
    moment = _archive_time(post.created_at)
    tags = post.tags if isinstance(post.tags, list) else []
    parts = (moment.strftime('%Y-%m-%d_%H%M') if moment else 'undated',
             _PLATFORM_COMPONENT.get(post.platform, ''),
             _name_component(tags[0] if tags else '', PRODUCT_COMPONENT_MAX),
             _name_component(post.text, SLUG_MAX, lower=True))
    base = _safe_folder_name("_".join(part for part in parts if part))
    if account_dir is None:
        return base
    # 名字里不再有 post_id，无正文帖和 undated 帖可能真的重名；只在撞上时加序号。
    name, ordinal = base, 1
    while _folder_name_taken(account_dir, name):
        ordinal += 1
        name = _safe_folder_name("%s-%d" % (base, ordinal))
    return name


def infer_tags(text: str, models: list[str] | None = None) -> list[str]:
    """明确的型号 hashtag 优先、正文补充；别名映射不改写原文。"""
    configured = models is None
    if models is None:
        models = cfg().get("image", "keep_verbatim", {}).get("models", [])
    choices = sorted({model for model in models if isinstance(model, str) and model.strip()},
                     key=lambda model: (-len(model), model))
    canonical = {model.casefold(): model for model in choices}
    if configured:
        aliases = cfg().get('storage', 'product_aliases', {})
        if not isinstance(aliases, dict):
            raise ValueError('storage.product_aliases 必须是型号到别名数组的映射')
        explicit = {}
        for product, names in aliases.items():
            if (not isinstance(product, str) or not product.strip() or not isinstance(names, list)
                    or any(not isinstance(name, str) or not name.strip().lstrip('#') for name in names)):
                raise ValueError('产品名与别名必须是非空字符串')
            for alias in [product, *names]:
                key = alias.strip().lstrip('#').casefold()
                if key in explicit and explicit[key] != product:
                    raise ValueError('同一产品别名不能指向多个型号')
                explicit[key] = product
        canonical.update(explicit)
    if not canonical:
        return []
    tagged = []
    body = list(text)
    for match in re.finditer(r'(?<![\w#])#\w+', text):
        product = canonical.get(match.group()[1:].casefold())
        if product:
            tagged.append(product)
        body[match.start():match.end()] = ' ' * len(match.group())
    names = sorted(canonical, key=lambda value: (-len(value), value))
    pattern = re.compile(r'(?<!\w)(?:' + '|'.join(map(re.escape, names)) + r')(?!\w)', re.IGNORECASE)
    return list(dict.fromkeys([*tagged, *(canonical[match.group().casefold()]
                                         for match in pattern.finditer(''.join(body)))]))


def _is_month_name(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}|undated", value))


def assert_post_directory(account_dir: Path, directory: Path) -> Path:
    """校验旧平铺、月份层级或新的月份/tag 层级；逐层拒绝链接，不能仅校验最末一层。"""
    account_dir, directory = Path(account_dir), Path(directory)
    assert_physical_direct_path(account_dir.parent, account_dir, kind="directory", label="账号归档目录")
    posts = assert_physical_direct_path(account_dir, account_dir / "posts", kind="directory", label="posts 根目录")
    if directory.parent != posts:
        tag = directory.parent
        month = tag if _is_month_name(tag.name) else tag.parent
        if month.parent != posts or not _is_month_name(month.name):
            raise ArchivePathError("帖子目录必须位于 posts/、posts/<月份>/ 或 posts/<月份>/<tag>/ 内")
        assert_physical_direct_path(posts, month, kind="directory", label="归档月份目录")
        if tag is not month:
            assert_physical_direct_path(month, tag, kind="directory", label="归档标签目录")
    return assert_physical_direct_path(directory.parent, directory, kind="directory", label="帖子目录")


def iter_post_dirs(account_dir: Path):
    """只遍历有效的布局层级，不递归进入恢复区或任意嵌套目录。

    帖子目录带 `post.json`，tag 母目录不带——据此区分，不必重算 tag 名。
    """
    account_dir = Path(account_dir)
    posts = assert_physical_direct_path(account_dir, account_dir / "posts", kind="directory", label="posts 根目录")
    if not posts.exists():
        return
    for child in sorted(posts.iterdir()):
        try:
            assert_physical_direct_path(posts, child, kind="directory", label="帖子或月份目录")
            if not _is_month_name(child.name):
                yield child
                continue
            for entry in sorted(child.iterdir()):
                try:
                    assert_physical_direct_path(child, entry, kind="directory", label="帖子或标签目录")
                    if (entry / "post.json").exists():
                        yield assert_post_directory(account_dir, entry)
                        continue
                    for directory in sorted(entry.iterdir()):
                        try:
                            yield assert_post_directory(account_dir, directory)
                        except ArchivePathError as exc:
                            print("    ! 跳过不安全的帖子目录：%s" % exc)
                except (ArchivePathError, NotADirectoryError, OSError) as exc:
                    print("    ! 跳过不安全的帖子目录：%s" % exc)
        except ArchivePathError as exc:
            print("    ! 跳过不安全的归档目录：%s" % exc)


def _existing_tagged_dirs(account_dir: Path, month: Path, name: str) -> list[Path]:
    """按 folder_name 在月份下的各 tag 目录里找现存落点。

    不按当前 `tags[0]` 直接拼路径：索引可能落后于一次改 tag 的移动，
    照着旧 tag 拼会得到一个不存在的路径，把已归档的帖子报成不存在。
    """
    if not month.is_dir():
        return []
    found = []
    for entry in sorted(month.iterdir()):
        candidate = entry / name
        if entry.is_dir() and not (entry / "post.json").exists() and candidate.is_dir():
            found.append(assert_post_directory(account_dir, candidate))
    return found


def post_directory(account_dir: Path, indexed: dict) -> Path:
    """统一定位稳定 folder_name/月目录/tag 目录与旧平铺目录；只读，不创建目录。"""
    account_dir = Path(account_dir)
    posts = account_dir / "posts"
    folder_name = indexed.get("folder_name")
    if folder_name is not None:
        name = _safe_folder_name(folder_name)
        month = posts / _folder_month(name)
        tagged = _existing_tagged_dirs(account_dir, month, name)
        if len(tagged) > 1:
            raise ArchivePathError("同一 folder_name 在多个标签目录下同时存在，请先核对归档")
        monthly = assert_post_directory(account_dir, month / name)
        legacy = assert_post_directory(account_dir, posts / name)
        if sum(map(bool, (tagged, monthly.exists(), legacy.exists()))) > 1:
            raise ArchivePathError("同一 folder_name 同时存在新旧目录，请先核对归档")
        if tagged:
            return tagged[0]
        if legacy.exists() or monthly.exists():
            return legacy if legacy.exists() else monthly
        return assert_post_directory(
            account_dir, month / primary_tag_folder(indexed) / name)
    name = _safe_folder_name(post_dirname(str(indexed.get("post_id") or ""), indexed.get("created_at")))
    legacy = assert_post_directory(account_dir, posts / name)
    if legacy.exists():
        return legacy
    # 迁移中断或索引落后时寻找真实目录，不能重算正文摘要——摘要变了就找不回原目录。
    # 2026-09-15 起名字里不再有 post_id，先按旧后缀找，找不到再打开 post.json 认。
    # 这一趟是慢的，但它只在 folder_name 丢失时走；认不出来的后果是把已归档的帖子报成不存在。
    post_id = str(indexed.get("post_id") or "")
    matches = [directory for directory in iter_post_dirs(account_dir)
               if post_folder_matches_id(directory.name, post_id)]
    if not matches and post_id:
        matches = [directory for directory in iter_post_dirs(account_dir)
                   if _directory_post_id(directory) == post_id]
    if len(matches) > 1:
        raise ArchivePathError("同一 post_id 有多个帖子目录，请先重建并核对索引")
    return matches[0] if matches else legacy


def planned_post_directory(account_dir: Path, post: Path | Post) -> Path:
    """新帖/重放的无写盘路径规划；固定对象元信息后执行阶段使用同一路径。"""
    if post.folder_name is None:
        existing = post_directory(account_dir, post.to_row())
        if existing.exists():
            post.folder_name = existing.name
        if (existing / "post.json").exists():
            row, _directory = read_post_truth(account_dir, post.to_row())
            if isinstance(row.get("tags"), list):
                post.tags = list(row["tags"])
    if post.tags is None:
        # 必须在算落点和文件夹名之前定下来：媒体下载先于 append 发生，晚一步会让
        # 图片落进 未分类/ 而 post.json 落进 <tag>/，名字里的产品段也会跟着丢。
        post.tags = infer_tags(post.text)
        post.tags_origin = 'auto'
    elif post.tags_origin is None:
        post.tags_origin = 'manual'
    if post.folder_name is None:
        post.folder_name = _new_folder_name(post, account_dir)
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


def resolve_media_path(account_dir: Path, source: dict, media: dict) -> Path | None:
    """通过稳定帖子目录解析当前/历史媒体；分类移动不改写历史证据。"""
    local = media.get('local_path')
    if local is None or local == '':
        return None
    if not isinstance(local, str):
        raise ArchivePathError('媒体路径必须是相对路径')
    relative = PurePosixPath(local.replace('\\', '/'))
    if relative.is_absolute() or '..' in relative.parts or ':' in local or len(relative.parts) < 3:
        raise ArchivePathError('媒体路径超出帖子归档')
    account_dir = Path(account_dir)
    directory = post_directory(account_dir, source)
    if not directory.exists():
        directory = post_directory(account_dir, dict(source, folder_name=None))
    previous_parent = account_dir.joinpath(*relative.parts[:-1])
    assert_post_directory(account_dir, previous_parent)
    if (previous_parent != directory and
            (not post_folder_matches(previous_parent.name, source)
             or not post_folder_matches(directory.name, source))):
        raise ArchivePathError('媒体路径不属于同一稳定帖子目录')
    if (directory / 'post.json').exists():
        read_post_truth(account_dir, dict(source, folder_name=directory.name))
    return assert_physical_direct_path(directory, directory / relative.name, kind='file', label='原图')


# ⚠️ 与 localize/images.py 的写入侧同源：Windows 上审校台的图片预览会短暂占住原图，
# 读到的是 WinError 32/33 而不是坏文件。这里不重试的后果不是报错，是把一张好图判成
# "原图损坏" 并把整篇推进人工队列，而下一次读它又是好的。
_SHARING_VIOLATION = {32, 33}
MEDIA_READ_RETRY_SECONDS = 2.0

# storage_status 只有 saved/missing/corrupt/metadata_only 四个值：SQLite 的 post_media 列、
# 展示索引签名和审校台详情都按它取，不要加值。细分原因走并列的 storage_detail，
# 它决定这一张能不能本地修好、还是必须重新取源、还是要人裁决。
MEDIA_STORAGE_DETAILS = {  # 逐条都是能直接接在"第 N 张"后面的短句。
    'ok': '已按字节核验',
    'video_metadata_only': '视频按设计只留元数据，不算下载失败',
    'never_downloaded': '从未下载成功，归档里没有本地路径',
    'file_absent': '归档记了本地路径，但文件已不在',
    'undecodable': '文件在，但不是能完整解码的图片（截断或格式异常）',
    'digest_mismatch': '能解码，但 SHA-256 与归档记录不符',
    'size_mismatch': '能解码，但字节数与归档记录不符',
    'changed_while_reading': '读取期间文件一直在变',
    'locked': '被其它进程占用，限时重试后仍读不到',
    'read_error': '读取时发生 I/O 错误',
    'path_rejected': '本地路径不在该帖的归档目录内',
}


def _read_stable_bytes(path: Path) -> bytes | None:
    """读到一份自洽的字节。返回 None 表示重试窗口内文件一直在变——那是正在被写，不是损坏。"""
    deadline = time.monotonic() + MEDIA_READ_RETRY_SECONDS
    while True:
        try:
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in _SHARING_VIOLATION or time.monotonic() >= deadline:
                raise
            time.sleep(0.025)
            continue
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and len(raw) == after.st_size):
            return raw
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.025)


def media_storage_info(account_dir: Path, source: dict) -> list[dict]:
    """只读观察每张原图实际字节，不以路径或 manifest 存在代替完整性。

    每项额外带 ``storage_detail``（取值见 ``MEDIA_STORAGE_DETAILS``）。缺这一层的后果
    真实发生过：缺图、截断、哈希不符、路径越界和一次瞬时占用会报出同一句话，
    运营看不出该重新取源、该核对档案还是什么都不用做。
    """
    result = []
    for ordinal, media in enumerate(source.get('media') or []):
        item = dict(media, ordinal=ordinal, source_url=media.get('url'))
        item.update(content_type=None, byte_size=None, sha256=None,
                    storage_status='missing', storage_detail='never_downloaded')
        if media.get('kind') == 'video':
            item.update(storage_status='metadata_only', storage_detail='video_metadata_only')
            result.append(item)
            continue
        try:
            path = resolve_media_path(account_dir, source, media)
            if path is not None:
                item['local_path'] = path.relative_to(account_dir).as_posix()
                item['storage_detail'] = 'file_absent'
            if path is not None and path.exists():
                raw = _read_stable_bytes(path)
                if raw is None:
                    item['storage_status'] = 'corrupt'
                    item['storage_detail'] = 'changed_while_reading'
                else:
                    digest = hashlib.sha256(raw).hexdigest()
                    item.update(byte_size=len(raw), sha256=digest, storage_status='corrupt')
                    facts = image_facts(raw)
                    if not facts:
                        item['storage_detail'] = 'undecodable'
                    elif media.get('sha256') and media['sha256'] != digest:
                        item['storage_detail'] = 'digest_mismatch'
                    elif media.get('byte_size') is not None and media['byte_size'] != len(raw):
                        item['storage_detail'] = 'size_mismatch'
                    else:
                        item.update(facts, storage_status='saved', storage_detail='ok')
        except ArchivePathError:
            item.update(content_type=None, byte_size=None, sha256=None,
                        storage_status='corrupt', storage_detail='path_rejected')
        except OSError as exc:
            item.update(content_type=None, byte_size=None, sha256=None, storage_status='corrupt',
                        storage_detail='locked' if getattr(exc, 'winerror', None) in _SHARING_VIOLATION
                        else 'read_error')
        result.append(item)
    return result


def _tag_destination(account_dir: Path, directory: Path, row: dict) -> Path:
    wanted = primary_tag_folder({"tags": row.get("tags")})
    if directory.parent.name != wanted and (
            _is_month_name(directory.parent.name) or _is_month_name(directory.parent.parent.name)):
        month = directory.parent if _is_month_name(directory.parent.name) else directory.parent.parent
        return assert_post_directory(account_dir, month / wanted / directory.name)
    return directory


def remap_archive_paths(value, old_prefix: str, new_prefix: str, *, path_value: bool = False):
    """只重定位明确的归档路径字段；正文、URL 与历史字节保持原样。"""
    if isinstance(value, dict):
        return {key: remap_archive_paths(item, old_prefix, new_prefix,
                                        path_value=key in {'local_path', 'out_path', 'source_rel'})
                for key, item in value.items()}
    if isinstance(value, list):
        return [remap_archive_paths(item, old_prefix, new_prefix, path_value=path_value) for item in value]
    if path_value and isinstance(value, str):
        normalized = value.replace('\\', '/')
        if normalized.startswith(old_prefix + '/'):
            return new_prefix + normalized[len(old_prefix):]
    return value


def _append_archive_record(account_dir: Path, name: str, row: dict) -> None:
    paid_model.append_jsonl(account_dir / name, row, guard=lambda path: assert_physical_direct_path(
        account_dir, path, kind='file', label=name))


def _complete_tag_move(account_dir: Path, event: dict) -> None:
    before = assert_post_directory(account_dir, account_dir / event['source'])
    target = assert_post_directory(account_dir, account_dir / event['target'])
    if (before.name != target.name or not post_folder_matches(target.name, event['row'])
            or _tag_destination(account_dir, before, event['row']) != target):
        raise ArchivePathError('分类移动记录的帖子或月份不一致')
    if before != target and before.exists():
        if target.exists():
            raise ArchivePathError('分类移动源与目标同时存在，请先核对归档')
        truth = assert_physical_direct_path(before, before / 'post.json', kind='file', label='移动源帖')
        raw = truth.read_bytes()
        if hashlib.sha256(raw).hexdigest() != event['before_sha256']:
            raise ArchiveRevisionConflict('移动开始之后源帖发生变化，保留移动记录等待核对')
        intended = remap_archive_paths(dict(json.loads(raw), tags=event['row'].get('tags'), tags_origin=event['row'].get('tags_origin')),
                                      event['source'], event['target'])
        if intended != event['row']:
            raise ArchiveRevisionConflict('分类移动计划含来源内容变化，未移动或覆盖原帖')
        target.parent.mkdir(parents=True, exist_ok=True)
        assert_post_directory(account_dir, target)
        before.rename(target)
    truth = assert_physical_direct_path(target, target / 'post.json', kind='file', label='移动源帖')
    raw = truth.read_bytes()
    if hashlib.sha256(raw).hexdigest() == event['before_sha256']:
        _atomic_write_text(truth, json.dumps(event['row'], ensure_ascii=False, indent=2), label='post.json')
    elif json.loads(raw) != event['row']:
        raise ArchiveRevisionConflict('移动后的源帖版本不符，未覆盖内容')
    images = assert_physical_direct_path(account_dir, account_dir / 'images_de.jsonl', kind='file', label='图片归属账本')
    if images.exists():
        raw = images.read_bytes()
        if raw and not raw.endswith(b'\n'):
            raise ArchivePathError('图片归属账本尚未写完，稍后恢复分类移动')
        entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
        seen = {json.dumps(item, sort_keys=True) for item in entries}
        for item in entries:
            updated = remap_archive_paths(item, event['source'], event['target'])
            if updated == item:
                continue
            updated['tag_move_operation_id'] = event['operation_id']
            signature = json.dumps(updated, sort_keys=True)
            if signature not in seen:
                _append_archive_record(account_dir, 'images_de.jsonl', updated)
                seen.add(signature)
    _append_archive_record(account_dir, 'manifest.jsonl', event['row'])
    _append_archive_record(account_dir, 'tag_moves.jsonl', dict(event, status='completed'))
    if before != target and not _is_month_name(before.parent.name):
        with suppress(OSError):
            before.parent.rmdir()


def _recover_tag_moves_locked(account_dir: Path) -> int:
    path = assert_physical_direct_path(account_dir, account_dir / 'tag_moves.jsonl', kind='file', label='分类移动账本')
    if not path.exists():
        return 0
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ArchivePathError('分类移动记录未写完，请先核对，不能自动重置')
    events = {}
    try:
        for line in raw.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if (not isinstance(event, dict) or event.get('status') not in {'started', 'completed'}
                    or not re.fullmatch(r'[a-f0-9]{32}', event.get('operation_id', ''))
                    or not re.fullmatch(r'[a-f0-9]{64}', event.get('before_sha256', ''))
                    or not isinstance(event.get('source'), str) or not isinstance(event.get('target'), str)
                    or _archive_row_error(event.get('row'))
                    or event.get('post_id') != event['row']['post_id']):
                raise ValueError('invalid move event')
            events[event['operation_id']] = event
    except (ValueError, KeyError, TypeError) as exc:
        raise ArchivePathError('分类移动记录损坏，请先核对，不能自动重置') from exc
    pending = [event for event in events.values() if event['status'] == 'started']
    for event in pending:
        _complete_tag_move(account_dir, event)
    return len(pending)


def recover_tag_moves(account_dir: Path) -> int:
    """显式恢复中断的分类移动；普通查询不调用此写入入口。"""
    account_dir = Path(account_dir)
    with archive_write_lock(account_dir):
        return _recover_tag_moves_locked(account_dir)


def update_post_tags(account_dir: Path, indexed: dict, tags: list[str], *,
                      expected_tags=_UNSET, expected_source_sha256: str | None = None) -> dict:
    """人工标签只写源帖；与抓取共用锁，并在锁内核对页面看到的版本。"""
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("tags 必须是非空字符串的数组；不分类时可保存空数组")
    normalized = list(dict.fromkeys(tag.strip() for tag in tags))
    account_dir = Path(account_dir)
    with archive_write_lock(account_dir):
        _recover_tag_moves_locked(account_dir)
        current, directory = read_post_truth(account_dir, indexed)
        if expected_tags is not _UNSET and (current.get("tags") or []) != expected_tags:
            raise ArchiveRevisionConflict("标签已有新版本，请重新载入后保存")
        source_hash = source_text_digest(current["text"])
        if expected_source_sha256 is not None and source_hash != expected_source_sha256:
            raise ArchiveRevisionConflict("源帖已更新，请重新载入后保存标签")
        return _save_tagged_row(account_dir, directory, dict(current, tags=normalized, tags_origin='manual'))


def _save_tagged_row(account_dir: Path, directory: Path, row: dict) -> dict:
    """调用方持账号锁；当前源帖可已更新，但移动意图必须先于目录移动。"""
    target = _tag_destination(account_dir, directory, row)
    if target == directory:
        _atomic_write_text(directory / 'post.json', json.dumps(row, ensure_ascii=False, indent=2), label='post.json')
        _append_archive_record(account_dir, 'manifest.jsonl', row)
    else:
        source_rel, target_rel = directory.relative_to(account_dir).as_posix(), target.relative_to(account_dir).as_posix()
        row = remap_archive_paths(row, source_rel, target_rel)
        event = {'operation_id': uuid4().hex, 'status': 'started', 'post_id': row['post_id'],
                 'source': source_rel, 'target': target_rel, 'row': row,
                 'before_sha256': hashlib.sha256((directory / 'post.json').read_bytes()).hexdigest()}
        _append_archive_record(account_dir, 'tag_moves.jsonl', event)
        _complete_tag_move(account_dir, event)
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
        """写入 post.json 事实源及可重建的 text.txt 副本。"""
        d = self.post_dir(post)
        d.mkdir(parents=True, exist_ok=True)
        assert_post_directory(self.base, d)
        post_json = assert_physical_direct_path(
            d, d / "post.json", kind="file", label="post.json")
        text_file = assert_physical_direct_path(
            d, d / "text.txt", kind="file", label="text.txt")
        # 先核验两个目标再写入，避免部分更新。
        _atomic_write_text(
            post_json, json.dumps(post.to_row(), ensure_ascii=False, indent=2),
            label="post.json")
        text_file.write_text(post.text or "", encoding="utf-8")

    def _load_rows(self) -> dict[str, dict]:
        """读取追加式 manifest；同一 post_id 取最后一条。"""
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
        """按帖子及身份判据去重追加；新冲突证据不能被旧拒绝记录掩盖。"""
        if not rows:
            return 0
        path = self.base / "_rejected.jsonl"
        assert_physical_direct_path(
            self.base, path, kind="file", label="_rejected.jsonl")
        def identity_key(row):
            if not isinstance(row, dict) or not row.get('post_id'):
                return ''
            return json.dumps({key: row.get(key) for key in (
                'post_id', 'owner', 'coauthors', 'owner_evidence', 'reason', 'expected_owner')},
                sort_keys=True, ensure_ascii=False)

        seen: set[str] = set()
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(identity_key(json.loads(line)))
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        added = 0
        assert_physical_direct_path(
            self.base, path, kind="file", label="_rejected.jsonl")
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                key = identity_key(row)
                if not key or key in seen:
                    continue
                seen.add(key)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                added += 1
        return added

    def media_path(self, post: "Post", idx: int, content_type: str | None) -> Path:
        """创建帖子目录并返回按顺序补零的媒体路径。"""
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

    def save_media(self, post: Post, idx: int, data: bytes, facts: dict) -> Path:
        """下载结束后持锁重新定位，完整临时文件落盘后再暴露原图路径。"""
        with archive_write_lock(self.base):
            _recover_tag_moves_locked(self.base)
            self._rows = self._load_rows()
            target = self.media_path(post, idx, facts['content_type'])
            if target.exists() and target.read_bytes() != data:
                target = target.with_name(f'{idx + 1:02d}_{facts["sha256"][:16]}{target.suffix}')
                revision = 1
                while True:
                    assert_physical_direct_path(target.parent, target, kind='file', label='版本原图落点')
                    if not target.exists() or target.read_bytes() == data:
                        break
                    target = target.with_name(f'{idx + 1:02d}_{facts["sha256"][:16]}_{revision}{target.suffix}')
                    revision += 1
            assert_physical_direct_path(target.parent, target, kind='file', label='原图落点')
            if not target.exists():
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.download-', suffix='.tmp', delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    assert_post_directory(self.base, target.parent)
                    assert_physical_direct_path(target.parent, target, kind='file', label='原图落点')
                    temporary.rename(target)
                    temporary = None
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            media = post.media[idx]
            media.local_path = target.relative_to(self.base).as_posix()
            for key, value in facts.items():
                setattr(media, key, value)
            return target

    def reusable_media(self, post: Post, url: str) -> tuple[Path, dict] | None:
        """复用须匹配已有字节证据；同时返回已验证元信息，调用方不重新认可被改写的文件。"""
        with archive_write_lock(self.base):
            old = self._current_row(post)
            if not isinstance(old, dict):
                return None
            candidate = next((m for m in post.media if m.url == url), None)
            for item in old.get('media') or []:
                if not isinstance(item, dict) or candidate is None or not same_media_locator(item, candidate):
                    continue
                try:
                    path = resolve_media_path(self.base, old, item)
                    if path is None:
                        continue
                    raw = path.read_bytes()
                except (ArchivePathError, OSError):
                    continue
                facts = image_facts(raw)
                if (facts and (not item.get('sha256') or item['sha256'] == facts['sha256'])
                        and (item.get('byte_size') is None or item['byte_size'] == facts['byte_size'])):
                    return path, facts
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
        """新 post.json 写成后，将同 ID 的旧目录移入可恢复的 _orphan_posts。"""
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
        """从 post.json 重建 manifest 与文本副本；重复目录按质量选取并告警，不移动或删除源目录。"""
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

        rows.sort(key=lambda r: (r.get("created_at") or "", r.get("post_id") or ""))
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        _atomic_write_text(self.manifest, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                           label="manifest.jsonl")
        self._rows = {r["post_id"]: r for r in rows}
        return len(rows)

    def append(self, post: Post) -> bool:
        """追加新帖或更完整的记录，返回是否写入；重复内容忽略。"""
        with archive_write_lock(self.base):
            _recover_tag_moves_locked(self.base)
            self._rows = self._load_rows()
            previous = self._current_row(post)
            if previous is not None:
                self._rows[post.post_id] = previous
            locator_refresh = bool(previous and post.media_complete and
                [m.get('url') for m in previous.get('media', [])] != [m.url for m in post.media])
            if previous is not None and not (self._is_upgrade(previous, post) or self._source_changed(previous, post)
                                             or locator_refresh):
                return False
            return self._append_locked(post, previous)

    def _append_locked(self, post: Post, previous: dict | None) -> bool:
        # 人工改过的 tag 优先于重新推断，且要早于落点计算。
        if previous is not None and isinstance(previous.get("tags"), list):
            post.tags_origin = previous.get('tags_origin') or 'legacy'
            post.tags = infer_tags(post.text) if post.tags_origin == 'auto' else list(previous['tags'])
        post.archived_at = (previous.get('archived_at') if previous is not None
                            else datetime.now(timezone.utc).isoformat())
        directory = self.post_dir(post)
        for media in post.media:
            if media.local_path and PurePosixPath(media.local_path.replace('\\', '/')).parent.name == directory.name:
                media.local_path = resolve_media_path(self.base, post.to_row(), asdict(media)).relative_to(self.base).as_posix()
        if previous is not None and self._source_changed(previous, post):
            old_media = previous.get("media") or []
            source_complete = post.source_media_complete if post.source_media_complete is not None else post.media_complete
            if not source_complete and ((previous.get('media_complete') is True and old_media)
                                        or len(old_media) > len(post.media)
                                        or _post_quality_parts(previous)[2] > _post_quality_parts(post)[2]):
                observed = {media.url: media for media in post.media}
                post.media = [Media(**item) for item in old_media]
                for media in post.media:
                    fresh = observed.get(media.url)
                    if fresh and fresh.local_path:
                        for key in ('local_path', 'content_type', 'byte_size', 'sha256', 'width', 'height'):
                            setattr(media, key, getattr(fresh, key))
                post.media_complete = previous.get('media_complete', False)
            else:
                by_url = {item.get("url"): item for item in old_media if isinstance(item, dict)}
                for media in post.media:
                    if not media.local_path and media.url in by_url:
                        previous_media = by_url[media.url]
                        for key in ('local_path', 'content_type', 'byte_size', 'sha256', 'width', 'height'):
                            if getattr(media, key) is None:
                                setattr(media, key, previous_media.get(key))
        row = post.to_row()
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        # 先写事实文件再写派生索引，确保中断后可重建。
        if previous is not None and self._source_changed(previous, post):
            history = self.post_dir(post) / "source_history.jsonl"
            paid_model.append_jsonl(history, {"recorded_at": datetime.now(timezone.utc).isoformat(),
                                               "source": previous},
                                    guard=lambda path: assert_physical_direct_path(
                                        path.parent, path, kind="file", label="source_history.jsonl"))
        self._write_post_files(post)
        # 新目录完整写成后隔离旧目录，保持同 ID 唯一事实源。
        if previous is not None:
            self._isolate_other_truth_dirs(post, self.post_dir(post))
        assert_physical_direct_path(
            self.base, self.manifest, kind="file", label="manifest.jsonl")
        if _tag_destination(self.base, directory, row) != directory:
            row = _save_tagged_row(self.base, directory, row)
            for media, item in zip(post.media, row['media']):
                media.local_path = item.get('local_path')
        else:
            paid_model.append_jsonl(self.manifest, row, guard=lambda path: assert_physical_direct_path(
                path.parent, path, kind="file", label="manifest.jsonl"))
        self._rows[post.post_id] = row
        return True

    def should_append(self, post: Post) -> bool:
        """下载前检查新帖或补齐记录是否可接受，避免重复下载且允许残缺重试。"""
        old = self._current_row(post)
        # 下载前尚无 local_path；不能用下载后的升级判据挡住缺图恢复。
        return (old is None or not old.get('media_complete', True)
                or self._source_changed(old, post))

    @staticmethod
    def _source_changed(old: dict, new: Post) -> bool:
        if any(old.get(key) != getattr(new, key) for key in ("text", "created_at", "owner", "coauthors", "permalink")):
            return True
        before = old.get('media') or []
        # 旧 FB 响应可能把缺附件误存为完整空帖；新图片证据必须能够进入补抓。
        if new.platform == 'facebook' and new.media and not before:
            return True
        source_complete = new.source_media_complete if new.source_media_complete is not None else new.media_complete
        if not source_complete:
            return False
        if len(before) != len(new.media):
            return True
        for item, media in zip(before, new.media):
            if item.get('kind') != media.kind:
                return True
            if item.get('sha256') and media.sha256:
                if item['sha256'] != media.sha256:
                    return True
            elif item.get('url') != media.url and (media.kind != 'video' or not same_media_locator(item, media)):
                return True
        return False

    @staticmethod
    def _is_upgrade(old: dict, new: Post) -> bool:
        was_incomplete = not old.get("media_complete", True)
        if not was_incomplete:
            return False
        new_complete, new_media, new_local = _post_quality_parts(new)
        if new_complete:
            return True
        _, old_media, old_local = _post_quality_parts(old)
        # 残缺重试必须增加已落盘成果，不能用更多失败 URL 覆盖已有文件。
        return (new_media >= old_media and new_local >= old_local
                and (new_media > old_media or new_local > old_local))

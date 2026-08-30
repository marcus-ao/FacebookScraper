r"""统一归档层。

三条获取路线（官方导出 / 官方 API / 浏览器拦截）产出格式各不相同，
在这里收敛为同一个 schema，下游的翻译与图像管线只认这个 schema。

归档布局（2026-08-30 起，对应实施计划的 J 组）::

    archive/<平台前缀>_<账号>/
      index.html                        全账号总览（派生，可重建）
      manifest.jsonl                    **派生索引**，可从 posts/ 重建
      _rejected.jsonl                   被丢弃的节点及原因
      _orphan_media/                    重建后无主的媒体文件
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
    # 源响应只给轮播封面拿不到子项时（IG 的 web_profile_info 就是这样），标 False，
    # 由完整性检查汇总，留给人工或下一次登录态回填补齐。
    media_complete: bool = True

    def to_row(self) -> dict:
        d = asdict(self)
        return d


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
    # 只认 core.parse.iso() 产出的 "%Y-%m-%dT%H:%M:%SZ"。宽松匹配会把
    # from_fb_story 原样透传的怪字符串也放进来，那才是真正难查的问题。
    if (len(ts) >= 16 and ts[4] == "-" and ts[7] == "-" and ts[10] == "T"
            and ts[13] == ":" and ts[:4].isdigit()):
        return f"{ts[:10]}_{ts[11:13]}{ts[14:16]}_{post_id}"
    return f"undated_{post_id}"


class Archive:
    def __init__(self, root: Path | str, account: str):
        self.base = Path(root) / account
        self.posts_dir = self.base / "posts"
        self.posts_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.base / "manifest.jsonl"
        self._rows = self._load_rows()

    # ---- 每帖文件夹 ----

    def post_dir(self, post: "Post") -> Path:
        return self.posts_dir / post_dirname(post.post_id, post.created_at)

    def _write_post_files(self, post: "Post") -> None:
        """写 `post.json`（真相源）与 `text.txt`（给人看的派生副本）。

        text.txt 与 post.json 里的 text 重复，是有意的：设计同事和审校人
        双击就能看，不必去读 JSON。post.json 永远是真相，
        text.txt 由 reindex 从它重新生成。
        """
        d = self.post_dir(post)
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.json").write_text(
            json.dumps(post.to_row(), ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "text.txt").write_text(post.text or "", encoding="utf-8")

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
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed:
                ext = ".jpg" if guessed == ".jpe" else guessed
        d = self.post_dir(post)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{idx + 1:02d}{ext}"

    def reindex(self) -> int:
        """从 `posts/*/post.json` 重建 `manifest.jsonl`，返回条数。

        **这是"文件夹与索引冲突时以文件夹为准"那条规则的执行者。**
        人删了一个文件夹、或者哪次运行崩在中途，索引就会和现实脱节；
        重建的方向永远是 posts/ → manifest，绝不反过来。

        顺带把 `text.txt` 也重新生成一遍（它是派生的）。
        """
        rows: list[dict] = []
        for d in sorted(self.posts_dir.iterdir()):
            f = d / "post.json"
            if not d.is_dir() or not f.exists():
                continue
            try:
                row = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"    ! {d.name}/post.json 不是合法 JSON，跳过")
                continue
            if not isinstance(row, dict) or not row.get("post_id"):
                print(f"    ! {d.name}/post.json 缺 post_id，跳过")
                continue
            rows.append(row)
            (d / "text.txt").write_text(row.get("text") or "", encoding="utf-8")

        # 按时间正序落盘：manifest 本来不保证顺序，但重建时顺手排一下，
        # 人 `tail` 它的时候看到的就是最新的几条。
        rows.sort(key=lambda r: (r.get("created_at") or "", r.get("post_id") or ""))
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
        if not self.should_append(post):
            return False
        row = post.to_row()
        # 顺序要紧：**先写文件夹，再写索引**。反过来的话，中途崩溃会留下
        # 一条指向不存在文件夹的索引记录 —— 而规则是"以文件夹为准"，
        # 那条记录会在下次 reindex 时凭空消失，且没人知道发生过什么。
        self._write_post_files(post)
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
        more_media = len(new.media) > len(old.get("media", []))
        return was_incomplete and (new.media_complete or more_media)

    @staticmethod
    def fingerprint(data: bytes) -> str:
        """媒体去重用。US/DE 两站同一张图只需处理一次。"""
        return hashlib.sha256(data).hexdigest()[:16]

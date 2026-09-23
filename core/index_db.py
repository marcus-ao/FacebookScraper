"""从源文件原子重建展示用 SQLite 索引；业务写入不依赖索引。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from core import paid_model, review, store, translated
from core.config import cfg
from core.store import (ArchivePathError, _archive_row_error, _post_quality_rank,
                        assert_physical_direct_path, iter_post_dirs)


SCHEMA_VERSION = 2
POST_COLUMNS = (
    "task_id", "account_dir", "platform", "post_id", "account", "owner",
    "coauthors_json", "created_at", "archived_at", "permalink", "text",
    "source_route", "month", "primary_tag", "archive_relpath", "folder_name",
    "media_complete", "status", "row_json",
)
TAG_COLUMNS = ("task_id", "tag", "ordinal")
MEDIA_COLUMNS = (
    "task_id", "ordinal", "kind", "source_url", "local_path", "content_type",
    "width", "height", "byte_size", "sha256", "storage_status",
)


def display_account_dirs(archive_root: Path, *, include_frozen: bool = False) -> list[Path]:
    """枚举当前目标账号，供索引重建与 Web 指纹计算共用；冻结历史另行查询。"""
    archive_root = Path(archive_root)
    if not archive_root.exists():
        return []
    assert_physical_direct_path(archive_root.parent, archive_root,
                                kind="directory", label="索引归档根目录")
    active = set(cfg().active_accounts())
    return [assert_physical_direct_path(archive_root, account_dir,
                                        kind="directory", label="索引账号目录")
            for account_dir in sorted(archive_root.iterdir())
            if (account_dir.name in active or (include_frozen and account_dir.name.startswith(('fa_', 'in_')))) and account_dir.is_dir()
            and (account_dir / "posts").exists()]


def _scheduled_display_refs(state_dir: Path | None) -> set[str]:
    """只投影发布账本里已确认的展示状态；不承担发布幂等或任何业务判断。"""
    if state_dir is None:
        return set()
    path = assert_physical_direct_path(state_dir, state_dir / "published.jsonl", kind="file", label="发布展示源")
    if not path.exists():
        return set()
    refs = set()
    with path.open("rb") as handle:
        for raw in handle:
            try:
                row = json.loads(raw.decode("utf-8"))
                if not isinstance(row, dict) or row.get("status") != "scheduled":
                    continue
                refs.update(value for value in (row.get("source_refs") or []) if isinstance(value, str))
                if isinstance(row.get("post_id"), str) and isinstance(row.get("platform"), str):
                    refs.add(row["platform"] + ":" + row["post_id"])
            except (ValueError, TypeError):
                continue
    return refs


def _account_records(account_dir: Path) -> list[tuple[dict, Path]]:
    selected: dict[str, tuple[dict, Path]] = {}
    for directory in iter_post_dirs(account_dir):
        path = assert_physical_direct_path(directory, directory / "post.json", kind="file", label="索引源 post.json")
        if not path.exists():
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArchivePathError("重建索引时源帖无法读取：%s" % path) from exc
        error = _archive_row_error(row)
        if error:
            raise ArchivePathError("重建索引时源帖 schema 无效：%s：%s" % (path, error))
        previous = selected.get(row["post_id"])
        if previous is None or _post_quality_rank(row) > _post_quality_rank(previous[0]):
            selected[row["post_id"]] = (row, directory)
        if previous is not None:
            print("    ! %s 有多个源帖目录，展示索引沿用质量更完整的记录" % row["post_id"])
    return list(selected.values())


def _projected_records(archive_root: Path, state_dir: Path | None,
                       *, include_frozen: bool = False) -> list[dict]:
    archive_root = Path(archive_root)
    records = []
    scheduled_refs = _scheduled_display_refs(state_dir)
    for account_dir in display_account_dirs(archive_root, include_frozen=include_frozen):
        machine = translated.load_translated(account_dir / "translated.jsonl")
        human = translated.load_human_translated(account_dir / "translated_human.jsonl")
        # 审校账本每个账号只读一次：同 machine/human 一样是这趟派生的快照。
        events = review.latest(account_dir)
        for source, directory in _account_records(account_dir):
            pid = source["post_id"]
            effective = translated.effective_translation(source, machine.get(pid), human.get(pid))
            default = "not_ready"
            if effective and effective["is_human"]:
                default = "pending_review" if effective["stale"] else "edited"
            elif effective and not effective["stale"]:
                default = "pending_review"
            status = review.state_for(account_dir, source, default_status=default, events=events,
                                      scheduled=("%s:%s" % (source.get("platform"), pid)) in scheduled_refs)["status"]
            created = source.get("created_at") or ""
            source_tags = source.get("tags") or []
            tags = list(dict.fromkeys(source_tags))
            row = dict(source, id=account_dir.name + "/" + pid, account_dir=account_dir.name,
                       month=store.archive_month(dict(source, folder_name=directory.name)), status=status, tags=source_tags,
                       text_de=effective["text_de"] if effective else None)
            task_id = row["id"]
            archive_relpath = directory.relative_to(archive_root).as_posix()
            media = store.media_storage_info(account_dir, source)
            post_values = (
                task_id, account_dir.name, source.get("platform"), pid,
                source.get("account"), source.get("owner"),
                json.dumps(source.get("coauthors") or [], ensure_ascii=False,
                           separators=(",", ":")),
                created, source.get("archived_at"), source.get("permalink"),
                source.get("text") or "", source.get("source_route") or "",
                row["month"], tags[0] if tags else None, archive_relpath,
                source.get("folder_name") or directory.name,
                int(bool(source.get("media_complete", True))), status,
                json.dumps(row, ensure_ascii=False, sort_keys=True),
            )
            tag_values = [(task_id, tag, ordinal) for ordinal, tag in enumerate(tags)]
            media_values = [(
                task_id, item["ordinal"], item.get("kind"), item.get("source_url"),
                item.get("local_path"), item.get("content_type"), item.get("width"),
                item.get("height"), item.get("byte_size"), item.get("sha256"),
                item.get("storage_status"),
            ) for item in media]
            records.append({"row": row, "post": post_values,
                            "tags": tag_values, "media": media_values})
    return records


def _display_rows(archive_root: Path, state_dir: Path | None,
                  *, include_frozen: bool = False) -> list[dict]:
    return [record["row"] for record in _projected_records(
        archive_root, state_dir, include_frozen=include_frozen)]


def rebuild_index(archive_root: Path, db_path: Path, *, state_dir: Path | None = None,
                  include_frozen: bool = False) -> int:
    """从文件完整重建；读取或写入失败时保留上一份数据库，不部分更新。"""
    archive_root, db_path = Path(archive_root), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    assert_physical_direct_path(db_path.parent, db_path, kind="file", label="展示索引数据库")
    lock = assert_physical_direct_path(db_path.parent, db_path.with_suffix(".lock"), kind="file", label="索引写入锁")
    with paid_model.FileLock(lock, busy_message="展示索引正在重建，请稍后重试"):
        records = _projected_records(
            archive_root, Path(state_dir) if state_dir is not None else None,
            include_frozen=include_frozen)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=db_path.parent, prefix=".index-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
            with closing(sqlite3.connect(temporary)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.executescript("""
                    PRAGMA user_version=2;
                    CREATE TABLE posts (
                        task_id TEXT PRIMARY KEY,
                        account_dir TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        post_id TEXT NOT NULL,
                        account TEXT NOT NULL,
                        owner TEXT,
                        coauthors_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        archived_at TEXT,
                        permalink TEXT,
                        text TEXT NOT NULL,
                        source_route TEXT NOT NULL,
                        month TEXT NOT NULL,
                        primary_tag TEXT,
                        archive_relpath TEXT NOT NULL,
                        folder_name TEXT NOT NULL,
                        media_complete INTEGER NOT NULL CHECK (media_complete IN (0, 1)),
                        status TEXT NOT NULL,
                        row_json TEXT NOT NULL,
                        UNIQUE (account_dir, post_id)
                    );
                    CREATE TABLE post_tags (
                        task_id TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                        PRIMARY KEY (task_id, ordinal),
                        UNIQUE (task_id, tag),
                        FOREIGN KEY (task_id) REFERENCES posts(task_id) ON DELETE CASCADE
                    );
                    CREATE TABLE post_media (
                        task_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                        kind TEXT NOT NULL,
                        source_url TEXT,
                        local_path TEXT,
                        content_type TEXT,
                        width INTEGER,
                        height INTEGER,
                        byte_size INTEGER,
                        sha256 TEXT,
                        storage_status TEXT NOT NULL,
                        PRIMARY KEY (task_id, ordinal),
                        FOREIGN KEY (task_id) REFERENCES posts(task_id) ON DELETE CASCADE
                    );
                    CREATE INDEX display_filters ON posts (month, status, created_at, task_id);
                    CREATE INDEX display_platform ON posts (platform, created_at, task_id);
                    CREATE INDEX display_tags ON post_tags (tag, task_id);
                """)
                post_placeholders = ",".join("?" for _ in POST_COLUMNS)
                tag_placeholders = ",".join("?" for _ in TAG_COLUMNS)
                media_placeholders = ",".join("?" for _ in MEDIA_COLUMNS)
                for record in records:
                    connection.execute(
                        "INSERT INTO posts (%s) VALUES (%s)" %
                        (",".join(POST_COLUMNS), post_placeholders), record["post"])
                    connection.executemany(
                        "INSERT INTO post_tags (%s) VALUES (%s)" %
                        (",".join(TAG_COLUMNS), tag_placeholders), record["tags"])
                    connection.executemany(
                        "INSERT INTO post_media (%s) VALUES (%s)" %
                        (",".join(MEDIA_COLUMNS), media_placeholders), record["media"])
                connection.commit()
                validation = _consistency_from_connection(connection, records)
                if not validation["consistent"]:
                    raise ValueError("临时展示索引校验失败：%s" % "; ".join(validation["details"]))
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            assert_physical_direct_path(db_path.parent, db_path, kind="file", label="展示索引数据库")
            os.replace(temporary, db_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return len(records)


def _required_schema(connection: sqlite3.Connection) -> list[str]:
    details = []
    expected = {"posts": POST_COLUMNS, "post_tags": TAG_COLUMNS, "post_media": MEDIA_COLUMNS}
    for table, columns in expected.items():
        actual = tuple(row[1] for row in connection.execute("PRAGMA table_info(%s)" % table))
        if actual != columns:
            details.append("schema %s columns mismatch" % table)
    for table in ("post_tags", "post_media"):
        foreign_keys = connection.execute("PRAGMA foreign_key_list(%s)" % table).fetchall()
        if not any(row[2] == "posts" and row[3] == "task_id" and row[4] == "task_id"
                   for row in foreign_keys):
            details.append("schema %s foreign key missing" % table)
    return details


def _relation_details(kind: str, expected: list[tuple], actual: list[tuple]) -> list[str]:
    expected_counts, actual_counts = Counter(expected), Counter(actual)
    details = []
    for row, count in (expected_counts - actual_counts).items():
        details.append("missing %s row %r x%d" % (kind, row, count))
    for row, count in (actual_counts - expected_counts).items():
        prefix = "duplicate" if actual_counts[row] > 1 and row in expected_counts else "extra"
        details.append("%s %s row %r x%d" % (prefix, kind, row, count))
    return details


def _consistency_from_connection(connection: sqlite3.Connection, records: list[dict]) -> dict:
    details = []
    try:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return {"consistent": False, "missing": [], "extra": [], "changed": [],
                "integrity": type(exc).__name__, "total": len(records),
                "schema_version": None, "foreign_key_errors": [],
                "details": ["database unreadable: %s" % type(exc).__name__]}
    if schema_version != SCHEMA_VERSION:
        details.append("schema version %s != %s" % (schema_version, SCHEMA_VERSION))
    if integrity != "ok":
        details.append("integrity: %s" % integrity)
    try:
        details.extend(_required_schema(connection))
    except sqlite3.DatabaseError as exc:
        details.append("schema unreadable: %s" % type(exc).__name__)

    expected_posts = [record["post"] for record in records]
    expected_tags = [row for record in records for row in record["tags"]]
    expected_media = [row for record in records for row in record["media"]]
    try:
        actual_posts = connection.execute(
            "SELECT %s FROM posts" % ",".join(POST_COLUMNS)).fetchall()
        actual_tags = connection.execute(
            "SELECT %s FROM post_tags" % ",".join(TAG_COLUMNS)).fetchall()
        actual_media = connection.execute(
            "SELECT %s FROM post_media" % ",".join(MEDIA_COLUMNS)).fetchall()
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.DatabaseError as exc:
        details.append("tables unreadable: %s" % type(exc).__name__)
        return {"consistent": False, "missing": [], "extra": [], "changed": [],
                "integrity": integrity, "total": len(records),
                "schema_version": schema_version, "foreign_key_errors": [],
                "details": details}

    expected_by_id = {row[0]: row for row in expected_posts}
    actual_by_id: dict[str, list[tuple]] = defaultdict(list)
    for row in actual_posts:
        actual_by_id[row[0]].append(row)
    missing = sorted(expected_by_id.keys() - actual_by_id.keys())
    extra = sorted(actual_by_id.keys() - expected_by_id.keys())
    changed = {task_id for task_id in expected_by_id.keys() & actual_by_id.keys()
               if actual_by_id[task_id] != [expected_by_id[task_id]]}
    details.extend("missing post %s" % task_id for task_id in missing)
    details.extend("extra post %s" % task_id for task_id in extra)
    for task_id in sorted(changed):
        details.append("changed posts scalar or row_json %s" % task_id)

    for kind, expected_rows, actual_rows in (
            ("tag", expected_tags, actual_tags), ("media", expected_media, actual_media)):
        relation_changes = _relation_details(kind, expected_rows, actual_rows)
        details.extend(relation_changes)
        for detail in relation_changes:
            for row in (expected_rows + actual_rows):
                if row and repr(row) in detail:
                    changed.add(row[0])
                    break

    for table, rowid, parent, fk_index in foreign_key_errors:
        task_id = None
        if table in {"post_tags", "post_media"}:
            found = connection.execute(
                "SELECT task_id FROM %s WHERE rowid=?" % table, (rowid,)).fetchone()
            task_id = found[0] if found else None
        details.append("foreign key %s rowid=%s task_id=%s -> %s[%s]" %
                       (table, rowid, task_id, parent, fk_index))
        if task_id is not None:
            changed.add(task_id)

    consistent = not details
    return {"consistent": consistent, "missing": missing, "extra": extra,
            "changed": sorted(changed), "integrity": integrity, "total": len(records),
            "schema_version": schema_version, "foreign_key_errors": foreign_key_errors,
            "details": details}


def check_consistency(archive_root: Path, db_path: Path, *, state_dir: Path | None = None,
                      include_frozen: bool = False) -> dict:
    records = _projected_records(
        Path(archive_root), Path(state_dir) if state_dir is not None else None,
        include_frozen=include_frozen)
    try:
        with closing(sqlite3.connect(
                Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            return _consistency_from_connection(connection, records)
    except sqlite3.DatabaseError as exc:
        return {"consistent": False, "missing": [], "extra": [], "changed": [],
                "integrity": type(exc).__name__, "total": len(records),
                "schema_version": None, "foreign_key_errors": [],
                "details": ["database unreadable: %s" % type(exc).__name__]}


def created_month(row: dict) -> str:
    """历史页月份跟着行内日期（created_at）走；归档目录月份按北京换算，与行内日期可能差一天跨月。"""
    created = row.get('created_at') or ''
    return created[:7] if re.match(r'^\d{4}-\d{2}-\d{2}', created) else 'undated'


def query_page(db_path: Path, *, platform=None, month=None, tag=None, status=None,
               page: int = 1, limit: int = 50) -> dict:
    if not 1 <= limit <= 100 or page < 1:
        raise ValueError('历史查询每页 1–100 篇，页数从 1 开始')
    filters, values = [], []
    for column, value in [('platform', platform), ('status', status)]:
        if value:
            filters.append(column + ' = ?')
            values.append(value)
    # month 按行内日期过滤，与 created_month 同一条规则；'undated' 兜住无日期帖子。
    if month == 'undated':
        filters.append("created_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-*'")
    elif month:
        filters.append('substr(created_at, 1, 7) = ?')
        values.append(month)
    if tag == '__untagged__':
        filters.append('NOT EXISTS (SELECT 1 FROM post_tags t WHERE t.task_id = posts.task_id)')
    elif tag:
        filters.append('EXISTS (SELECT 1 FROM post_tags t WHERE t.task_id = posts.task_id AND t.tag = ?)')
        values.append(tag)
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        total = connection.execute('SELECT count(*) FROM posts' + where, values).fetchone()[0]
        rows = [json.loads(row[0]) for row in connection.execute(
            'SELECT row_json FROM posts' + where + ' ORDER BY created_at DESC, task_id LIMIT ? OFFSET ?',
            [*values, limit, (page - 1) * limit])]
        tags = [row[0] for row in connection.execute('SELECT DISTINCT tag FROM post_tags ORDER BY tag')]
        months = [row[0] for row in connection.execute(
            "SELECT DISTINCT substr(created_at, 1, 7) FROM posts"
            " WHERE created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-*' ORDER BY 1 DESC")]
        if connection.execute("SELECT 1 FROM posts WHERE created_at NOT GLOB"
                              " '[0-9][0-9][0-9][0-9]-[0-9][0-9]-*' LIMIT 1").fetchone():
            months.append('undated')
    return {'rows': rows, 'total': total, 'tags': tags, 'months': months}


def query_posts(db_path: Path, *, account: str | None = None, month: str | None = None,
                 tag: str | None = None, status: str | None = None,
                 limit: int = 50, offset: int = 0) -> list[dict]:
    """只读分页查询；所有过滤值作为 SQL 参数，禁止把查询结果用于业务决策。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit 必须在 1 到 500 之间")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    db_path = Path(db_path)
    assert_physical_direct_path(db_path.parent, db_path, kind="file", label="展示索引数据库")
    filters, values = [], []
    for column, value in (("month", month), ("status", status)):
        if value is not None:
            filters.append("p.%s = ?" % column)
            values.append(value)
    if account is not None:
        filters.append("(p.account_dir = ? OR p.account = ?)")
        values.extend((account, account))
    if tag is not None:
        filters.append("EXISTS (SELECT 1 FROM post_tags t WHERE t.task_id = p.task_id AND t.tag = ?)")
        values.append(tag)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    sql = "SELECT p.row_json FROM posts p" + where + " ORDER BY p.created_at DESC, p.task_id LIMIT ? OFFSET ?"
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return [json.loads(row[0]) for row in connection.execute(sql, [*values, limit, offset])]

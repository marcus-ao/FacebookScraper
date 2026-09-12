"""仅供界面展示的可重建 SQLite 索引；业务决定始终读取源文件。

唯一写入口 rebuild_index 从帖子文件夹及各追加账本派生完整数据库，成功后原子替换。
抓取、翻译、图片、流水线与发布不得依赖此模块，hygiene 对导入边界做机器检查。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from core import paid_model, review, translated
from core.store import (ArchivePathError, _archive_row_error, _post_quality_rank,
                        assert_physical_direct_path, iter_post_dirs)


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


def _account_rows(account_dir: Path) -> list[dict]:
    selected: dict[str, dict] = {}
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
        if previous is None or _post_quality_rank(row) > _post_quality_rank(previous):
            selected[row["post_id"]] = row
        if previous is not None:
            print("    ! %s 有多个源帖目录，展示索引沿用质量更完整的记录" % row["post_id"])
    return list(selected.values())


def _display_rows(archive_root: Path, state_dir: Path | None) -> list[dict]:
    rows = []
    scheduled_refs = _scheduled_display_refs(state_dir)
    if not archive_root.exists():
        return rows
    assert_physical_direct_path(archive_root.parent, archive_root, kind="directory", label="索引归档根目录")
    for account_dir in sorted(archive_root.iterdir()):
        if not account_dir.is_dir() or not (account_dir / "posts").exists():
            continue
        assert_physical_direct_path(archive_root, account_dir, kind="directory", label="索引账号目录")
        machine = translated.load_translated(account_dir / "translated.jsonl")
        human = translated.load_human_translated(account_dir / "translated_human.jsonl")
        for source in _account_rows(account_dir):
            pid = source["post_id"]
            effective = translated.effective_translation(source, machine.get(pid), human.get(pid))
            default = "not_ready"
            if effective and effective["is_human"]:
                default = "pending_review" if effective["stale"] else "edited"
            elif effective and not effective["stale"]:
                default = "pending_review"
            status = review.state_for(account_dir, source, default_status=default,
                                      scheduled=("%s:%s" % (source.get("platform"), pid)) in scheduled_refs)["status"]
            created = source.get("created_at") or ""
            row = dict(source, id=account_dir.name + "/" + pid, account_dir=account_dir.name,
                       month=created[:7] if len(created) >= 7 else "undated", status=status,
                       tags=source.get("tags") or [],
                       text_de=effective["text_de"] if effective else None)
            rows.append(row)
    return rows


def rebuild_index(archive_root: Path, db_path: Path, *, state_dir: Path | None = None) -> int:
    """从文件完整重建；读取或写入失败时保留上一份数据库，不部分更新。"""
    archive_root, db_path = Path(archive_root), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    assert_physical_direct_path(db_path.parent, db_path, kind="file", label="展示索引数据库")
    lock = assert_physical_direct_path(db_path.parent, db_path.with_suffix(".lock"), kind="file", label="索引写入锁")
    with paid_model.FileLock(lock, busy_message="展示索引正在重建，请稍后重试"):
        rows = _display_rows(archive_root, Path(state_dir) if state_dir is not None else None)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=db_path.parent, prefix=".index-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
            with closing(sqlite3.connect(temporary)) as connection:
                connection.executescript("""
                    PRAGMA user_version=1;
                    CREATE TABLE posts (
                        task_id TEXT PRIMARY KEY, account_dir TEXT NOT NULL,
                        account TEXT, month TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, row_json TEXT NOT NULL
                    );
                    CREATE TABLE post_tags (task_id TEXT NOT NULL, tag TEXT NOT NULL,
                                            PRIMARY KEY (task_id, tag));
                    CREATE INDEX display_filters ON posts (month, status, created_at);
                    CREATE INDEX display_tags ON post_tags (tag, task_id);
                """)
                for row in rows:
                    connection.execute("INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                                       (row["id"], row["account_dir"], row.get("account"), row["month"],
                                        row["status"], row.get("created_at") or "",
                                        json.dumps(row, ensure_ascii=False, sort_keys=True)))
                    connection.executemany("INSERT INTO post_tags VALUES (?, ?)",
                                           [(row["id"], tag) for tag in dict.fromkeys(row["tags"])])
                connection.commit()
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            assert_physical_direct_path(db_path.parent, db_path, kind="file", label="展示索引数据库")
            os.replace(temporary, db_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return len(rows)


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

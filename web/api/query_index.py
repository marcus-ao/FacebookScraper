"""列表专用的 SQLite 查询入口。结果只选择展示候选，不能进入保存或发布判据。

索引不可用或过期且重建失败时 task_ids=None，调用方沿用源文件筛选并展示 stale。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from core import index_db
from core.config import ROOT, cfg
from core.paid_model import FileLock, atomic_write_json
from core.store import assert_physical_direct_path, iter_post_dirs


def _paths():
    c = cfg()
    state = ROOT / c.get('paths', 'state', 'state')
    return c.archive_dir, state, state / 'index.sqlite'


def _file_stamp(path: Path) -> list | None:
    assert_physical_direct_path(path.parent, path, kind='file', label='展示索引输入')
    if not path.exists():
        return None
    info = path.stat()
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _source_signature(archive: Path, state: Path) -> str:
    files = []
    if archive.exists():
        assert_physical_direct_path(archive.parent, archive, kind='directory', label='展示归档根')
        for account in sorted(archive.iterdir()):
            if not account.is_dir() or not (account / 'posts').exists():
                continue
            assert_physical_direct_path(archive, account, kind='directory', label='展示账号目录')
            for name in ('translated.jsonl', 'translated_human.jsonl', 'review_items.jsonl'):
                path = account / name
                files.append([str(path), _file_stamp(path)])
            for directory in iter_post_dirs(account):
                path = directory / 'post.json'
                files.append([str(path), _file_stamp(path)])
    files.append([str(state / 'published.jsonl'), _file_stamp(state / 'published.jsonl')])
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _meta_guard(path, _role=None):
    return assert_physical_direct_path(path.parent, path, kind='file', label='展示索引重建记录')


def refresh_display_index(*, now: datetime | None = None, force: bool = False) -> dict:
    """Web/展示进程的重建入口；业务引擎不调用此函数做决策。"""
    archive, state, database = _paths()
    metadata_path = state / 'index.meta.json'
    previous = {}
    try:
        assert_physical_direct_path(state.parent, state, kind='directory', label='展示状态目录')
        state.mkdir(parents=True, exist_ok=True)
        with FileLock(_meta_guard(state / 'index.refresh.lock'), busy_message='展示索引正在刷新'):
            _meta_guard(metadata_path)
            if metadata_path.exists():
                try:
                    previous = json.loads(metadata_path.read_text(encoding='utf-8'))
                    if not isinstance(previous, dict):
                        previous = {}
                except (OSError, ValueError):
                    previous = {}
            signature = _source_signature(archive, state)
            dirty = (force or not database.exists() or previous.get('schema_version') != 1
                     or previous.get('source_signature') != signature
                     or previous.get('database_stamp') != _file_stamp(database))
            if dirty:
                index_db.rebuild_index(archive, database, state_dir=state)
                moment = now or datetime.now(timezone.utc)
                if moment.tzinfo is None or moment.utcoffset() is None:
                    raise ValueError('展示索引时刻必须带时区')
                previous = {'schema_version': 1, 'source_signature': signature,
                            'database_stamp': _file_stamp(database),
                            'rebuilt_at': moment.astimezone(timezone.utc).isoformat()}
                atomic_write_json(metadata_path, previous, guard=_meta_guard)
            stale = signature != _source_signature(archive, state)
            return {'available': True, 'stale': stale, 'rebuilt_at': previous['rebuilt_at'],
                    'error': 'source_changed_during_rebuild' if stale else None}
    except Exception as exc:
        return {'available': database.exists(), 'stale': True, 'rebuilt_at': previous.get('rebuilt_at'),
                'error': type(exc).__name__}


def candidates(*, status: str | None = None, tag: str | None = None,
               month: str | None = None, account: str | None = None,
               now: datetime | None = None) -> dict:
    """返回 SQL 过滤的 task_id；None 表示必须回到文件展示，不是空结果。"""
    metadata = refresh_display_index(now=now)
    if metadata['stale']:
        return {'task_ids': None, 'index': metadata}
    _archive, _state, database = _paths()
    try:
        ids, offset = [], 0
        while True:
            rows = index_db.query_posts(database, account=account, month=month, status=status,
                                        tag=None if tag == '__untagged__' else tag,
                                        limit=500, offset=offset)
            ids.extend(row['id'] for row in rows if tag != '__untagged__' or not row.get('tags'))
            if len(rows) < 500:
                break
            offset += len(rows)
        return {'task_ids': ids, 'index': metadata}
    except Exception as exc:
        return {'task_ids': None, 'index': dict(metadata, stale=True, error=type(exc).__name__)}

"""展示索引不可用时返回 task_ids=None，回退源文件；写入不依赖索引。"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from core import index_db
from core.config import ROOT, cfg
from core.paid_model import FileLock, atomic_write_json
from core import store
from core.store import assert_physical_direct_path, iter_post_dirs


def _paths(*, history=False):
    c = cfg()
    state = ROOT / c.get('paths', 'state', 'state')
    return c.archive_dir, state, state / ('index-history.sqlite' if history else 'index.sqlite')


def _file_stamp(path: Path) -> list | None:
    assert_physical_direct_path(path.parent, path, kind='file', label='展示索引输入')
    if not path.exists():
        return None
    info = path.stat()
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns, hashlib.sha256(path.read_bytes()).hexdigest()]


def _source_signature(archive: Path, state: Path, *, history=False) -> str:
    files = []
    # 与重建器共用账号范围，保证指纹对应数据库内容。
    for account in index_db.display_account_dirs(archive, include_frozen=history):
        for name in ('translated.jsonl', 'translated_human.jsonl', 'review_items.jsonl'):
            path = account / name
            files.append([str(path), _file_stamp(path)])
        for directory in iter_post_dirs(account):
            path = directory / 'post.json'
            if not path.exists():
                continue
            stamp = _file_stamp(path)
            source = json.loads(path.read_text(encoding='utf-8'))
            physical_media = [
                [item.get('ordinal'), item.get('kind'), item.get('source_url'),
                 item.get('local_path'), item.get('content_type'), item.get('width'),
                 item.get('height'), item.get('byte_size'), item.get('sha256'),
                 item.get('storage_status')]
                for item in store.media_storage_info(account, source)
            ]
            files.append([str(path), stamp, physical_media])
    files.append([str(state / 'published.jsonl'), _file_stamp(state / 'published.jsonl')])
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _meta_guard(path, _role=None):
    return assert_physical_direct_path(path.parent, path, kind='file', label='展示索引重建记录')


# 历史查询短时复用源校验，避免翻页反复扫描全档；待审查询仍逐次核对。
HISTORY_INDEX_MAX_AGE_SECONDS = 30.0


def index_status(*, history: bool = False) -> dict:
    """只读报告最近一次已验证索引；不触发源扫描、重建或元数据写入。"""
    _archive, _state, database = _paths(history=history)
    metadata_path = database.with_suffix('.meta.json')
    if not database.exists():
        return {'status': 'unbuilt', 'verified_at': None, 'message': None}
    try:
        if not metadata_path.exists():
            raise ValueError('verification metadata missing')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if not isinstance(metadata, dict) or metadata.get('schema_version') != index_db.SCHEMA_VERSION:
            raise ValueError('verification metadata schema mismatch')
        verified_at = metadata.get('verified_at')
        verified_moment = datetime.fromisoformat(verified_at) if isinstance(verified_at, str) else None
        if verified_moment is None or verified_moment.tzinfo is None or verified_moment.utcoffset() is None:
            raise ValueError('verification time missing')
        if metadata.get('database_stamp') != _file_stamp(database):
            raise ValueError('database changed after verification')
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA user_version').fetchone()[0] != index_db.SCHEMA_VERSION:
                raise ValueError('database schema mismatch')
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('database integrity check failed')
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'posts', 'post_tags', 'post_media'}.issubset(tables):
                raise ValueError('database tables missing')
        return {'status': 'verified', 'verified_at': verified_at, 'message': None}
    except (OSError, ValueError, TypeError, sqlite3.DatabaseError) as exc:
        return {'status': 'stale', 'verified_at': None, 'message': str(exc) or type(exc).__name__}


def refresh_display_index(*, now: datetime | None = None, force: bool = False,
                          history: bool = False, max_age_seconds: float = 0.0) -> dict:
    """Web/展示进程的重建入口；业务引擎不调用此函数做决策。"""
    archive, state, database = _paths(history=history)
    metadata_path = database.with_suffix('.meta.json')
    previous = {}
    try:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError('展示索引时刻必须带时区')
        assert_physical_direct_path(state.parent, state, kind='directory', label='展示状态目录')
        state.mkdir(parents=True, exist_ok=True)
        with FileLock(_meta_guard(database.with_suffix('.refresh.lock')), busy_message='展示索引正在刷新'):
            _meta_guard(metadata_path)
            if metadata_path.exists():
                try:
                    previous = json.loads(metadata_path.read_text(encoding='utf-8'))
                    if not isinstance(previous, dict):
                        previous = {}
                except (OSError, ValueError):
                    previous = {}
            # 窗口从校验完成时起算，不能使用重建开始时间。
            if (max_age_seconds > 0 and not force and database.exists()
                    and previous.get('schema_version') == index_db.SCHEMA_VERSION
                    and isinstance(previous.get('verified_at'), str)):
                try:
                    age = (moment - datetime.fromisoformat(previous['verified_at'])).total_seconds()
                except ValueError:
                    age = None
                if (age is not None and 0 <= age < max_age_seconds
                        and previous.get('database_stamp') == _file_stamp(database)):
                    return {'available': True, 'stale': False,
                            'rebuilt_at': previous['rebuilt_at'], 'error': None}
            signature = _source_signature(archive, state, history=history)
            dirty = (force or not database.exists()
                     or previous.get('schema_version') != index_db.SCHEMA_VERSION
                     or previous.get('source_signature') != signature
                     or previous.get('database_stamp') != _file_stamp(database))
            if dirty:
                candidate = database.with_name(database.stem + '.candidate.sqlite')
                assert_physical_direct_path(
                    database.parent, candidate, kind='file', label='展示索引候选数据库')
                try:
                    index_db.rebuild_index(
                        archive, candidate, state_dir=state, include_frozen=history)
                    current_signature = _source_signature(archive, state, history=history)
                    if signature != current_signature:
                        return {'available': database.exists(), 'stale': True,
                                'rebuilt_at': previous.get('rebuilt_at'),
                                'error': 'source_changed_during_rebuild'}
                    consistency = index_db.check_consistency(
                        archive, candidate, state_dir=state, include_frozen=history)
                    if not consistency['consistent']:
                        raise ValueError('展示索引与源文件不一致')
                    if signature != _source_signature(archive, state, history=history):
                        return {'available': database.exists(), 'stale': True,
                                'rebuilt_at': previous.get('rebuilt_at'),
                                'error': 'source_changed_during_rebuild'}
                    assert_physical_direct_path(
                        database.parent, database, kind='file', label='展示索引数据库')
                    os.replace(candidate, database)
                    candidate = None
                finally:
                    if candidate is not None:
                        candidate.unlink(missing_ok=True)
                previous = {'schema_version': index_db.SCHEMA_VERSION,
                            'source_signature': signature,
                            'database_stamp': _file_stamp(database),
                            'rebuilt_at': moment.astimezone(timezone.utc).isoformat(),
                            'verified_at': moment.astimezone(timezone.utc).isoformat()}
                atomic_write_json(metadata_path, previous, guard=_meta_guard)
            elif max_age_seconds > 0:
                # 刚走完一趟核对且结论是干净的；记下来，窗口内的后续请求不必重走。
                previous = dict(previous, verified_at=moment.astimezone(timezone.utc).isoformat())
                atomic_write_json(metadata_path, previous, guard=_meta_guard)
            return {'available': True, 'stale': False, 'rebuilt_at': previous['rebuilt_at'],
                    'error': None}
    except Exception as exc:
        return {'available': database.exists(), 'stale': True, 'rebuilt_at': previous.get('rebuilt_at'),
                'error': type(exc).__name__}


def history_page(*, platform=None, month=None, tag=None, status=None, page=1, limit=50, now=None):
    metadata = refresh_display_index(history=True, now=now,
                                     max_age_seconds=HISTORY_INDEX_MAX_AGE_SECONDS)
    archive, state, database = _paths(history=True)
    if not metadata['stale']:
        try:
            return dict(index_db.query_page(database, platform=platform, month=month, tag=tag,
                                            status=status, page=page, limit=limit), index=metadata)
        except Exception as exc:
            metadata = dict(metadata, stale=True, error=type(exc).__name__)
    rows = index_db._display_rows(archive, state, include_frozen=True)
    tags = sorted({tag for row in rows for tag in row['tags']})
    months = sorted({row['month'] for row in rows}, reverse=True)
    rows = [row for row in rows if (not platform or row['platform'] == platform)
            and (not month or row['month'] == month) and (not status or row['status'] == status)
            and (not tag or (not row['tags'] if tag == '__untagged__' else tag in row['tags']))]
    rows.sort(key=lambda row: row['id'])
    rows.sort(key=lambda row: row['created_at'], reverse=True)
    return {'rows': rows[(page-1)*limit:page*limit], 'total': len(rows), 'tags': tags, 'months': months, 'index': metadata}


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

"""只为一份源帖授权付费；名单、发布许可和人工文案均不由此改写。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from core import paid_model, review, translated
from core.store import (archive_write_lock, assert_physical_direct_path,
                        post_directory, read_post_truth)


def _ledger(account_dir: Path) -> Path:
    account_dir = Path(account_dir)
    assert_physical_direct_path(account_dir.parent, account_dir, kind='directory', label='授权账号目录')
    return assert_physical_direct_path(account_dir, account_dir / 'paid_consent.jsonl',
                                       kind='file', label='单帖付费授权')


def _identity(source: dict, account_dir: Path) -> dict:
    """tags、派生稿和文件夹布局不属于许可；全文、作者关系和原图字节属于许可。"""
    account_dir = Path(account_dir)
    directory = post_directory(account_dir, source)
    media = source.get('media')
    if not isinstance(source.get('text'), str) or not isinstance(media, list):
        raise review.ReviewValidationError('源帖全文或素材清单无效，不能确认付费依据')
    originals = []
    for item in media:
        if not isinstance(item, dict) or not isinstance(item.get('local_path'), str):
            raise review.ReviewValidationError('原图路径缺失，不能确认付费依据')
        relative = PurePosixPath(item['local_path'].replace('\\', '/'))
        if relative.is_absolute() or '..' in relative.parts:
            raise review.ReviewValidationError('原图路径不在源帖目录内')
        path = account_dir.joinpath(*relative.parts)
        assert_physical_direct_path(directory, path, kind='file', label='授权原图')
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (not data or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(data) != after.st_size):
            raise review.ReviewConflict('读取期间原图发生变化，请刷新后重试')
        originals.append({'kind': item.get('kind'), 'url': item.get('url'),
                          'sha256': hashlib.sha256(data).hexdigest()})
    return {'platform': source.get('platform'), 'account': source.get('account'),
            'account_dir': account_dir.name, 'post_id': source.get('post_id'),
            'source_full_text_sha256': hashlib.sha256(source['text'].encode('utf-8')).hexdigest(),
            'owner': source.get('owner'), 'coauthors': source.get('coauthors'),
            'media_complete': source.get('media_complete'), 'media': originals}


def _digest(identity: dict) -> str:
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def fingerprint(source: dict, account_dir: Path) -> str:
    """供页面 CAS 使用；损坏或缺失原图抛错，由能力端点展示不可用原因。"""
    return _digest(_identity(source, account_dir))


def history(account_dir: Path) -> list[dict]:
    path = _ledger(account_dir)
    if not path.exists():
        return []
    data = path.read_bytes()
    if data and not data.endswith(b'\n'):
        raise review.ReviewValidationError('单帖付费授权记录未完整写入，请先核对')
    events = []
    for line in data.splitlines():
        try:
            event = json.loads(line)
            if (not isinstance(event, dict) or event.get('action') != 'granted'
                    or event.get('actor') is not None or event.get('account') != Path(account_dir).name
                    or not isinstance(event.get('post_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', event.get('source_fingerprint', ''))
                    or _digest(event['source_identity']) != event['source_fingerprint']):
                raise ValueError
            UUID(event['revision'])
            events.append(event)
        except (KeyError, TypeError, ValueError) as exc:
            raise review.ReviewValidationError('单帖付费授权记录损坏，请先核对') from exc
    return events


def is_current(account_dir: Path, source: dict) -> bool:
    """读取真实 post.json 再比对；旧 manifest 不能延续已经失效的许可。"""
    try:
        current, _ = read_post_truth(account_dir, source)
        latest = {row['post_id']: row for row in history(account_dir)}.get(current['post_id'])
        return bool(latest and latest['source_fingerprint'] == fingerprint(current, account_dir))
    except (OSError, ValueError):
        return False


def grant(account_dir: Path, indexed: dict, *, source_fingerprint: str,
          source_text_sha256: str, review_revision: str | None,
          human_revision: str | None) -> dict:
    """在审校与源帖写锁内核对页面版本，再追加单帖授权。调用层负责业务硬闸。"""
    account_dir = Path(account_dir)
    with review.transaction(account_dir) as session, archive_write_lock(account_dir):
        source, state = session.validate(indexed, expected_revision=review_revision,
                                         expected_source_sha256=source_text_sha256)
        if state['status'] not in {'pending_review', 'edited', 'not_ready'}:
            raise review.ReviewConflict('请先恢复这篇的审校，再授权翻译')
        human = translated.load_human_translated(account_dir / 'translated_human.jsonl').get(source['post_id'])
        if (human or {}).get('revision') != human_revision:
            raise review.ReviewConflict('人工稿已有新版本，请刷新后重试')
        identity = _identity(source, account_dir)
        if _digest(identity) != source_fingerprint:
            raise review.ReviewConflict('原文、作者关系或原图已有更新，请重新确认付费')
        events = history(account_dir)
        previous = next((row for row in reversed(events) if row['post_id'] == source['post_id']), None)
        event = {'action': 'granted', 'revision': str(uuid4()),
                 'previous_revision': (previous or {}).get('revision'),
                 'post_id': source['post_id'], 'platform': source['platform'], 'account': account_dir.name,
                 'source_fingerprint': source_fingerprint, 'source_identity': identity,
                 'source_text_sha256': source_text_sha256, 'review_revision': review_revision,
                 'human_revision': human_revision, 'recorded_at': datetime.now(timezone.utc).isoformat(),
                 'actor': None}
        paid_model.append_jsonl(_ledger(account_dir), event, guard=lambda path: _ledger(path.parent))
        return event

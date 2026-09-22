"""Immutable approved bytes, shared by Web, CLI and receipt recovery."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core import paid_consent, review, store
from core.config import cfg
from core.paid_model import atomic_write_json
from core.process_identity import current_worker
from core.translated import source_text_sha256
from publish import journal


def folder(snapshot_id: str) -> Path:
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'[0-9a-f]{32}', snapshot_id):
        raise review.ReviewConflict('发布快照编号无效或缺失')
    root = cfg().state_dir / 'publish_snapshots'
    store.assert_physical_direct_path(root.parent, root, kind='directory', label='发布快照根目录')
    return store.assert_physical_direct_path(root, root / snapshot_id, kind='directory', label='发布快照目录')


def freeze(post, source: dict, *, expected_fingerprint: str, scheduled_at=None):
    """冻结正文与图片字节。`scheduled_at=None` 表示人已确认内容、时刻待选。"""
    account = cfg().archive_dir / (post.platform[:2] + '_' + post.account)
    source_fingerprint = paid_consent.fingerprint(source, account)
    files = {'text_de.txt': post.text_de.encode('utf-8'),
             '元信息.json': json.dumps(source, ensure_ascii=False, indent=2).encode('utf-8')}
    image_names = []
    for index, path in enumerate(post.image_paths, 1):
        name = f'{index:02d}{path.suffix}'
        files[name] = path.read_bytes()
        image_names.append(name)
    fingerprint = journal.text_sha256(post.text_de) + ':' + ','.join(
        hashlib.sha256(files[name]).hexdigest() for name in image_names)
    if fingerprint != expected_fingerprint or source_fingerprint != paid_consent.fingerprint(source, account):
        raise review.ReviewConflict('图片或文案在批准前已有变化，请刷新后重新确认')
    directory = folder(uuid4().hex)
    directory.mkdir(parents=True)
    for name, content in files.items():
        with (directory / name).open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    atomic_write_json(directory / 'snapshot.json', {
        'schema_version': 1, 'snapshot_id': directory.name, 'fingerprint': fingerprint,
        'source_fingerprint': source_fingerprint, 'source_text_sha256': source_text_sha256(source['text']),
        'account': account.name, 'post_id': post.post_id, 'platform': post.platform,
        'status': 'frozen',
        'scheduled_at': scheduled_at.isoformat() if scheduled_at is not None else None,
        'worker': current_worker(), 'files': {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        'images': image_names})
    return replace(post, image_paths=tuple(directory / name for name in image_names),
                   snapshot_id=directory.name, source_fingerprint=source_fingerprint), files, directory


def bind_schedule(snapshot_id: str, when: datetime, *, target=None) -> dict:
    """把已冻结内容绑到一个时刻；调用方须持发布锁。重复绑同一时刻幂等。"""
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise review.ReviewValidationError('发布时间需要包含时区')
    metadata, _, _, directory = load(snapshot_id)
    if metadata.get('status') != 'frozen':
        raise review.ReviewConflict('这份冻结内容已作废，请重新确认后再排期')
    bound = metadata.get('scheduled_at')
    if bound is not None and datetime.fromisoformat(bound) != when:
        raise review.ReviewConflict('这份冻结内容已绑定其它时刻，请重新确认后再排期')
    if target is not None:
        if not isinstance(target, dict):
            raise review.ReviewConflict('发布目标无效，请重新确认')
        previous = metadata.get('publish_target')
        if previous is not None and previous != target:
            raise review.ReviewConflict('发布目标已变化，请重新确认')
        metadata['publish_target'] = target
    metadata['scheduled_at'] = when.isoformat()
    atomic_write_json(directory / 'snapshot.json', metadata)
    return metadata


def discard(snapshot_id: str) -> None:
    """解冻时作废快照。⛔ 只改状态位，保留字节——它是当时确认过什么的证据。"""
    metadata, _, _, directory = load(snapshot_id)
    if metadata.get('status') == 'discarded':
        return
    atomic_write_json(directory / 'snapshot.json', dict(metadata, status='discarded'))


def require_bound(metadata: dict) -> datetime:
    """提交路径共用：拒绝尚未绑定时刻或已作废的快照。"""
    if metadata.get('status') != 'frozen':
        raise review.ReviewConflict('这份冻结内容已作废，不能提交')
    if metadata.get('scheduled_at') is None:
        raise review.ReviewConflict('这份冻结内容尚未选定发布时间')
    return datetime.fromisoformat(metadata['scheduled_at'])


def load(snapshot_id: str):
    directory = folder(snapshot_id)
    path = store.assert_physical_direct_path(directory, directory / 'snapshot.json', kind='file', label='快照清单')
    metadata = json.loads(path.read_text(encoding='utf-8'))
    if metadata.get('snapshot_id') != snapshot_id or not isinstance(metadata.get('files'), dict):
        raise review.ReviewConflict('快照清单无效，不能用当前稿件代替')
    files = {}
    for name, digest in metadata['files'].items():
        if name != Path(name).name or name in {'.', '..'}:
            raise review.ReviewConflict('快照文件路径无效')
        path = store.assert_physical_direct_path(directory, directory / name, kind='file', label='发布冻结内容')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise review.ReviewConflict('冻结内容已经损坏；保留现场，不能用当前稿件代替')
        files[name] = content
    source = json.loads(files['元信息.json'])
    if any(source.get(key) != metadata.get(key) for key in ('platform', 'post_id')):
        raise review.ReviewConflict('快照身份不一致')
    images = metadata.get('images')
    if (not isinstance(images, list) or not images or len(set(images)) != len(images)
            or any(not isinstance(name, str) or name not in files or not re.fullmatch(r'\d{2}\.(jpg|jpeg|png|webp)', name, re.I)
                   for name in images)):
        raise review.ReviewConflict('快照图片清单无效')
    fingerprint = hashlib.sha256(files['text_de.txt']).hexdigest() + ':' + ','.join(
        hashlib.sha256(files[name]).hexdigest() for name in images)
    if fingerprint != metadata.get('fingerprint'):
        raise review.ReviewConflict('快照正文、图片内容或顺序与批准指纹不一致')
    if metadata.get('account') != source['platform'][:2] + '_' + source['account']:
        raise review.ReviewConflict('快照账号身份不一致')
    return metadata, source, files, directory


def ensure(post, *, bind: bool = False, target=None):
    """核对 post 与其冻结快照一致。`bind=True` 在核对通过后才绑定时刻——顺序反过来会
    在内容不符时留下一个已绑错时刻、只能解冻才能脱身的快照。"""
    if getattr(post, 'snapshot_id', ''):
        metadata, _, files, directory = load(post.snapshot_id)
        if (files['text_de.txt'].decode('utf-8') != post.text_de
                or metadata.get('post_id') != post.post_id or metadata.get('platform') != post.platform
                or metadata.get('account') != post.platform[:2] + '_' + post.account
                or metadata.get('source_fingerprint') != post.source_fingerprint):
            raise review.ReviewConflict('提交身份或内容与冻结快照不一致，请重新审核')
        if bind:
            metadata = bind_schedule(post.snapshot_id, post.scheduled_at, target=target)
        if require_bound(metadata) != post.scheduled_at:
            raise review.ReviewConflict('提交时间与冻结快照不一致，请重新审核')
        return replace(post, image_paths=tuple(directory / name for name in metadata['images']))
    account = cfg().archive_dir / (post.platform[:2] + '_' + post.account)
    source, _ = store.read_post_truth(account, {'post_id': post.post_id, 'platform': post.platform,
                                               'account': post.account, 'created_at': None})
    fingerprint = journal.text_sha256(post.text_de) + ':' + ','.join(journal.file_sha256(path) for path in post.image_paths)
    frozen, _, _ = freeze(post, source, expected_fingerprint=fingerprint,
                          scheduled_at=post.scheduled_at)
    return frozen

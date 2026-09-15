"""监测基线、逐帖采集事实与人工接手；发送状态仍由飞书发件箱负责。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from core.integrity import parse_ts
from core.media import image_facts
from core.paid_model import FileLock, atomic_write_json
from core.process_identity import current_worker
from core.store import Archive, Post, resolve_media_path


class CaptureStateError(ValueError):
    pass


def post_key(platform, account, post_id):
    return ':'.join((platform, account.lower(), str(post_id)))


def verified_images(account_dir: Path, row: dict) -> int:
    """计数来自完整解码及字节校验；旧行没有哈希不能补造已验证证据。"""
    count = 0
    for item in row.get('media') or []:
        if item.get('kind') != 'image' or not item.get('sha256'):
            continue
        try:
            path = resolve_media_path(account_dir, row, item)
            facts = image_facts(path.read_bytes(), item.get('content_type') or '') if path else None
            if facts and all(facts.get(k) == item.get(k) for k in ('sha256', 'byte_size', 'width', 'height')):
                count += 1
        except (OSError, ValueError):
            continue
    return count


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def content_revision(row):
    media = [(m.get('kind'), m.get('sha256') or m.get('source_media_id') or
              urlsplit(m.get('url') or '').path) for m in row.get('media') or []]
    return _digest([row.get('text') or '', media])


class CaptureState:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / 'capture_state.json'
        self.lock = Path(state_dir) / 'capture_state.lock'

    def status(self):
        try:
            data = json.loads(self.path.read_text('utf-8'))
            if (not isinstance(data, dict) or data.get('version') != 1
                    or type(data.get('revision')) is not int or data['revision'] < 1
                    or not isinstance(data.get('baselines'), dict)
                    or not isinstance(data.get('items'), dict)
                    or not isinstance(data.get('events'), dict)):
                raise ValueError('invalid schema')
            for platform in ('facebook', 'instagram'):
                baseline = data['baselines'][platform]
                if (not isinstance(baseline.get('account'), str) or not baseline['account']
                        or parse_ts(baseline.get('enabled_at')) is None
                        or not isinstance(baseline.get('known_ids'), list)):
                    raise ValueError('invalid baseline')
            for key, item in data['items'].items():
                if (not isinstance(item, dict) or item.get('key') != key
                        or item.get('status') not in ('pending', 'complete', 'manual')
                        or not isinstance(item.get('source'), dict)):
                    raise ValueError('invalid capture item')
                row = item['source']
                if (row.get('platform') not in ('facebook', 'instagram')
                        or not isinstance(row.get('account'), str) or not isinstance(row.get('post_id'), str)
                        or key != post_key(row['platform'], row['account'], row['post_id'])
                        or not isinstance(row.get('text'), str) or not isinstance(row.get('media'), list)
                        or parse_ts(item.get('first_seen_at')) is None or not item.get('scan_id')):
                    raise ValueError('invalid candidate')
            for key, event in data['events'].items():
                if (not isinstance(event, dict) or event.get('event_id') != key
                        or type(event.get('acknowledged')) is not bool
                        or type(event.get('eligible')) is not bool or not isinstance(event.get('source'), dict)
                        or event.get('status') not in ('manual', 'complete')):
                    raise ValueError('invalid event')
            return data
        except FileNotFoundError as exc:
            raise CaptureStateError('监测基线尚未建立；完成人工回填后运行 --initialize-baseline --reason 处理说明') from exc
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise CaptureStateError('采集事实不可读或结构错误；已拒绝继续采集，请保留文件并人工核对') from exc

    def _save(self, data):
        data['revision'] += 1
        atomic_write_json(self.path, data)

    def initialize(self, archive_root, targets, reason, *, now=None, lookback_days=30):
        if not reason.strip():
            raise CaptureStateError('建立基线需要人工回填核对说明')
        now = now or datetime.now(timezone.utc)
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            if self.path.exists():
                raise CaptureStateError('监测基线已存在，不能重新启用或覆盖')
            data = {'version': 1, 'revision': 0, 'baselines': {}, 'items': {}, 'events': {}}
            for platform, account in targets.items():
                arc = Archive(Path(archive_root), platform[:2] + '_' + account)
                rows = arc.rows()
                recent = [r for r in rows if (stamp := parse_ts(r.get('created_at'))) is not None
                          and now - timedelta(days=lookback_days) <= stamp <= now]
                data['baselines'][platform] = {'account': account.lower(), 'enabled_at': now.isoformat(),
                    'lookback_days': lookback_days, 'recent_count': len(recent),
                    'known_ids': [r['post_id'] for r in rows], 'reason': reason.strip()}
            self._save(data)
            return data

    def begin(self, scan_id, posts, known, now):
        """同一响应的候选在任何图片请求之前原子保存；未完成项只接受人工恢复。"""
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            data = self.status()
            candidates = []
            for post in posts:
                key = post_key(post.platform, post.account, post.post_id)
                baseline = data['baselines'][post.platform]
                if baseline['account'] != post.account.lower():
                    raise CaptureStateError('目标账号与监测基线不同，请先核对基线')
                previous = data['items'].get(key)
                if previous and previous['status'] in ('pending', 'manual'):
                    continue
                stamp = parse_ts(post.created_at)
                category = ('source_updated' if post.post_id in known else
                            'time_unknown' if stamp is None else
                            'new' if stamp >= parse_ts(baseline['enabled_at']) else 'historical')
                item = {'key': key, 'scan_id': scan_id, 'source': post.to_row(),
                        'first_seen_at': previous['first_seen_at'] if previous else now.isoformat(),
                        'status': 'pending', 'classification': category,
                        'prior_revision': (previous or {}).get('content_revision') or
                                          (content_revision(known[post.post_id]) if post.post_id in known else None),
                        'last_result': (previous or {}).get('last_result'), 'recovery': False, 'worker': current_worker()}
                data['items'][key] = item
                candidates.append(post)
            self._save(data)
            return candidates

    def finish(self, post: Post, arc: Archive, now, *, reason='', archived=False):
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            data = self.status()
            key = post_key(post.platform, post.account, post.post_id)
            item = data['items'][key]
            row = post.to_row()
            saved = verified_images(arc.base, row) if archived else 0
            images = [m for m in post.media if m.kind == 'image']
            complete = bool(archived and post.source_media_complete is True and
                            saved == len(images) and post.media_complete and not reason)
            item.update(status='complete' if complete else 'manual', source=row,
                        finished_at=now.isoformat(), saved_images=saved,
                        archived=archived, reason=reason or ('' if complete else '原文或图片未完整保存，请人工处理'))
            revision = content_revision(row)
            category = ('recovered' if complete and item['recovery'] else item['classification'])
            outcome = _digest([revision, complete, saved, post.source_media_complete, post.source_media_count, archived])
            # 签名刷新、移动目录和重复扫描不形成新的业务结果。
            changed = outcome != item.get('last_result') and not (
                complete and category == 'source_updated' and revision == item.get('prior_revision'))
            item.update(content_revision=revision, last_result=outcome, classification=category)
            kinds = {m.kind for m in post.media}
            if changed:
                event_id = 'capture:' + key + ':' + outcome
                data['events'].setdefault(event_id, dict(item, event_id=event_id,
                    eligible=bool(post.text.strip() and kinds == {'image'}), acknowledged=False))
            self._save(data)
            return dict(item)

    def started(self, post, now):
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            data = self.status()
            data['items'][post_key(post.platform, post.account, post.post_id)]['attempt_started_at'] = now.isoformat()
            self._save(data)

    def interrupt(self, scan_id, now, reason):
        """调用方已结束该轮时，将尚未产出结果的候选转人工；不丢掉其发现事实。"""
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            data = self.status()
            for key, item in data['items'].items():
                if item['scan_id'] != scan_id or item['status'] != 'pending':
                    continue
                item.update(status='manual', reason=reason, archived=False,
                            saved_images=0, finished_at=now.isoformat())
                event_id = 'capture:' + key + ':interrupted:' + scan_id
                row = item['source']
                data['events'].setdefault(event_id, dict(item, event_id=event_id,
                    eligible=bool((row.get('text') or '').strip() and
                                  {m['kind'] for m in row.get('media', [])} == {'image'}), acknowledged=False))
            self._save(data)

    def recover(self, key, expected_revision, reason):
        """消费一次人工尝试授权；调用方必须持有 delta.lock，随后立即执行该项。"""
        if not reason.strip():
            raise CaptureStateError('人工恢复需要处理说明')
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            data = self.status()
            if data['revision'] != expected_revision:
                raise CaptureStateError('采集状态已变化，请刷新后核对')
            item = data['items'].get(key)
            if not item or item['status'] != 'manual':
                raise CaptureStateError('该项当前不需要人工恢复')
            item.update(status='pending', recovery=True, recovery_reason=reason.strip(), scan_id=uuid4().hex,
                        worker=current_worker())
            self._save(data)
            return dict(item)

    def acknowledge(self, event_ids):
        with FileLock(self.lock, busy_message='采集事实正在写入，请稍后重试'):
            data = self.status()
            for key in event_ids:
                if key in data['events']:
                    data['events'][key]['acknowledged'] = True
            self._save(data)

    def recover_interrupted(self, now):
        """仅供持有 delta.lock 的新入口调用；旧进程不可能仍在写本轮结果。"""
        data = self.status()
        scans = {item['scan_id'] for item in data['items'].values() if item['status'] == 'pending'}
        for scan_id in scans:
            self.interrupt(scan_id, now, '上次进程未完成采集结果登记，请核对已有归档后人工恢复')

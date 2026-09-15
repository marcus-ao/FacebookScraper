"""向飞书云盘上传不可覆盖的单向快照；上传失败不改变本地事实。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import tomllib
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from core.config import cfg
from core import store
from core.feishu import FeishuAuthError, FeishuClient
from core.paid_model import FileLock, atomic_write_json
from core.store import assert_physical_direct_path, read_post_truth


class MirrorError(RuntimeError):
    pass


DRIVE_ERROR_SUMMARIES = {
    'rate_limit': '云盘请求已限流，队列等待重试。',
    'quota_or_transient': '云盘配额或临时限制尚未核清，请人工核对后继续。',
    'auth': '请核对本机飞书应用凭据与授权配置后重试。',
    'permission': '飞书应用缺少云盘资源或接口权限，请核对授权。',
    'capacity': '云盘租户、个人或目录容量已达限制，请核对可用容量。',
    'transient': '云盘服务暂时无法完成请求；结果不确定的写入仍需先核对。',
    'uncertain': '云盘操作结果尚未确认，请先核对远端结果。',
    'blocked': '云盘操作受阻，请核对记录后再恢复。',
    'pending': '云盘操作仍待完成，请稍后查看。',
    'legacy_attempt': '旧镜像操作缺少确定回执，请先人工核对。',
    'interrupted': '镜像操作在回执落盘前中断，请先核对远端结果。',
    'upload_expired': '云盘分片上传事务已过期，请核对后再恢复。',
    'move_task_failed': '云盘异步移动任务失败，请核对后再恢复。',
}


class DriveError(MirrorError):
    """Only structured diagnostics cross the HTTP boundary; never response bodies."""
    def __init__(self, status='uncertain', *, http_status=None, code=None, request_id=None):
        self.status = status
        if code == 1061045:
            reason = 'quota_or_transient'
        elif http_status == 429:
            reason = 'rate_limit'
        elif http_status == 401:
            reason = 'auth'
        elif http_status == 403 or code in {1061004, 1061073}:
            reason = 'permission'
        elif code in {1061101, 1061061, 1062507}:
            reason = 'capacity'
        elif code in {1061001, 1064230} or isinstance(http_status, int) and http_status >= 500:
            reason = 'transient'
        else:
            reason = status if status in {'pending', 'blocked'} else 'uncertain'
        self.details = {'status': status, 'http_status': http_status,
                        'reason': reason,
                        'code': code if isinstance(code, int) else None,
                        'request_id': request_id if isinstance(request_id, str)
                        and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id) else None}
        self.summary = DRIVE_ERROR_SUMMARIES[reason]
        super().__init__('Drive request ' + status)


class DriveClient(FeishuClient):
    """只包含云端新增、移动与异步状态查询；复用飞书凭据及脱敏 HTTP 错误处理。"""
    SMALL_FILE_LIMIT = 20 * 1024 * 1024

    def _pace_drive(self, *, move=False):
        gap = .25 - (time.monotonic() - getattr(self, '_last_drive_request', 0.0))
        if move:
            gap = max(gap, 3.05 - (time.monotonic() - getattr(self, '_last_move_request', 0.0)))
        if gap > 0:
            time.sleep(gap)
        self._last_drive_request = time.monotonic()
        if move:
            self._last_move_request = self._last_drive_request

    def _drive_post(self, path, **kwargs):
        self._pace_drive(move=path.endswith('/move'))
        try:
            return self._drive_response(self.http.post('https://open.feishu.cn/open-apis/' + path, **kwargs))
        except (httpx.HTTPError, ValueError) as exc:
            raise DriveError() from exc

    @staticmethod
    def _drive_response(response):
        try:
            value = response.json()
        except ValueError:
            value = {}
        code = value.get('code') if isinstance(value, dict) else None
        if response.is_success and code == 0:
            return value
        # 1061045 also means the daily quota: do not loop against an unknown quota scope.
        state = ('blocked' if code == 1061045 else 'pending'
                 if response.status_code == 429 or code == 1064230 else 'blocked'
                 if 400 <= response.status_code < 500 or code in {1061004, 1061073, 1061101, 1061061, 1062507}
                 else 'uncertain')
        raise DriveError(state, http_status=response.status_code, code=code,
                         request_id=response.headers.get('X-Tt-Logid') or response.headers.get('X-Request-Id'))

    def list_files(self, parent: str) -> list[dict]:
        """Directory metadata only. Never fetch cloud file content."""
        files, seen, page = [], set(), ''
        while True:
            params = {'folder_token': parent, 'page_size': 200}
            if page:
                params['page_token'] = page
            try:
                self._pace_drive()
                response = self.http.get('https://open.feishu.cn/open-apis/drive/v1/files',
                                         headers=self._headers(), params=params)
                data = self._drive_response(response).get('data') or {}
            except httpx.HTTPError as exc:
                raise DriveError() from exc
            rows = data.get('files')
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise DriveError()
            files.extend({key: row.get(key) for key in ('name', 'type', 'token', 'parent_token')}
                         for row in rows)
            if not data.get('has_more'):
                return files
            page = data.get('next_page_token')
            if not isinstance(page, str) or not page or page in seen:
                raise DriveError()
            seen.add(page)

    def create_folder(self, parent: str, name: str) -> str:
        if not name or len(name.encode('utf-8')) > 256:
            raise DriveError('blocked')
        result = self._drive_post('drive/v1/files/create_folder', headers=self._headers(),
                            json={'name': name, 'folder_token': parent})
        return str((result.get('data') or {}).get('token') or '')

    def upload_file(self, parent: str, name: str, content: bytes) -> str:
        if not name or len(name) > 250:
            raise DriveError('blocked')
        if not content:
            raise MirrorError('Drive 不接收空文件；应使用快照空文件清单留证')
        if len(content) > self.SMALL_FILE_LIMIT:
            prepared = self.prepare_upload(parent, name, len(content))
            upload_id = prepared.get('upload_id')
            size, count = prepared.get('block_size'), prepared.get('block_num')
            if (not upload_id or not isinstance(size, int) or isinstance(size, bool) or size < 1
                    or count != (len(content) + size - 1) // size):
                raise MirrorError('云盘分片上传参数不完整')
            for sequence in range(count):
                block = content[sequence * size:(sequence + 1) * size]
                self.upload_part(upload_id, sequence, name, block)
            return self.finish_upload(upload_id, count)
        result = self._drive_post('drive/v1/files/upload_all', headers=self._headers(), timeout=120,
                            data={'file_name': name, 'parent_type': 'explorer',
                                  'parent_node': parent, 'size': str(len(content))},
                            files={'file': (name, content, 'application/octet-stream')})
        return str((result.get('data') or {}).get('file_token') or '')

    def prepare_upload(self, parent, name, size):
        if not name or len(name) > 250:
            raise DriveError('blocked')
        prepared = self._drive_post('drive/v1/files/upload_prepare', headers=self._headers(),
                               json={'file_name': name, 'parent_type': 'explorer',
                                     'parent_node': parent, 'size': size}).get('data') or {}
        block_size, count = prepared.get('block_size'), prepared.get('block_num')
        if (not isinstance(prepared.get('upload_id'), str) or not prepared['upload_id']
                or type(block_size) is not int or block_size < 1 or type(count) is not int
                or count != (size + block_size - 1) // block_size):
            raise DriveError('uncertain')
        return prepared

    def upload_part(self, upload_id, sequence, name, block):
        self._drive_post('drive/v1/files/upload_part', headers=self._headers(), timeout=120,
                        data={'upload_id': upload_id, 'seq': str(sequence), 'size': str(len(block)),
                              'checksum': str(zlib.adler32(block))},
                        files={'file': (name, block, 'application/octet-stream')})

    def finish_upload(self, upload_id, count):
        data = self._drive_post('drive/v1/files/upload_finish', headers=self._headers(),
                               json={'upload_id': upload_id, 'block_num': count}).get('data') or {}
        return str(data.get('file_token') or '')

    def move_folder(self, token: str, parent: str) -> str | None:
        result = self._drive_post('drive/v1/files/' + quote(token, safe='') + '/move',
                            headers=self._headers(), json={'type': 'folder', 'folder_token': parent})
        return (result.get('data') or {}).get('task_id') or None

    def task_status(self, task_id: str) -> str:
        # 唯一 GET 是任务状态元数据；不读取任何云端内容。
        try:
            self._pace_drive()
            response = self.http.get('https://open.feishu.cn/open-apis/drive/v1/files/task_check',
                                     headers=self._headers(), params={'task_id': task_id})
            data = response.json()
            if not response.is_success or not isinstance(data, dict) or data.get('code') != 0:
                raise MirrorError('云盘移动任务未能确认')
            status = (data.get('data') or {}).get('status')
            if status not in {'success', 'failed', 'pending'}:
                raise MirrorError('云盘返回未知任务状态')
            return status
        except (httpx.HTTPError, ValueError) as exc:
            raise MirrorError('无法查询云盘任务状态，请检查连接') from exc


@dataclass(frozen=True)
class MirrorSettings:
    enabled: bool
    root_folder_token: str
    mirror_state: bool = True
    state_interval_minutes: int = 60

    @classmethod
    def load(cls):
        c = cfg()
        result = cls(c.get('mirror', 'enabled', False), c.get('mirror', 'root_folder_token', ''),
                     c.get('mirror', 'mirror_state', True), c.get('mirror', 'state_interval_minutes', 60))
        if not isinstance(result.enabled, bool) or not isinstance(result.mirror_state, bool):
            raise ValueError('mirror enabled / mirror_state 必须是布尔值')
        if (isinstance(result.state_interval_minutes, bool)
                or not isinstance(result.state_interval_minutes, int) or result.state_interval_minutes < 1):
            raise ValueError('mirror state_interval_minutes 必须是正整数')
        if result.enabled and not re.fullmatch(r'[A-Za-z0-9_-]+', result.root_folder_token):
            raise ValueError('启用镜像前请配置飞书云盘 root_folder_token')
        return result


def _stamp(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('镜像时刻必须带时区')
    return now.astimezone(timezone.utc).isoformat()


# 周期备份仅含账本与文本日志；二进制证据另传，避免小时级重复复制。
STATE_BACKUP_SUFFIXES = ('.json', '.jsonl', '.log')

# 排除绑定本机环境的探查转储。
STATE_BACKUP_SKIP_FILE = re.compile(r'^publish_probe_.*\.json$')

# 排除镜像自身和二进制证据目录，避免递归备份。
STATE_BACKUP_SKIP_DIRS = ('mirror_spool', 'publish_attempts', 'publish_failures',
                          'publish_snapshots', 'runtime-backups')

# 超限报备份失败，避免静默上传意外的大文件。
STATE_BACKUP_MAX_BYTES = 32 * 1024 * 1024


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_identity(source, media, history=()):
    """原文与媒体冻结可用性决定版本；声明 SHA 不代表已经保存过图片字节。"""
    identity = {key: source.get(key) for key in ('post_id', 'platform', 'account', 'text',
                'created_at', 'permalink', 'owner', 'owner_name', 'coauthors', 'media_complete')}
    return dict(identity, media=media, history=list(history))


def _name(value: str) -> str:
    cleaned = re.sub(r'[\s/\\<>:"|?*\x00-\x1f]+', '-', str(value)).strip(' .-')
    if not cleaned:
        return '未分类'
    if len(cleaned.encode('utf-8')) <= 256:
        return cleaned
    prefix = cleaned.encode('utf-8')[:240].decode('utf-8', errors='ignore')
    return prefix + '-' + _digest(cleaned.encode())[:12]


class MirrorService:
    """本地耐久队列与冻结字节。调用方可持续排队；只有 dispatch 接触适配器。"""
    def __init__(self, state_dir: Path, settings: MirrorSettings):
        self.state_dir, self.settings = Path(state_dir), settings
        self.path = self.state_dir / 'mirror_queue.json'
        self.spool = self.state_dir / 'mirror_spool'

    def _guard(self, path, _role=None):
        return assert_physical_direct_path(path.parent, path, kind='file', label='镜像专用文件')

    def _lock(self):
        assert_physical_direct_path(self.state_dir.parent, self.state_dir, kind='directory', label='镜像状态目录')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return FileLock(self._guard(self.state_dir / 'mirror_queue.lock'), busy_message='镜像队列正在写入，请稍后重试')

    def _load(self):
        assert_physical_direct_path(self.state_dir.parent, self.state_dir, kind='directory', label='镜像状态目录')
        self._guard(self.path)
        if not self.path.exists():
            return {'schema_version': 2, 'root_token': self.settings.root_folder_token,
                    'posts': {}, 'folders': {}, 'snapshots': {}, 'operations': {}}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise MirrorError('镜像队列无法读取，保留现场，不重置云盘回执') from exc
        if (not isinstance(data, dict) or data.get('schema_version') not in {1, 2}
                or any(not isinstance(data.get(key), dict) for key in ('posts', 'folders', 'snapshots'))):
            raise MirrorError('镜像队列损坏，保留现场，不重置云盘回执')
        if data.get('root_token') != self.settings.root_folder_token:
            raise MirrorError('镜像根目录与既有回执不同，请核对后使用独立镜像状态目录')
        if data['schema_version'] == 1:
            # v1 had no durable request intent: tokenless attempted work is unprovable.
            data['operations'] = {}
            def legacy(kind, identity, post_key):
                oid = _digest(_json_bytes({'kind': kind, **identity}))
                data['operations'][oid] = dict(identity, kind=kind, status='uncertain',
                    post_keys=[post_key], error={'status': 'uncertain', 'reason': 'legacy_attempt'})
                return data['operations'][oid]
            for item in data['snapshots'].values():
                if item.get('attempts', 0) and any(not f.get('token') for f in item['files']):
                    item.update(status='uncertain', error={'status': 'uncertain', 'reason': 'legacy_attempt'})
                    if item.get('folder_token'):
                        for file in item['files']:
                            if not file.get('token'):
                                legacy('upload', {'parent': item['folder_token'], 'name': file['name'],
                                                  'sha256': file['sha256']}, item['post_key'])
                    elif data['posts'][item['post_key']].get('token'):
                        legacy('create_folder', {'parent': data['posts'][item['post_key']]['token'],
                                                  'name': item['folder_name']}, item['post_key'])
            for key, post in data['posts'].items():
                if post.get('move_task'):
                    operation = legacy('move', {'token': post['token'], 'parent': post['move_parent'],
                        'previous_parent': post['parent'], 'sequence': post.get('move_sequence', 0)}, key)
                    operation.update(status='completed', result=post['move_task'], remote_token=post['move_task'])
                    operation.pop('error', None)
                    post['status'] = 'pending'
                elif post.get('error'):
                    post['status'] = 'uncertain'
                    parent = data['root_token']
                    names = (post['month'], post['tag']) + (() if post.get('token') else (post['name'],))
                    for name in names:
                        token = data['folders'].get(parent + '\0' + name)
                        if not token:
                            legacy('create_folder', {'parent': parent, 'name': name}, key)
                            break
                        parent = token
                    else:
                        if post.get('token') and post.get('parent') != parent:
                            legacy('move', {'token': post['token'], 'parent': parent,
                                'previous_parent': post.get('parent'), 'sequence': 0}, key)
            data['schema_version'] = 2
        if not isinstance(data.get('operations'), dict):
            raise MirrorError('镜像操作账本损坏')
        for operation in data['operations'].values():
            if (not isinstance(operation, dict) or operation.get('kind') not in
                    {'create_folder', 'upload', 'upload_prepare', 'upload_part', 'upload_finish', 'move'}
                    or operation.get('status') not in {'pending', 'in_flight', 'completed', 'uncertain', 'blocked'}):
                raise MirrorError('镜像操作账本损坏')
            if operation['status'] == 'in_flight':
                operation.update(status='uncertain', error={'status': 'uncertain', 'reason': 'interrupted'})
        return data

    def _save(self, data):
        atomic_write_json(self.path, data, guard=self._guard)

    def snapshot(self) -> dict:
        """只读查看队列；不创建文件、不构造网络客户端。"""
        return self._load()

    def status(self) -> dict:
        """Read-only summary; credentials and cloud access are deliberately not consulted."""
        data = self._load()
        counts = {key: 0 for key in ('pending', 'completed', 'uncertain', 'blocked')}
        posts = {}
        for key, post in data['posts'].items():
            states = [i['status'] for i in data['snapshots'].values() if i['post_key'] == key]
            states.extend(op['status'] for op in data['operations'].values() if key in op.get('post_keys', []))
            states.append(post.get('status', 'completed' if post.get('token') else 'pending'))
            state = next((s for s in ('uncertain', 'blocked', 'pending') if s in states), 'completed')
            posts[key] = {'status': state, 'incomplete_source': post.get('incomplete_source', False),
                          'missing_media': post.get('missing_media', [])}
        for item in data['snapshots'].values():
            state = posts[item['post_key']]['status']
            counts[state if state in {'uncertain', 'blocked'} else item['status']] += 1
        overall = next((s for s in ('uncertain', 'blocked', 'pending', 'completed')
                        if counts[s] or any(p['status'] == s for p in posts.values())), 'idle')
        failures = [op for op in data['operations'].values()
                    if op['status'] != 'completed' and isinstance(op.get('error'), dict)]
        if not failures:
            failures = [row for row in (*data['snapshots'].values(), *data['posts'].values())
                        if row.get('status') != 'completed' and isinstance(row.get('error'), dict)]
        last_error = None
        if failures:
            latest = max(failures, key=lambda row: ({'uncertain': 3, 'blocked': 2, 'pending': 1}.get(row.get('status'), 0),
                                                  row.get('started_at', row.get('next_at', ''))))
            last_error = DRIVE_ERROR_SUMMARIES.get(latest['error'].get('reason'),
                DRIVE_ERROR_SUMMARIES.get(latest.get('status'), DRIVE_ERROR_SUMMARIES['blocked']))
        return {'enabled': self.settings.enabled, 'status': overall if self.settings.enabled else 'disabled',
                'counts': counts, 'last_success_at': data.get('last_success_at'), 'last_error': last_error, 'posts': posts,
                'operations': [{'id': oid, **{key: op.get(key) for key in
                                ('kind', 'status', 'sha256', 'remote_token', 'error')}}
                               for oid, op in data['operations'].items()]}

    def preflight(self, client) -> dict:
        """Explicit metadata access; listing proves visibility, never upload permission or file bytes."""
        data = self._load()
        listings = {data['root_token']: client.list_files(data['root_token'])}
        reconciliation = []
        for oid, operation in data['operations'].items():
            if operation['status'] not in {'uncertain', 'blocked'}:
                continue
            parent = operation.get('parent')
            if parent and parent not in listings:
                listings[parent] = client.list_files(parent)
            matches = [row for row in listings.get(parent, []) if row.get('name') == operation.get('name')
                       and row.get('type') == 'folder' and row.get('token')
                       and row.get('parent_token') in {None, parent}]
            unique = operation['kind'] == 'create_folder' and len(matches) == 1
            reconciliation.append({'id': oid, 'status': 'unique_folder' if unique else 'uncertain',
                                   'remote_token': matches[0]['token'] if unique else None})
        return {'root_visible': True, 'write_permission': 'unverified',
                'root_children': len(listings[data['root_token']]), 'reconciliation': reconciliation}

    def resolve(self, operation_id: str, *, sha256: str | None = None, remote_token: str | None = None,
                not_created: bool = False, note: str, now: datetime) -> dict:
        """Record an operator's evidence. Resolution never makes a remote write or dispatches work."""
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise MirrorError('人工核对必须填写 1–1000 字说明')
        if bool(remote_token) == bool(not_created):
            raise MirrorError('必须提供远端 token 或明确断言未创建，二选一')
        if remote_token and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', remote_token):
            raise MirrorError('远端 token 格式无效')
        with self._lock():
            data = self._load()
            operation = data['operations'].get(operation_id)
            if not operation or operation['status'] not in {'uncertain', 'blocked'}:
                raise MirrorError('操作不存在或不需要人工核对')
            if operation.get('sha256') and sha256 != operation['sha256']:
                raise MirrorError('必须提供该操作冻结文件的完整 SHA-256')
            if remote_token and operation['kind'] not in {'upload', 'upload_finish', 'create_folder', 'move'}:
                raise MirrorError('该操作不能仅凭文件 token 确认；需核实未完成后重新执行')
            operation.setdefault('resolutions', []).append({'at': _stamp(now), 'note': note.strip(),
                'sha256': sha256, 'remote_token': remote_token, 'not_created': not_created})
            if not_created:
                operation.update(status='pending', next_at=_stamp(now))
                operation.pop('result', None)
                operation.pop('remote_token', None)
            else:
                if operation['kind'] == 'move' and remote_token != operation['token']:
                    raise MirrorError('移动确认 token 必须等于原目录 token')
                operation.update(status='completed', result=None if operation['kind'] == 'move' else remote_token,
                                 remote_token=remote_token, completed_at=_stamp(now))
            operation.pop('error', None)
            # Remaining unresolved intents still stop their own dependent work at dispatch.
            for post in data['posts'].values():
                post.update(status='pending', next_at=_stamp(now))
            for item in data['snapshots'].values():
                if item['status'] != 'completed':
                    item.update(status='pending', next_at=_stamp(now))
            self._save(data)
        return self.status()

    def _operation(self, data, kind, identity, action, now):
        oid = _digest(_json_bytes({'kind': kind, **identity}))
        operation = data['operations'].setdefault(oid, dict(identity, kind=kind, status='pending'))
        if getattr(self, '_dispatch_post_key', None):
            keys = operation.setdefault('post_keys', [])
            if self._dispatch_post_key not in keys:
                keys.append(self._dispatch_post_key)
        if operation['status'] == 'completed':
            return operation.get('result')
        if operation['status'] in {'uncertain', 'blocked', 'in_flight'}:
            raise DriveError(operation['status'] if operation['status'] != 'in_flight' else 'uncertain')
        if operation.get('next_at', '') > _stamp(now):
            raise DriveError('pending')
        operation.update(status='in_flight', started_at=_stamp(now))
        self._save(data)  # Intent must reach disk before the remote mutation.
        try:
            result = action()
            operation.update(status='completed', result=result,
                             remote_token=result if isinstance(result, str) else None,
                             completed_at=_stamp(now))
            operation.pop('error', None)
            self._save(data)  # A lost local ACK is as ambiguous as a lost HTTP response.
            return result
        except Exception as exc:
            state = exc.status if isinstance(exc, DriveError) else 'blocked' if isinstance(exc, FeishuAuthError) else 'uncertain'
            operation.update(status=state, error=exc.details if isinstance(exc, DriveError)
                             else {'status': state, 'reason': 'auth' if isinstance(exc, FeishuAuthError) else 'uncertain',
                                   'exception_type': type(exc).__name__},
                             next_at=_stamp(now + timedelta(minutes=15)))
            operation.pop('result', None)
            operation.pop('remote_token', None)
            self._save(data)
            raise DriveError(state) from exc

    def _blob(self, content: bytes) -> str:
        digest = _digest(content)
        assert_physical_direct_path(self.state_dir, self.spool, kind='directory', label='镜像冻结字节目录')
        self.spool.mkdir(exist_ok=True)
        path = self._guard(self.spool / (digest + '.json'))
        if not path.exists():
            atomic_write_json(path, {'sha256': digest, 'base64': base64.b64encode(content).decode('ascii')}, guard=self._guard)
        return digest

    def _content(self, digest: str) -> bytes:
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise MirrorError('镜像快照摘要无效')
        assert_physical_direct_path(self.state_dir, self.spool, kind='directory', label='镜像冻结字节目录')
        path = self._guard(self.spool / (digest + '.json'))
        content = base64.b64decode(json.loads(path.read_text(encoding='utf-8'))['base64'], validate=True)
        if _digest(content) != digest:
            raise MirrorError('冻结的镜像字节已损坏；不使用当前源文件替代历史版本')
        return content

    def _frozen_source_identity(self, files):
        frozen = {file['name']: file['sha256'] for file in files}
        if '元信息.json' not in frozen:
            return None
        if '空文件清单.json' in frozen:
            for name in json.loads(self._content(frozen['空文件清单.json'])):
                frozen.setdefault(name, _digest(b''))

        def media_identity(source, historical=False):
            result = []
            for index, entry in enumerate(source.get('media') or []):
                if historical:
                    basename = Path(str(entry.get('local_path') or '').replace('\\', '/')).name
                    candidates = [digest for name, digest in frozen.items() if basename
                                  and name.startswith('原图历史_') and name.endswith('_' + basename)]
                else:
                    candidates = [digest for name, digest in frozen.items()
                                  if re.fullmatch(rf'{index + 1:02d}\.[^.]+', name)]
                matches = [digest for digest in candidates if not entry.get('sha256') or digest == entry['sha256']]
                result.append({'kind': entry.get('kind'), 'sha256': matches[0] if len(matches) == 1
                               else entry.get('sha256'), 'available': len(matches) == 1})
            return result

        source = json.loads(self._content(frozen['元信息.json']))
        history = []
        if 'source_history.jsonl' in frozen:
            for line in self._content(frozen['source_history.jsonl']).splitlines():
                if line.strip():
                    previous = json.loads(line)['source']
                    history.append(_source_identity(previous, media_identity(previous, historical=True)))
        return _source_identity(source, media_identity(source), history)

    def _queue(self, data, key, placement, stage, files, identity, now):
        fingerprint = _digest(_json_bytes(identity))
        item_id = _digest((key + '\0' + stage + '\0' + fingerprint).encode())
        previous = data['posts'].get(key)
        if previous:
            if previous['month'] != placement['month'] or previous['tag'] != placement['tag']:
                previous.pop('next_at', None)
                if previous.get('status') not in {'uncertain', 'blocked'}:
                    previous['status'] = 'pending'
            previous.update(month=placement['month'], tag=placement['tag'])
        else:
            data['posts'][key] = dict(placement, token=None, parent=None)
        duplicate = item_id in data['snapshots']
        if stage == '01_原帖' and not duplicate:
            for old in data['snapshots'].values():
                if old['post_key'] != key or old['stage'] != stage:
                    continue
                if old.get('source_identity_version') != 2 or not old.get('content_sha256'):
                    original = self._frozen_source_identity(old['files'])
                    if original is not None:
                        old['content_sha256'] = _digest(_json_bytes(original))
                        old['source_identity_version'] = 2
                if old.get('content_sha256') == fingerprint:
                    duplicate = True
        if duplicate:
            self._save(data)
            return False
        version = 1 + sum(item['post_key'] == key and item['stage'] == stage for item in data['snapshots'].values())
        folder = stage if version == 1 else stage + '_v' + str(version)
        frozen = []
        empty = [name for name, content in files.items() if not content]
        if empty:
            files = dict(files, **{'空文件清单.json': _json_bytes(empty)})
        for name, content in sorted(files.items()):
            if not content:
                continue  # Drive 不接收空文件；清单保留其准确文件名。
            if not isinstance(name, str) or name != Path(name).name or name in {'.', '..'}:
                raise MirrorError('镜像快照文件必须使用单个文件名')
            frozen.append({'name': name, 'sha256': self._blob(content), 'token': None})
        data['snapshots'][item_id] = {'post_key': key, 'stage': stage, 'folder_name': folder,
                                       'content_sha256': fingerprint,
                                       'files': frozen, 'status': 'pending', 'folder_token': None,
                                       'attempts': 0, 'next_at': _stamp(now), 'created_at': _stamp(now)}
        if stage == '01_原帖':
            data['snapshots'][item_id]['source_identity_version'] = 2
        self._save(data)
        return True

    @staticmethod
    def _source_media(account_dir, source, media):
        path = store.resolve_media_path(Path(account_dir), source, media)
        if path is None:
            return None, None
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return None, None
        if media.get('sha256') and media['sha256'] != _digest(content):
            return None, None
        return path, content

    def queue_source(self, account_dir: Path, source: dict, *, now: datetime) -> bool:
        source, directory = read_post_truth(Path(account_dir), source)
        files = {'text_en.txt': source['text'].encode('utf-8'), '元信息.json': _json_bytes(source)}
        media_identity, history_identity, missing = [], [], []
        for index, media in enumerate(source.get('media') or []):
            path, content = self._source_media(account_dir, source, media)
            if path is None:
                media_identity.append({'kind': media.get('kind'), 'sha256': media.get('sha256'), 'available': False})
                missing.append({'index': index, 'kind': media.get('kind')})
                continue
            files[f'{index + 1:02d}{path.suffix}'] = content
            media_identity.append({'kind': media.get('kind'), 'sha256': _digest(content), 'available': True})
        history = self._guard(directory / 'source_history.jsonl')
        if history.exists():
            raw = history.read_bytes()
            files['source_history.jsonl'] = raw
            for line in raw.splitlines():
                if not line.strip():
                    continue
                previous = json.loads(line)['source']
                if any(previous.get(key) != source.get(key) for key in ('post_id', 'platform', 'account')):
                    raise MirrorError('源版本历史与当前帖子身份不一致')
                previous_media = []
                for media in previous.get('media') or []:
                    path, content = self._source_media(account_dir, previous, media)
                    if path is None:
                        previous_media.append({'kind': media.get('kind'), 'sha256': media.get('sha256'), 'available': False})
                        missing.append({'history': True, 'kind': media.get('kind')})
                        continue
                    files['原图历史_' + _digest(content)[:12] + '_' + path.name] = content
                    previous_media.append({'kind': media.get('kind'), 'sha256': _digest(content), 'available': True})
                history_identity.append(_source_identity(previous, previous_media))
        identity = _source_identity(source, media_identity, history_identity)
        if missing or source.get('media_complete') is False:
            files['原帖不完整.json'] = _json_bytes({'missing_media': missing,
                                                  'media_complete': source.get('media_complete')})
        key, placement = self._placement(account_dir, source, directory)
        with self._lock():
            data = self._load()
            changed = self._queue(data, key, placement, '01_原帖', files, identity, now)
            data['posts'][key].update(incomplete_source=bool(missing) or source.get('media_complete') is False,
                                      missing_media=missing)
            self._save(data)
            return changed

    @staticmethod
    def _placement(account_dir, source, directory):
        month = store.archive_month(dict(source, folder_name=directory.name))
        # 目录名自己带平台段（F2-2），云盘不再另加前缀，否则会变成 IG_..._IG_...。
        # 2026-09-15 之前建的旧目录名没有平台，补一个前缀让云盘上仍能区分两个平台。
        name = directory.name
        prefix = 'IG' if source['platform'] == 'instagram' else 'FB'
        if prefix not in name.split('_'):
            name = prefix + '_' + name
        return Path(account_dir).name + '/' + source['post_id'], {
            'name': name, 'month': month, 'tag': store.primary_tag_folder(source)}

    def queue_stage(self, account_dir: Path, source: dict, stage: str, files: dict,
                    *, evidence: dict, now: datetime) -> bool:
        """冻结上层已确认的审校/发布产物；绝不把半提交状态投影为 03_已发布。"""
        if stage not in {'approved', 'scheduled'} or evidence.get('status') != stage:
            raise MirrorError('待发布快照要求 approved；已发布快照只接受 scheduled 确认回执')
        if any(evidence.get(key) != source.get(key) for key in ('post_id', 'platform')):
            raise MirrorError('镜像回执与源帖身份不一致')
        if stage == 'scheduled' and not evidence.get('attempt_id'):
            raise MirrorError('已发布快照缺少可追溯的排期 attempt_id')
        account_dir = Path(account_dir)
        current, directory = read_post_truth(account_dir, source)
        frozen = {}
        for name, value in files.items():
            if isinstance(value, Path):
                relative = value.relative_to(account_dir)
                if '..' in relative.parts:
                    raise MirrorError('镜像产物路径超出账号目录')
                parent = account_dir
                for part in relative.parts[:-1]:
                    parent = assert_physical_direct_path(parent, parent / part, kind='directory', label='镜像产物目录')
                self._guard(value)
                value = value.read_bytes()
            if not isinstance(value, bytes):
                raise MirrorError('镜像文件只接受 bytes 或账号归档内的 Path')
            frozen[name] = value
        if not frozen:
            raise MirrorError('镜像阶段产物不能为空')
        frozen['元信息.json'] = _json_bytes(source)
        frozen['排期回执.json' if stage == 'scheduled' else '审校回执.json'] = _json_bytes(evidence)
        identity = {name: _digest(content) for name, content in frozen.items() if name != '元信息.json'}
        key, placement = self._placement(account_dir, current, directory)
        with self._lock():
            return self._queue(self._load(), key, placement,
                               '03_已发布' if stage == 'scheduled' else '02_待发布', frozen, identity, now)

    @staticmethod
    def _backs_up(name: str) -> bool:
        """只收能校验完整性的账本；理由见 STATE_BACKUP_SUFFIXES 那段。"""
        return (name.endswith(STATE_BACKUP_SUFFIXES)
                and not name.startswith(('mirror_queue.', '.mirror_queue.', 'index.sqlite'))
                and not STATE_BACKUP_SKIP_FILE.match(name))

    def queue_state(self, state_dir: Path, *, now: datetime, config_path: Path | None = None) -> bool:
        """按配置间隔备份不可重建的 state 账本；排除派生 DB、锁与镜像自身。"""
        if not self.settings.mirror_state:
            return False
        state_dir = Path(state_dir)
        assert_physical_direct_path(state_dir.parent, state_dir, kind='directory', label='待备份 state')
        with self._lock():
            data = self._load()
            if data.get('last_state_at'):
                elapsed = now - datetime.fromisoformat(data['last_state_at'])
                if elapsed < timedelta(minutes=self.settings.state_interval_minutes):
                    return False
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                if config_path is not None:
                    content = self._stable_state_bytes(Path(config_path))
                    tomllib.loads(content.decode('utf-8'))
                    archive.writestr(zipfile.ZipInfo('config.toml'), content)
                for parent, directories, names in os.walk(state_dir, followlinks=False):
                    directories[:] = sorted(name for name in directories
                                            if name not in STATE_BACKUP_SKIP_DIRS
                                            and not name.endswith('_screenshots'))
                    for name in directories:
                        assert_physical_direct_path(Path(parent), Path(parent) / name, kind='directory', label='待备份 state 子目录')
                    for name in sorted(names):
                        if not self._backs_up(name):
                            continue
                        path = self._guard(Path(parent) / name)
                        relative = path.relative_to(state_dir).as_posix()
                        info = zipfile.ZipInfo(relative)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, self._stable_state_bytes(path))
                # 账本只备份回执；冻结图片按内容版本单独上传。
                snapshots = state_dir / 'publish_snapshots'
                if snapshots.exists():
                    assert_physical_direct_path(state_dir, snapshots, kind='directory', label='冻结快照备份')
                    for directory in sorted(snapshots.iterdir()):
                        assert_physical_direct_path(snapshots, directory, kind='directory', label='冻结版本')
                        for name in ('snapshot.json', 'receipt.json', 'projection.json'):
                            path = self._guard(directory / name)
                            if path.exists():
                                archive.writestr(zipfile.ZipInfo(path.relative_to(state_dir).as_posix()),
                                                 self._stable_state_bytes(path))
            payload = buffer.getvalue()
            if len(payload) > STATE_BACKUP_MAX_BYTES:
                raise MirrorError('state 账本备份达到 %.1f MB，超过 %d MB 上限；'
                                  '请先核对是否有 bulk 文件落进了 state 根目录，本次未创建备份'
                                  % (len(payload) / 1e6, STATE_BACKUP_MAX_BYTES // 1024 // 1024))
            month = store.archive_month(now.isoformat())
            placement = {'name': 'state', 'month': '_state', 'tag': month}
            changed = self._queue(data, '_state/' + month, placement, '快照',
                                  {'state.zip': payload}, {'sha256': _digest(payload)}, now)
            changed = self._queue_evidence(data, state_dir, now) or changed
            data['last_state_at'] = _stamp(now)
            self._save(data)
            return changed

    def _queue_evidence(self, data, state_dir, now):
        """Version each evidence file independently; no hourly image ZIP duplication."""
        changed = False
        for parent, directories, names in os.walk(state_dir, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in {'mirror_spool', 'runtime-backups'})
            for name in directories:
                assert_physical_direct_path(Path(parent), Path(parent) / name, kind='directory', label='证据目录')
            for name in sorted(names):
                relative = (Path(parent) / name).relative_to(state_dir)
                if not (Path(name).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}
                        or STATE_BACKUP_SKIP_FILE.match(name)
                        or relative.parts[0] == 'publish_snapshots' and name in {'text_de.txt', '元信息.json'}):
                    continue
                path = self._guard(Path(parent) / name)
                content = self._stable_state_bytes(path)
                identity = {'path': relative.as_posix(), 'sha256': _digest(content)}
                placement = {'name': _name(relative.as_posix()), 'month': '_evidence', 'tag': relative.parts[0] if len(relative.parts) > 1 else 'screenshots'}
                changed = self._queue(data, '_evidence/' + relative.as_posix(), placement,
                                      '版本', {name: content}, identity, now) or changed
        return changed

    @staticmethod
    def _stable_state_bytes(path: Path) -> bytes:
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns or len(content) != after.st_size):
            raise MirrorError('state 文件读取期间发生变化，本次备份稍后重试：' + path.name)
        try:
            if path.suffix == '.jsonl' and content:
                if not content.endswith(b'\n'):
                    raise ValueError('JSONL 末行尚未写完')
                for line in content.splitlines():
                    if line.strip() and not isinstance(json.loads(line), dict):
                        raise ValueError('JSONL 记录不是对象')
            elif path.suffix == '.json':
                json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise MirrorError('state JSON 记录不完整，本次未创建备份：' + path.name) from exc
        return content

    def _folder(self, data, client, parent, name, now):
        key = parent + '\0' + name
        if key not in data['folders']:
            def create():
                token = client.create_folder(parent, name)
                if not token:
                    raise MirrorError('云盘未确认文件夹 token')
                return token
            token = self._operation(data, 'create_folder', {'parent': parent, 'name': name}, create, now)
            data['folders'][key] = token
            self._save(data)
        return data['folders'][key]

    def _sync_post(self, data, client, post, now):
        if post.get('status') in {'uncertain', 'blocked'}:
            return False
        if post.get('next_at', '') > _stamp(now):
            return False
        try:
            if post.get('move_task'):
                status = client.task_status(post['move_task'])
                if status == 'success':
                    post['parent'] = post.pop('move_parent')
                    post.pop('move_task')
                    post['move_sequence'] = post.get('move_sequence', 0) + 1
                    self._save(data)
                elif status == 'failed':
                    task = post.pop('move_task')
                    post.pop('move_parent')
                    for operation in data['operations'].values():
                        if operation['kind'] == 'move' and operation.get('result') == task:
                            operation.update(status='blocked', error={'status': 'blocked', 'reason': 'move_task_failed'})
                    raise DriveError('blocked')
                else:
                    raise MirrorError('云盘移动仍待确认')
            month = self._folder(data, client, data['root_token'], post['month'], now)
            parent = self._folder(data, client, month, post['tag'], now)
            if not post['token']:
                post.update(token=self._folder(data, client, parent, post['name'], now), parent=parent)
            elif post['parent'] != parent:
                task = self._operation(data, 'move', {'token': post['token'], 'parent': parent,
                    'previous_parent': post['parent'], 'sequence': post.get('move_sequence', 0)},
                    lambda: client.move_folder(post['token'], parent), now)
                if task:
                    post.update(move_task=task, move_parent=parent)
                    self._save(data)
                    raise MirrorError('云盘移动仍待确认')
                post['parent'] = parent
                post['move_sequence'] = post.get('move_sequence', 0) + 1
            post.pop('error', None)
            post.pop('next_at', None)
            post['status'] = 'completed'
            self._save(data)
            return True
        except Exception as exc:
            post.update(status=exc.status if isinstance(exc, DriveError) else 'pending',
                        error=exc.details if isinstance(exc, DriveError) else {'reason': type(exc).__name__},
                        next_at=_stamp(now + timedelta(minutes=15)))
            self._save(data)
            return False

    def _upload_frozen(self, data, client, parent, file, now):
        content = self._content(file['sha256'])
        identity = {'parent': parent, 'name': file['name'], 'sha256': file['sha256']}
        def confirmed(action):
            token = action()
            if not token:
                raise MirrorError('云盘未确认上传文件 token')
            return token
        if len(content) <= getattr(client, 'SMALL_FILE_LIMIT', DriveClient.SMALL_FILE_LIMIT):
            return self._operation(data, 'upload', identity,
                lambda: confirmed(lambda: client.upload_file(parent, file['name'], content)), now)
        prepared = self._operation(data, 'upload_prepare', identity,
            lambda: client.prepare_upload(parent, file['name'], len(content)), now)
        upload_id, size, count = (prepared.get(key) for key in ('upload_id', 'block_size', 'block_num'))
        if (not isinstance(upload_id, str) or not upload_id or type(size) is not int or size < 1
                or type(count) is not int or count != (len(content) + size - 1) // size):
            raise DriveError('uncertain')
        finish_identity = dict(identity, upload_id=upload_id, block_num=count)
        finish_id = _digest(_json_bytes({'kind': 'upload_finish', **finish_identity}))
        finish = data['operations'].get(finish_id)
        if finish and finish['status'] == 'completed':
            return finish['result']
        preparation = data['operations'][_digest(_json_bytes({'kind': 'upload_prepare', **identity}))]
        if now - datetime.fromisoformat(preparation['completed_at']) >= timedelta(hours=24):
            if finish and finish['status'] == 'uncertain':
                raise DriveError('uncertain')
            preparation.update(status='blocked', error={'status': 'blocked', 'reason': 'upload_expired'})
            self._save(data)
            raise DriveError('blocked')
        for sequence in range(count):
            block = content[sequence * size:(sequence + 1) * size]
            self._operation(data, 'upload_part', dict(identity, upload_id=upload_id, sequence=sequence,
                block_sha256=_digest(block)),
                lambda: client.upload_part(upload_id, sequence, file['name'], block), now)
        return self._operation(data, 'upload_finish', finish_identity,
            lambda: confirmed(lambda: client.finish_upload(upload_id, count)), now)

    def dispatch(self, client, *, now: datetime) -> dict:
        """每个上传回执落盘后才继续；失败保留 pending，并且至少等待 15 分钟。"""
        if not self.settings.enabled:
            return {'completed': 0, 'pending': 0}
        stamp = _stamp(now)
        completed = 0
        with self._lock():
            data = self._load()
            ready = set()
            for key, post in data['posts'].items():
                self._dispatch_post_key = key
                if self._sync_post(data, client, post, now):
                    ready.add(key)
            for item in data['snapshots'].values():
                if (item['status'] in {'completed', 'uncertain', 'blocked'} or item['next_at'] > stamp
                        or item['post_key'] not in ready):
                    continue
                self._dispatch_post_key = item['post_key']
                item['attempts'] += 1
                item['next_at'] = _stamp(now + timedelta(minutes=15))
                self._save(data)
                try:
                    post = data['posts'][item['post_key']]
                    if not item['folder_token']:
                        item['folder_token'] = self._folder(data, client, post['token'], item['folder_name'], now)
                        self._save(data)
                    for file in item['files']:
                        if file['token']:
                            continue
                        token = self._upload_frozen(data, client, item['folder_token'], file, now)
                        file['token'] = token
                        self._save(data)
                    item.update(status='completed', completed_at=stamp)
                    data['last_success_at'] = stamp
                    item.pop('error', None)
                    completed += 1
                except Exception as exc:
                    item.update(status=exc.status if isinstance(exc, DriveError) else 'blocked',
                                error=exc.details if isinstance(exc, DriveError) else {'reason': type(exc).__name__})
                self._save(data)
            pending = sum(item['status'] != 'completed' for item in data['snapshots'].values())
            pending += len(data['posts']) - len(ready)
        return {'completed': completed, 'pending': pending}

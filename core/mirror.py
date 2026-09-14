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
from core.feishu import FeishuClient
from core.paid_model import FileLock, atomic_write_json
from core.store import (ArchivePathError, assert_physical_direct_path,
                        assert_post_directory, read_post_truth)


class MirrorError(RuntimeError):
    pass


class DriveClient(FeishuClient):
    """只包含云端新增、移动与异步状态查询；复用飞书凭据及脱敏 HTTP 错误处理。"""
    SMALL_FILE_LIMIT = 20 * 1024 * 1024

    def _pace_drive(self):
        gap = .25 - (time.monotonic() - getattr(self, '_last_drive_request', 0.0))
        if gap > 0:
            time.sleep(gap)
        self._last_drive_request = time.monotonic()

    def _drive_post(self, path, **kwargs):
        self._pace_drive()
        return self._post(path, **kwargs)

    def create_folder(self, parent: str, name: str) -> str:
        result = self._drive_post('drive/v1/files/create_folder', headers=self._headers(),
                            json={'name': name, 'folder_token': parent})
        return str((result.get('data') or {}).get('token') or '')

    def upload_file(self, parent: str, name: str, content: bytes) -> str:
        if not content:
            raise MirrorError('Drive 不接收空文件；应使用快照空文件清单留证')
        if len(content) > self.SMALL_FILE_LIMIT:
            prepared = self._drive_post('drive/v1/files/upload_prepare', headers=self._headers(),
                                  json={'file_name': name, 'parent_type': 'explorer',
                                        'parent_node': parent, 'size': len(content)})['data']
            upload_id = prepared.get('upload_id')
            size, count = prepared.get('block_size'), prepared.get('block_num')
            if (not upload_id or not isinstance(size, int) or isinstance(size, bool) or size < 1
                    or count != (len(content) + size - 1) // size):
                raise MirrorError('云盘分片上传参数不完整')
            for sequence in range(count):
                block = content[sequence * size:(sequence + 1) * size]
                self._drive_post('drive/v1/files/upload_part', headers=self._headers(), timeout=120,
                           data={'upload_id': upload_id, 'seq': str(sequence), 'size': str(len(block)),
                                 'checksum': str(zlib.adler32(block))},
                           files={'file': (name, block, 'application/octet-stream')})
            result = self._drive_post('drive/v1/files/upload_finish', headers=self._headers(),
                                json={'upload_id': upload_id, 'block_num': count})
            return str((result.get('data') or {}).get('file_token') or '')
        result = self._drive_post('drive/v1/files/upload_all', headers=self._headers(), timeout=120,
                            data={'file_name': name, 'parent_type': 'explorer',
                                  'parent_node': parent, 'size': str(len(content))},
                            files={'file': (name, content, 'application/octet-stream')})
        return str((result.get('data') or {}).get('file_token') or '')

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


def _name(value: str) -> str:
    cleaned = re.sub(r'[\s/\\<>:"|?*\x00-\x1f]+', '-', str(value)).strip(' .-')
    if not cleaned:
        return '未分类'
    return cleaned if len(cleaned) <= 100 else cleaned[:80] + '-' + _digest(cleaned.encode())[:12]


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
            return {'schema_version': 1, 'root_token': self.settings.root_folder_token,
                    'posts': {}, 'folders': {}, 'snapshots': {}}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('schema_version') != 1
                or any(not isinstance(data.get(key), dict) for key in ('posts', 'folders', 'snapshots'))):
            raise MirrorError('镜像队列损坏，保留现场，不重置云盘回执')
        if data['root_token'] != self.settings.root_folder_token:
            raise MirrorError('镜像根目录与既有回执不同，请核对后使用独立镜像状态目录')
        return data

    def _save(self, data):
        atomic_write_json(self.path, data, guard=self._guard)

    def snapshot(self) -> dict:
        """只读查看队列；不创建文件、不构造网络客户端。"""
        return self._load()

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

    def _queue(self, data, key, placement, stage, files, identity, now):
        fingerprint = _digest(_json_bytes(identity))
        item_id = _digest((key + '\0' + stage + '\0' + fingerprint).encode())
        previous = data['posts'].get(key)
        if previous:
            if previous['month'] != placement['month'] or previous['tag'] != placement['tag']:
                previous.pop('next_at', None)
            previous.update(month=placement['month'], tag=placement['tag'])
        else:
            data['posts'][key] = dict(placement, token=None, parent=None)
        if item_id in data['snapshots']:
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
                                      'files': frozen, 'status': 'pending', 'folder_token': None,
                                      'attempts': 0, 'next_at': _stamp(now), 'created_at': _stamp(now)}
        self._save(data)
        return True

    def queue_source(self, account_dir: Path, source: dict, *, now: datetime) -> bool:
        source, directory = read_post_truth(Path(account_dir), source)
        files = {'text_en.txt': source['text'].encode('utf-8'), '元信息.json': _json_bytes(source)}
        for index, media in enumerate(source.get('media') or []):
            local = media.get('local_path')
            if not local:
                continue
            path = Path(account_dir) / local
            assert_post_directory(Path(account_dir), path.parent)
            if path.parent != directory:
                raise ArchivePathError('镜像原图不在该帖真相目录')
            self._guard(path)
            files[f'{index + 1:02d}{path.suffix}'] = path.read_bytes()
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
                for media in previous.get('media') or []:
                    if not media.get('local_path'):
                        continue
                    path = Path(account_dir) / media['local_path']
                    if path.parent != directory:
                        raise ArchivePathError('历史原图不在该帖真相目录，请先核对版本历史')
                    self._guard(path)
                    content = path.read_bytes()
                    files['原图历史_' + _digest(content)[:12] + '_' + path.name] = content
        identity = dict(source)
        identity.pop('tags', None)
        identity.pop('folder_name', None)
        identity['file_hashes'] = {name: _digest(content) for name, content in files.items() if name != '元信息.json'}
        key, placement = self._placement(account_dir, source, directory)
        with self._lock():
            return self._queue(self._load(), key, placement, '01_原帖', files, identity, now)

    @staticmethod
    def _placement(account_dir, source, directory):
        created = source.get('created_at') or ''
        month = created[:7] if re.match(r'^\d{4}-\d{2}', created) else 'undated'
        return Path(account_dir).name + '/' + source['post_id'], {
            'name': ('IG_' if source['platform'] == 'instagram' else 'FB_') + directory.name,
            'month': month, 'tag': _name((source.get('tags') or ['未分类'])[0])}

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
            placement = {'name': 'state', 'month': '_state', 'tag': now.strftime('%Y-%m')}
            changed = self._queue(data, '_state/' + now.strftime('%Y-%m'), placement, '快照',
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

    def _folder(self, data, client, parent, name):
        key = parent + '\0' + name
        if key not in data['folders']:
            token = client.create_folder(parent, name)
            if not token:
                raise MirrorError('云盘未确认文件夹 token')
            data['folders'][key] = token
            self._save(data)
        return data['folders'][key]

    def _sync_post(self, data, client, post, now):
        if post.get('next_at', '') > _stamp(now):
            return False
        try:
            if post.get('move_task'):
                status = client.task_status(post['move_task'])
                if status == 'success':
                    post['parent'] = post.pop('move_parent')
                    post.pop('move_task')
                    self._save(data)
                elif status == 'failed':
                    post.pop('move_task')
                    post.pop('move_parent')
                    raise MirrorError('云盘移动任务失败，保留原目录回执并重试')
                else:
                    raise MirrorError('云盘移动仍待确认')
            month = self._folder(data, client, data['root_token'], post['month'])
            parent = self._folder(data, client, month, post['tag'])
            if not post['token']:
                post.update(token=self._folder(data, client, parent, post['name']), parent=parent)
            elif post['parent'] != parent:
                task = client.move_folder(post['token'], parent)
                if task:
                    post.update(move_task=task, move_parent=parent)
                    self._save(data)
                    raise MirrorError('云盘移动仍待确认')
                post['parent'] = parent
            post.pop('error', None)
            post.pop('next_at', None)
            self._save(data)
            return True
        except Exception as exc:
            post.update(error=type(exc).__name__, next_at=_stamp(now + timedelta(minutes=15)))
            self._save(data)
            return False

    def dispatch(self, client, *, now: datetime) -> dict:
        """每个上传回执落盘后才继续；失败保留 pending，并且至少等待 15 分钟。"""
        if not self.settings.enabled:
            return {'completed': 0, 'pending': 0}
        stamp = _stamp(now)
        completed = 0
        with self._lock():
            data = self._load()
            ready = {key for key, post in data['posts'].items() if self._sync_post(data, client, post, now)}
            for item in data['snapshots'].values():
                if (item['status'] == 'completed' or item['next_at'] > stamp
                        or item['post_key'] not in ready):
                    continue
                item['attempts'] += 1
                item['next_at'] = _stamp(now + timedelta(minutes=15))
                self._save(data)
                try:
                    post = data['posts'][item['post_key']]
                    if not item['folder_token']:
                        item['folder_token'] = self._folder(data, client, post['token'], item['folder_name'])
                        self._save(data)
                    for file in item['files']:
                        if file['token']:
                            continue
                        token = client.upload_file(item['folder_token'], file['name'], self._content(file['sha256']))
                        if not token:
                            raise MirrorError('云盘未确认上传文件 token')
                        file['token'] = token
                        self._save(data)
                    item.update(status='completed', completed_at=stamp)
                    item.pop('error', None)
                    completed += 1
                except Exception as exc:
                    item.update(status='pending', error=type(exc).__name__)
                self._save(data)
            pending = sum(item['status'] != 'completed' for item in data['snapshots'].values())
            pending += len(data['posts']) - len(ready)
        return {'completed': completed, 'pending': pending}

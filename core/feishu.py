"""飞书私聊卡片与耐久发件箱；凭据从环境变量读取。"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx

from core.config import MonitorSchedule, cfg
from core.paid_model import FileLock, atomic_write_json, ModelCredentials

KINDS = {'discovered', 'ready', 'backlog', 'scheduled', 'schedule_failed', 'system', 'morning'}
TITLES = {'discovered': '监测到新帖', 'ready': '新的待审内容', 'backlog': '待审情况', 'scheduled': '排期已确认',
          'schedule_failed': '排期未完成，请核对', 'system': '系统需要处理', 'morning': '晨间处理情况'}
# 静默窗只压业务待办；发现提醒和系统告警是链路存活信号，压住它们等于让沉默继续有歧义。
ALWAYS_DELIVERED = {'system', 'discovered'}
# 发送前重新取当前素材并上传首图的消息类型。
PREVIEWED = {'ready', 'discovered'}


class FeishuError(RuntimeError):
    pass


class FeishuAuthError(FeishuError):
    pass


@dataclass(frozen=True)
class FeishuSettings:
    enabled: bool
    base_url: str
    recipients: tuple[str, ...]
    technical_recipients: tuple[str, ...]
    keep_delivered_days: int = 30

    def __post_init__(self):
        if type(self.keep_delivered_days) is not int or self.keep_delivered_days < 1:
            raise ValueError('[feishu].keep_delivered_days 必须是正整数')

    @classmethod
    def load(cls):
        c = cfg()
        enabled = c.get('feishu', 'enabled', False)
        if not isinstance(enabled, bool):
            raise ValueError('[feishu].enabled 必须是布尔值')
        result = cls(enabled, c.get('feishu', 'base_url', ''),
                     tuple(c.get('feishu', 'recipients', [])),
                     tuple(c.get('feishu', 'technical_recipients', [])),
                     c.get('feishu', 'keep_delivered_days', 30))
        if enabled:
            result.validate()
        return result

    def validate(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('请配置可访问的审校台 base_url，不要包含凭据、查询参数或锚点')
        if not self.recipients or not self.technical_recipients:
            raise ValueError('请分别配置运营与开发者的飞书 open_id')
        if any(not isinstance(item, str) or not item.strip()
               for item in (*self.recipients, *self.technical_recipients)):
            raise ValueError('飞书收件人必须是非空 open_id 字符串')
        if set(self.recipients) & set(self.technical_recipients):
            raise ValueError('运营和开发者接收组应分别配置')


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, *, http=None):
        if not app_id or not app_secret:
            raise FeishuError('未配置 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量')
        self.app_id, self.app_secret = app_id, app_secret
        self.http = http or httpx.Client(timeout=15, follow_redirects=False)
        self.token, self.expires = '', 0.0

    @classmethod
    def from_environment(cls):
        return cls(ModelCredentials('FEISHU_APP_ID').optional_value(),
                   ModelCredentials('FEISHU_APP_SECRET').optional_value())

    def close(self):
        self.http.close()

    def _post(self, path: str, **kwargs) -> dict:
        try:
            response = self.http.post('https://open.feishu.cn/open-apis/' + path, **kwargs)
            if not response.is_success:
                raise FeishuError('飞书 HTTP 请求失败（状态 %d）' % response.status_code)
            data = response.json()
            if not isinstance(data, dict) or data.get('code') != 0:
                code = data.get('code') if isinstance(data, dict) else 'invalid'
                raise FeishuError('飞书未接受请求（code=%s），请检查应用授权与收件人范围' % code)
            return data
        except (httpx.HTTPError, ValueError) as exc:
            # 不记录响应体或异常原文，认证响应可能含令牌。
            raise FeishuError('飞书请求未能确认成功，请检查网络或应用配置') from exc

    def _headers(self) -> dict:
        if time.monotonic() >= self.expires:
            try:
                data = self._post('auth/v3/tenant_access_token/internal',
                                  json={'app_id': self.app_id, 'app_secret': self.app_secret})
            except FeishuError as exc:
                raise FeishuAuthError('飞书认证未通过，请核对应用凭据与管理员授权') from exc
            self.token = str(data.get('tenant_access_token') or '')
            if not self.token:
                raise FeishuError('飞书认证响应缺少 tenant_access_token')
            self.expires = time.monotonic() + max(0, int(data.get('expire', 0)) - 60)
        return {'Authorization': 'Bearer ' + self.token}

    def upload_image(self, content: bytes) -> str:
        """im/v1/images 官方 multipart 契约；只上传已读取的本地图片。"""
        if not content or len(content) >= 10 * 1024 * 1024:
            raise FeishuError('通知图片须小于 10 MB 且非空')
        result = self._post('im/v1/images', headers=self._headers(),
            data={'image_type': 'message'}, files={'image': ('preview.jpg', content)})
        key = (result.get('data') or {}).get('image_key')
        if not isinstance(key, str) or not key:
            raise FeishuError('通知图片未获得上传回执')
        return key

    def send_card(self, recipient: str, card: dict, delivery_id: str) -> str:
        result = self._post('im/v1/messages', params={'receive_id_type': 'open_id'},
                            headers=self._headers(),
                            json={'receive_id': recipient, 'msg_type': 'interactive',
                                  'content': json.dumps(card, ensure_ascii=False), 'uuid': delivery_id})
        message_id = str((result.get('data') or {}).get('message_id') or '')
        if not message_id:
            raise FeishuError('飞书响应缺少 message_id，投递结果尚不明确')
        return message_id


def notification_card(kind: str, payloads: list[dict], settings: FeishuSettings) -> dict:
    """源文案以纯文本渲染；按钮只导航审校台，不在卡片里直接批准发布。"""
    elements = []
    for payload in payloads:
        if payload.get('image_key'):
            elements.append({'tag': 'img', 'img_key': payload['image_key'],
                             'alt': {'tag': 'plain_text', 'content': payload.get('image_note', '帖子首图')}, 'mode': 'fit_horizontal'})
        parts = [str(payload[key]) for key in ('platform', 'account', 'created_at', 'meta', 'text',
                                               'image_note', 'risk', 'next_step')
                 if payload.get(key)]
        elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': '\n'.join(parts)[:1600]}})
        if payload.get('task_id'):
            url = settings.base_url.rstrip('/') + '/?task=' + quote(str(payload['task_id']), safe='')
            actions = [{'tag': 'button', 'type': 'primary',
                        'text': {'tag': 'plain_text', 'content': '去审校'}, 'url': url}]
            original = urlsplit(str(payload.get('permalink') or ''))
            if original.scheme == 'https' and original.hostname and not original.username and not original.password:
                actions.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': '查看原帖'},
                                'url': payload['permalink']})
            elements.append({'tag': 'action', 'actions': [
                *actions]})
    title = TITLES[kind] + (f' · {len(payloads)} 篇' if len(payloads) > 1 else '')
    return {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': 'Neakasa 德国站 · ' + title},
                       'template': 'red' if kind in {'system', 'schedule_failed'} else 'blue'},
            'elements': elements}


def _iso(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('通知时刻必须带时区')
    return now.astimezone(timezone.utc).isoformat()


class Outbox:
    """仅保存投递事实，不参与审校或排期判定；成功与不确定投递都可追溯。"""
    def __init__(self, path: Path, settings: FeishuSettings, *, schedule=None):
        self.path, self.settings = Path(path), settings
        self.schedule = schedule or MonitorSchedule.load()

    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(self.path.with_suffix('.lock'), busy_message='飞书发件箱正在投递，请稍后重试')

    def _load(self):
        if not self.path.exists():
            return {'schema_version': 1, 'events': {}, 'deliveries': {}, 'archived_events': {}}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or not isinstance(data.get('events'), dict)
                or not isinstance(data.get('deliveries'), dict)):
            raise FeishuError('飞书投递记录损坏，请先核对；未发送消息')
        for row in data['deliveries'].values():
            if (not isinstance(row, dict) or row.get('status') not in {'pending', 'retry', 'sent', 'uncertain', 'cancelled'}
                    or not isinstance(row.get('events'), list) or not isinstance(row.get('card'), dict)):
                raise FeishuError('飞书投递条目损坏，请保留现场核对')
        index = data.setdefault('archived_events', {})
        if (not isinstance(index, dict) or any(not isinstance(key, str)
                or not isinstance(value, str) or len(value) != 64
                or any(char not in '0123456789abcdef' for char in value)
                for key, value in index.items())):
            raise FeishuError('飞书归档去重索引损坏，请先核对；未发送消息')
        return data

    def enqueue(self, event_id: str, kind: str, payload: dict, now: datetime) -> bool:
        if kind not in KINDS or not event_id:
            raise ValueError('通知需要有效的事件 ID 与类型')
        with self._lock():
            data = self._load()
            if event_id in data['archived_events']:
                return False
            if event_id in data['events']:
                assigned = [item for item in data['deliveries'].values() if event_id in item['events']]
                # 首次尝试后冻结事件集、卡片和 UUID；仅未尝试消息可跟随人工修改。
                if (kind == data['events'][event_id]['kind'] and not any(item['attempts'] for item in assigned)
                        and data['events'][event_id]['payload'] != payload):
                    data['events'][event_id]['payload'] = payload
                    data['deliveries'] = {key: item for key, item in data['deliveries'].items()
                                          if event_id not in item['events']}
                    atomic_write_json(self.path, data)
                return False
            data['events'][event_id] = {'kind': kind, 'payload': payload, 'created_at': _iso(now)}
            atomic_write_json(self.path, data)
        return True

    def status(self) -> dict:
        """Safe read-only delivery health; no recipient IDs, credentials or message bodies."""
        try:
            data = self._load()
        except (OSError, ValueError, TypeError, FeishuError):
            return {'enabled': self.settings.enabled, 'status': 'state_unreadable', 'counts': {}}
        counts = {}
        deliveries = []
        for identifier, row in data['deliveries'].items():
            counts[row['status']] = counts.get(row['status'], 0) + 1
            deliveries.append({'delivery_id': identifier, **{key: row.get(key) for key in
                               ('kind', 'status', 'attempts', 'created_at', 'next_at', 'error', 'sent_at', 'preview_error')},
                               'version': self._version(row), 'task_ids': [data['events'][key]['payload'].get('task_id')
                                   for key in row['events'] if key in data['events']]})
        return {'enabled': self.settings.enabled, 'status': 'disabled' if not self.settings.enabled else
                'needs_attention' if any(counts.get(key) for key in ('retry', 'uncertain')) else 'ready',
                'counts': counts, 'deliveries': deliveries[-100:]}

    @staticmethod
    def _version(row):
        return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _archive_path(self, identifier):
        return self.path.with_name(self.path.stem + '_archive') / (identifier + '.json')

    def _read_archive(self, identifier):
        data = json.loads(self._archive_path(identifier).read_text(encoding='utf-8'))
        if self._version(data) != identifier:
            raise FeishuError('飞书归档内容与指纹不一致，请保留现场核对')
        return data

    def archived_event(self, event_id):
        """Read the original event and its immutable connected delivery receipts."""
        identifier = self._load()['archived_events'].get(event_id)
        if identifier is None:
            return None
        archived = self._read_archive(identifier)
        return {'event': archived['events'][event_id], 'deliveries': archived['deliveries']}

    def _archive_completed(self, data, now):
        """Archive whole event/delivery components before trimming their live data."""
        cutoff = now - timedelta(days=self.settings.keep_delivered_days)

        def old(value):
            try:
                stamp = datetime.fromisoformat(value)
                return stamp.tzinfo is not None and stamp.utcoffset() is not None and stamp < cutoff
            except (ValueError, TypeError):
                return False

        def terminal(item):
            if item['status'] == 'sent':
                return old(item.get('sent_at'))
            if item['status'] != 'cancelled':
                return False
            timestamps = [item[key] for key in ('cancelled_at', 'retry_authorized_at') if item.get(key)]
            return bool(timestamps) and all(old(value) for value in timestamps)

        linked = {}
        for identifier, item in data['deliveries'].items():
            for event_id in item['events']:
                linked.setdefault(event_id, set()).add(identifier)
        visited = set()
        changed = False
        for initial in list(data['events']):
            if initial in visited:
                continue
            events, deliveries, pending = set(), set(), [initial]
            while pending:
                event_id = pending.pop()
                if event_id in events:
                    continue
                events.add(event_id)
                for identifier in linked.get(event_id, ()):
                    if identifier not in deliveries:
                        deliveries.add(identifier)
                        pending.extend(data['deliveries'][identifier]['events'])
            visited.update(events)
            if not events.issubset(data['events']) or not all(terminal(data['deliveries'][key]) for key in deliveries):
                continue
            eligible = True
            for event_id in events:
                event = data['events'][event_id]
                if event.get('cancelled_at'):
                    eligible = old(event['cancelled_at'])
                else:
                    # Unassigned off-duty work and missing recipient receipts remain live.
                    assigned = [data['deliveries'][key] for key in linked.get(event_id, ())]
                    recipients = (self.settings.technical_recipients if event['kind'] == 'system'
                                  else self.settings.recipients)
                    required = set(recipients) | {item['recipient'] for item in assigned}
                    received = {item['recipient'] for item in assigned if item['status'] == 'sent'}
                    eligible = bool(assigned) and old(event.get('created_at')) and required <= received
                if not eligible:
                    break
            if not eligible:
                continue
            archived = {'schema_version': 1,
                        'events': {key: data['events'][key] for key in sorted(events)},
                        'deliveries': {key: data['deliveries'][key] for key in sorted(deliveries)}}
            identifier = self._version(archived)
            path = self._archive_path(identifier)
            if path.exists():
                self._read_archive(identifier)
            else:
                # 内容寻址让裁剪失败后的重试复用同一归档。
                atomic_write_json(path, archived)
            for event_id in events:
                data['archived_events'][event_id] = identifier
                del data['events'][event_id]
            for delivery_id in deliveries:
                del data['deliveries'][delivery_id]
            changed = True
        if changed:
            atomic_write_json(self.path, data)

    def resolve(self, identifier, *, action, expected_version, message_id='', now):
        """Record a human-checked result; this call never sends a message."""
        if action not in {'delivered', 'not_delivered'}:
            raise ValueError('请选择已确认送达或已核对未送达')
        if action == 'delivered' and (not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 200):
            raise ValueError('请填写核对后的飞书消息 ID')
        with self._lock():
            data = self._load()
            row = data['deliveries'].get(identifier)
            if not row or self._version(row) != expected_version or row['status'] not in {'retry', 'uncertain'}:
                raise FeishuError('消息状态已有变化，请刷新后再核对')
            stamp = _iso(now)
            row.setdefault('resolutions', []).append({'action': action, 'recorded_at': stamp, 'actor': None})
            if action == 'delivered':
                row.update(status='sent', message_id=message_id.strip(), sent_at=stamp)
            else:
                # 取消的提醒已过期，保留原卡片和 UUID，不重新入队。
                obsolete = any(data['events'][key].get('cancelled_at') for key in row['events'])
                row.update(status='cancelled' if obsolete else 'retry', next_at=stamp,
                           retry_authorized_at=stamp)
            row.pop('error', None)
            atomic_write_json(self.path, data)
        return self.status()

    def started_at(self, now: datetime) -> datetime:
        """第一次启用的本地边界；不把旧发布回执当成今天的新消息。"""
        with self._lock():
            data = self._load()
            if not data.get('started_at'):
                data['started_at'] = _iso(now)
                atomic_write_json(self.path, data)
            return datetime.fromisoformat(data['started_at'])

    def retain_ready(self, valid_ids: set[str], now: datetime) -> None:
        """撤下过期待审提醒。已尝试的请求保留不确定事实，不能改卡片后换 UUID 重发。"""
        with self._lock():
            data = self._load()
            invalid = {key for key, item in data['events'].items()
                       if item['kind'] in {'ready', 'backlog'} and key not in valid_ids}
            for key in invalid:
                data['events'][key].setdefault('cancelled_at', _iso(now))
            for key in valid_ids:
                if key in data['events']:
                    data['events'][key].pop('cancelled_at', None)
            for item in data['deliveries'].values():
                if (item['status'] not in {'sent', 'uncertain', 'cancelled'}
                        and invalid.intersection(item['events'])):
                    item['status'] = 'uncertain' if item['attempts'] else 'cancelled'
                    item['cancelled_at'] = _iso(now)
            atomic_write_json(self.path, data)

    def dispatch(self, now: datetime, send: Callable[[str, dict, str], str], *, prepare_payload=None) -> int:
        if not self.settings.enabled:
            return 0
        self.settings.validate()
        stamp = _iso(now)
        sent = 0
        with self._lock():
            data = self._load()
            for kind in sorted(KINDS):
                if kind not in ALWAYS_DELIVERED and not self.schedule.is_on_duty(now):
                    continue
                recipients = (self.settings.technical_recipients if kind == 'system' else self.settings.recipients)
                for recipient in dict.fromkeys(recipients):
                    assigned = {event_id for item in data['deliveries'].values()
                                if item['status'] != 'cancelled' and item['recipient'] == recipient for event_id in item['events']}
                    ids = [key for key, item in data['events'].items()
                           if key not in assigned and item['kind'] == kind and not item.get('cancelled_at')]
                    # Recipient success never masks another recipient's missing reminder.
                    overnight = [key for key in ids if kind == 'ready' and not self.schedule.is_on_duty(
                        datetime.fromisoformat(data['events'][key]['created_at']))]
                    groups = [overnight[offset:offset + 10] for offset in range(0, len(overnight), 10)]
                    groups.extend([key] for key in ids if key not in overnight)
                    for group in groups:
                        payloads = [dict(data['events'][key]['payload']) for key in group]
                        for payload in payloads:
                            if kind in PREVIEWED and prepare_payload is not None:
                                try:
                                    prepare_payload(kind, payload)
                                except Exception as exc:
                                    payload['next_step'] = '预览图暂未加载，请进入审校台查看完整素材。'
                                    payload['preview_error'] = 'authentication' if isinstance(exc, FeishuAuthError) else 'image_upload_failed'
                        card = notification_card(kind, payloads, self.settings)
                        seed = recipient + '\0' + '\0'.join(group)
                        delivery_id = hashlib.sha256(seed.encode()).hexdigest()[:32]
                        version = 1
                        while delivery_id in data['deliveries']:
                            # 已取消的尝试保持不变；核对结果后另建提醒。
                            delivery_id = hashlib.sha256((seed + ':v' + str(version)).encode()).hexdigest()[:32]
                            version += 1
                        data['deliveries'][delivery_id] = {
                            'events': group, 'kind': kind, 'recipient': recipient, 'card': card,
                            'status': 'pending', 'attempts': 0, 'next_at': stamp, 'created_at': stamp,
                            'preview_error': next((p['preview_error'] for p in payloads if p.get('preview_error')), None)}
            # 先固定每次投递的事件集合与 UUID，重启后不会把新条目混入未确认的请求。
            atomic_write_json(self.path, data)
            for delivery_id, item in data['deliveries'].items():
                if item['status'] in {'sent', 'uncertain', 'cancelled'} or item['next_at'] > stamp:
                    continue
                if item['kind'] not in ALWAYS_DELIVERED and not self.schedule.is_on_duty(now):
                    continue
                if item['attempts'] and now - datetime.fromisoformat(item.get('retry_authorized_at', item['created_at'])) >= timedelta(minutes=50):
                    # 不无限依赖远端去重时间窗；旧不确定请求交由人核对，避免重复催促。
                    item['status'] = 'uncertain'
                    atomic_write_json(self.path, data)
                    continue
                item['attempts'] += 1
                item['next_at'] = _iso(now + timedelta(minutes=15))
                atomic_write_json(self.path, data)
                try:
                    message_id = send(item['recipient'], item['card'], delivery_id)
                    if not message_id:
                        raise FeishuError('未获得投递回执')
                except Exception as exc:
                    item['error'] = str(exc) if isinstance(exc, FeishuError) else type(exc).__name__
                    item['status'] = 'retry'
                else:
                    item.update(status='sent', message_id=message_id, sent_at=stamp)
                    sent += 1
                atomic_write_json(self.path, data)
            self._archive_completed(data, now)
        return sent

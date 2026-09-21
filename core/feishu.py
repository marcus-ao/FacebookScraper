"""飞书群卡片与耐久发件箱；凭据从环境变量读取。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx

from core.config import MonitorSchedule, cfg
from core.web_access import load_web_access
from core.paid_model import FileLock, atomic_write_json, ModelCredentials

# ⚠️ dispatch 先按 sorted(KINDS) 建投递，再按建立顺序逐条发送——**kind 名字的字典序就是群里
# 消息的先后顺序**。monitor_found < monitor_saved 才让"监测到"排在"抓取完成"前面；改名会静默换序。
MONITOR_KINDS = frozenset({'monitor_found', 'monitor_saved'})
KIND_ROLES = {'monitor_found': 'detect', 'monitor_saved': 'capture', 'ready': 'publish',
              'scheduled': 'publish', 'backlog': 'alert', 'schedule_failed': 'alert',
              'morning': 'alert', 'system': 'alert'}
KINDS = set(KIND_ROLES)
# 静默窗只压业务待办；监测播报和系统告警是链路存活信号，压住它们等于让沉默继续有歧义。
# 这里只管静默豁免；同一告警机器人接收的不同 kind 仍分别判断静默。
ALWAYS_DELIVERED = {'system', *MONITOR_KINDS}
# 发送前重新取当前素材的消息类型。监测卡的内容在入队时已由抓取事实定稿，不参与重取。
PREVIEWED = {'ready'}
# selftest 故意只在 TITLES 里、不在 KINDS 里：它能渲染成卡片，但 enqueue 会拒绝它，
# 所以人工自检不会在发件箱里留下假事件。
TITLES = {'ready': '新的待审内容', 'backlog': '待审情况', 'scheduled': '排期已确认',
          'schedule_failed': '排期未完成，请核对', 'system': '系统需要处理', 'morning': '晨间处理情况',
          'monitor_found': '监测到新帖', 'monitor_saved': '原帖抓取结果', 'selftest': '通道自检'}


class FeishuError(RuntimeError):
    pass


class FeishuAuthError(FeishuError):
    pass


class FeishuRejected(FeishuError):
    """已明确拒绝的请求可退避重试；其余异常均可能已经送达。"""


@dataclass(frozen=True)
class FeishuSettings:
    enabled: bool
    base_url: str
    # 接收方是角色标签，不是群地址。发件箱会把它原样写进投递记录，而群机器人地址带
    # token——落盘就等于把凭据写进了状态文件（红线 10）。URL 由 WebhookBot 按标签解析。
    publish_recipients: tuple[str, ...] = ('publish',)
    alert_recipients: tuple[str, ...] = ('alert',)
    keep_delivered_days: int = 30
    detect_recipients: tuple[str, ...] = ('detect',)
    capture_recipients: tuple[str, ...] = ('capture',)

    def __post_init__(self):
        if type(self.keep_delivered_days) is not int or self.keep_delivered_days < 1:
            raise ValueError('[feishu].keep_delivered_days 必须是正整数')

    @classmethod
    def load(cls):
        c = cfg()
        enabled = c.get('feishu', 'enabled', False)
        if not isinstance(enabled, bool):
            raise ValueError('[feishu].enabled 必须是布尔值')
        # Web 与卡片共用显式网络策略；独立调度器未设置网络环境变量时读 TOML。
        network_configured = (os.environ.get('FBSCRAPER_CONTROL_DIR')
                              or os.environ.get('FBSCRAPER_NETWORK_CONFIG', '').strip())
        base_url = load_web_access().public_base_url if network_configured else c.get('feishu', 'base_url', '')
        result = cls(enabled, base_url,
                     keep_delivered_days=c.get('feishu', 'keep_delivered_days', 30))
        if enabled:
            result.validate()
        return result

    def validate(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('审校台 base_url 须为 HTTP(S) 地址，不含凭据、查询参数或锚点')
        groups = [getattr(self, role + '_recipients') for role in WEBHOOK_ROLES]
        if any(not group for group in groups):
            raise ValueError('检测、爬取、发布、状态告警各需要一个接收角色')
        if any(not isinstance(item, str) or not item.strip()
               for group in groups for item in group):
            raise ValueError('飞书接收角色必须是非空标签')
        if sum(len(set(group)) for group in groups) != len({item for group in groups for item in group}):
            raise ValueError('四个阶段机器人必须分别配置接收角色')

    def recipients_for(self, kind: str) -> tuple[str, ...]:
        return getattr(self, KIND_ROLES[kind] + '_recipients')


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


WEBHOOK_ROLES = {'detect': 'FEISHU_WEBHOOK_DETECT', 'capture': 'FEISHU_WEBHOOK_CAPTURE',
                 'publish': 'FEISHU_WEBHOOK_PUBLISH', 'alert': 'FEISHU_WEBHOOK_ALERT'}
BOT_LABELS = {'detect': '新帖检测推送机器人', 'capture': '新帖爬取推送机器人',
              'publish': '新帖发布推送机器人', 'alert': '状态告警推送机器人'}
WEBHOOK_PREFIX = 'https://open.feishu.cn/open-apis/bot/v2/hook/'


def webhook_configuration(targets: dict) -> dict:
    """仅返回角色和配置健康度，运行页和传输端共用；地址不进入状态。"""
    bots, normalized = [], []
    for role in WEBHOOK_ROLES:
        value = targets.get(role) or ''
        try:
            parsed = urlsplit(value.strip())
            token = parsed.path.removeprefix('/open-apis/bot/v2/hook/')
            valid = (parsed.scheme == 'https' and parsed.netloc.lower() == 'open.feishu.cn'
                     and parsed.path.startswith('/open-apis/bot/v2/hook/') and bool(token)
                     and all(char.isascii() and (char.isalnum() or char in '-_') for char in token)
                     and not parsed.query and not parsed.fragment)
        except (ValueError, AttributeError):
            valid = False
        bots.append({'role': role, 'name': BOT_LABELS[role], 'configured': bool(value), 'valid': valid})
        if valid:
            normalized.append(token)
    return {'bots': bots, 'credentials_present': all(bot['configured'] for bot in bots),
            'bot_configuration_valid': all(bot['valid'] for bot in bots) and len(set(normalized)) == len(normalized),
            'duplicate_bot_targets': len(set(normalized)) != len(normalized)}


def webhook_signature(timestamp: str, secret: str) -> str:
    """官方算法：拿 ``timestamp\\n密钥`` 当 HMAC 的**密钥**，消息体为空。"""
    digest = hmac.new((timestamp + '\n' + secret).encode(), b'', hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class WebhookBot:
    """群自建机器人。地址按角色标签解析，响应里没有 message_id，也没有远端去重。"""

    def __init__(self, targets: dict, secrets: dict | None = None, *, http=None):
        health = webhook_configuration(targets)
        missing = [WEBHOOK_ROLES[bot['role']] for bot in health['bots'] if not bot['configured']]
        if missing:
            raise FeishuError('未配置群机器人地址（%s）；四个阶段机器人都必须填'
                              % '、'.join(missing))
        if health['duplicate_bot_targets']:
            raise FeishuError('四个阶段的机器人地址有重复；同群可用，须分别填写四个机器人的地址')
        if not health['bot_configuration_valid']:
            raise FeishuError('群机器人地址必须是 %s 开头的完整链接，不能带查询参数或锚点' % WEBHOOK_PREFIX)
        self.targets = {role: targets[role].strip() for role in WEBHOOK_ROLES}
        self.secrets = dict(secrets or {})
        self.http = http or httpx.Client(timeout=15, follow_redirects=False)
        self.next_send = {}

    @classmethod
    def from_environment(cls):
        return cls({role: ModelCredentials(name).optional_value()
                    for role, name in WEBHOOK_ROLES.items()},
                   {role: ModelCredentials(name + '_SECRET').optional_value()
                    for role, name in WEBHOOK_ROLES.items()})

    def close(self):
        self.http.close()

    def envelope(self, role: str, card: dict, now: float) -> dict:
        # ⚠️ card 是 JSON **对象**。im/v1/messages 的 content 必须先 json.dumps，机器人相反，
        # 照着那边抄会被拒。签名每次尝试重算——冻结的是卡片，不是请求信封（超 1 小时即失效）。
        body = {'msg_type': 'interactive', 'card': card}
        secret = self.secrets.get(role)
        if secret:
            stamp = str(int(now))
            body.update(timestamp=stamp, sign=webhook_signature(stamp, secret))
        return body

    def send(self, recipient: str, card: dict, delivery_id: str) -> str:
        if recipient not in self.targets:
            raise FeishuError('未知的飞书接收角色，不猜测投递目标')
        try:
            delay = self.next_send.get(recipient, 0) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_send[recipient] = time.monotonic() + 0.61
            response = self.http.post(self.targets[recipient],
                                      json=self.envelope(recipient, card, time.time()))
            if not response.is_success:
                error = FeishuRejected if 400 <= response.status_code < 500 else FeishuError
                raise error('群机器人 HTTP 请求失败（状态 %d）' % response.status_code)
            data = response.json()
            if not isinstance(data, dict):
                raise FeishuError('群机器人响应不是对象，投递结果尚不明确')
            # 成功响应同时带 code 与旧版 StatusCode；错误只带 code。
            code = data.get('code', data.get('StatusCode'))
            if type(code) is not int:
                raise FeishuError('群机器人响应缺少有效状态码，投递结果尚不明确')
            if code != 0:
                raise FeishuRejected('群机器人未接受卡片（code=%s）：19021 为签名或时间戳不符，'
                                  '19022 为 IP 限制，19024 为群关键词限制' % code)
        except (httpx.HTTPError, ValueError) as exc:
            # 不记录响应体或异常原文，连接错误的文案里可能带地址。
            raise FeishuError('群机器人请求未能确认成功，请检查网络或机器人配置') from exc
        # 机器人不返回 message_id，这个回执只是本地凭证，不能当平台消息 ID 用。
        return 'bot-accepted:' + delivery_id


def notification_card(kind: str, payloads: list[dict], settings: FeishuSettings, *, role: str | None = None) -> dict:
    """源文案以纯文本渲染；按钮只导航审校台，不在卡片里直接批准发布。"""
    elements = []
    for payload in payloads:
        if payload.get('image_key'):
            elements.append({'tag': 'img', 'img_key': payload['image_key'],
                             'alt': {'tag': 'plain_text', 'content': payload.get('image_note', '帖子首图')}, 'mode': 'fit_horizontal'})
        if payload.get('fields'):
            for field in payload['fields']:
                elements.append({'tag': 'div', 'text': {'tag': 'plain_text',
                    'content': str(field['label']) + '：' + str(field['value'])}})
        else:
            for key in ('platform', 'account', 'created_at', 'meta', 'text', 'image_note', 'risk', 'next_step'):
                if payload.get(key):
                    elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': str(payload[key])[:1600]}})
        actions = []
        if payload.get('capture_key'):
            if payload.get('archived'):
                suffix = '/history/' + quote(payload['account_dir'], safe='') + '/' + quote(payload['post_id'], safe='')
                label = '查看已存原帖'
            else:
                suffix = '/runtime?capture=' + quote(payload['capture_key'], safe='')
                label = '查看该项采集异常'
            actions.append({'tag': 'button', 'type': 'primary',
                'text': {'tag': 'plain_text', 'content': label}, 'url': settings.base_url.rstrip('/') + suffix})
        elif payload.get('run_id'):
            actions.append({'tag': 'button', 'type': 'primary',
                'text': {'tag': 'plain_text', 'content': '查看运行详情'},
                'url': settings.base_url.rstrip('/') + '/runtime?scan=' + quote(payload['run_id'], safe='')})
        if payload.get('task_id'):
            url = settings.base_url.rstrip('/') + '/?task=' + quote(str(payload['task_id']), safe='')
            actions.append({'tag': 'button', 'type': 'primary',
                            'text': {'tag': 'plain_text', 'content': '去审校'}, 'url': url})
        elif kind == 'backlog' and payload.get('platform') in {'facebook', 'instagram'}:
            actions.append({'tag': 'button', 'type': 'primary',
                'text': {'tag': 'plain_text', 'content': '查看待审列表'},
                'url': settings.base_url.rstrip('/') + '/review/' + payload['platform']})
        # 原帖按钮不依赖 task_id：监测与抓取卡片发生在有审校任务之前。
        original = urlsplit(str(payload.get('permalink') or ''))
        if original.scheme == 'https' and original.hostname and not original.username and not original.password:
            actions.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': '查看源帖' if payload.get('capture_key') else '查看原帖'},
                            'url': payload['permalink']})
        if actions:
            elements.append({'tag': 'action', 'actions': actions})
    title = TITLES[kind] + (f' · {len(payloads)} 篇' if len(payloads) > 1 else '')
    # 两个平台是独立的审校入口，标题里说清是哪一边，运营才知道该开哪个队列。
    channels = {str(p['platform']) for p in payloads if p.get('platform')}
    if kind in {'ready', 'backlog'} and channels:
        title += ' · ' + ' / '.join(sorted(channels))
    if kind == 'monitor_saved' and len(payloads) == 1 and payloads[0].get('capture_status'):
        title += '：' + payloads[0]['capture_status']
    bot_name = BOT_LABELS.get(role or KIND_ROLES.get(kind))
    if bot_name:
        title += ' · ' + bot_name
    return {'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': 'Neakasa 德国站 · ' + title},
                       'template': 'red' if kind in {'system', 'schedule_failed'}
                       else 'turquoise' if kind in MONITOR_KINDS else 'blue'},
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
            return {'schema_version': 2, 'events': {}, 'deliveries': {}, 'archived_events': {}}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('schema_version', 1) not in {1, 2}
                or not isinstance(data.get('events'), dict)
                or not isinstance(data.get('deliveries'), dict)):
            raise FeishuError('飞书投递记录损坏，请先核对；未发送消息')
        for event in data['events'].values():
            if (not isinstance(event, dict) or event.get('kind') not in KINDS
                    or not isinstance(event.get('payload'), dict)
                    or ('recipients' in event and (not isinstance(event['recipients'], list)
                        or not event['recipients'] or any(not isinstance(role, str) or not role.strip()
                                                         for role in event['recipients'])))):
                raise FeishuError('飞书事件或冻结路由损坏，请保留现场核对')
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

    def _event_recipients(self, event):
        return event.get('recipients', self.settings.recipients_for(event['kind']))

    def _migrate_routes(self, data, now):
        """旧回执保留原角色；未尝试的事件转新路由，已尝试的未知结果先核对。"""
        for event_id, event in data['events'].items():
            if 'recipients' in event:
                continue
            assigned = [row for row in data['deliveries'].values() if event_id in row['events']]
            if any(row['attempts'] or row['status'] == 'sent' for row in assigned):
                event['recipients'] = list(dict.fromkeys(row['recipient'] for row in assigned))
                for row in assigned:
                    if row['status'] in {'pending', 'retry'}:
                        row.update(status='uncertain', error='旧通道路由待核对；确认送达或未送达后再结转')
            else:
                event['recipients'] = list(self.settings.recipients_for(event['kind']))
                for row in assigned:
                    if row['status'] == 'pending':
                        row.update(status='cancelled', cancelled_at=_iso(now))
        data['schema_version'] = 2

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
            data['events'][event_id] = {'kind': kind, 'payload': payload, 'created_at': _iso(now),
                                       'recipients': list(self.settings.recipients_for(kind))}
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
                               'version': self._version(row),
                               'capture_keys': [data['events'][key]['payload']['capture_key'] for key in row['events']
                                                if key in data['events'] and data['events'][key]['payload'].get('capture_key')],
                               'captured_at': [data['events'][key]['payload']['captured_at'] for key in row['events']
                                               if key in data['events'] and data['events'][key]['payload'].get('captured_at')],
                               'task_ids': [data['events'][key]['payload'].get('task_id')
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
                    required = set(self._event_recipients(event))
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
            raise ValueError('请填写在群里核对到的送达情况')
        with self._lock():
            data = self._load()
            row = data['deliveries'].get(identifier)
            if not row or self._version(row) != expected_version or row['status'] not in {'retry', 'uncertain'}:
                raise FeishuError('消息状态已有变化，请刷新后再核对')
            self._migrate_routes(data, now)
            self._retire_network(data, now)
            stamp = _iso(now)
            row.setdefault('resolutions', []).append({'action': action, 'recorded_at': stamp, 'actor': None})
            if action == 'delivered':
                row.update(status='sent', message_id=message_id.strip(), sent_at=stamp)
            else:
                # 取消的提醒已过期，保留原卡片和 UUID，不重新入队。
                obsolete = any(data['events'][key].get('cancelled_at') for key in row['events'])
                row.update(status='cancelled' if obsolete else 'retry', next_at=stamp,
                           retry_authorized_at=stamp)
                if row['recipient'] not in self.settings.recipients_for(row['kind']):
                    self._rebind_confirmed_delivery(data, identifier, row, stamp)
            row.pop('error', None)
            atomic_write_json(self.path, data)
        return self.status()

    def _rebind_confirmed_delivery(self, data, identifier, row, stamp):
        """人已确认旧通道未送达；新建当前通道的冻结投递，原卡与回执仍保留。"""
        row.update(status='cancelled', cancelled_at=stamp)
        recipients = self.settings.recipients_for(row['kind'])
        live = [key for key in row['events'] if not data['events'][key].get('cancelled_at')]
        for key in live:
            event = data['events'][key]
            event['recipients'] = list(dict.fromkeys(
                [role for role in self._event_recipients(event) if role != row['recipient']] + list(recipients)))
        for recipient in recipients:
            assigned = {key for item in data['deliveries'].values()
                        if item['recipient'] == recipient and item['status'] != 'cancelled' for key in item['events']}
            missing = [key for key in live if key not in assigned]
            # 整张原卡仍有效才复用；部分失效或部分已送达时由 dispatch 只组尚缺事件。
            if not missing or missing != row['events']:
                continue
            seed = identifier + '\0' + recipient + '\0' + str(len(row['resolutions']))
            next_id = hashlib.sha256(seed.encode()).hexdigest()[:32]
            if next_id in data['deliveries']:
                raise FeishuError('已有该次路由结转，请刷新投递状态')
            data['deliveries'][next_id] = {
                'events': list(row['events']), 'kind': row['kind'], 'recipient': recipient, 'card': row['card'],
                'status': 'pending', 'attempts': 0, 'next_at': stamp, 'created_at': stamp,
                'routing_from': identifier, 'preview_error': row.get('preview_error')}

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

    def dispatch(self, now: datetime, send: Callable[[str, dict, str], str], *, prepare_payload=None,
                 allowed_kinds=None) -> int:
        if not self.settings.enabled:
            return 0
        self.settings.validate()
        stamp = _iso(now)
        sent = 0
        with self._lock():
            data = self._load()
            self._migrate_routes(data, now)
            self._retire_network(data, now)
            for kind in sorted(KINDS):
                if allowed_kinds is not None and kind not in allowed_kinds:
                    continue
                if kind not in ALWAYS_DELIVERED and not self.schedule.is_on_duty(now):
                    continue
                recipients = [recipient for event in data['events'].values() if event['kind'] == kind
                              for recipient in self._event_recipients(event)]
                for recipient in dict.fromkeys(recipients):
                    assigned = {event_id for item in data['deliveries'].values()
                                if item['status'] != 'cancelled' and item['recipient'] == recipient for event_id in item['events']}
                    ids = [key for key, item in data['events'].items()
                           if key not in assigned and item['kind'] == kind and not item.get('cancelled_at')
                           and recipient in self._event_recipients(item)]
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
                                    prepare_payload(payload)
                                except Exception:
                                    # 卡片不带图，所以这里只剩"当前稿没读出来"一种失败。
                                    payload['next_step'] = '当前德语稿未能重新读取，卡片内容可能不是最新，请进入审校台核对。'
                                    payload['preview_error'] = 'content_refresh_failed'
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
                if allowed_kinds is not None and item['kind'] not in allowed_kinds:
                    continue
                if item['status'] in {'sent', 'uncertain', 'cancelled'} or item['next_at'] > stamp:
                    continue
                if item['kind'] not in ALWAYS_DELIVERED and not self.schedule.is_on_duty(now):
                    continue
                item['attempts'] += 1
                item['next_at'] = _iso(now + timedelta(minutes=15))
                # webhook 无远端去重。意图先记为未知，进程退出或成功回执落盘失败都不能自动重发。
                item['status'] = 'uncertain'
                atomic_write_json(self.path, data)
                try:
                    message_id = send(item['recipient'], item['card'], delivery_id)
                    if not message_id:
                        raise FeishuError('未获得投递回执')
                except Exception as exc:
                    item['error'] = str(exc) if isinstance(exc, FeishuError) else type(exc).__name__
                    item['status'] = 'retry' if isinstance(exc, FeishuRejected) else 'uncertain'
                else:
                    item.update(status='sent', message_id=message_id, sent_at=stamp)
                    sent += 1
                atomic_write_json(self.path, data)
            self._archive_completed(data, now)
        return sent

    def _retire_network(self, data, now):
        """退出旧网络告警；冻结卡片和已送达/未知回执保留，混合卡的有效事件重新组卡。"""
        retired = {key for key in data['events'] if key.startswith(('network:', 'network-change:'))}
        for key in retired:
            data['events'][key].setdefault('cancelled_at', _iso(now))
        for item in data['deliveries'].values():
            if retired.intersection(item['events']) and item['status'] in {'pending', 'retry'}:
                item.update(status='cancelled', cancelled_at=_iso(now))

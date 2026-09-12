"""常驻入口的应用组装：监测、处理、到期唤醒与飞书；不自动批准发布。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from core.config import MonitorSchedule, cfg
from core.feishu import FeishuClient, FeishuSettings, Outbox
from core.heartbeat import Heartbeat, HeartbeatSettings
from core.mirror import DriveClient, MirrorService, MirrorSettings
from core.paid_model import atomic_write_json
from core.network_evidence import NetworkEvidence, NetworkEvidenceSettings, network_evidence_status
from core import notify, review
from core.store import Archive, account_dirs, read_post_truth
from pipeline import engine
from publish import journal, planner_cache
import localize_images


class Runtime:
    def __init__(self, *, detector, process: bool = False):
        self.detector, self.process = detector, process
        self.c = cfg()
        if process and engine.activation_time(self.c.state_dir) is None:
            raise ValueError('流水线尚未激活；请先完成发布前置核验，再启用 --process')
        self.pending_processing = False
        self.settings = FeishuSettings.load()
        self.outbox = Outbox(self.c.state_dir / 'feishu_outbox.json', self.settings)
        self.client = None
        self.heartbeat = Heartbeat(self.c.state_dir / 'heartbeat.json', HeartbeatSettings.load(self.c))
        self.mirror_settings = MirrorSettings.load()
        self.mirror = MirrorService(self.c.state_dir, self.mirror_settings)
        self.drive = None
        self.calendar_enabled = self.c.get('calendar', 'enabled', False)
        self.calendar_interval = float(self.c.get('calendar', 'refresh_minutes', 60)) * 60
        if not isinstance(self.calendar_enabled, bool) or self.calendar_interval < 60:
            raise ValueError('calendar.enabled 须为布尔值，刷新间隔至少 1 分钟')
        self.calendar_attempt = None
        self.thumbnail_sources = {}
        self.network = NetworkEvidence(self.c.state_dir / 'network_evidence.json', NetworkEvidenceSettings.load(self.c))

    def close(self):
        self.heartbeat.close()
        self.network.close()
        if self.drive is not None:
            self.drive.close()
        if self.client is not None:
            self.client.close()

    def scan(self, kind: str, platform: str) -> int:
        code = self.detector(kind, platform)
        now = datetime.now(timezone.utc)
        if code:
            self._system(f'capture:{platform}:{now.date()}:{code}',
                         f'{platform} 监测未完成（退出码 {code}），请检查抓取日志和手动登录状态。', now)
            return code
        self.pending_processing = True
        return code

    def _system(self, event_id, text, now):
        if self.settings.enabled:
            try:
                self.outbox.enqueue(event_id, 'system', {'text': text}, now)
            except Exception:
                notify.notify('系统告警暂未投递', '请检查飞书发件箱；抓取退出状态保持原样。', popup=False)

    def maintenance(self, now: datetime):
        """投递故障留在本地日志，不改变抓取或发布状态。"""
        try:
            directories = engine.active_account_dirs(account_dirs(self.c.archive_dir))
            scheduled = journal.scheduled_source_refs(self.c.state_dir)
            awakened = review.wake_due(directories, now=now, scheduled_refs=scheduled)
            self.pending_processing = self.pending_processing or bool(awakened)
            if self.process and self.pending_processing:
                self.pending_processing = False
                all_directories = account_dirs(self.c.archive_dir)
                if all_directories:
                    code = engine.run(account_dirs=all_directories, state_dir=self.c.state_dir,
                                      settings=engine.pipeline_settings(), now=now, detect_updates=False)
                    if code:
                        self._system(f'processing:{now.date()}:{code}',
                                     f'本轮内容处理未完成（退出码 {code}），请检查流水线日志与付费请求记录。', now)
        except Exception as exc:
            notify.notify('本轮维护暂未完成', type(exc).__name__ + '；请检查本地记录。', popup=False)
        self.heartbeat.tick(now)
        network = self.network.refresh(now)
        if network.get('status') == 'failed':
            self._system(f'network:{now.date()}', '出口信息服务暂未返回有效结果；请检查网络或服务配置，不能据此判断社媒账号被封。', now)
        elif network.get('status') == 'recorded':
            evidence = network_evidence_status(self.network.path, self.network.settings, now)
            if evidence['stability'] == 'changed' or evidence['network_type'] in {'hosting', 'anonymous'}:
                self._system(f'network-change:{now.date()}:{evidence["latest"]["ip"]}',
                    '观测到出口变化或提供方标记的代理/托管网络，请核对 Chrome 是否使用了同一出口。', now)
        self.refresh_calendar(now)
        if self.mirror_settings.enabled:
            try:
                self.mirror_sources(now)
            except Exception as exc:
                self._system(f'mirror:{now.date()}',
                             f'归档云盘镜像尚未完成（{type(exc).__name__}），本地内容仍保留。', now)
        if not self.settings.enabled:
            return
        try:
            self.collect(engine.active_account_dirs(account_dirs(self.c.archive_dir)), now)
        except Exception as exc:
            notify.notify('审校提醒汇总失败', type(exc).__name__ + '；已有系统告警仍会尝试投递。', popup=False)
        # 汇总失败不能让队列里的故障告警永久失声。
        try:
            if self.client is None:
                self.client = FeishuClient.from_environment()
            self.outbox.dispatch(now, self.client.send_card, prepare_payload=self.prepare_preview)
        except Exception as exc:
            notify.notify('审校提醒暂未投递', type(exc).__name__ + '；请检查飞书配置与发件箱。', popup=False)

    def prepare_preview(self, payload):
        origin = self.thumbnail_sources.get(payload.get('task_id'))
        if origin is None:
            return
        directory, indexed = origin
        source, _ = read_post_truth(directory, indexed)
        if journal.text_sha256(source['text']) != payload.get('source_text_sha256'):
            return
        media = next((item for item in source.get('media', []) if item.get('kind') == 'image'), None)
        if media is None:
            return
        path, _ = localize_images._source_from_manifest(directory, source, media)
        if path.stat().st_size >= 10 * 1024 * 1024:
            raise ValueError('通知图片过大')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        cache_path = self.c.state_dir / 'feishu_images.json'
        try:
            cache = json.loads(cache_path.read_text(encoding='utf-8'))
            if not isinstance(cache, dict):
                cache = {}
        except (OSError, ValueError):
            cache = {}
        key = cache.get(digest)
        if not isinstance(key, str) or not key:
            key = self.client.upload_image(content)
            if not isinstance(key, str) or not key:
                raise ValueError('通知图片未获得回执')
            cache[digest] = key
            atomic_write_json(cache_path, cache)
        payload['image_key'] = key

    def refresh_calendar(self, now: datetime):
        if not self.calendar_enabled:
            return
        path = self.c.state_dir / 'planner_cache.json'
        snapshot = planner_cache.read_cache(path, now=now)
        last = snapshot.get('last_attempt_at')
        previous = self.calendar_attempt or (datetime.fromisoformat(last) if last else None)
        if previous is not None and 0 <= (now - previous).total_seconds() < self.calendar_interval:
            return
        self.calendar_attempt = now
        try:
            result = asyncio.run(planner_cache.refresh_cache(path, planner_cache.read_live_inventory,
                                                            state_dir=self.c.state_dir))
            if result.get('refresh_status') == 'failed':
                self._system(f'calendar:{now.date()}', '发布月历刷新失败，页面保留上次成功的数据，请检查发布浏览器。', now)
        except Exception:
            self._system(f'calendar:{now.date()}', '发布月历暂未刷新，请检查本地日志和发布浏览器。', now)

    def mirror_sources(self, now: datetime):
        for directory in engine.active_account_dirs(account_dirs(self.c.archive_dir)):
            for source in Archive(directory.parent, directory.name).rows():
                try:
                    self.mirror.queue_source(directory, source, now=now)
                except (OSError, ValueError, RuntimeError) as exc:
                    self._system(f'mirror-source:{directory.name}:{source.get("post_id")}:{now.date()}',
                                 f'有一篇源帖尚未镜像（{type(exc).__name__}），请检查本地素材。', now)
        try:
            self.mirror.queue_state(self.c.state_dir, now=now)
        except (OSError, ValueError, RuntimeError) as exc:
            self._system(f'mirror-state:{now.date()}',
                         f'状态备份等待稳定文件（{type(exc).__name__}）；已冻结的镜像继续投递。', now)
        if self.drive is None:
            self.drive = DriveClient.from_environment()
        result = self.mirror.dispatch(self.drive, now=now)
        if result['pending']:
            self._system(f'mirror-pending:{now.date()}', '云盘镜像还有未完成条目，系统会重试；本地留档不受影响。', now)

    def collect(self, directories, now: datetime):
        started_at = self.outbox.started_at(now)
        sources = {}
        for directory in directories:
            for indexed in Archive(directory.parent, directory.name).rows():
                try:
                    source, _ = read_post_truth(directory, indexed)
                except (OSError, ValueError) as exc:
                    self._system(f'archive:{directory.name}:{indexed.get("post_id")}:{now.date()}',
                                 f'有一篇本地源帖无法读取（{type(exc).__name__}），请检查归档。', now)
                    continue
                ref = journal.source_ref(source['platform'], source['post_id'])
                sources[ref] = (directory, source)
        self.thumbnail_sources = {directory.name + '/' + source['post_id']: (directory, source)
                                  for directory, source in sources.values()}
        scheduled = journal.scheduled_source_refs(self.c.state_dir)
        pending = []
        valid_ready = set()
        for event in engine.latest_human_items(self.c.state_dir).values():
            if event.get('status') != 'open':
                continue
            if event.get('kind') in {'budget_stopped', 'paid_request_unresolved'}:
                self._system(event['item_id'], str(event.get('summary') or ''), now)
            if event.get('kind') not in {'ready_to_publish', 'human_translation_stale',
                                         'unknown_owner', 'unknown_collaborator', 'unmapped_price', 'offline_gate'}:
                continue
            refs = event.get('source_refs') or []
            if len(refs) != 1 or refs[0] not in sources or refs[0] in scheduled:
                continue
            directory, source = sources[refs[0]]
            state = review.state_for(directory, source)
            if state['status'] not in {'pending_review', 'edited'}:
                continue
            details = event.get('details') or {}
            if (event.get('kind') == 'ready_to_publish'
                    and details.get('source_text_sha256') != journal.text_sha256(source['text'])):
                continue
            payload = {'task_id': directory.name + '/' + source['post_id'],
                       'platform': source['platform'], 'account': source['account'],
                       'created_at': source['created_at'],
                       'permalink': source.get('permalink'),
                       'source_text_sha256': journal.text_sha256(source['text']),
                       'text': f"{details.get('image_count', 0)} 张图\n" + (details.get('text_de_preview') or ''),
                       'risk': ('\n'.join(details.get('warnings') or [])
                                if event['kind'] == 'ready_to_publish' else event.get('summary', ''))}
            revision = state.get('revision') or ''
            event_id = 'ready:' + event['item_id'] + ':' + revision
            valid_ready.add(event_id)
            self.outbox.enqueue(event_id, 'ready', payload, now)
            pending.append(event)

        # 只在当前 14/18 点小时内补发当次提醒，重启不会追着补昨天的两轮。
        local = MonitorSchedule.local(now)
        if pending and local.hour in {14, 18} and self.outbox.schedule.is_on_duty(now):
            earliest = min(datetime.fromisoformat(item['recorded_at'].replace('Z', '+00:00')) for item in pending)
            hours = max(0, int((now - earliest).total_seconds() // 3600))
            backlog_id = f'backlog:{local.date()}:{local.hour}'
            valid_ready.add(backlog_id)
            self.outbox.enqueue(backlog_id, 'backlog',
                                {'text': f'当前 {len(pending)} 篇待审，最早一篇已等待 {hours} 小时。'}, now)

        self.outbox.retain_ready(valid_ready, now)

        latest = {item['attempt_id']: item for item in journal.load(self.c.state_dir)}
        for attempt in latest.values():
            refs = attempt.get('source_refs') or []
            origin = next((sources[ref] for ref in refs if ref in sources), None)
            if origin is None:
                continue
            directory, source = origin
            payload = {'task_id': directory.name + '/' + source['post_id'],
                       'platform': source['platform'], 'account': source['account']}
            status = attempt['status']
            if status == journal.STATUS_SCHEDULED:
                payload['text'] = '排期已确认：' + str(attempt.get('scheduled_at') or '')
                payload['next_step'] = '请核对排期时刻；详细回执可在审校台查看。'
                kind = 'scheduled'
            elif status in {journal.STATUS_FAILED_PRE_SUBMIT, journal.STATUS_SUBMITTED_UNVERIFIED,
                            journal.STATUS_SUBMIT_AMBIGUOUS}:
                payload['text'] = '本次排期尚未确认完成。'
                payload['next_step'] = '请先在 Business Suite 核对是否已有排期，再决定后续操作，避免重复提交。'
                kind = 'schedule_failed'
            else:
                continue
            recorded_at = datetime.fromisoformat(attempt['recorded_at'].replace('Z', '+00:00'))
            if recorded_at >= started_at:
                self.outbox.enqueue(f'{kind}:{attempt["attempt_id"]}', kind, payload, now)
            if (status == journal.STATUS_SCHEDULED and attempt.get('source_text_sha256')
                    and attempt['source_text_sha256'] != journal.text_sha256(source['text'])):
                digest = hashlib.sha256(source['text'].encode()).hexdigest()[:16]
                self.outbox.enqueue(f'source-changed:{attempt["attempt_id"]}:{digest}', 'schedule_failed',
                                    dict(payload, text='已排期帖的原文发生变化，远端排期保持原样。',
                                         next_step='请比较新原文与已排内容，人工决定是否调整。'), now)

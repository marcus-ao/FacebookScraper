"""常驻入口的应用组装：监测、处理、到期唤醒与飞书；不自动批准发布。"""
from __future__ import annotations

from pipeline.runtime_status import record_process_tick
from pipeline import hashtag_suggestions
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from core.config import MonitorSchedule, cfg
from core.capture_state import CaptureState, CaptureStateError
from core.feishu import FeishuSettings, Outbox, WebhookBot
from core.heartbeat import Heartbeat, HeartbeatSettings
from core.integrity import parse_ts
from core.mirror import DriveClient, MirrorService, MirrorSettings
from core.monitoring import MonitoringJournal, SKIP_LABELS, SKIP_REASONS
from core import notify, review, paid_consent, paid_requests
from core.store import Archive, account_dirs, read_post_truth
from core.translated import source_text_sha256
from pipeline import engine, notifications
from publish import journal, planner_cache


class Runtime:
    def __init__(self, *, detector, process: bool = False):
        self.detector, self.process = detector, process
        self.clock = lambda: datetime.now(timezone.utc)
        self.c = cfg()
        if process and engine.activation_time(self.c.state_dir) is None:
            raise ValueError('流水线尚未激活；请先完成发布前置核验，再启用 --process')
        self.processing = MonitoringJournal(self.c.state_dir, now=self.clock())
        self.processing_executor = ThreadPoolExecutor(max_workers=1,
                                                      thread_name_prefix='content-processing')
        self.processing_future = None
        self.sampling_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hashtag-sampling')
        self.sampling_future = None
        self.delivery_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='workflow-delivery')
        self.delivery_future = None
        self.settings = FeishuSettings.load()
        self.scan_reports = self.c.get('feishu', 'scan_reports', True)
        if not isinstance(self.scan_reports, bool):
            raise ValueError('[feishu].scan_reports 必须是布尔值')
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

    def await_delivery(self, timeout: float = 120) -> None:
        """等本轮消息与镜像投递跑完。

        ⛔ 单轮执行（`--once`）必须调它再退出：`close()` 用 `cancel_futures=True` 关投递
        执行器，刚提交、尚未启动的 `_deliver` 会被直接取消——实测 8/8 轮一条消息都没发出，
        而且不报错，看起来和"飞书配置有问题"一模一样。连续 `--run` 靠下一轮重投，不受影响。
        """
        future = self.delivery_future
        if future is None:
            return
        try:
            future.result(timeout=timeout)
        except Exception as exc:
            notify.notify('消息和镜像维护等待重试', type(exc).__name__, popup=False)

    def close(self):
        self.processing_executor.shutdown(wait=False, cancel_futures=False)
        self.sampling_executor.shutdown(wait=False, cancel_futures=True)
        self.delivery_executor.shutdown(wait=False, cancel_futures=True)
        self.heartbeat.close()
        if self.delivery_future is not None:
            self.delivery_future.add_done_callback(lambda _: self._close_delivery_clients())
        else:
            self._close_delivery_clients()

    def _close_delivery_clients(self):
        if self.drive is not None:
            self.drive.close()
        if self.client is not None:
            self.client.close()

    def scan(self, kind: str, platform: str) -> int:
        started = self.clock()
        before = self._delta_entry(platform)
        before_archive = self._archive_images(platform)
        self.processing.fact('scan_started', started, kind=kind, platform=platform)
        code = self.detector(kind, platform)
        now = self.clock()
        # 先读观测再分支：抓取中途失败也可能已经落了几篇，播报不能因为退出码非零就整段消失。
        observation = self._scan_observation(kind, platform, before, before_archive)
        self._scan_cards(kind, platform, started, now, int(code or 0), observation['skipped'])
        capture_tracked = self._request_captured_content(now) if self.process else False
        if code:
            self.processing.fact('scan_finished', now, kind=kind, platform=platform,
                                 exit_code=int(code), discovered=0,
                                 skipped=dict.fromkeys(SKIP_REASONS, 0))
            self._system(f'capture:{platform}:{now.date()}:{code}',
                         f'{platform} 监测未完成（退出码 {code}），请检查抓取日志和手动登录状态。', now)
            return code
        if observation['discovered']:
            self.processing.fact('content_discovered', now, kind=kind, platform=platform,
                                 count=observation['discovered'])
        self.processing.fact('scan_finished', now, kind=kind, platform=platform, exit_code=0,
                             discovered=observation['discovered'], skipped=observation['skipped'])
        if self.process and not capture_tracked and observation['discovered']:
            self.processing.request(now, platform, kind, observation['discovered'],
                                    observation['skipped'], image_count=observation['image_count'])
        return code

    def _request_captured_content(self, now: datetime) -> bool:
        """完整采集结果独立入队；扫描部分失败、人工恢复与飞书状态不影响受理。"""
        ledger = CaptureState(self.c.state_dir)
        if not ledger.path.exists():
            return False
        snapshot = ledger.status()
        activated = engine.activation_time(self.c.state_dir)
        if activated is None:
            return True
        for event in snapshot['events'].values():
            source = event['source']
            item = snapshot['items'].get(event.get('key'), {})
            created = parse_ts(source.get('created_at'))
            if (event['status'] != 'complete' or not event['eligible']
                    or item.get('status') != 'complete'
                    or item.get('last_result') != event.get('last_result')
                    or source.get('account', '').lower() != self.c['targets'][source['platform']].lower()
                    or created is None or not activated <= created <= now):
                continue
            self.processing.request(now, source['platform'], event['classification'], 1, {},
                                    image_count=event['saved_images'],
                                    capture_event_id=event['event_id'])
        return True

    def _scan_cards(self, kind, platform, started, now, code, skipped):
        """先持久入队；抓取已完成后由投递线程发送，失败不改变采集事实。"""
        self.enqueue_capture_results(now)

    def enqueue_capture_results(self, now):
        if not self.settings.enabled or not self.scan_reports:
            return
        ledger = CaptureState(self.c.state_dir)
        if not ledger.path.exists():
            return
        try:
            snapshot = ledger.status()
            events = snapshot['events']
            scans = {event['scan_id'] for event in events.values() if not event['acknowledged']}
            for scan in scans:
                rows = [event for event in events.values() if event['scan_id'] == scan]
                source = rows[0]['source']
                cards = notifications.scan_cards('delta', source['platform'], source['account'], rows, {}, now)
                if cards is None:
                    continue
                found, saved = cards
                for event_id, payload in saved:
                    self.outbox.enqueue('monitor_saved:' + event_id, 'monitor_saved', payload, now)
                # 未终结的扫描保留未确认事件，维护重放才会生成最终一次摘要。
                if any(item['scan_id'] == scan and item['status'] == 'pending'
                       for item in snapshot['items'].values()):
                    continue
                self.outbox.enqueue('monitor_found:' + source['platform'] + ':' + scan, 'monitor_found', found, now)
                manual = [event for event in rows if event['status'] == 'manual']
                if manual:
                    self.outbox.enqueue('capture-manual:' + scan, 'system', {
                        'text': f"{source['platform']} 本轮 {len(manual)} 篇需要人工处理，已有内容仍保留。",
                        'run_id': scan, 'next_step': '打开运行详情核对逐帖原因，再授权一次恢复。'}, now)
                ledger.acknowledge([row['event_id'] for row in rows])
        except (CaptureStateError, OSError, ValueError, RuntimeError) as exc:
            notify.notify('监测播报未入队', type(exc).__name__ + '；事实保留，后续维护会重新入队。', popup=False)

    def _delta_entry(self, platform: str) -> dict:
        path = self.c.state_dir / 'delta_state.json'
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        entry = payload.get(platform) if isinstance(payload, dict) else None
        return dict(entry) if isinstance(entry, dict) else {}

    def _archive_images(self, platform: str) -> dict[str, int]:
        account = dict(zip(('facebook', 'instagram'), self.c.active_accounts()))[platform]
        directory = self.c.archive_dir / account
        if not (directory / 'manifest.jsonl').exists():
            return {}
        try:
            rows = Archive(directory.parent, directory.name).rows()
            return {str(row['post_id']): sum(
                1 for item in row.get('media', [])
                if isinstance(item, dict) and item.get('kind') == 'image') for row in rows}
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def _scan_observation(self, kind: str, platform: str, before: dict,
                          before_archive: dict[str, int]) -> dict:
        after = self._delta_entry(platform)
        stamp_key = 'last_reconcile_at' if kind == 'reconcile' else 'last_success'
        changed = bool(after.get(stamp_key) and after.get(stamp_key) != before.get(stamp_key))
        count_key = 'last_reconcile_new_count' if kind == 'reconcile' else 'last_new_count'
        discovered = int(after.get(count_key, 0) or 0) if changed else 0
        skipped = after.get('last_observed_skipped') if changed else {}
        after_archive = self._archive_images(platform) if changed else before_archive
        new_images = sum(after_archive[post_id] for post_id in after_archive.keys() - before_archive.keys())
        return {'discovered': max(0, discovered),
                'image_count': max(0, discovered, new_images),
                'skipped': {key: max(0, int((skipped or {}).get(key, 0) or 0))
                            for key in SKIP_REASONS}}

    def processing_status(self) -> dict:
        return self.processing.processing_status()

    def activity_summary(self, now: datetime) -> dict | None:
        return self.processing.activity_summary(now)

    def start_processing(self, now: datetime):
        """Run a pending batch off-thread; interrupted batches require explicit recovery."""
        if not self.process:
            return None
        if self.processing_future is not None and not self.processing_future.done():
            return self.processing_future
        batch = self.processing.claim(now)
        if batch is None:
            return None
        try:
            self.processing_future = self.processing_executor.submit(self._process_batch, batch)
        except Exception:
            self.processing.finish(batch, self.clock(), code=1)
            self._system('processing-executor:' + batch['batch_id'], '内容执行线程未启动，本次未调用模型。', now)
        return self.processing_future

    def _process_batch(self, batch: dict):
        started = self.clock()
        try:
            before = {key for key, item in engine.latest_human_items(self.c.state_dir).items()
                      if item.get('status') == 'open' and item.get('kind') == 'ready_to_publish'}
            directories = account_dirs(self.c.archive_dir)
            code = 0
            if directories:
                with paid_requests.operation_scope(batch['batch_id']):
                    code = engine.run(account_dirs=directories, state_dir=self.c.state_dir,
                                      settings=engine.pipeline_settings(), now=started,
                                      detect_updates=False)
            after = {key: item for key, item in engine.latest_human_items(self.c.state_dir).items()
                     if item.get('status') == 'open' and item.get('kind') == 'ready_to_publish'}
            newly_ready = after.keys() - before
            finished = self.clock()
            for key in newly_ready:
                details = after[key].get('details') or {}
                if details.get('canonical_ref') and details.get('source_created_at'):
                    self.processing.fact('post_content_ready', finished, batch_id=batch['batch_id'],
                        source_ref=details['canonical_ref'], source_created_at=details['source_created_at'],
                        item_id=key, requested_at=batch.get('requested_at'))
            result = self.processing.finish(batch, finished, code=int(code or 0), ready_count=len(newly_ready))
            if code:
                self._system(f'processing:{started.date()}:{code}',
                             f'本轮内容处理未完成（退出码 {code}），请检查流水线日志与付费请求记录。',
                             self.clock())
            return result
        except BaseException as exc:
            self.processing.finish(batch, self.clock(), code=None, error=type(exc).__name__)
            self._system(f'processing-uncertain:{batch["batch_id"]}',
                         '内容处理进程意外中断；可能已有付费请求，恢复前请先核账。', self.clock())
            return self.processing_status()

    def _system(self, event_id, text, now):
        if self.settings.enabled:
            try:
                self.outbox.enqueue(event_id, 'system', {'text': text}, now)
            except Exception:
                notify.notify('系统告警暂未投递', '请检查飞书发件箱；抓取退出状态保持原样。', popup=False)

    def maintenance(self, now: datetime, results=()):
        """投递故障留在本地日志，不改变抓取或发布状态。"""
        record_process_tick(now)
        for result in results:
            if not result.get('skipped'):
                continue
            self.processing.fact('scan_skipped', now, kind=result['kind'], platform=result['platform'],
                reason=result['skipped'], deadline_at=result.get('deadline_at'), business_date=result.get('business_date'))
            self._system('scan-skipped:%s:%s' % (result.get('business_date'), result['platform']),
                '%s 兜底未执行：%s。普通探测按计划继续，请核对早班素材是否完整。'
                % (result['platform'], result['skipped']), now)
        self.c = cfg()
        if self.process:
            self.refresh_hashtags(now)
        try:
            if self.process:
                self._request_captured_content(now)
            directories = engine.active_account_dirs(account_dirs(self.c.archive_dir))
            scheduled = journal.scheduled_source_refs(self.c.state_dir)
            awakened = review.wake_due(directories, now=now, scheduled_refs=scheduled) if self.process else []
            if self.process and awakened:
                self.processing.request(now, 'review', 'wakeup', len(awakened), {},
                                        image_count=len(awakened))
        except Exception as exc:
            notify.notify('本轮维护暂未完成', type(exc).__name__ + '；请检查本地记录。', popup=False)
        self.heartbeat.tick(now)
        if self.delivery_future is None or self.delivery_future.done():
            previous, self.delivery_future = self.delivery_future, None
            if previous is not None:
                try:
                    previous.result()
                except Exception as exc:
                    notify.notify('消息和镜像维护等待重试', type(exc).__name__, popup=False)
            try:
                self.delivery_future = self.delivery_executor.submit(self._deliver, now)
            except Exception as exc:
                notify.notify('消息和镜像维护等待重试', type(exc).__name__, popup=False)

    def _deliver(self, now):
        # Keep batch network work off the monitoring loop.
        if self.process:
            self.refresh_calendar(now)
        if self.process and self.mirror_settings.enabled:
            try:
                self.mirror_sources(now)
            except Exception:
                self._system(f'mirror:{now.date()}',
                             '归档云盘镜像尚未完成，请由维护人员核对运行状态；本地内容仍保留。', now)
        if not self.settings.enabled:
            return
        try:
            self.enqueue_capture_results(now)
            if self.process:
                self.collect(engine.active_account_dirs(account_dirs(self.c.archive_dir)), now)
        except Exception as exc:
            notify.notify('审校提醒汇总失败', type(exc).__name__ + '；已有系统告警仍会尝试投递。', popup=False)
        # 汇总失败不能让队列里的故障告警永久失声。
        try:
            if self.client is None:
                self.client = WebhookBot.from_environment()
            self.outbox.dispatch(now, self.client.send, prepare_payload=self.prepare_preview,
                                 allowed_kinds=None if self.process else {'monitor_found', 'monitor_saved', 'system'})
        except Exception as exc:
            notify.notify('审校提醒暂未投递', type(exc).__name__ + '；请检查飞书配置与发件箱。', popup=False)

    def prepare_preview(self, payload):
        """发送前重新读当前有效稿（REQUIREMENTS §6）；群机器人没有图片接口，只补文字。"""
        origin = self.thumbnail_sources.get(payload.get('task_id'))
        if origin is None:
            return
        directory, indexed = origin
        source, _ = read_post_truth(directory, indexed)
        current, _path = notifications.material(directory, source)
        payload.update(current)
        payload['risk'] = '\n'.join(dict.fromkeys([*payload.get('processing_notes', []), current['risk']])).strip()
        # 首图是不是德语图仍然影响"现在开还是等会儿开"，所以状态照报，只说明图不在卡片里。
        payload['image_note'] = current['image_note'] + '（未随卡片投递，请在审校台查看）'

    def refresh_hashtags(self, now):
        if self.sampling_future is not None:
            if not self.sampling_future.done():
                return
            try:
                self.sampling_future.result()
            except Exception:
                notify.notify('标签采样暂未完成', '继续保留语义候选，请查看运行状态。', popup=False)
            self.sampling_future = None
        try:
            if hashtag_suggestions.weekly_refresh_due(now=now, c=self.c):
                self.sampling_future = self.sampling_executor.submit(hashtag_suggestions.weekly_refresh, now=now, c=self.c)
        except Exception:
            notify.notify('标签采样未启动', '请检查采样配置；监测继续运行。', popup=False)

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
            self.mirror.queue_state(self.c.state_dir, now=now, config_path=self.c.path)
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
        pending = {}
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
                    and details.get('source_text_sha256') != source_text_sha256(source['text'])):
                continue
            entry = pending.setdefault(refs[0], {'source': source, 'directory': directory,
                'state': state, 'recorded_at': event['recorded_at'], 'notes': []})
            entry['recorded_at'] = min(entry['recorded_at'], event['recorded_at'])
            if event['kind'] != 'ready_to_publish' and event.get('summary'):
                entry['notes'].append(event['summary'])

        for ref, item in pending.items():
            source, directory, state = item['source'], item['directory'], item['state']
            try:
                current, _ = notifications.material(directory, source)
            except (OSError, ValueError) as exc:
                # 一篇读不出的稿子只丢它自己的卡片，不能连累本轮其余提醒；失败要留痕。
                self._system(f'review-material:{ref}:{now.date()}',
                             f'{ref} 的审校素材读不出（{type(exc).__name__}），请检查归档与译文。', now)
                continue
            if current['image_variant'] == 'unreadable':
                # 卡片已降级为纯文字，运营那边看得见；归档原图不可重建，维护方也要知道。
                self._system(f'lead-image:{ref}:{now.date()}',
                             f'{ref} 的首图读不出，卡片已降级为纯文字，请检查归档原图。', now)
            payload = {'task_id': directory.name + '/' + source['post_id'],
                       'platform': source['platform'], 'account': source['account'],
                       'created_at': source['created_at'], 'permalink': source.get('permalink'),
                       'processing_notes': item['notes'], **current}
            payload['risk'] = '\n'.join(dict.fromkeys([*item['notes'], current['risk']])).strip()
            event_id = 'ready:' + ref + ':' + payload['source_text_sha256'] + ':' + (state.get('revision') or '')
            valid_ready.add(event_id)
            self.outbox.enqueue(event_id, 'ready', payload, now)

        # 只在当前 14/18 点小时内补发当次提醒，重启不会追着补昨天的两轮。
        local = MonitorSchedule.local(now)
        if pending and local.hour in {14, 18} and self.outbox.schedule.is_on_duty(now):
            for platform in ('facebook', 'instagram'):
                lane = [item for item in pending.values() if item['source']['platform'] == platform]
                if not lane:
                    continue
                earliest = min(datetime.fromisoformat(item['recorded_at'].replace('Z', '+00:00')) for item in lane)
                hours = max(0, int((now - earliest).total_seconds() // 3600))
                backlog_id = f'backlog:{platform}:{local.date()}:{local.hour}'
                valid_ready.add(backlog_id)
                self.outbox.enqueue(backlog_id, 'backlog',
                    {'platform': platform,
                     'text': f'当前 {len(lane)} 篇待审，最早一篇已等待 {hours} 小时。'}, now)

        if local.hour == 8 and self.outbox.schedule.is_on_duty(now):
            activity = self.activity_summary(now)
            processing = self.processing_status()
            abnormal = processing.get('status') in {'failed', 'interrupted', 'uncertain'}
            if activity or pending or abnormal:
                text = []
                if activity:
                    text.append(f"发现 {activity['discovered']} 篇，晨间补抓 {activity['reconcile_discovered']} 篇。")
                    if activity.get('reconcile_skipped'):
                        text.append('未执行兜底 %s 次（%s），请核对覆盖。' % (activity['reconcile_skipped'],
                                    '、'.join(activity.get('reconcile_skipped_platforms', []))))
                    text.extend(f"分类跳过 {SKIP_LABELS.get(key, key)}：{count}"
                                for key, count in activity['skipped'].items() if count)
                if pending:
                    text.append(f'当前 {len(pending)} 篇待审。')
                duration = processing.get('last_success_duration_minutes')
                if duration is not None:
                    text.append(f'最近整批处理用时 {duration:.1f} 分钟。')
                if abnormal:
                    text.append('内容处理需要核对：' + str(processing.get('recovery_reason') or processing['status']))
                self.outbox.enqueue(f'morning:{local.date()}', 'morning', {'text': '\n'.join(text)}, now)

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
            if status == journal.STATUS_SCHEDULED:
                if attempt.get('source_fingerprint'):
                    digest = paid_consent.fingerprint(source, directory)
                    changed = digest != attempt['source_fingerprint']
                else:
                    digest = source_text_sha256(source['text'])
                    changed = bool(attempt.get('source_text_sha256') and attempt['source_text_sha256'] != digest)
                if changed:
                    self.outbox.enqueue(f'source-changed:{attempt["attempt_id"]}:{digest}', 'schedule_failed',
                        dict(payload, text='已排期帖的源文或图片内容、数量、顺序发生变化，远端排期保持原样。',
                             next_step='请比较新源内容与批准快照，人工决定是否调整。'), now)

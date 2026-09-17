"""Shared admission and cooperative maintenance for one managed business instance."""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from pathlib import Path
from threading import RLock
from uuid import uuid4

from core.paid_model import FileLock, FileLockBusy, atomic_write_json
from core.process_identity import current_worker, worker_alive

PROTOCOL = 1
SESSION_SECONDS = 60
_threads = RLock()
_admission: ContextVar[tuple[str, str] | None] = ContextVar('maintenance_admission', default=None)


class MaintenanceBlocked(RuntimeError):
    """A managed operation cannot safely begin or a control state is uncertain."""


def managed_gate() -> Gate | None:
    raw = os.environ.get('FBSCRAPER_CONTROL_DIR')
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise MaintenanceBlocked('维护控制目录必须是绝对路径')
    return Gate(path)


class Gate:
    def __init__(self, control: Path):
        self.control = Path(control).resolve()
        self.path = self.control / 'maintenance.json'

    @contextmanager
    def _transaction(self):
        with _threads:
            lock = FileLock(self.control / 'admission.lock', busy_message='维护状态正在更新')
            deadline = time.monotonic() + 5
            while True:
                try:
                    lock.__enter__()
                    break
                except FileLockBusy:
                    if time.monotonic() >= deadline:
                        raise MaintenanceBlocked('维护状态被占用，请稍后重试') from None
                    time.sleep(.01)
            try:
                yield
            finally:
                lock.__exit__(None, None, None)

    def initialize(self):
        """Installer only: an existing or corrupt state is never replaced."""
        with self._transaction():
            if self.path.exists():
                self._read()
                return
            self._save({'version': PROTOCOL, 'phase': 'open', 'epoch': None,
                        'not_before': 0, 'deferred_until': 0, 'operations': {},
                        'sessions': {}, 'interrupted': [], 'stop_workers': [], 'workers': {}})

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != PROTOCOL
                    or value.get('phase') not in {'open', 'announcing', 'quiesced'}
                    or any(not isinstance(value.get(key), dict) for key in ('operations', 'sessions', 'workers'))
                    or not isinstance(value.get('stop_workers'), list)
                    or not isinstance(value.get('interrupted'), list)
                    or any(type(value.get(key)) not in (int, float) or not math.isfinite(value[key])
                           for key in ('not_before', 'deferred_until'))
                    or value['phase'] != 'open' and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(value.get('epoch', '')))):
                raise ValueError('invalid maintenance state')
            for row in value['sessions'].values():
                if (not isinstance(row, dict) or type(row.get('dirty')) is not bool
                        or type(row.get('busy')) is not bool
                        or type(row.get('seen_at')) not in (float, int) or not math.isfinite(row['seen_at'])):
                    raise ValueError('invalid session state')
            return value
        except (OSError, ValueError, TypeError) as exc:
            raise MaintenanceBlocked('维护状态缺失或损坏，请保留现场并由技术人员核对') from exc

    def _save(self, value):
        atomic_write_json(self.path, value)

    def acquire(self, kind: str, *, independent=False) -> tuple[str, bool]:
        with self._transaction():
            value = self._read()
            inherited = _admission.get()
            parent = (value['operations'].get(inherited[1])
                      if inherited and inherited[0] == str(self.control) else None)
            if parent and parent.get('worker') == current_worker() and not independent:
                return inherited[1], False
            if value['phase'] == 'quiesced':
                raise MaintenanceBlocked('系统正在更新，当前内容已保留，请稍后重试')
            operation_id = uuid4().hex
            value['operations'][operation_id] = {'id': operation_id, 'kind': str(kind),
                'worker': current_worker(), 'accepted_at': time.time()}
            self._save(value)
            return operation_id, True

    def release(self, operation_id: str):
        with self._transaction():
            value = self._read()
            if operation_id in value['operations']:
                del value['operations'][operation_id]
                self._save(value)

    def _view(self, value, now):
        live, dead = [], []
        for row in value['operations'].values():
            if not isinstance(row, dict):
                raise MaintenanceBlocked('在途任务记录损坏')
            alive = worker_alive(row.get('worker'))
            (dead if alive is False else live).append(dict(row, alive=alive))
        sessions = list(value['sessions'].values())
        blocked = []
        for row in sessions:
            if not isinstance(row, dict) or type(row.get('seen_at')) not in (int, float):
                raise MaintenanceBlocked('编辑会话记录损坏')
            if row.get('closed') and not row['dirty'] and not row['busy']:
                continue
            if row.get('dirty') or row.get('busy'):
                blocked.append(dict(row, reason='unsaved' if row.get('dirty') else 'request_pending'))
            elif now - row['seen_at'] <= SESSION_SECONDS and row.get('ack_epoch') != value['epoch']:
                blocked.append(dict(row, reason='awaiting_ack'))
        return dict(value, operations=live, sessions=sessions, blockers=blocked,
                    dead_operations=dead)

    def status(self, *, now=None):
        with self._transaction():
            return self._view(self._read(), time.time() if now is None else now)

    def announce(self, *, now=None, delay=60) -> str:
        now = time.time() if now is None else now
        with self._transaction():
            value = self._read()
            if value['phase'] == 'quiesced':
                raise MaintenanceBlocked('已经进入维护，不能重新预告')
            if value['deferred_until'] > now:
                raise MaintenanceBlocked('本次更新已延后')
            value.update(phase='announcing', epoch=uuid4().hex, not_before=now + delay)
            self._save(value)
            return value['epoch']

    def try_quiesce(self, *, now=None) -> bool:
        now = time.time() if now is None else now
        with self._transaction():
            value = self._read()
            if value['phase'] != 'announcing' or now < max(value['not_before'], value['deferred_until']):
                return False
            view = self._view(value, now)
            if view['operations'] or view['blockers']:
                return False
            for row in view['dead_operations']:
                value['operations'].pop(row['id'], None)
                value['interrupted'].append(dict(row, observed_at=now))
            value['phase'] = 'quiesced'
            self._save(value)
            return True

    def reopen(self):
        with self._transaction():
            value = self._read()
            value.update(phase='open', epoch=None, not_before=0, stop_workers=[])
            self._save(value)

    def defer(self, *, now=None):
        with self._transaction():
            value = self._read()
            if value['phase'] == 'quiesced':
                raise MaintenanceBlocked('版本正在切换，暂不能延后')
            value.update(phase='open', epoch=None, not_before=0,
                         deferred_until=(time.time() if now is None else now) + 1800)
            self._save(value)

    def session(self, session_id: str, *, runtime_id: str, dirty: bool, busy: bool,
                ack_epoch: str | None = None, closed: bool = False, sequence: int | None = None, now=None):
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session_id)
                or not re.fullmatch(r'[0-9a-f]{64}', runtime_id)
                or type(dirty) is not bool or type(busy) is not bool or type(closed) is not bool
                or sequence is not None and (type(sequence) is not int or sequence < 1)
                or ack_epoch is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ack_epoch)):
            raise ValueError('编辑会话参数无效')
        with self._transaction():
            value = self._read()
            previous = value['sessions'].get(session_id, {}).get('sequence', 0)
            if sequence is not None and sequence <= previous:
                return
            value['sessions'][session_id] = {'session_id': session_id, 'runtime_id': runtime_id,
                'dirty': dirty, 'busy': busy, 'ack_epoch': ack_epoch if not dirty and not busy else None,
                'closed': closed and not dirty and not busy, 'sequence': sequence or previous + 1,
                'seen_at': time.time() if now is None else now}
            self._save(value)

    def clear_session(self, session_id: str):
        """Local operator acknowledgment; an expired dirty session never clears itself."""
        with self._transaction():
            value = self._read()
            value['sessions'].pop(session_id, None)
            self._save(value)

    def request_stop(self, owners: list[dict]):
        with self._transaction():
            value = self._read()
            view = self._view(value, time.time())
            if value['phase'] != 'quiesced' or view['operations'] or view['blockers']:
                raise MaintenanceBlocked('必须先确认空闲并进入维护，才能请求退出')
            value['stop_workers'] = owners
            self._save(value)

    def should_stop(self, owner: dict) -> bool:
        with self._transaction():
            return owner in self._read()['stop_workers']

    def heartbeat(self, role: str, metadata: dict):
        with self._transaction():
            value = self._read()
            value['workers'][role] = dict(metadata, seen_at=time.time())
            self._save(value)


@contextmanager
def operation(kind: str):
    gate = managed_gate()
    if gate is None:
        yield
        return
    operation_id, owned = gate.acquire(kind)
    token = _admission.set((str(gate.control), operation_id))
    try:
        yield
    finally:
        _admission.reset(token)
        if owned:
            gate.release(operation_id)


def guarded(kind: str):
    """Register complete public operations, including async ones and nested calls."""
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def asynchronous(*args, **kwargs):
                with operation(kind):
                    return await fn(*args, **kwargs)
            return asynchronous
        @wraps(fn)
        def synchronous(*args, **kwargs):
            with operation(kind):
                return fn(*args, **kwargs)
        return synchronous
    return decorate


def submit(executor, kind: str, fn, /, *args, **kwargs):
    """Reserve before queueing; the reservation outlives the submitting HTTP request."""
    gate = managed_gate()
    if gate is None:
        return executor.submit(fn, *args, **kwargs)
    operation_id, _owned = gate.acquire(kind, independent=True)
    context = copy_context()
    def invoke():
        token = _admission.set((str(gate.control), operation_id))
        try:
            return fn(*args, **kwargs)
        finally:
            _admission.reset(token)
            gate.release(operation_id)
    try:
        future = executor.submit(context.run, invoke)
    except BaseException:
        gate.release(operation_id)
        raise
    future.add_done_callback(lambda completed: gate.release(operation_id) if completed.cancelled() else None)
    return future


def create_task(kind: str, fn, /, *args, **kwargs):
    """Reserve background async work independently of its submitting HTTP request."""
    gate = managed_gate()
    if gate is None:
        return asyncio.create_task(fn(*args, **kwargs))
    operation_id, _owned = gate.acquire(kind, independent=True)

    async def invoke():
        token = _admission.set((str(gate.control), operation_id))
        try:
            return await fn(*args, **kwargs)
        finally:
            _admission.reset(token)
            gate.release(operation_id)

    coroutine = invoke()
    try:
        task = asyncio.create_task(coroutine)
    except BaseException:
        coroutine.close()
        gate.release(operation_id)
        raise
    task.add_done_callback(lambda done: gate.release(operation_id) if done.cancelled() else None)
    return task

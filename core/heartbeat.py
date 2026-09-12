"""外部存活心跳：只发送空 POST，缺席判断和告警由外部服务负责。

URL 是匿名访问凭据，仅在启用且到期时读取环境变量；本地记录不保存地址、
响应体或异常原文。调用者应在常驻调度器完成健康维护后调用 tick。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from core.config import cfg
from core.paid_model import FileLock, FileLockBusy, atomic_write_json
from core.store import assert_physical_direct_path


class _PrivateRequestLogFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.request_thread = threading.get_ident()

    def filter(self, record):
        return record.thread != self.request_thread


@contextmanager
def _private_request_logs():
    """httpx INFO 会记录完整URL；只屏蔽本线程这次心跳的传输日志。

    不改变应用日志级别，也不屏蔽其它线程的请求。心跳结果有不含地址的耐久记录。
    """
    names = {"httpx", *(name for name in logging.Logger.manager.loggerDict
                        if name.startswith("httpcore"))}
    loggers = [logging.getLogger(name) for name in names]
    guard = _PrivateRequestLogFilter()
    for logger in loggers:
        logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(guard)


@dataclass(frozen=True)
class HeartbeatSettings:
    enabled: bool = False
    interval_minutes: float = 5
    timeout_seconds: float = 3
    stale_after_minutes: float = 45
    url_env: str = "HEARTBEAT_URL"

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError("[heartbeat].enabled 必须是布尔值")
        values = (self.interval_minutes, self.timeout_seconds, self.stale_after_minutes)
        if (not all(isinstance(value, (float, int)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in values)
                or self.interval_minutes < 1 or not 0 < self.timeout_seconds <= 10
                or self.stale_after_minutes <= self.interval_minutes):
            raise ValueError("心跳间隔至少1分钟，超时须在0到10秒内，过期阈值须大于间隔")
        if not isinstance(self.url_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.url_env):
            raise ValueError("心跳 url_env 须为环境变量名；URL 不写入配置文件")

    @classmethod
    def load(cls, c=None):
        c = c or cfg()
        return cls(enabled=c.get("heartbeat", "enabled", False),
                   interval_minutes=c.get("heartbeat", "interval_minutes", 5),
                   timeout_seconds=c.get("heartbeat", "timeout_seconds", 3),
                   stale_after_minutes=c.get("heartbeat", "stale_after_minutes", 45),
                   url_env=c.get("heartbeat", "url_env", "HEARTBEAT_URL"))


def _moment(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("心跳时刻必须包含时区")
    return value.astimezone(timezone.utc)


def _guard(path: Path, _role: str = "target") -> Path:
    path = Path(path)
    assert_physical_direct_path(path.parent.parent, path.parent,
                                kind="directory", label="心跳状态目录")
    return assert_physical_direct_path(path.parent, path, kind="file", label="心跳状态文件")


def _load(path: Path) -> dict:
    _guard(path)
    if not path.exists():
        return {"version": 1, "last_attempt_at": None, "last_success_at": None,
                "next_due_at": None, "error_code": None, "consecutive_failures": 0}
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("version") != 1:
        raise ValueError("心跳状态格式无效")
    for key in ("last_attempt_at", "last_success_at", "next_due_at"):
        if record.get(key) is not None:
            _moment(record[key])
    if not isinstance(record.get("consecutive_failures"), int) or record["consecutive_failures"] < 0:
        raise ValueError("心跳失败计数无效")
    error = record.get("error_code")
    if error is not None and not re.fullmatch(r"[a-z_]+(?:\d{3})?", str(error)):
        raise ValueError("心跳错误记录无效")
    # 只接收固定字段；手工添加的地址或异常详情不会进入日志和 preflight。
    return {key: record.get(key) for key in (
        "version", "last_attempt_at", "last_success_at", "next_due_at",
        "error_code", "consecutive_failures")}


def heartbeat_status(path: Path, settings: HeartbeatSettings, now: datetime) -> dict:
    """只读派生新鲜度；不联系外部服务，不断言告警通道已经配置成功。"""
    moment = _moment(now)
    if not settings.enabled:
        return {"status": "disabled", "last_success_at": None, "age_seconds": None}
    try:
        record = _load(Path(path))
    except (OSError, ValueError, TypeError):
        return {"status": "unknown", "last_success_at": None, "age_seconds": None,
                "error_code": "state_unreadable"}
    last = record.get("last_success_at")
    result = {"status": "never", "last_success_at": last, "age_seconds": None,
              "last_attempt_at": record.get("last_attempt_at"),
              "error_code": record.get("error_code")}
    if last is not None:
        age = (moment - _moment(last)).total_seconds()
        result.update(age_seconds=age, status=("clock_skew" if age < 0 else
                      "stale" if age > settings.stale_after_minutes * 60 else "healthy"))
    return result


class Heartbeat:
    def __init__(self, path: Path, settings: HeartbeatSettings, *, http=None, environ=None):
        self.path, self.settings = Path(path), settings
        self.http = http
        self._owns_http = http is None
        self.environ = os.environ if environ is None else environ

    def close(self):
        if self.http is not None and self._owns_http:
            self.http.close()
            self.http = None

    def _send(self) -> str | None:
        url = self.environ.get(self.settings.url_env, "")
        if not isinstance(url, str) or not url.strip():
            return "missing_url"
        url = url.strip()
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.fragment or any(char.isspace() for char in url)):
                return "invalid_url"
        except ValueError:
            return "invalid_url"
        try:
            if self.http is None:
                self.http = httpx.Client(timeout=self.settings.timeout_seconds,
                                         follow_redirects=False, trust_env=False)
            # 不读取响应体，也不跟重定向，避免把匿名凭据带给另一个地址。
            with _private_request_logs(), self.http.stream(
                    "POST", url, content=b"", follow_redirects=False,
                    timeout=self.settings.timeout_seconds) as response:
                return None if response.is_success else f"http_{response.status_code}"
        except httpx.TimeoutException:
            return "timeout"
        except Exception:
            # HTTP 异常文本经常包含完整请求 URL，不能写入业务日志或状态。
            return "request_failed"

    def tick(self, now: datetime) -> dict:
        if not self.settings.enabled:
            return {"status": "disabled"}
        try:
            moment = _moment(now)
            lock = _guard(self.path.with_suffix(".lock"))
            with FileLock(lock, busy_message="心跳正在发送"):
                record = _load(self.path)
                due = record.get("next_due_at")
                if due is not None and moment < _moment(due):
                    return {"status": "not_due", "next_due_at": due}
                record.update(last_attempt_at=moment.isoformat(), error_code="unconfirmed",
                              next_due_at=(moment + timedelta(minutes=self.settings.interval_minutes)).isoformat())
                # 先记录本轮尝试，崩溃重启不会立刻重发；没有响应不能记成功。
                atomic_write_json(self.path, record, guard=_guard)
                error = self._send()
                record["error_code"] = error
                if error is None:
                    record["last_success_at"] = moment.isoformat()
                    record["consecutive_failures"] = 0
                else:
                    record["consecutive_failures"] += 1
                atomic_write_json(self.path, record, guard=_guard)
                return {"status": "sent" if error is None else "failed", "error_code": error,
                        "last_success_at": record["last_success_at"]}
        except FileLockBusy:
            return {"status": "busy"}
        except Exception:
            # 心跳自身的网络/状态故障不能中断已经授权的处理链。
            return {"status": "failed", "error_code": "state_unavailable"}

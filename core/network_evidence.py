"""本进程的出口 IP/ASN 观测；不发起社媒访问，不推断账号封禁。

官方接口核对（2026-09-12）：
https://ipinfo.io/developers/lite-api  /lite/me 提供 IP、ASN，不提供 ASN 类型。
https://ipinfo.io/developers/core-api  /lookup/me 提供 as.type 和网络标志，需 Core 权限。
https://ipinfo.io/developers 支持 Authorization Bearer；token 不进入 URL 或本地记录。

这只能证明此进程到 IPinfo 的出口。Chrome 代理、VPN 分流可能不同，因此不声称
这是 Facebook/Instagram 看到的地址，也不把 ISP 分类当成住宅 IP 证明。
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from core.config import cfg
from core.paid_model import FileLock, FileLockBusy, atomic_write_json, ModelCredentials
from core.store import assert_physical_direct_path

_TYPES = {"hosting", "isp", "education", "government", "business", "unknown"}
_ENDPOINTS = {"lite": "https://api.ipinfo.io/lite/me", "core": "https://api.ipinfo.io/lookup/me"}


@dataclass(frozen=True)
class NetworkEvidenceSettings:
    enabled: bool = False
    tier: str = "lite"
    interval_minutes: float = 60
    history_size: int = 20
    timeout_seconds: float = 3

    def __post_init__(self):
        if not isinstance(self.enabled, bool) or self.tier not in _ENDPOINTS:
            raise ValueError("出口观测开关须为布尔值，tier 仅支持 lite/core")
        if (isinstance(self.history_size, bool) or not isinstance(self.history_size, int)
                or not 2 <= self.history_size <= 100):
            raise ValueError("出口观测历史数须在 2 到 100 之间")
        if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in (self.interval_minutes, self.timeout_seconds))
                or self.interval_minutes < 1 or not 0 < self.timeout_seconds <= 10):
            raise ValueError("出口观测间隔至少 1 分钟，超时须在 0 到 10 秒内")

    @classmethod
    def load(cls, c=None):
        config = c or cfg()
        return cls(**{name: config.get("network_evidence", name, default)
                      for name, default in (("enabled", False), ("tier", "lite"),
                                            ("interval_minutes", 60), ("history_size", 20),
                                            ("timeout_seconds", 3))})


def _at(value) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
        raise ValueError("出口观测时刻须带时区")
    return parsed.astimezone(timezone.utc)


def _safe(path: Path, _role: str = "state") -> Path:
    parent = Path(path).parent
    assert_physical_direct_path(parent.parent, parent, kind="directory", label="出口证据目录")
    return assert_physical_direct_path(parent, Path(path), kind="file", label="出口证据文件")


def _load(path: Path) -> dict:
    if not _safe(path).exists():
        return {"version": 1, "history": [], "last_attempt_at": None,
                "next_due_at": None, "last_error": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("history"), list):
        raise ValueError("出口证据格式无效")
    for key in ("last_attempt_at", "next_due_at"):
        if state.get(key) is not None:
            _at(state[key])
    for row in state["history"]:
        _at(row["at"])
        ipaddress.ip_address(row["ip"])
        if row["asn_type"] not in _TYPES or not re.fullmatch(r"AS[1-9][0-9]*|unknown", row["asn"]):
            raise ValueError("出口证据值无效")
    return state


def _observation(data: dict, at: datetime, tier: str) -> dict:
    address = ipaddress.ip_address(data["ip"])
    raw_as = data.get("as") if isinstance(data.get("as"), dict) else {}
    asn = raw_as.get("asn") or data.get("asn") or "unknown"
    if not isinstance(asn, str) or not re.fullmatch(r"AS[1-9][0-9]*|unknown", asn):
        raise ValueError("不是有效 ASN")
    asn_type = raw_as.get("type")
    if not isinstance(asn_type, str) or asn_type not in _TYPES:
        asn_type = "unknown"
    return {"at": at.isoformat(), "ip": str(address), "asn": asn, "asn_type": asn_type,
            "is_hosting": data.get("is_hosting") if isinstance(data.get("is_hosting"), bool) else None,
            "is_anonymous": data.get("is_anonymous") if isinstance(data.get("is_anonymous"), bool) else None,
            "provider": "ipinfo", "tier": tier, "scope": "python_http_to_ipinfo"}


def network_evidence_status(path: Path, settings: NetworkEvidenceSettings,
                            now: datetime | None = None) -> dict:
    """只读最近成功的样本；未知类型和浏览器出口均不推测。"""
    result = {"status": "disabled", "history": [], "latest": None, "last_success_at": None,
              "age_seconds": None, "last_error": None, "stability": "insufficient_samples",
              "distinct_ips": 0, "distinct_asns": 0, "network_type": "unknown",
              "residential_verified": False, "scope": "python_http_to_ipinfo"}
    if not settings.enabled:
        return result
    try:
        state = _load(Path(path))
        history = state["history"][-settings.history_size:]
        result.update(history=history, last_error=state.get("last_error"), status="never")
        if not history:
            return result
        latest = history[-1]
        age = (_at(now or datetime.now(timezone.utc)) - _at(latest["at"])).total_seconds()
        ips, asns = {row["ip"] for row in history}, {row["asn"] for row in history if row["asn"] != "unknown"}
        network_type = ("hosting" if latest.get("is_hosting") or latest["asn_type"] == "hosting" else
                        "anonymous" if latest.get("is_anonymous") else
                        "isp_unverified" if latest["asn_type"] == "isp" else latest["asn_type"])
        return {**result, "latest": latest, "last_success_at": latest["at"], "age_seconds": age,
                "distinct_ips": len(ips), "distinct_asns": len(asns), "network_type": network_type,
                "status": "clock_skew" if age < 0 else "stale" if age >= settings.interval_minutes * 180 else "healthy",
                "stability": "insufficient_samples" if len(history) < 2 else
                             "changed" if len(ips) > 1 or len(asns) > 1 else "stable_observed"}
    except Exception:
        return {**result, "status": "unknown", "last_error": "state_unavailable"}


def detection_failure_kind(error: str | None) -> str | None:
    """仅归类已发生的探测失败文字，不新增登录/账号探测。"""
    value = str(error or "").lower()
    if not value:
        return None
    if "/checkpoint" in value or "/challenge" in value:
        return "account_checkpoint"
    if "429" in value:
        return "rate_limited"
    if any(marker in value for marker in ("/login", "401", "403", "会话失效")):
        return "session_or_permission"
    if any(marker in value for marker in ("net::err_", "timeout", "timed out", "连接失败", "connection")):
        return "connection_or_timeout"
    return "unclassified"


class NetworkEvidence:
    def __init__(self, path: Path, settings: NetworkEvidenceSettings, *, http=None, environ=None):
        self.path, self.settings = Path(path), settings
        self.http, self._owned = http, False
        self.environ = environ

    def close(self):
        if self._owned and self.http is not None:
            self.http.close()
            self.http = None

    def _request(self, token: str) -> dict:
        if self.http is None:
            self.http, self._owned = httpx.Client(), True
        started = time.monotonic()
        with self.http.stream("GET", _ENDPOINTS[self.settings.tier],
                              headers={"Authorization": "Bearer " + token},
                              timeout=self.settings.timeout_seconds, follow_redirects=False) as response:
            if response.status_code != 200:
                code = ("provider_auth" if response.status_code in {401, 403} else
                        "provider_rate_limited" if response.status_code == 429 else
                        "provider_redirect" if 300 <= response.status_code < 400 else "provider_error")
                return {"error_code": code}
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 32768:
                    return {"error_code": "invalid_payload"}
                if time.monotonic() - started > self.settings.timeout_seconds:
                    return {"error_code": "network_timeout"}
            return {"body": json.loads(data)}

    def refresh(self, now: datetime | None = None) -> dict:
        if not self.settings.enabled:
            return {"status": "disabled"}
        at = _at(now or datetime.now(timezone.utc))
        try:
            _safe(self.path)
            lock = _safe(self.path.with_name("network_evidence.lock"))
            with FileLock(lock, busy_message='出口观测正在进行'):
                state = _load(self.path)
                if state.get("next_due_at") and at < _at(state["next_due_at"]):
                    return {"status": "not_due", "next_due_at": state["next_due_at"]}
                state.update(last_attempt_at=at.isoformat(),
                             next_due_at=(at + timedelta(minutes=self.settings.interval_minutes)).isoformat())
                atomic_write_json(self.path, state, guard=_safe)
                error, row = None, None
                token = (ModelCredentials('IPINFO_TOKEN').optional_value() if self.environ is None
                         else self.environ.get('IPINFO_TOKEN', ''))
                if not isinstance(token, str) or not token.strip():
                    error = "missing_token"
                else:
                    try:
                        response = self._request(token.strip())
                        error = response.get("error_code")
                        if not error:
                            row = _observation(response["body"], _at(now or datetime.now(timezone.utc)), self.settings.tier)
                    except httpx.TimeoutException:
                        error = "network_timeout"
                    except httpx.NetworkError:
                        error = "network_connection_failed"
                    except (ValueError, TypeError, KeyError):
                        error = "invalid_payload"
                    except Exception:
                        error = "request_failed"
                if row:
                    state["history"] = [*state["history"], row][-self.settings.history_size:]
                state["last_error"] = error
                atomic_write_json(self.path, state, guard=_safe)
                return {"status": "failed" if error else "recorded", "error_code": error,
                        "next_due_at": state["next_due_at"]}
        except FileLockBusy:
            return {"status": "busy"}
        except Exception:
            return {"status": "failed", "error_code": "state_unavailable"}

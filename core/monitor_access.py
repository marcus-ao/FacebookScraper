"""Persistent navigation intents and profile stop state for every detect entrance."""
from __future__ import annotations

import json
import random

from core.config import MonitorSchedule
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core.integrity import parse_ts
from core.paid_model import FileLock, atomic_write_json

PLATFORMS = ("facebook", "instagram")


class AccessDenied(ValueError):
    """Access is closed; no navigation may follow this error."""


def _normalize_stop(stop, *, legacy=False):
    if stop is None:
        return None
    if not isinstance(stop, dict):
        raise AccessDenied("invalid hard stop record")
    at = stop.get("at") or (stop.get("recorded_at") if legacy else None)
    if (not isinstance(stop.get("reason"), str) or not stop["reason"].strip()
            or stop.get("platform") not in (*PLATFORMS, "google_trends") or parse_ts(at) is None):
        raise AccessDenied("invalid hard stop record")
    return {**stop, "at": at}


def next_homepage_due(now, schedule, rng):
    """Draw once across duty boundaries; the first morning visit keeps a 45 minute floor."""
    minutes = schedule.interval_minutes(now) * rng.uniform(1 - schedule.jitter_ratio, 1 + schedule.jitter_ratio)
    following = now + timedelta(minutes=minutes)
    local = schedule.local(now)
    start = local.replace(hour=8, minute=0, second=0, microsecond=0)
    if local >= start:
        start += timedelta(days=1)
    if now < start < following or start - timedelta(minutes=30) < following < start:
        following = max(start + timedelta(minutes=rng.uniform(0, 15)), now + timedelta(minutes=45))
    return following.astimezone(timezone.utc)


class AccessController:
    def __init__(self, state_dir: Path, *, clock=None, schedule=None, rng=None):
        self.path = Path(state_dir) / "monitor_access.json"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.schedule = schedule or MonitorSchedule()
        self.rng = rng or random.SystemRandom()

    def _lock(self):
        return FileLock(self.path.with_suffix(".lock"), error_type=AccessDenied,
                        busy_message="monitor access state is busy")

    def status(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (data["version"] != 1 or type(data["revision"]) is not int
                    or data["revision"] < 1 or not isinstance(data["history"], list)
                    or (data["hard_stop"] is not None and not isinstance(data["hard_stop"], dict))):
                raise ValueError("invalid header")
            _normalize_stop(data["hard_stop"])
            for platform in PLATFORMS:
                entry = data["platforms"][platform]
                if (type(entry["failures"]) is not int or entry["failures"] < 0
                        or type(entry["paused"]) is not bool or not isinstance(entry["intents"], list)
                        or parse_ts(entry["next_due_at"]) is None
                        or (entry["failures"] >= 3 and not entry["paused"])):
                    raise ValueError("invalid platform")
                for intent in entry["intents"]:
                    if (intent["kind"] not in {"homepage", "detail"}
                            or parse_ts(intent["at"]) is None or not isinstance(intent["scan_id"], str)
                            or not intent["scan_id"] or not isinstance(intent["post_id"], str)
                            or intent.get("run_kind") not in {"delta", "reconcile", "recovery", "sampling"}
                            or (intent["run_kind"] in {"reconcile", "sampling"} and intent["kind"] != "homepage")
                            or (intent["run_kind"] == "recovery" and intent["kind"] != "detail")):
                        raise ValueError("invalid intent")
                for item in entry["intents"]:
                    if item["run_kind"] == "reconcile":
                        local = MonitorSchedule.local(parse_ts(item["at"]))
                        start = local.replace(hour=6, minute=30, second=0, microsecond=0)
                        if not start <= local <= start + timedelta(hours=1):
                            raise ValueError("morning intent outside allowed window")
                mornings = [str(MonitorSchedule.local(parse_ts(item["at"])).date())
                            for item in entry["intents"] if item["kind"] == "homepage" and item["run_kind"] == "reconcile"]
                marker = entry.get("morning_date")
                if "morning_date" in entry:
                    if not isinstance(marker, str) or date.fromisoformat(marker).isoformat() != marker:
                        raise ValueError("invalid morning date")
                if marker != (max(mornings) if mornings else None) or len(mornings) != len(set(mornings)):
                    raise ValueError("morning consumption differs from navigation intents")
            return data
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AccessDenied("monitor access state missing/unreadable; explicit initialization or repair required") from exc

    def _save(self, data):
        data["revision"] += 1
        atomic_write_json(self.path, data)
        return data

    def initialize(self, reason: str):
        if not reason.strip():
            raise AccessDenied("initialization requires a reason")
        with self._lock():
            if self.path.exists():
                raise AccessDenied("access state already exists; use revision-checked recovery")
            legacy_path = self.path.with_name("delta_state.json")
            legacy = {}
            if legacy_path.exists():
                try:
                    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                    if not isinstance(legacy, dict):
                        raise ValueError("legacy state must be an object")
                    for platform in PLATFORMS:
                        if not isinstance(legacy.get(platform, {}), dict):
                            raise ValueError("legacy platform must be an object")
                except (OSError, ValueError) as exc:
                    raise AccessDenied("legacy delta safety is unreadable; repair before initialization") from exc
            legacy_stop = _normalize_stop(legacy.get("detect_hard_blocked"), legacy=True)
            trends_path = self.path.with_name("trends_export_state.json")
            if trends_path.exists():
                try:
                    trends = json.loads(trends_path.read_text(encoding="utf-8"))
                    if not isinstance(trends, dict):
                        raise ValueError("invalid legacy Trends safety")
                    if trends.get("status") == "blocked":
                        trends_stop = _normalize_stop({"reason": trends.get("reason"),
                                                       "platform": "google_trends", "at": trends.get("blocked_at")})
                        legacy_stop = legacy_stop or trends_stop
                except (OSError, ValueError) as exc:
                    raise AccessDenied("legacy Trends safety is unreadable; repair before initialization") from exc
            data = {"version": 1, "revision": 0, "hard_stop": legacy_stop,
                    "platforms": {}, "history": [{"action": "initialize", "reason": reason,
                                                   "at": self.clock().isoformat()}]}
            for platform in PLATFORMS:
                failures = legacy.get(platform, {}).get("consecutive_failures", 0)
                if type(failures) is not int or failures < 0:
                    raise AccessDenied("invalid legacy failure counter")
                data["platforms"][platform] = {"failures": failures, "paused": failures >= 3,
                                                "intents": [], "next_due_at": self.clock().isoformat()}
            return self._save(data)

    def check_profile(self):
        data = self.status()
        if data["hard_stop"] is not None:
            raise AccessDenied("detect profile hard stopped: " + data["hard_stop"]["reason"])
        return data

    def profile_stop(self, reason, *, source="google_trends"):
        stop = _normalize_stop({"reason": reason, "platform": source, "at": self.clock().isoformat()})
        with self._lock():
            data = self.status()
            data["hard_stop"] = stop
            return self._save(data)

    def _check(self, data, platform):
        if platform not in PLATFORMS:
            raise AccessDenied("unknown platform")
        if data["hard_stop"]:
            raise AccessDenied("detect profile hard stopped: " + str(data["hard_stop"].get("reason")))
        if data["platforms"][platform]["paused"]:
            raise AccessDenied(platform + " paused after ordinary failures")

    def check(self, platform):
        data = self.status()
        self._check(data, platform)
        return data

    def _reserve(self, platform, scan_id, kind, post_id="", run_kind="delta", manual=False):
        if (not isinstance(scan_id, str) or not scan_id or (kind == "detail" and not post_id)
                or run_kind not in {"delta", "reconcile", "recovery", "sampling"}
                or (kind == "homepage" and run_kind == "recovery")):
            raise AccessDenied("navigation intent needs scan/post identity")
        with self._lock():
            data = self.status()
            self._check(data, platform)
            now = self.clock()
            entry = data["platforms"][platform]
            local = MonitorSchedule.local(now)
            duty_start = local.replace(hour=8, minute=0, second=0, microsecond=0)
            if kind == "homepage" and run_kind != "reconcile" and duty_start - timedelta(minutes=30) < local < duty_start:
                entry["next_due_at"] = (duty_start + timedelta(minutes=self.rng.uniform(0, 15))).isoformat()
                self._save(data)
                raise AccessDenied("late off-duty visit merged into the first 08:00-08:15 visit")
            if kind == "homepage" and run_kind == "reconcile":
                if not duty_start - timedelta(minutes=90) <= local <= duty_start - timedelta(minutes=30):
                    raise AccessDenied("morning scan outside 06:30-07:30 Shanghai")
                if entry.get("morning_date") == str(local.date()):
                    raise AccessDenied("morning scan already consumed for Shanghai date")
            if kind == "homepage" and now < parse_ts(entry["next_due_at"]):
                raise AccessDenied(platform + " homepage is not due until " + entry["next_due_at"])
            intents = data["platforms"][platform]["intents"]
            recent = [item for item in intents if parse_ts(item["at"]) > now - timedelta(hours=24)]
            limit = 24 if kind == "homepage" else 12
            if sum(item["kind"] == kind for item in recent) >= limit:
                raise AccessDenied(platform + " rolling 24h " + kind + " quota exhausted")
            if kind == "homepage" and any(item["scan_id"] == scan_id and item["kind"] == "homepage" for item in intents):
                raise AccessDenied("homepage already reserved for scan")
            if kind == "detail":
                if not manual and not any(item["scan_id"] == scan_id and item["kind"] == "homepage" for item in intents):
                    raise AccessDenied("detail scan has no reserved homepage")
                scan = [item for item in intents if item["scan_id"] == scan_id and item["kind"] == kind]
                if len(scan) >= 3 or any(item["post_id"] == post_id for item in scan):
                    raise AccessDenied("detail per-scan quota or duplicate post")
            if kind == "homepage":
                entry["next_due_at"] = next_homepage_due(now, self.schedule, self.rng).isoformat()
                if run_kind == "reconcile":
                    entry["morning_date"] = str(local.date())
            intents.append({"kind": kind, "run_kind": run_kind, "scan_id": scan_id, "post_id": str(post_id), "at": now.isoformat()})
            return self._save(data)

    def plan_homepage(self, platform, when):
        """Scheduler publishes its persisted draw while holding the global delta lock."""
        if parse_ts(when.isoformat()) is None:
            raise AccessDenied("invalid next due time")
        with self._lock():
            data = self.status()
            entry = data["platforms"][platform]
            previous = [parse_ts(item["at"]) for item in entry["intents"] if item["kind"] == "homepage"]
            if previous:
                when = max(when, max(previous) + timedelta(minutes=45))
            if parse_ts(entry["next_due_at"]) == when:
                return data
            entry["next_due_at"] = when.isoformat()
            return self._save(data)

    def reserve_homepage(self, platform, scan_id, *, run_kind="delta"):
        return self._reserve(platform, scan_id, "homepage", run_kind=run_kind)

    def reserve_detail(self, platform, scan_id, post_id, *, manual=False):
        return self._reserve(platform, scan_id, "detail", str(post_id), manual=manual,
                             run_kind="recovery" if manual else "delta")

    def outcome(self, platform, *, success: bool, reason="", hard=False):
        with self._lock():
            data = self.status()
            entry = data["platforms"][platform]
            if not success and parse_ts(entry["next_due_at"]) <= self.clock():
                entry["next_due_at"] = next_homepage_due(self.clock(), self.schedule, self.rng).isoformat()
            if hard:
                data["hard_stop"] = {"reason": reason, "platform": platform, "at": self.clock().isoformat()}
            if not success:
                entry["failures"] += 1
                entry["paused"] = entry["paused"] or entry["failures"] >= 3
            elif not entry["paused"]:
                entry["failures"] = 0
            entry["last_outcome"] = {"success": success, "reason": reason, "at": self.clock().isoformat()}
            return self._save(data)

    def recover(self, expected_revision: int, reason: str, platform=None):
        if type(expected_revision) is not int or not reason.strip():
            raise AccessDenied("recovery requires an integer revision and a reason")
        with self._lock():
            data = self.status()
            if data["revision"] != expected_revision:
                raise AccessDenied("access revision changed; review current state first")
            if platform is not None and platform not in PLATFORMS:
                raise AccessDenied("unknown platform")
            if platform is None:
                data["hard_stop"] = None
            for name in PLATFORMS if platform is None else (platform,):
                data["platforms"][name].update(failures=0, paused=False)
            data["history"].append({"action": "recover", "reason": reason, "platform": platform,
                                    "at": self.clock().isoformat(), "expected_revision": expected_revision})
            return self._save(data)

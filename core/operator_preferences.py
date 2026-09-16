"""Portable operator choices shared by immutable release configurations."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

FIELDS = {'default_times': ('publish.schedule_rule', 'times'),
          'snooze_default_days': ('review', 'snooze_default_days')}


def validate_default_times(times):
    if (not isinstance(times, list) or not 1 <= len(times) <= 12
            or any(not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) for value in times)
            or len(set(times)) != len(times)):
        raise ValueError('默认时间须为 1 至 12 个不重复的 HH:MM 柏林时刻')
    return tuple(times)


def validate(values):
    if not isinstance(values, dict) or not values or set(values) - FIELDS.keys():
        raise ValueError('仅可修改默认排期时间和挂起工作日数')
    if 'snooze_default_days' in values:
        days = values['snooze_default_days']
        if type(days) is not int or not 1 <= days <= 30:
            raise ValueError('挂起天数须为 1 至 30 个工作日')
    if 'default_times' in values:
        validate_default_times(values['default_times'])
    return values


def revision(path: Path | None) -> bytes:
    return path.read_bytes() if path and path.exists() else b''


def decode(raw: bytes) -> dict:
    return validate(json.loads(raw)) if raw else {}


def apply(config: dict, values: dict):
    for name, value in values.items():
        section, key = FIELDS[name]
        parent = config
        for part in section.split('.'):
            parent = parent.setdefault(part, {})
        parent[key] = value


def version(config: bytes, preferences: bytes = b'') -> str:
    return hashlib.sha256(config + (b'\0' + preferences if preferences else b'')).hexdigest()

"""Two operator preferences with compare-and-swap, preserving the TOML document."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from copy import deepcopy

from core.config import cfg, invalidate_cfg_cache
from core.paid_model import FileLock

FIELDS = {'default_times': ('publish.schedule_rule', 'times'),
          'snooze_default_days': ('review', 'snooze_default_days')}


class SettingsConflict(RuntimeError):
    pass


def validate_default_times(times):
    """Validate the shared operator-facing Berlin slot defaults."""
    if (not isinstance(times, list) or not 1 <= len(times) <= 12
            or any(not isinstance(value, str)
                   or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value)
                   for value in times)
            or len(set(times)) != len(times)):
        raise ValueError('默认时间须为 1 至 12 个不重复的 HH:MM 柏林时刻')
    return tuple(times)


def _validated(values):
    if not isinstance(values, dict) or not values or set(values) - FIELDS.keys():
        raise ValueError('仅可修改默认排期时间和挂起工作日数')
    if 'snooze_default_days' in values:
        days = values['snooze_default_days']
        if type(days) is not int or not 1 <= days <= 30:
            raise ValueError('挂起天数须为 1 至 30 个工作日')
    if 'default_times' in values:
        validate_default_times(values['default_times'])
    return values


def read():
    data = cfg().path.read_bytes()
    raw = tomllib.loads(data.decode('utf-8'))
    pub = raw.get('publish', {})
    result = {'version': hashlib.sha256(data).hexdigest(),
            'editable': {'default_times': pub.get('schedule_rule', {}).get('times', ['10:00', '17:00']),
                         'snooze_default_days': raw.get('review', {}).get('snooze_default_days', 3)},
            'business_timezone': 'Europe/Berlin', 'workday_timezone': 'Asia/Shanghai',
            # Only non-secret policy fields are exposed, never the local runtime binding or credentials.
            'controlled': {key: deepcopy(raw.get(key, {})) for key in
                           ('targets', 'delta', 'pipeline', 'network_evidence')}
                          | {'publish_identity': {key: value for key, value in pub.items()
                                                  if key.startswith('expected_') or key in
                                                  {'facebook_page_name', 'instagram_account'}},
                             'price_map': pub.get('price_map', {}),
                             'trusted_owners': pub.get('trusted_owners', {})}}
    notes = _comments(data.decode('utf-8'))
    result['controlled_fields'] = {}
    for group, values in result['controlled'].items():
        prefix = {'publish_identity': 'publish', 'price_map': 'publish.price_map',
                  'trusted_owners': 'publish.trusted_owners'}.get(group, group)
        result['controlled_fields'][group] = _fields(values, prefix, notes)
    result['editable_help'] = {name: notes.get(section + '.' + key, '')
                               for name, (section, key) in FIELDS.items()}
    return result


def _comments(text):
    """Associate TOML comments with exposed keys, without returning hidden values.

    Quoted hashes (URLs, hashtags, price keys) are data. The original TOML parser
    remains authoritative; this pass only supplies operator help text.
    """
    notes, pending, section = {}, [], ''
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            pending.append(stripped[1:].strip())
            continue
        if not stripped:
            pending = []
            continue
        code, inline, quote, escaped = [], '', None, False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
            elif quote == '"' and char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            elif quote is None and char in {'"', "'"}:
                quote = char
            elif quote is None and char == '#':
                inline = line[index + 1:].strip()
                break
            code.append(char)
        body = ''.join(code).strip()
        header = re.fullmatch(r'\[([\w.]+)\]', body)
        if header:
            section = header[1]
        elif '=' in body:
            key = body.split('=', 1)[0].strip()
            try:
                keys = list(tomllib.loads(key + ' = 0'))
                if len(keys) == 1:
                    notes[section + '.' + keys[0]] = '\n'.join(pending + ([inline] if inline else []))
            except tomllib.TOMLDecodeError:
                pass
        pending = []
    return notes


def _fields(values, prefix, notes):
    rows = []
    for key, value in values.items():
        path = prefix + '.' + key
        if isinstance(value, dict):
            rows.extend(_fields(value, path, notes))
        else:
            rows.append({'key': path, 'value': value, 'help': notes.get(path, '')})
    return rows


def _replace(text, section, key, value):
    """Change the existing scalar/inline-array value, retaining comments and line endings.

    TOML parsing before and after the patch verifies that no unrelated field changes.
    A manually expanded multiline setting stays untouched and gets an explicit conflict.
    """
    pattern = re.compile(r'(?m)^\[' + re.escape(section) + r'\][^\r\n]*(?:\r?\n|$)')
    header = pattern.search(text)
    rendered = json.dumps(value, ensure_ascii=False)
    newline = '\r\n' if '\r\n' in text else '\n'
    if header is None:
        return text + ('' if text.endswith('\n') else newline) + f'[{section}]' + newline + f'{key} = {rendered}' + newline
    next_header = re.search(r'(?m)^\s*\[', text[header.end():])
    end = header.end() + next_header.start() if next_header else len(text)
    body = text[header.end():end]
    entry = re.search(r'(?m)^(\s*' + re.escape(key) + r'\s*=\s*)([^\r\n#]*?)(\s*(?:#[^\r\n]*)?)(?=\r?$)', body)
    if entry is None:
        return text[:end] + f'{key} = {rendered}' + newline + text[end:]
    try:
        tomllib.loads('value = ' + entry.group(2))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsConflict('此设置使用多行格式，请先在配置文件中整理为单行，再从页面修改') from exc
    start = header.end() + entry.start(2)
    finish = header.end() + entry.end(2)
    return text[:start] + rendered + text[finish:]


def save(values, expected_version):
    values = _validated(values)
    c = cfg()
    path = c.path
    with FileLock(path.with_suffix('.lock'), busy_message='另一位同事正在保存设置，请稍后重试'):
        before = path.read_bytes()
        if hashlib.sha256(before).hexdigest() != expected_version:
            raise SettingsConflict('配置已有更新，请重新读取后再保存；本次内容尚未写入')
        text = before.decode('utf-8')
        expected = tomllib.loads(text)
        for name, value in values.items():
            section, key = FIELDS[name]
            text = _replace(text, section, key, value)
            parent = expected
            for part in section.split('.'):
                parent = parent.setdefault(part, {})
            parent[key] = value
        if tomllib.loads(text) != expected:
            raise SettingsConflict('配置格式不能安全保留，请检查配置文件；本次没有写入')
        descriptor, temporary = tempfile.mkstemp(prefix='.settings-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(text.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != before:
                raise SettingsConflict('保存期间配置被其他程序修改，请重新读取')
            os.replace(temporary, path)
            # 这次改写和原文等长，(mtime_ns, size) 判据看不见它。原因在
            # core.config.invalidate_cfg_cache 的注释里。
            invalidate_cfg_cache()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return read()

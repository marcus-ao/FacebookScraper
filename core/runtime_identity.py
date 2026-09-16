"""Read-only release identity and mandatory bindings for managed installations."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path


def read_release(root: Path) -> dict | None:
    path = root / 'release.json'
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or value.get('protocol') != 1 or value.get('truth_contract') != 1
                or not re.fullmatch(r'[a-f0-9]{40}', str(value.get('sha', '')))
                or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('runtime_id', '')))):
            raise ValueError('invalid release identity')
        return value
    except (OSError, ValueError) as exc:
        raise ValueError('发布版本清单缺失或无效') from exc


def _plain_path(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError('服务机运行绑定必须使用绝对路径')
    for item in (path, *path.parents):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('服务机运行绑定不能包含链接或重解析点')
    return path.resolve(strict=True)


def validate_binding(config, root: Path) -> dict:
    """Never create directories while proving the business instance identity."""
    try:
        raw = os.environ.get('FBSCRAPER_CONTROL_DIR', '')
        if not raw:
            raise ValueError('发布版本必须由部署控制器或受管 CLI 启动')
        control = _plain_path(Path(raw))
        _plain_path(control / 'instance.json')
        marker = json.loads((control / 'instance.json').read_text(encoding='utf-8'))
        if (not isinstance(marker, dict) or marker.get('version') != 1
                or not isinstance(marker.get('instance_id'), str) or not marker['instance_id']
                or not isinstance(marker.get('shared'), str)):
            raise ValueError('实例标识无效')
        shared = _plain_path(Path(marker['shared']))
        _plain_path(shared / 'instance.json')
        if json.loads((shared / 'instance.json').read_text(encoding='utf-8')) != marker:
            raise ValueError('运行目录与业务实例标识不一致')
        for section, key, expected in (
                ('paths', 'archive', shared / 'archive'), ('paths', 'state', shared / 'state'),
                ('runtime', 'env_file', shared / '.env')):
            bound = _plain_path(Path(config.get(section, key, '')))
            if bound != expected or (key != 'env_file' and not bound.is_dir()):
                raise ValueError('运行绑定不属于此业务实例：' + key)
        if not (shared / '.env').is_file():
            raise ValueError('业务凭据文件不存在')
        return marker
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('无法确认服务机业务数据绑定：' + str(exc)) from exc

"""tests_operating_settings 的偶发失败复现器（只读调查，不改业务代码）。

背景：`tests_operating_settings.test_preserves_other_bytes_and_reloads_current_values`
在 65 脚本全量首轮出现过一次 `3 != 4`，单独复跑通过。怀疑与
`core/config.cfg()` 用 `(st_mtime_ns, st_size)` 判断重载有关：

  * `save()` 把 `["10:00", "17:00"]` 改成 `["11:30", "18:00"]`、
    `snooze_default_days = 3` 改成 `= 4`，**两处替换都不改变字节数**，
    所以 `st_size` 永远相同；
  * 于是能否重载完全取决于 `st_mtime_ns` 是否变化。

这个脚本重复执行同一条用例，并同时记录两次写入的 mtime 是否相同，
用来判断"同一时钟刻度内的等长改写"是不是真实成因。

用法：
    scripts\\run_python.bat docs/ui-refactor/tools/flaky_settings_probe.py --runs 300
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from core import config, operating_settings  # noqa: E402

ORIGINAL = ('# 保留运营说明\r\n[publish.schedule_rule]\r\n'
            'times = ["10:00", "17:00"] # 柏林时刻\r\n'
            '\r\n[review]\r\nsnooze_default_days = 3 # 工作日\r\n'
            '[pipeline]\r\ndaily_budget_usd = 5\r\n')


def one_round() -> dict:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'config.toml'
        path.write_bytes(ORIGINAL.encode())
        before_stat = path.stat()
        current = config.Config(path)
        with patch.object(config, '_cfg', current):
            snapshot = operating_settings.read()
            operating_settings.save({'default_times': ['11:30', '18:00'],
                                     'snooze_default_days': 4}, snapshot['version'])
            after_stat = path.stat()
            observed = config.cfg().get('review', 'snooze_default_days')
        return {
            'observed': observed,
            'same_mtime': before_stat.st_mtime_ns == after_stat.st_mtime_ns,
            'same_size': before_stat.st_size == after_stat.st_size,
            'delta_ns': after_stat.st_mtime_ns - before_stat.st_mtime_ns,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=300)
    args = parser.parse_args()

    failures = 0
    same_mtime = 0
    same_size = 0
    deltas: list[int] = []
    for _ in range(args.runs):
        result = one_round()
        if result['observed'] != 4:
            failures += 1
        if result['same_mtime']:
            same_mtime += 1
        if result['same_size']:
            same_size += 1
        deltas.append(result['delta_ns'])

    deltas.sort()
    print(f'runs={args.runs}')
    print(f'failures={failures} ({failures / args.runs:.2%})')
    print(f'identical_mtime={same_mtime} ({same_mtime / args.runs:.2%})')
    print(f'identical_size={same_size} ({same_size / args.runs:.2%})')
    print(f'mtime_delta_ns min={deltas[0]} p50={deltas[len(deltas) // 2]} max={deltas[-1]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

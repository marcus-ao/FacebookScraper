"""Run the versioned offline scripts with isolated runtime paths and per-script logs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', action='append', help='Test script stem, repeatable')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args(argv)
    selected = [p for p in sorted((ROOT / 'tests').glob('tests_*.py')) if not args.only or p.stem in args.only]
    if not selected or args.only and set(args.only) - {p.stem for p in selected}:
        parser.error('Unknown or empty test selection')
    output = ROOT / 'state' / ('offline-validation-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir(parents=True)
    results = []
    for script in selected:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='fbscraper-test-') as raw:
            root = Path(raw)
            local = root / 'runtime.toml'
            local.write_text('[paths]\narchive = ' + json.dumps(str(root / 'archive')) + '\nstate = ' +
                             json.dumps(str(root / 'state')) + '\n[runtime]\nenv_file = ' +
                             json.dumps(str(root / 'empty.env')), encoding='utf-8')
            (root / 'empty.env').write_bytes(b'')
            environment = dict(os.environ, PYTHONIOENCODING='utf-8', FBSCRAPER_RUNTIME_CONFIG=str(local),
                               PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / 'tests'))
            log = output / (script.stem + '.log')
            try:
                with log.open('w', encoding='utf-8') as stream:
                    process = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=environment,
                        stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        row = {'test': script.stem, 'exit_code': code, 'seconds': round(time.monotonic() - started, 2), 'log': str(log)}
        results.append(row)
        print(('PASS' if code == 0 else 'FAIL') + f' {script.stem} ({row["seconds"]}s)', flush=True)
        if code:
            content = log.read_text('utf-8', errors='replace')
            if len(content) > 7000:
                content = content[:3500] + '\n... middle omitted; full log below ...\n' + content[-3500:]
            print(content, flush=True)
            print(f'Full log: {log}', flush=True)
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    failed = sum(r['exit_code'] != 0 for r in results)
    print(f'{len(results) - failed}/{len(results)} scripts passed; evidence {output}', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Start source LAN Web through the existing frontend build launcher."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from core.web_access import load_web_access

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        if os.environ.get('FBSCRAPER_CONTROL_DIR') or (ROOT / 'release.json').exists():
            raise ValueError('Use a source-only terminal and checkout for LAN startup.')
        os.environ['FBSCRAPER_NETWORK_CONFIG'] = str(ROOT / 'ops/service-machine.network.json')
        policy = load_web_access()
        if policy.web_host != '0.0.0.0':
            raise ValueError('Source LAN startup requires web_host=0.0.0.0.')
        print(f'Source LAN Web: {policy.public_base_url}', flush=True)
        return subprocess.call([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
            str(ROOT / 'scripts/run_web.bat'), '--host', policy.web_host,
            '--port', str(policy.web_port), '--no-proxy-headers'], cwd=ROOT)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Read and preserve media evidence for an existing scheduled attempt; never submit."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import review
from core.console import force_utf8
from publish import business_suite as bs, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt-id', required=True, help='Existing scheduled attempt_id from published.jsonl')
    parser.add_argument('--timeout', type=float, default=30, help='Read timeout in seconds')
    args = parser.parse_args(argv)
    if not 0 < args.timeout < float('inf'):
        parser.error('--timeout must be positive and finite')
    force_utf8()
    try:
        result = asyncio.run(records.reverify_media(args.attempt_id, timeout=args.timeout))
    except (review.ReviewConflict, bs.PublishStepError, RuntimeError) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['remote_images_verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

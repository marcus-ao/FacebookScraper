"""Record a prepared single-channel form without filling, uploading or submitting."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.chrome import attach
from core import config, maintenance
from core.console import force_utf8
from publish.channel_evidence import SURFACE, capture


@maintenance.guarded('channel_probe')
async def run(channel):
    c = config.cfg()
    c.assert_chrome_profiles_isolated()
    pw, _browser, context = await attach(port=c.publish_debug_port, profile=c.publish_profile_dir,
        start_script=r'scripts\start_chrome_publish.bat', login_hint='DE 发布账号')
    try:
        pages = [p for p in context.pages if p.url.startswith(SURFACE)]
        if len(pages) != 1:
            raise ValueError('请仅保留一个待探查 composer 标签页')
        row = await capture(pages[0], channel)
        print(f"已记录 {channel} 单渠道控件；截图 {row['screenshot']}。尚未创建排期。")
    finally:
        await pw.stop()


if __name__ == '__main__':
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('channel', choices=('facebook', 'instagram'))
    asyncio.run(run(parser.parse_args().channel))

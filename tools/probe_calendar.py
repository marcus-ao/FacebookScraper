"""Record the complete visible Planner month without editing or submitting posts."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.chrome import attach
from core.config import cfg
from core import maintenance
from core.console import force_utf8
from publish.month_inventory import capture


@maintenance.guarded('calendar_probe')
async def run():
    c = cfg()
    pw, _browser, context = await attach(port=c.publish_debug_port, profile=c.publish_profile_dir,
        start_script=r'scripts\start_chrome_publish.bat', login_hint='DE 发布账号')
    try:
        pages = [page for page in context.pages if page.url.startswith('https://business.facebook.com/latest/content_calendar')]
        if len(pages) != 1:
            raise ValueError('请只保留一个待探查月历标签页')
        row = await capture(pages[0])
        print(f"已记录 {row['days_observed']} 个日期格及加载状态，截图 {row['screenshot']}；未创建排期。")
    finally:
        await pw.stop()


if __name__ == '__main__':
    force_utf8()
    asyncio.run(run())

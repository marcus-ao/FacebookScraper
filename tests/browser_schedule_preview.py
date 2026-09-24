"""Schedule confirmation layout and snapshot behavior, using isolated HTTP fixtures."""
from __future__ import annotations

import copy
import json
import re

from playwright.sync_api import expect

from browser_fixture import approval_options, frozen_preview, publish_operation
from ui_fixture import EVIDENCE


def stage_schedule_preview(page, ui):
    page.emulate_media(reduced_motion='reduce')
    task_id = ui.fx.fb_id
    endpoint = f'/api/tasks/{task_id}'
    detail = copy.deepcopy(ui.fx.detail(task_id))
    detail['status'] = 'content_locked'
    ui.overrides[('GET', endpoint)] = (200, detail)
    caption = ('Riko serviert jeder Katze automatisch eine frische, individuell angepasste Portion.\n'
               'Kein Teilen. Kein Kämpfen. Kein Rätselraten.\n'
               'Jede Katze. Jede Mahlzeit. Perfekt portioniert. 💧 🐱\n\n'
               '➡️ https://example.invalid/riko\n\n#NeakasaRiko #CatFeeder #WetFood #CatsHealth')
    images = [{'index': i, 'url': f'/api/preview-fixture/{i}.svg?snapshot=offline-snapshot'} for i in range(3)]
    preview = frozen_preview(text=caption, images=images)
    options = approval_options(preview=preview, earliest='2026-09-24T08:00:00+08:00',
                               latest='2026-10-28T23:00:00+08:00')
    ui.overrides[('GET', endpoint + '/approval-options')] = (200, options)
    ui.overrides[('POST', endpoint + '/approve')] = (202, publish_operation(task_id=task_id))
    ui.overrides[('GET', '/api/publish-operations/offline-operation')] = (200, publish_operation())
    sizes = [(1600, 2000), (2400, 800), (600, 2400)]

    def picture(route):
        index = int(route.request.url.split('/preview-fixture/')[1].split('.')[0])
        width, height = sizes[index]
        # 合成大图仅测排版，不含真实账号、归档图片或业务内容。
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 800 1000" preserveAspectRatio="none">
          <rect width="800" height="1000" fill="#f0ece4"/>
          <circle cx="690" cy="240" r="270" fill="#e2e6da"/>
          <text x="70" y="115" font-family="sans-serif" font-size="38" fill="#4b5148">neakasa</text>
          <text x="70" y="260" font-family="sans-serif" font-size="55" fill="#25312b">Jede Katze.</text>
          <text x="70" y="335" font-family="sans-serif" font-size="55" fill="#25312b">Jede Mahlzeit.</text>
          <rect x="120" y="480" width="560" height="270" rx="70" fill="#c3c9be"/>
          <rect x="155" y="510" width="490" height="160" rx="45" fill="#747e73"/>
          <ellipse cx="400" cy="760" rx="210" ry="70" fill="#ddd9ce"/>
          <text x="70" y="920" font-family="sans-serif" font-size="24" fill="#4b5148">RIKO / OFFLINE PREVIEW {index + 1}</text>
        </svg>'''
        route.fulfill(status=200, content_type='image/svg+xml', body=svg)

    page.route('**/api/preview-fixture/**', picture)
    page.goto(ui.fx.base_url + '/review/' + task_id, wait_until='networkidle')
    field = page.get_by_role('textbox', name='发布时间', exact=True)
    field.fill('2026-09-30T17:30')
    trigger = page.get_by_role('button', name='确认发布时间并排期', exact=True)
    trigger.click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    expect(dialog).to_have_css('opacity', '1')
    expect(dialog).not_to_have_class(re.compile(r'.*ant-zoom-(appear|enter|leave).*'))
    expect(dialog.locator('img').first).to_be_visible()
    page.wait_for_function("""() => [...document.querySelectorAll('[role=dialog] img')].every(img => img.complete && img.naturalWidth > 0)""")

    measurements = {}
    for width, height in [(1366, 768), (1920, 1080), (827, 620), (390, 720)]:
        page.set_viewport_size({'width': width, 'height': height})
        metric = dialog.evaluate('''el => {
          const box = el.getBoundingClientRect();
          const images = [...el.querySelectorAll('img')].map(img => {
            const r = img.getBoundingClientRect();
            return {width:r.width, height:r.height, inside:r.left >= box.left && r.right <= box.right + 1};
          });
          return {width:box.width, height:box.height, top:box.top, bottom:box.bottom,
            horizontalOverflow:el.scrollWidth > el.clientWidth + 1, images};
        }''')
        measurements[str(width)] = metric
        page.screenshot(path=str(EVIDENCE / f'schedule-preview-{width}.png'))
        (EVIDENCE / 'schedule-preview-layout.json').write_text(json.dumps(measurements, indent=2), encoding='utf-8', newline='')
        assert not metric['horizontalOverflow'] and all(image['inside'] for image in metric['images']), metric
        assert metric['top'] >= 0 and metric['bottom'] <= height + 1, metric
        expect(dialog.get_by_role('button', name='确认并创建排期', exact=True)).to_be_in_viewport(ratio=1)

    expect(dialog.get_by_text('Neakasa Deutschland', exact=True)).to_be_visible()
    expect(dialog.get_by_text('2026-09-30 17:30', exact=True)).to_be_visible()
    expect(dialog.get_by_text('确认后将使用', exact=False)).to_have_count(0)
    page.set_viewport_size({'width': 1366, 'height': 768})
    expect(dialog.get_by_label('发布文案')).to_have_text(caption)
    for index in [1, 2, 0]:
        dialog.get_by_role('button', name=f'查看第 {index + 1} 张图片', exact=True).click()
        expect(dialog.get_by_alt_text(f'发布图片 {index + 1}')).to_be_visible()
        expect(dialog.get_by_role('button', name=f'查看第 {index + 1} 张图片', exact=True)).to_have_attribute('aria-pressed', 'true')

    dialog.get_by_role('button', name='继续核对', exact=True).click()
    long_caption = (caption + '\n\n') * 24 + 'https://example.invalid/' + 'long-link-' * 50
    variants = {
        'single': frozen_preview(text=caption, images=images[:1]),
        'long-caption': frozen_preview(text=long_caption, images=images[:1]),
        'text-only': frozen_preview(text=caption),
        'missing-image': frozen_preview(text=caption, images=[{'index': 0, 'url': '/api/missing-preview-image'}]),
    }
    page.route('**/api/missing-preview-image', lambda route: route.fulfill(status=404, body='Missing fixture'))
    for name, variant in variants.items():
        ui.overrides[('GET', endpoint + '/approval-options')] = (200, {**options, 'preview': variant})
        page.reload(wait_until='networkidle')
        field.fill('2026-09-30T17:30')
        trigger.click()
        expect(dialog).to_be_visible()
        expect(dialog).to_have_css('opacity', '1')
        expect(dialog).not_to_have_class(re.compile(r'.*ant-zoom-(appear|enter|leave).*'))
        prose = dialog.get_by_label('发布文案')
        assert prose.text_content() == variant['text']
        expect(dialog.get_by_role('button', name='查看第 1 张图片', exact=True)).to_have_count(0)
        if name == 'single':
            expect(dialog.get_by_alt_text('发布图片 1')).to_be_visible()
            page.screenshot(path=str(EVIDENCE / 'schedule-preview-single.png'), animations='disabled')
        elif name == 'long-caption':
            assert prose.evaluate('el => el.scrollHeight > el.clientHeight && el.scrollWidth <= el.clientWidth + 1')
            prose.evaluate('el => { el.scrollTop = el.scrollHeight }')
            expect(dialog.get_by_role('button', name='确认并创建排期', exact=True)).to_be_in_viewport(ratio=1)
            page.screenshot(path=str(EVIDENCE / 'schedule-preview-long-caption.png'))
        elif name == 'text-only':
            expect(dialog.locator('img')).to_have_count(0)
        else:
            expect(dialog.get_by_role('status')).to_contain_text('第 1 张图片暂时无法显示')
        # 首尾 Tab 循环及 Esc 返回原触发入口。
        confirm = dialog.get_by_role('button', name='确认并创建排期', exact=True)
        confirm.focus()
        page.keyboard.press('Tab')
        assert dialog.evaluate('el => el.contains(document.activeElement)')
        page.keyboard.press('Shift+Tab')
        expect(confirm).to_be_focused()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()

    ui.overrides[('GET', endpoint + '/approval-options')] = (200, options)
    page.reload(wait_until='networkidle')
    field.fill('2026-09-30T17:30')
    trigger.click()

    # 网络恢复会刷新选项，已打开的确认内容与提交目标必须仍是同一版。
    changed = copy.deepcopy(options)
    changed['preview'].update(text='Changed caption after reconnect', target={**preview['target'], 'account': 'Changed account'})
    ui.overrides[('GET', endpoint + '/approval-options')] = (200, changed)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    with page.expect_response(lambda response: response.url.endswith('/approval-options')):
        page.evaluate("window.dispatchEvent(new Event('online'))")
    expect(dialog.get_by_label('发布文案')).to_have_text(caption)
    expect(dialog.get_by_text('Neakasa Deutschland', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='继续核对', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(field).to_have_value('2026-09-30T17:30')
    expect(trigger).to_be_focused()
    assert not [r for r in ui.requests if r['method'] == 'POST'], ui.requests

    trigger.click()
    expect(dialog.get_by_label('发布文案')).to_have_text('Changed caption after reconnect')
    dialog.get_by_role('button', name='确认并创建排期', exact=True).dblclick()
    expect(dialog).not_to_be_visible()
    posted = [r['body'] for r in ui.requests if r['path'].endswith('/approve')]
    assert len(posted) == 1 and posted[0]['publish_target'] == changed['preview']['target'], posted
    assert posted[0]['scheduled_at'] == '2026-09-30T17:30', posted
    return {'layout': measurements, 'gallery_order': True, 'content_variants': list(variants),
            'keyboard_loop_and_escape': True, 'preview_pinned_on_reconnect': True,
            'cancel_keeps_time': True, 'focus_restored': True, 'single_submission': True}

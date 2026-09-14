"""Final screenshots, density and keyboard evidence using the existing isolated fixture."""
from __future__ import annotations
import copy
import json
import re
from playwright.sync_api import sync_playwright, expect
from ui_fixture import BrowserFixture, ROOT, UIFixture

OUT=ROOT/'docs/ui-refactor/screenshots'
PROBE=r'''() => {
 const all=[...document.querySelectorAll('main *')];
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const inView=e=>visible(e)&&e.getBoundingClientRect().bottom>0&&e.getBoundingClientRect().top<innerHeight;
 const rows=[...document.querySelectorAll('tr[data-task-id]')];
 const primary=[...document.querySelectorAll('main .ant-btn-primary')].filter(visible);
 const bordered=all.filter(e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return visible(e)&&s.borderStyle!=='none'&&parseFloat(s.borderWidth)>=1&&r.height>60});
 const head=document.querySelector('main article>header');
 const overflow=all.filter(e=>visible(e)&&e.children.length===0&&e.clientWidth>0&&getComputedStyle(e).textOverflow!=='ellipsis'&&getComputedStyle(e).overflowX!=='auto'&&e.scrollWidth>e.clientWidth+2);
 return {viewport:[innerWidth,innerHeight],rowsAboveFold:rows.filter(e=>inView(e)&&e.getBoundingClientRect().bottom<=innerHeight).length,rowCount:rows.length,rowHeights:[...new Set(rows.map(e=>Math.round(e.getBoundingClientRect().height)))],
 documentHeight:document.documentElement.scrollHeight,screensOfScroll:+(document.documentElement.scrollHeight/innerHeight).toFixed(2),h1Count:document.querySelectorAll('main h1').length,
 primaryCount:primary.length,primaryAboveFold:primary.filter(inView).length,primaryButtons:primary.map(e=>e.textContent.trim()),dangerCount:[...document.querySelectorAll('main .ant-btn-dangerous')].filter(visible).length,
 borderedRegions:bordered.length,borderedElements:bordered.map(e=>e.tagName+'.'+e.className),horizontalOverflow:document.documentElement.scrollWidth>innerWidth,overflow:overflow.map(e=>e.tagName+'.'+e.className+' '+e.textContent.trim().slice(0,28)),
 headingSizes:[...new Set([...document.querySelectorAll('main h1,main h2,main h3,main h4')].filter(visible).map(e=>e.tagName+' '+getComputedStyle(e).fontSize))],
 statusTagsAboveFold:[...document.querySelectorAll('main .ant-tag')].filter(inView).length,detailHeaderHeight:head?Math.round(head.getBoundingClientRect().height):null,
 detailHeaderOverflow:head?head.scrollWidth>head.clientWidth:false,lang:document.documentElement.lang,scrollBehavior:getComputedStyle(document.documentElement).scrollBehavior,
 paginationAboveFold:[...document.querySelectorAll('.ant-pagination')].some(e=>inView(e)&&e.getBoundingClientRect().bottom<=innerHeight)};
}'''

def seed(ui):
    ui.host.add('third','facebook','Freigabe der Zusammenarbeit','Riko','219.99','30',3,'third_party',age_days=1)
    calendar=ui.fx.client.get('/api/calendar').json()
    calendar.update(refresh_available=True,cached_at='2026-09-13T08:00:00Z',display_start='2026-09-01T09:00:00+02:00',display_end_exclusive='2026-10-01T09:00:00+02:00',month_ui='2026-09',status='cached',stale=False,error=None)
    calendar['coverage'].update(matches_current_month=True,channels_complete=True)
    calendar['cards']=[{'at':f'2026-09-{day}T08:00:00Z','at_business':f'2026-09-{day}T10:00:00+02:00','channels':[platform],'card_sha256':str(i),'delivery':delivery,'rendered':'德国站运营内容 · 查看本篇正文'} for i,(day,platform,delivery) in enumerate([('03','facebook','published'),('08','instagram','published'),('15','facebook','scheduled'),('18','instagram','scheduled'),('24','facebook','unknown')])]
    ui.overrides[('GET','/api/calendar')]=(200,calendar)
    runtime=ui.fx.client.get('/api/runtime').json()
    runtime['business']['processing']={'batch_id':'fixture','state_revision':'v1','status':'interrupted','paid_request_ids':['fixture-paid'],'cost_usd':0.12}
    runtime['stages'][2]['status']='interrupted';runtime['stages'][4]['unconfirmed_attempts']=2
    ui.overrides[('GET','/api/runtime')]=(200,runtime)

def accessibility(page,ui):
    task_id=ui.host.ids['risk']
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    page.get_by_role('button',name='更多处理动作',exact=True).click();page.get_by_role('menuitem',name='这篇不发',exact=True).click()
    dialog=page.get_by_role('dialog');expect(dialog).to_be_visible()
    page.wait_for_timeout(220)  # 现有浮层进入过渡结束后再验键盘循环。
    enters=dialog.evaluate('(el)=>el.contains(document.activeElement)');assert enters
    for key in ['Tab'] * 9 + ['Shift+Tab'] * 9:
        page.keyboard.press(key);assert dialog.evaluate('(el)=>el.contains(document.activeElement)')
    before=page.locator('main .mk-active').count();page.keyboard.press('n');assert page.locator('main .mk-active').count()==before
    page.keyboard.press('Escape');expect(dialog).to_have_count(0);expect(page.get_by_role('button',name='更多处理动作',exact=True)).to_be_focused()
    page.keyboard.press('n');expect(page.locator('main .mk-active').first).to_be_visible()
    for key in ['p','ArrowDown','ArrowUp']: page.keyboard.press(key)
    page.get_by_role('button',name='编辑德语',exact=True).click();field=page.get_by_role('textbox',name='德语正文');field.focus()
    text=field.input_value();field.evaluate('(el)=>el.setSelectionRange(el.value.length,el.value.length)')
    page.keyboard.press('n');assert field.input_value()==text+'n'
    page.get_by_role('tab',name='话题标签与链接').click()
    tags=page.get_by_role('textbox',name='本篇语义标签');tags.fill('#Neu #Zweite')
    page.locator('.ant-tag').filter(has_text='本篇新增 #Neu').get_by_role('button').click()
    expect(tags).to_have_value('#Zweite')
    page.get_by_role('button',name='放弃修改').click()
    page.get_by_role('button',name='技术诊断',exact=True).click()
    drawer=page.get_by_role('dialog');expect(drawer).to_be_visible()
    for key in ['Tab'] * 4 + ['Shift+Tab'] * 4:
        page.keyboard.press(key);assert drawer.evaluate('(el)=>el.contains(document.activeElement)')
    page.keyboard.press('Escape');expect(drawer).to_have_count(0)
    page.keyboard.press('Escape');expect(page.get_by_role('heading',level=1,name='审校队列',exact=True)).to_be_visible()
    page.emulate_media(reduced_motion='reduce');page.keyboard.press('Tab')
    reduced=page.evaluate('''()=>({transition:getComputedStyle(document.querySelector('button')).transitionDuration,animation:getComputedStyle(document.querySelector('button')).animationDuration,scroll:getComputedStyle(document.documentElement).scrollBehavior})''')
    assert reduced['scroll']=='auto' and all(float(v.rstrip('s'))<=0.00001 for v in reduced['transition'].split(', ')),reduced
    page.emulate_media(reduced_motion='no-preference')
    return {'modal_focus_enters':enters,'modal_tab_trap_both_directions':True,'drawer_tab_trap_both_directions':True,'modal_escape_returns_focus':True,'overlay_suppresses_shortcuts':True,'n_p_arrows_escape':True,'tag_removal_updates_draft':True,'reduced_motion':reduced}

def main():
    with BrowserFixture() as fx:
        ui=UIFixture(fx);seed(ui)
        frozen=next(row['id'] for row in fx.client.get('/api/tasks?scope=history&limit=50').json()['tasks'] if row['read_only'])
        screens=[('review','/review'),('history','/history'),('review-detail','/review/'+ui.host.ids['risk']),('review-detail-images','/review/'+ui.host.ids['carousel']+'?tab=images'),('review-detail-tags','/review/'+ui.host.ids['clean-1']+'?tab=localization'),('review-detail-thirdparty','/review/'+ui.host.ids['third']),('history-detail','/history/'+frozen),('calendar','/calendar'),('settings','/settings'),('runtime','/runtime')]
        metrics={}
        with sync_playwright() as driver:
            context=driver.chromium.launch_persistent_context(str(fx.root/'final-profile'),executable_path=fx.chrome_exe,headless=True,viewport={'width':1366,'height':768},timezone_id='America/New_York')
            try:
                page=context.new_page();page.set_default_timeout(8000);ui.attach(page)
                for width,height in [(1366,768),(1920,1080)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for name,path in screens:
                        page.goto(fx.base_url+path,wait_until='networkidle');page.evaluate('scrollTo(0,0)');page.wait_for_timeout(220)
                        key=f'{name}@{width}x{height}'
                        page.screenshot(path=str(OUT/f'final-{key}.png'))
                        metrics[key]=page.evaluate(PROBE)
                        print(key+' '+json.dumps({k:metrics[key][k] for k in ['rowsAboveFold','screensOfScroll','primaryCount','borderedRegions','horizontalOverflow','detailHeaderOverflow']},ensure_ascii=False),flush=True)
                (ROOT/'docs/ui-refactor/final-measurements.json').write_text(json.dumps({'screens':metrics,'accessibility':'pending'},ensure_ascii=False,indent=2),encoding='utf-8')
                page.set_viewport_size({'width':1366,'height':768})
                a11y=accessibility(page,ui)
                assert not ui.errors,ui.errors
                report={'screens':metrics,'accessibility':a11y,'source':'isolated BrowserFixture archive and explicit cached calendar/runtime fixtures','requests':ui.requests,'page_errors':ui.errors,'real_external_actions':False}
                (ROOT/'docs/ui-refactor/final-measurements.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print('Captured '+str(len(metrics))+' screens; accessibility passed.',flush=True)
            finally: context.close()

if __name__=='__main__': main()

"""Behavior gates for the remaining React stages, using the existing Python Playwright fixture."""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from browser_fixture import frozen_preview
from ui_fixture import EVIDENCE, BrowserFixture, ROOT, UIFixture


def choose(page, label, value):
    page.get_by_role("combobox", name=label).click()
    page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter(has_text=re.compile('^' + re.escape(value) + '$')).click()


def stage_c(page, ui):
    data = ui.synthetic_buckets()
    page.goto(ui.fx.base_url + "/review", wait_until="networkidle")
    expect(page.locator("tr[data-task-id]")).to_have_count(2)
    for name, count in [("未就绪 1",1),("已挂起 1",1),("已处理 4",4),("待我审 2",2)]:
        page.get_by_role("tab", name=name, exact=True).click()
        expect(page.locator("tr[data-task-id]")).to_have_count(count)
    page.get_by_role("tab",name="未就绪 1",exact=True).click()
    expect(page.get_by_text("第三方作者 · 需授权初翻")).to_be_visible()
    expect(page.locator('[data-problem="hard_alert"]')).to_be_visible()
    # 审校台按平台分了入口，平台不再是筛选项；历史页仍然保留跨平台检索。
    expect(page.get_by_role("combobox", name="筛选平台")).to_have_count(0)
    expect(page.get_by_role("link", name="Instagram 待审", exact=True)).to_be_visible()
    choose(page,"筛选月份","2026-09")
    choose(page,"筛选分类","Riko")
    page.get_by_role("button", name="硬闸 1", exact=True).click()
    assert ui.count_list_gets() == 1, ui.requests
    synthetic=copy.deepcopy(ui.fx.detail(ui.fx.fb_id));synthetic.update(id=data['tasks'][2]['id'],status='not_ready',read_only=False)
    ui.overrides[('GET',f"/api/tasks/{synthetic['id']}")]=(200,synthetic)
    page.locator("tr[data-task-id] a").first.click()
    page.get_by_role("link", name=re.compile("返回")).last.click()
    expect(page.get_by_role("tab",name="未就绪 1",exact=True)).to_have_attribute("aria-selected","true")
    assert ui.count_list_gets() == 1
    page.get_by_role("button",name="更多处理动作").click()
    page.get_by_role("menuitem",name="这篇不发",exact=True).click()
    dialog=page.get_by_role("dialog")
    expect(dialog.get_by_role("button",name="确认",exact=True)).to_be_disabled()
    dialog.get_by_role("textbox",name="理由",exact=True).fill("暂不发布")
    expect(dialog.get_by_role("button",name="确认",exact=True)).to_be_enabled()
    assert "danger" in dialog.get_by_role("button",name="确认",exact=True).get_attribute("class")
    row=data["tasks"][2]
    changed=copy.deepcopy(ui.fx.detail(ui.fx.fb_id))
    changed.update(id=row["id"],status="skipped",tags=row["tags"])
    changed["review"].update(status="skipped",reason="暂不发布")
    ui.overrides[("POST",f'/api/tasks/{row["id"]}/review')] = (200,changed)
    dialog.get_by_role("button",name="确认",exact=True).click()
    expect(page.get_by_role("tab",name="未就绪 0",exact=True)).to_be_visible()
    expect(page.get_by_role("tab",name="已处理 5",exact=True)).to_be_visible()
    assert ui.count_list_gets() == 1
    write=[entry for entry in ui.requests if entry["method"]=="POST"][-1]
    assert write["body"] == {"source_text_sha256":row["source_text_sha256"],"review_revision":row["review"]["revision"],"action":"skipped","reason":"暂不发布","handoff_url":""}
    page.get_by_role("link",name="Facebook 待审",exact=True).click()
    expect(page.locator("tr[data-task-id]")).to_have_count(2)
    assert ui.count_list_gets() == 2
    ui.overrides.pop(("GET", "/api/tasks"))
    page.goto(ui.fx.base_url + "/review",wait_until="networkidle")
    measurements={}
    for width,height,target in [(1366,768,12),(1920,1080,18)]:
        page.set_viewport_size({"width":width,"height":height})
        page.wait_for_timeout(150)
        metric=page.evaluate('''() => { const rows=[...document.querySelectorAll('tr[data-task-id]')]; return {
            rowsAboveFold:rows.filter(row=>row.getBoundingClientRect().bottom<=innerHeight).length,
            rowHeights:[...new Set(rows.map(row=>Math.round(row.getBoundingClientRect().height)))],
            firstRowTop:rows[0]?.getBoundingClientRect().top, totalRows:rows.length}; }''')
        assert metric["rowsAboveFold"] >= target, metric
        assert metric["rowHeights"] == [48], metric
        measurements[str(width)] = metric
        page.screenshot(path=str(EVIDENCE/f"final-review@{width}x{height}.png"))
    return {"B21":"PASS","B22":"PASS","filter_and_roundtrip_gets":1,"mutation_list_gets":0,"explicit_navigation_gets":1,"density":measurements}


def stage_e(page, ui):
    page.goto(ui.fx.base_url + '/history',wait_until='networkidle')
    expect(page.locator('tr[data-task-id]')).to_have_count(50)
    assert parse_qs(urlparse(page.url).query)['limit'] == ['50']
    measurements={}
    for width,height,target in [(1366,768,12),(1920,1080,18)]:
        page.set_viewport_size({'width':width,'height':height}); page.wait_for_timeout(150)
        metric=page.evaluate('''() => { const rows=[...document.querySelectorAll('tr[data-task-id]')]; const pager=document.querySelector('.ant-pagination'); return {
            rowsAboveFold:rows.filter(row=>row.getBoundingClientRect().bottom<=innerHeight).length,
            rowHeights:[...new Set(rows.map(row=>Math.round(row.getBoundingClientRect().height)))],paginationVisible:pager.getBoundingClientRect().bottom<=innerHeight}; }''')
        assert metric['rowsAboveFold']>=target and metric['rowHeights']==[48] and metric['paginationVisible'], metric
        measurements[str(width)]=metric
        page.screenshot(path=str(EVIDENCE/f'final-history@{width}x{height}.png'))
    page.locator('.ant-pagination-options-size-changer').click()
    page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter(has_text=re.compile('^20')).click()
    expect(page.locator('tr[data-task-id]')).to_have_count(20)
    choose(page,'筛选平台','Instagram')
    choose(page,'筛选月份','2021-01')
    choose(page,'筛选分类','OfflineFixture')
    expect(page.get_by_text('共 31 篇',exact=True)).to_be_visible()
    page.locator('.ant-pagination-item-2').click()
    expect(page.locator('tr[data-task-id]')).to_have_count(11)
    params=parse_qs(urlparse(page.url).query)
    assert params=={'page':['2'],'limit':['20'],'platform':['instagram'],'month':['2021-01'],'tag':['OfflineFixture']},params
    first=page.locator('tr[data-task-id]').first.get_attribute('data-task-id')
    # E′ 核对历史 API 无年龄上限；D 接入后同一条路径再验证完整只读详情。
    detail=ui.fx.detail(first)
    assert detail['read_only'] and detail['meta']['created_at'].startswith('2021-01')
    page.locator('tr[data-task-id] a').first.click()
    page.reload(wait_until='networkidle')
    page.get_by_role('link',name=re.compile('返回')).last.click()
    expect(page.locator('tr[data-task-id]')).to_have_count(11)
    assert parse_qs(urlparse(page.url).query)==params
    page.reload(wait_until='networkidle')
    expect(page.locator('tr[data-task-id]')).to_have_count(11)
    assert parse_qs(urlparse(page.url).query)['limit']==['20']
    last=[entry for entry in ui.requests if entry['path']=='/api/tasks'][-1]
    assert parse_qs(last['query'])=={'scope':['history'],**params}
    return {'B19_list':'PASS','B19_old_detail_api':'PASS','B19_full_detail':'PASS','B24':'PASS','density':measurements}


def stage_d1(page, ui):
    page.goto(ui.fx.base_url + '/review', wait_until='networkidle')
    # ⚠️ /review 是 Facebook 队列，而 /api/tasks 不分平台。少了 platform 这一条，
    # 列表改成按原帖时间降序后挑中的就是 IG 行，页面上永远等不到它。
    row=next(row for row in ui.list_data['tasks'] if row['status']=='pending_review'
             and row['platform']=='facebook' and not row['hard_alerts'])
    task_id=row['id']; detail=ui.fx.detail(task_id)
    page.locator(f'tr[data-task-id="{task_id}"] a').first.click()
    expect(page.get_by_role('button',name='编辑德语',exact=True)).to_be_visible()
    assert ui.count_list_gets()==1
    page.get_by_role('button',name='编辑德语',exact=True).click()
    page.get_by_role('textbox',name='德语正文').fill('Manuell geprüft 😀 $219.99')
    expect(page.get_by_role('button',name='下一篇',exact=True)).to_be_disabled()
    page.wait_for_timeout(400)
    checks=[r for r in ui.requests if r['path'].endswith('/check')]
    assert checks and checks[-1]['body']['localization']['body_de']=='Manuell geprüft 😀 $219.99'
    assert checks[-1]['body']['body_only'] is True
    assert page.evaluate("() => {const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented}")
    page.get_by_role('link',name='返回列表',exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button',name='留在本页').click()
    expect(page.get_by_role('textbox',name='德语正文')).to_have_value('Manuell geprüft 😀 $219.99')
    ui.overrides[('PUT',f'/api/tasks/{task_id}/localization')]=(409,{'detail':'conflict'})
    page.get_by_role('button',name='保存',exact=True).click()
    page.get_by_role('button',name='载入最新内容并保留我的修改').click()
    expect(page.get_by_role('textbox',name='德语正文')).to_have_value('Manuell geprüft 😀 $219.99')
    updated=copy.deepcopy(detail); updated['localization']['body_de']='Manuell geprüft 😀 $219.99';updated['text']['de_human']='Manuell geprüft 😀 $219.99';updated['status']='edited'
    ui.overrides[('PUT',f'/api/tasks/{task_id}/localization')]=(200,updated)
    page.get_by_role('button',name='保存',exact=True).click()
    expect(page.get_by_role('textbox',name='德语正文')).to_have_count(0)
    saved=[r for r in ui.requests if r['method']=='PUT'][-1]['body']
    assert set(saved)=={'body_de','tags','hashtags_confirmed','links_confirmed','links','ig_cta','source_text_sha256','human_revision','review_revision','localization_revision'}
    page.get_by_role('link',name='返回列表',exact=True).click()
    expect(page.locator(f'tr[data-task-id="{task_id}"]')).to_contain_text('Manuell geprüft')
    assert ui.count_list_gets()==1
    return {'D1':'PASS','check_full_localization':True,'dirty_guard':True,'save_conflict_preserves_draft':True,'B23_GET_list_count':1}


def stage_d2(page, ui):
    row=next(row for row in ui.list_data['tasks'] if row['status']=='pending_review' and row['platform']=='facebook' and not row['hard_alerts'])
    task_id=row['id']; detail=ui.fx.detail(task_id)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    page.get_by_role('button',name='编辑德语',exact=True).click()
    field=page.get_by_role('textbox',name='德语正文'); field.fill('Anfang Ende')
    field.evaluate('(el) => el.setSelectionRange(7,7)')
    page.get_by_role('tab',name='话题标签与链接',exact=True).click()
    page.get_by_role('textbox',name='本篇语义标签').fill('#Katzen #Katzen Haustiere，#Tierpflege')
    page.get_by_role('button',name='将链接 1 插入正文光标处').click()
    expect(field).to_have_value('Anfang 〔链接 1〕Ende')
    assert '{{link' not in page.locator('main').inner_text()
    page.wait_for_timeout(400)
    body=[r['body'] for r in ui.requests if r['path'].endswith('/check')][-1]
    assert body['text_de']=='Anfang {{link1}}Ende'
    assert body['localization']['tags']==list(dict.fromkeys(detail['localization']['protected_tags']+['#Katzen','#Haustiere','#Tierpflege']))
    page.get_by_role('button',name='放弃修改').click()
    page.get_by_role('button',name='编辑分类').click()
    page.get_by_role('textbox',name='产品分类',exact=True).fill('Riko，促销')
    ui.overrides[('PUT',f'/api/tasks/{task_id}/tags')]=(200,{**detail,'tags':['Riko','促销']})
    page.get_by_role('button',name='保存分类',exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    body=[r['body'] for r in ui.requests if r['path'].endswith('/tags')][-1]
    assert body=={'tags':['Riko','促销'],'tags_revision':detail['tags_revision'],'source_text_sha256':detail['text']['source_text_sha256']}
    return {'D2':'PASS','cursor_insertion_and_storage_token':True,'protected_deduplicated_tags':True,'independent_category_CAS':True}


def stage_d3(page, ui):
    row=next(row for row in ui.list_data['tasks'] if row['image_count']>=3)
    task_id=row['id']; detail=ui.fx.detail(task_id); detail['images'][1]['de_present']=False
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,detail)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    page.get_by_role('button',name=f"图片 1/{len(detail['images'])}",exact=True).click()
    expect(page.get_by_role('button',name='第 1 张（看过）',exact=True)).to_be_visible()
    page.get_by_role('button',name='第 2 张（未看）',exact=True).click()
    expect(page.get_by_text('这一张缺少德语图，当前展示原图，请人工核对',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name=f"图片 2/{len(detail['images'])}",exact=True)).to_be_visible()
    page.get_by_role('button',name='放大对照').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(page.get_by_role('button',name='放大对照')).to_be_focused()
    other=next(item for item in ui.list_data['tasks'] if item['image_count']>=2 and item['id']!=task_id)
    page.goto(ui.fx.base_url+'/review/'+other['id'],wait_until='networkidle')
    expect(page.get_by_role('button',name=f"图片 1/{other['image_count']}",exact=True)).to_be_visible()

    # 改动占比为 0 是"模型没干活"和"图里本来没英文"共用的信号，必须自己冒出来。
    zero=ui.fx.detail(task_id)
    zero['images'][0]['metrics']=dict(zero['images'][0].get('metrics') or {},changed_pixel_ratio=0)
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,zero)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    page.get_by_role('button',name=f"图片 1/{len(zero['images'])}",exact=True).click()
    expect(page.get_by_text('未检测到明显像素变化',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name=re.compile('上传图片替换第 1 张'))).to_be_visible()

    # 历史版本不折叠：换回上一版是动作，折起来她就不知道有这条路。
    versions=[{'out_path':'posts/p/media_de/01.jpg','created_at':'2026-09-15T02:00:00Z','refine_id':None,
               'refine_instruction':None,'model':'gpt-image-2','available':True,'current':False,'usable':True,
               'unusable_reasons':[],'metrics':{'dhash_distance':1,'aspect_drift':0,'scale_ratio':1,
                                                'elapsed_s':9,'changed_pixel_ratio':0.012}},
              {'out_path':'posts/p/media_de/01_vab.jpg','created_at':'2026-09-15T03:00:00Z','refine_id':'ab',
               'refine_instruction':'把 CTA 换成更短的说法','model':'gpt-image-2','available':True,'current':True,
               'usable':True,'unusable_reasons':[],'metrics':{'dhash_distance':2,'aspect_drift':0,'scale_ratio':1,
                                                              'elapsed_s':11,'changed_pixel_ratio':0.031}}]
    ui.overrides[('GET',f'/api/refinements/task/{task_id}')]=(200,{
        'max_refine_per_media':3,'image_attempts':{'0':2},'estimated_image_usd':0.211,
        'estimate_basis':'本地 usage 样本中位数','estimate_samples':4,'jobs':[],'image_versions':{'0':versions}})
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    page.get_by_role('button',name=f"图片 1/{len(zero['images'])}",exact=True).click()
    expect(page.get_by_text('这一张生成过 2 版',exact=True)).to_be_visible()
    expect(page.get_by_text('指令：把 CTA 换成更短的说法',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='采用这一版')).to_have_count(1)
    return {'D3':'PASS','initial_seen_zero':True,'fallback_visible':True,'reset_on_task_change':True,
            'zoom_escape_focus_return':True,'zero_change_alert':True,'upload_entry':True,'versions_not_collapsed':True}


def stage_d4(page, ui):
    row=next(row for row in ui.list_data['tasks'] if row['status']=='pending_review' and not row['hard_alerts'])
    task_id=row['id']; detail=ui.fx.detail(task_id)
    # 时刻选择框只在冻结之后出现；这一档从「内容已冻结」开始验排期本身。
    locked=copy.deepcopy(detail);locked['status']='content_locked'
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,locked)
    options=ui.fx.client.get(f'/api/tasks/{task_id}/approval-options').json()
    options.update(available=True,reason='',fingerprint='fixture-fingerprint',lockable=True,lock_reason='',
                   earliest='2026-09-14T08:00:00+08:00',latest='2026-10-28T23:00:00+08:00',default_times=['09:00','17:00'],
                   business_timezone='Asia/Shanghai',audience_timezone='Europe/Berlin',audience_quiet_hours=[0,6],
                   preview=options.get('preview') or frozen_preview())
    ui.overrides[('GET',f'/api/tasks/{task_id}/approval-options')]=(200,options)
    running={'version':1,'operation_id':'regression-op','task_id':task_id,'platform':detail['platform'],
             'snapshot_id':'regression-snapshot','scheduled_at':'2026-09-15T10:30:00+08:00','status':'running',
             'step_index':0,'step_total':7,'step':'准备中','message':'','result':None,
             'started_at':'2026-09-15T00:00:00Z','updated_at':'2026-09-15T00:00:00Z'}
    ui.overrides[('POST',f'/api/tasks/{task_id}/approve')]=(202,running)
    ui.overrides[('GET','/api/publish-operations/regression-op')]=(200,dict(running,status='failed',message='提交前时刻复核未通过'))
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    field=page.get_by_role('textbox',name='发布时间（北京时间）',exact=True)
    field.fill('2026-09-15T10:30')
    page.get_by_role('button',name='确认发布时间并排期',exact=True).click()
    page.get_by_role('button',name='确认并创建排期',exact=True).click()
    expect(page.get_by_text('排期没有创建成功',exact=True)).to_be_visible(timeout=15000)
    body=[r['body'] for r in ui.requests if r['path'].endswith('/approve')][-1]
    assert body=={'scheduled_at':'2026-09-15T10:30','source_text_sha256':detail['text']['source_text_sha256'],'human_revision':detail['text']['human_revision'],'review_revision':detail['review']['revision'],'content_fingerprint':'fixture-fingerprint','publish_target':options['preview']['target']}
    # 非严格成功回执也须重读详情，使本地回执恢复入口可见。
    order=[i for i,r in enumerate(ui.requests) if r['method']=='POST' and r['path'].endswith('/approve')]
    reread=[i for i,r in enumerate(ui.requests) if r['method']=='GET' and r['path']==f'/api/tasks/{task_id}' and i>order[-1]]
    assert reread, '提交失败之后没有重读这一篇，恢复入口不会出现'
    # 冲突建议从失败的操作记录里读出来，点一下就填回选择框。
    ui.overrides[('GET','/api/publish-operations/regression-op')]=(200,dict(running,status='failed',
        message='这个时刻暂不能排期',result={'suggestions':['2026-09-15T11:30:00+08:00']}))
    page.get_by_role('button',name='确认发布时间并排期',exact=True).click();page.get_by_role('button',name='确认并创建排期',exact=True).click()
    page.get_by_role('button',name='2026-09-15 11:30 北京',exact=True).click(timeout=15000)
    expect(field).to_have_value('2026-09-15T11:30')
    ui.overrides[('GET','/api/publish-operations/regression-op')]=(200,dict(running,status='succeeded',step_index=7,message='自动提交并回读为 scheduled'))
    scheduled=copy.deepcopy(detail);scheduled['status']='scheduled';scheduled['schedule']={'at':'2026-09-15T11:30:00+08:00','channel':detail['platform']}
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,scheduled)
    page.get_by_role('button',name='确认发布时间并排期',exact=True).click();page.get_by_role('button',name='确认并创建排期',exact=True).click()
    expect(page.get_by_text(re.compile('排期已确认。'))).to_be_visible(timeout=15000)
    assert ui.count_list_gets()==1
    return {'D4':'PASS','strict_receipt':True,'exact_five_body_fields':True,'no_offset_submission':True,'clickable_409_suggestion':True,'polled_operation':True,'list_GET_count':1}


def stage_d5(page, ui):
    row=next(row for row in ui.list_data['tasks'] if row['status']=='pending_review' and not row['hard_alerts'])
    task_id=row['id']; detail=ui.fx.detail(task_id)
    initial_path=f'/api/initial-translation/task/{task_id}'; refine_path=f'/api/refinements/task/{task_id}'
    cap={'available':True,'reason':'','source_fingerprint':'a'*64,'needs_consent':True,'third_party':True,'job':None}
    ui.overrides[('GET',initial_path)]=(200,cap)
    caps={'max_refine_per_media':3,'image_attempts':{},'estimated_image_usd':0.045,'estimate_basis':'fixture','estimate_samples':1,'jobs':[]}
    ui.overrides[('GET',refine_path)]=(200,caps)
    initial_job={'job_id':'fixture-initial','status':'pending','recorded_at':'2026-09-13T00:00:00Z'}
    refine_job={'job_id':'fixture-refine','status':'pending','kind':'text','source_text_sha256':detail['text']['source_text_sha256'],'recorded_at':'2026-09-13T00:00:00Z'}
    refine_job['prompt_current'] = True
    ui.overrides[('POST',initial_path)]=(202,initial_job);ui.overrides[('POST',refine_path)]=(202,refine_job)
    polls={'initial':0,'refine':0}
    def result(name,job):
        def call(_):
            polls[name]+=1
            return 200,{**job,'status':'running' if polls[name]==1 else 'succeeded',**({'body_de':'Neue Kandidatin 😀'} if name=='refine' else {})}
        return call
    ui.overrides[('GET','/api/initial-translation/jobs/fixture-initial')]=result('initial',initial_job)
    ui.overrides[('GET','/api/refinements/jobs/fixture-refine')]=result('refine',refine_job)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    initial_button=page.locator('[data-paid-action="翻译这篇"]')
    expect(initial_button).to_be_disabled()
    page.get_by_role('checkbox',name='我已确认可以处理这篇内容，开始本篇模型处理。').check();initial_button.click()
    expect(page.get_by_text('本轮处理完成',exact=True)).to_be_visible(timeout=8000)
    initial_body=[r['body'] for r in ui.requests if r['method']=='POST' and r['path']==initial_path][-1]
    assert initial_body=={'consent':True,'source_fingerprint':'a'*64,'source_text_sha256':detail['text']['source_text_sha256'],'human_revision':detail['text']['human_revision'],'review_revision':detail['review']['revision']}
    page.get_by_text('单篇优化（可选）',exact=True).click()
    page.get_by_role('textbox',name='这一次希望怎样调整').fill('保持事实，缩短开头')
    page.locator('[data-paid-action="生成文案候选"]').click()
    page.get_by_role('button',name='采用到正文编辑区',exact=True).wait_for(state='visible',timeout=8000)
    stopped=dict(polls);page.wait_for_timeout(1650);assert polls==stopped,polls
    page.get_by_role('button',name='编辑德语',exact=True).click();page.get_by_role('textbox',name='德语正文').fill('保留这段人工修改')
    page.get_by_role('button',name='采用到正文编辑区',exact=True).click();expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button',name='采用候选',exact=True).click();expect(page.get_by_role('textbox',name='德语正文')).to_have_value('Neue Kandidatin 😀')
    page.get_by_role('button',name='放弃修改').click()
    for expired in (dict(refine_job, status='succeeded', body_de='Neue Kandidatin 😀', prompt_current=False),
                    {key: value for key, value in dict(refine_job, status='succeeded', body_de='Neue Kandidatin 😀').items() if key != 'prompt_current'}):
        ui.overrides[('GET','/api/refinements/jobs/fixture-refine')] = (200, expired)
        page.get_by_role('button', name='刷新任务状态', exact=True).click()
        expect(page.get_by_role('button', name='采用到正文编辑区', exact=True)).to_be_disabled()
        expect(page.get_by_text(re.compile('提示词已更新或任务缺少版本依据'))).to_be_visible()
    page.get_by_role('button',name='处理记录',exact=True).click();expect(page.get_by_role('dialog')).to_be_visible();page.keyboard.press('Escape')
    expect(page.get_by_role('button',name='处理记录',exact=True)).to_be_focused()
    interrupted={**initial_job,'status':'interrupted'};cap['job']=interrupted
    ui.overrides[('GET','/api/initial-translation/jobs/fixture-initial')]=(200,interrupted)
    ui.overrides[('POST','/api/content-jobs/fixture-initial/recover')]=(200,{**interrupted,'status':'failed'})
    page.reload(wait_until='networkidle');page.get_by_role('button',name='核对并恢复本地状态（不重新生成）',exact=True).click()
    page.wait_for_timeout(300)
    recovery=[r for r in ui.requests if r['path'].endswith('/recover')][-1]
    assert recovery['body']=={'expected_updated_at':initial_job['recorded_at']}
    assert len([r for r in ui.requests if r['method']=='POST' and r['path']==initial_path])==1
    return {'D5':'PASS','consent_and_versions':True,'polls_stop_at_terminal':True,'candidate_dirty_confirmation':True,'expired_or_unversioned_candidate_disabled':True,'recovery_only_no_model_repeat':True,'drawer_escape_focus':True,'poll_counts':polls}


def stage_d(page, ui):
    row=next(row for row in ui.list_data['tasks'] if row['status']=='pending_review' and row['image_count']>=3)
    task_id=row['id']; original=ui.fx.detail(task_id)
    options=ui.fx.client.get(f'/api/tasks/{task_id}/approval-options').json()
    options.update(available=True,reason='',fingerprint='fixture',lockable=True,lock_reason='',
                   earliest='2026-03-01T08:00:00+08:00',latest='2026-11-01T20:00:00+08:00',
                   business_timezone='Asia/Shanghai',audience_timezone='Europe/Berlin',audience_quiet_hours=[0,6],
                   preview=options.get('preview') or frozen_preview())
    ui.overrides[('GET',f'/api/tasks/{task_id}/approval-options')]=(200,options)
    states={}
    for status in ['not_ready','pending_review','edited','content_locked','snoozed','approved','scheduled','skipped','handed_off']:
        detail=copy.deepcopy(original);detail['status']=status;detail['review']['status']=status
        ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,detail)
        page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
        # 冻结之后不能再编辑；这正是「编辑确认无误」要挡住的误触。
        editable=status in ['not_ready','pending_review','edited','snoozed']
        expect(page.get_by_role('button',name='编辑德语',exact=True)).to_have_count(int(editable))
        expect(page.get_by_role('button',name='更多处理动作',exact=True)).to_have_count(int(editable or status=='handed_off'))
        freeze=page.get_by_role('button',name='编辑确认无误',exact=True)
        schedule=page.get_by_role('button',name='确认发布时间并排期',exact=True)
        if status in ['pending_review','edited']:
            expect(freeze).to_be_enabled();expect(schedule).to_have_count(0)
        elif status=='content_locked':
            expect(freeze).to_have_count(0);expect(schedule).to_be_enabled()
        else:
            expect(schedule).to_have_count(0);expect(freeze).to_be_disabled()
        states[status]='PASS'
    detail=copy.deepcopy(original);detail['read_only']=True
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,detail)
    page.goto(ui.fx.base_url+'/history/'+task_id,wait_until='networkidle')
    for label in ['编辑德语','更多处理动作','编辑确认无误','确认发布时间并排期','编辑分类','翻译这篇','生成文案候选']:
        expect(page.get_by_role('button',name=label,exact=True)).to_have_count(0)
    detail=copy.deepcopy(original)
    prose='🚀 Anfang\n'+'Eine lange Zeile zum Prüfen.\n'*65+'$219.99 Ende'
    start=prose.index('$219.99');span=[start,start+7]
    detail['localization']['source_body']=prose;detail['localization']['body_de']=prose
    detail['body_highlights']=[{'kind':'money','en_span':span,'de_span':span,'severity':'warn','label':'确认金额'},{'kind':'money','en_span':span,'de_span':span,'severity':'error','label':'金额错误'}]
    detail['body_risks']=[{'kind':'ambiguous','en_span':span,'quote':'$219.99','label':'金额语义'}]
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,detail)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    expect(page.locator('[data-testid="english-prose"] mark.mk-error')).to_have_text('$219.99')
    page.keyboard.press('n')
    geometry=page.locator('[data-testid="english-prose"]').evaluate('(el)=>{const m=el.querySelector("mark");return {scroll:el.scrollTop,top:m.getBoundingClientRect().top,bottom:m.getBoundingClientRect().bottom,paneTop:el.getBoundingClientRect().top,paneBottom:el.getBoundingClientRect().bottom}}')
    assert geometry['scroll']>0 and geometry['top']>=geometry['paneTop'] and geometry['bottom']<=geometry['paneBottom'],geometry
    assert page.evaluate('getComputedStyle(document.documentElement).scrollBehavior')=='auto'
    expect(page.get_by_role('button',name=f"图片 1/{len(detail['images'])}",exact=True)).to_be_visible()
    # 这一段验的是界面怎么显示 DST 报错。北京没有夏令时，所以临时把业务时区切到柏林
    # 才谈得上「不存在/出现两次」——不能因为当前时区没这问题就把这条删掉。
    locked=copy.deepcopy(detail);locked['status']='content_locked'
    ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,locked)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    expect(page.get_by_role('button',name='确认发布时间并排期',exact=True)).to_be_enabled()
    from unittest.mock import patch as _patch
    from publish import business_suite as _bs
    from web.api.approval import business_time
    def dst(body):
        with _patch.object(_bs,'business_timezone',return_value='Europe/Berlin'):
            try: business_time(body['scheduled_at'])
            except ValueError as exc: return 400,{'detail':str(exc)}
        raise AssertionError('Expected DST rejection')
    ui.overrides[('POST',f'/api/tasks/{task_id}/approve')]=dst
    for value in ['2026-03-29T02:30','2026-10-25T02:30']:
        page.get_by_role('textbox',name='发布时间（北京时间）',exact=True).fill(value)
        page.get_by_role('button',name='确认发布时间并排期',exact=True).click();page.get_by_role('button',name='确认并创建排期',exact=True).click()
        expect(page.get_by_text('这个时刻在夏令时切换中不存在或出现两次，请选择其他时刻',exact=True)).to_be_visible()
    ui.overrides.pop(('GET',f'/api/tasks/{task_id}'))
    filtered=[item for item in ui.list_data['tasks'] if item['status'] in ['pending_review','edited'] and item['platform']=='facebook' and 'Riko' in item['tags']]
    assert len(filtered)>=2
    page.goto(ui.fx.base_url+'/review?queue=review&platform=facebook&tag=Riko',wait_until='networkidle')
    page.locator(f'tr[data-task-id="{filtered[0]["id"]}"] a').first.click();page.reload(wait_until='networkidle')
    expect(page.get_by_text(f'1 / {len(filtered)}',exact=True)).to_be_visible()
    page.get_by_role('button',name='下一篇',exact=True).click()
    expect(page).to_have_url(re.compile(re.escape(filtered[1]['id'])))
    page.get_by_role('button',name='编辑德语',exact=True).click();page.get_by_role('textbox',name='德语正文').fill('dirty back')
    page.evaluate('history.back()');expect(page.get_by_role('dialog')).to_be_visible();page.get_by_role('button',name='留在本页').click()
    expect(page.get_by_role('textbox',name='德语正文')).to_have_value('dirty back')
    page.get_by_role('button',name='放弃修改').click();page.get_by_role('link',name='返回列表',exact=True).click()
    assert parse_qs(urlparse(page.url).query)=={'queue':['review'],'platform':['facebook'],'tag':['Riko']}
    return {'B5':states,'B9':'PASS','B10':'PASS','B11':'PASS','B12_DST':'PASS','B15':'PASS','B17':'PASS','B23_refresh_neighbors':'PASS','B4_browser_back':'PASS','mark_geometry':geometry}


def stage_f(page, ui):
    data=ui.fx.client.get('/api/calendar').json();data.update(refresh_available=True,cached_at='2026-09-13T08:00:00Z',display_start='2026-09-01T09:00:00+02:00',display_end_exclusive='2026-10-01T09:00:00+02:00',month_ui='2026-09',status='cached')
    data['coverage']['matches_current_month']=True
    data['cards']=[{'at':'2026-09-15T08:00:00Z','at_business':'2026-09-15T10:00:00+02:00','channels':['facebook'],'card_sha256':'fixture','delivery':'scheduled','rendered':'fixture'}]
    data['cards']=[{**data['cards'][0],'delivery':'published','rendered':'公开发布正文','card_sha256':'published'},
        {**data['cards'][0],'delivery':'scheduled','rendered':'已排期正文','card_sha256':'scheduled'}]
    ui.overrides[('GET','/api/calendar')]=(200,data)
    ui.overrides[('POST','/api/calendar/refresh')]=(503,{**data,'stale':True,'error':'fixture failure','cards':[data['cards'][0]]})
    page.goto(ui.fx.base_url+'/calendar',wait_until='networkidle')
    expect(page.get_by_text('已观测到公开发布',exact=True)).to_be_visible();expect(page.get_by_text('已创建定时任务',exact=True)).to_be_visible()
    assert '公开发布正文' not in page.locator('main').inner_text()
    page.get_by_role('button',name=re.compile('已观测到公开发布')).click();expect(page.get_by_text('公开发布正文',exact=True)).to_be_visible()
    page.get_by_role('heading',name='发布月历',exact=True).click()
    page.get_by_role('button',name='刷新月历',exact=True).click()
    expect(page.get_by_text('本次月历未完整更新',exact=True)).to_be_visible()
    expect(page.get_by_text('fixture failure',exact=True)).to_be_visible()
    expect(page.get_by_text('已观测到公开发布',exact=True)).to_be_visible();expect(page.get_by_text('已创建定时任务',exact=True)).to_have_count(0)
    call=[r for r in ui.requests if r['method']=='POST'][-1];assert call['path']=='/api/calendar/refresh' and call['body']=={}
    heights=page.locator('[data-day]').evaluate_all('(els)=>els.filter(el=>!el.querySelector("button")).map(el=>el.getBoundingClientRect().height)')
    assert heights and min(heights)>=64
    # 明细读不出来的条目照常进这个月：整月不再被标成过期，卡片自己说明它是什么。
    unread={**data,'status':'ready','refresh_status':'refreshed','stale':False,'error':None,
        'coverage':{**data['coverage'],'decision_complete':False,'unresolved_count':1},
        'cards':[{**data['cards'][0],'placement':'story','caption_status':'empty','rendered':'',
                  'card_sha256':'story','delivery':'published','read_status':'complete'},
                 {**data['cards'][0],'placement':'unknown','caption_status':'unknown','rendered':'',
                  'card_sha256':'unread','channels':[],'read_status':'incomplete','delivery':'scheduled'}]}
    ui.overrides[('POST','/api/calendar/refresh')]=(200,unread)
    page.get_by_role('button',name='刷新月历',exact=True).click()
    expect(page.get_by_text('后台定时任务',exact=True)).to_be_visible()
    expect(page.get_by_text('明细未读取',exact=True)).to_be_visible()
    expect(page.get_by_text('渠道未读取',exact=True)).to_be_visible()
    expect(page.get_by_text('此条尚未核实',exact=True)).to_have_count(0)
    expect(page.get_by_text('数据可能已过期',exact=True)).to_have_count(0)
    page.get_by_role('button',name=re.compile('后台定时任务')).click()
    expect(page.get_by_text('这条的明细没读出来。展示上的已发布或定时只说明格子里有没有链接，不能当作公开事实，也不能据此确认可排时段。',exact=True)).to_be_visible()
    page.get_by_role('heading',name='发布月历',exact=True).click()
    page.get_by_role('button',name=re.compile('Story')).click()
    expect(page.get_by_text('此内容无独立正文',exact=True)).to_be_visible()
    expect(page.get_by_text('This content has no text',exact=True)).to_have_count(0)
    page.screenshot(path=str(EVIDENCE/'calendar-unread-item.png'))
    return {'F':'PASS','published_scheduled_distinct':True,'refresh_payload_cards_retained':True,
            'unread_item_still_occupies':True,'empty_caption_not_placeholder':True,
            'refresh_body':{},'empty_day_min_height':min(heights)}


def stage_g(page, ui):
    base=ui.fx.client.get('/api/settings').json()
    page.goto(ui.fx.base_url+'/review',wait_until='networkidle');page.locator('aside').get_by_role('link',name='运营设置',exact=True).click()
    field=page.get_by_role('textbox',name='默认排期时间（北京）');field.fill('11:00, 18:30')
    page.get_by_role('spinbutton',name='默认挂起期限').fill('5')
    assert page.locator('main input:not([type="hidden"])').count()==2
    assert page.evaluate("()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented}")
    page.locator('aside').get_by_role('link',name='历史归档',exact=True).click();expect(page.get_by_role('dialog')).to_be_visible();page.get_by_role('button',name='留在本页').click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    page.evaluate('history.back()');expect(page.get_by_role('dialog')).to_be_visible();page.get_by_role('button',name='留在本页').click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    ui.fx.client.put('/api/settings',json={'values':{'default_times':['09:00'],'snooze_default_days':4},'version':base['version']}).raise_for_status()
    page.get_by_role('button',name='保存设置',exact=True).click()
    page.get_by_role('button',name='载入最新设置并保留我的修改',exact=True).click()
    expect(field).to_have_value('11:00, 18:30');expect(page.get_by_role('spinbutton',name='默认挂起期限')).to_have_value('5')
    page.get_by_role('button',name='保存设置',exact=True).click()
    expect(page.get_by_text('设置已保存，下次选期或挂起时生效。',exact=True)).to_be_visible()
    bodies=[r['body'] for r in ui.requests if r['method']=='PUT' and r['path']=='/api/settings']
    assert len(bodies)==2 and bodies[0]['version']!=bodies[1]['version']
    assert bodies[1]['values']=={'default_times':['11:00','18:30'],'snooze_default_days':5}
    assert set(bodies[1])=={'values','version'}
    field.fill('11:00, 11:00');expect(page.get_by_role('button',name='保存设置',exact=True)).to_be_disabled()
    return {'B20':'PASS','B4_settings_three_paths':'PASS','two_editable_fields':True,'CAS_keeps_draft':True,'duplicate_time_rejected':True,'exact_body_keys':True}


def stage_h(page, ui):
    data=ui.fx.client.get('/api/runtime').json()
    data['process']['alive']=True
    data['business']['processing']={'batch_id':'private-batch','state_revision':'batch-v1','paid_request_ids':['private-paid-id'],'cost_usd':0.12,'status':'interrupted'}
    data['stages'][2]['status']='interrupted'
    item={'delivery_id':'fixture-delivery','version':'delivery-v1','status':'uncertain','created_at':'2026-09-13T00:00:00Z','task_ids':[]}
    data['stages'][3]['outbox']={'enabled':True,'credentials_present':True,'counts':{'uncertain':1},'deliveries':[item]}
    bot_names=['新帖检测推送机器人','新帖爬取推送机器人','新帖发布推送机器人','状态告警推送机器人']
    data['stages'][3]['outbox'].update(duplicate_bot_targets=True, bot_configuration_valid=False,
        bots=[{'role':role,'name':name,'configured':True,'valid':True}
              for role,name in zip(('detect','capture','publish','alert'),bot_names)])
    data['stages'][3]['status']='needs_attention';data['stages'][4]['unconfirmed_attempts']=2
    ui.overrides[('GET','/api/runtime')]=(200,data)
    ui.overrides[('POST','/api/runtime/processing/recover')]=(200,{'ok':True})
    ui.overrides[('POST','/api/runtime/notifications/fixture-delivery/resolve')]=(200,{'ok':True})
    page.clock.install()
    page.goto(ui.fx.base_url+'/runtime',wait_until='networkidle')
    for name in bot_names:
        expect(page.get_by_text(name+'：地址格式已检查',exact=True)).to_be_visible()
    expect(page.get_by_text('不同阶段重复配置了同一个机器人地址，请分别填写四个机器人的地址。',exact=True)).to_be_visible()
    assert '业务组与技术组当前指向同一个群' not in page.locator('main').inner_text()
    assert 'private-paid-id' not in page.locator('main').inner_text()
    page.get_by_role('button',name='登记已送达',exact=True).click()
    modal=page.get_by_role('dialog');expect(modal.get_by_role('button',name='登记已送达',exact=True)).to_be_disabled()
    page.get_by_role('textbox',name='送达核对说明',exact=True).fill('业务群 21:07 已收到')
    modal.get_by_role('button',name='登记已送达',exact=True).click();expect(modal).to_have_count(0)
    page.get_by_role('button',name='核对未送达后恢复',exact=True).click()
    modal=page.get_by_role('dialog');expect(modal.get_by_role('button',name='确认未送达并恢复投递')).to_be_disabled()
    page.get_by_role('textbox',name='已核对的机器人').fill('新帖检测推送机器人')
    page.get_by_role('textbox',name='原消息内容摘要').fill('三篇新内容待审校')
    page.get_by_role('checkbox',name='已在飞书核对未送达，确认机器人和摘要无误').check()
    modal.get_by_role('button',name='确认未送达并恢复投递').click();expect(modal).to_have_count(0)
    page.get_by_role('button',name='核对后关闭中断批次').click()
    expect(page.get_by_role('dialog')).to_contain_text('费用、已保存文案和图片')
    page.get_by_role('button',name='已核对，关闭批次').click();expect(page.get_by_role('dialog')).to_have_count(0)
    bodies=[r for r in ui.requests if r['method']=='POST']
    # message_id 仍是接口字段名，但群机器人没有平台消息 ID，填的是人写的核对说明。
    assert [r['body'] for r in bodies]==[{'action':'delivered','version':'delivery-v1','message_id':'业务群 21:07 已收到'},{'action':'not_delivered','version':'delivery-v1','message_id':''},{'batch_id':'private-batch','version':'batch-v1','outputs_reviewed':True}]
    page.get_by_role('button',name='查看维护说明').click();expect(page.get_by_role('dialog')).to_contain_text('当前接口未提供帖子列表');page.keyboard.press('Escape')
    data['process']['alive']=False
    page.clock.fast_forward(31000)
    expect(page.get_by_text('运行状态有更新，点击刷新后查看',exact=True)).to_be_visible()
    expect(page.get_by_text('调度进程：活跃',exact=True)).to_be_visible()
    page.get_by_role('button',name='刷新状态',exact=True).click();expect(page.get_by_text('调度进程：已退出',exact=True)).to_be_visible()
    return {'H':'PASS','four_bot_status_and_duplicate_warning':True,'delivered_requires_message_id':True,'resend_context_confirmation':True,'paid_batch_confirmation_and_versions':True,'count_only_maintenance':True,'poll_does_not_replace_reading_page':True,'exact_three_recovery_bodies':True}


def stage_i(page, ui):
    task_id=ui.fx.fb_id
    # 旧入口落到 Facebook 待审：`/` 与 `?view=review` 都不带平台，取第一个入口。
    for path,title in [('/','Facebook 待审'),('/?task='+task_id,'单篇审核'),('/?view=history','历史归档'),('/?view=calendar','发布月历'),('/?view=settings','运营设置'),('/?view=runtime','运行状态')]:
        page.goto(ui.fx.base_url+path,wait_until='networkidle');expect(page.get_by_role('heading',level=1,name=title,exact=True)).to_be_visible()
    page.goto(ui.fx.base_url+'/review',wait_until='networkidle')
    for view in ['历史归档','发布月历','运营设置']:
        page.locator('aside').get_by_role('link',name=view,exact=True).click();expect(page.get_by_role('heading',level=1,name=view,exact=True)).to_be_visible()
    page.evaluate('history.back()');expect(page.get_by_role('heading',level=1,name='发布月历',exact=True)).to_be_visible()
    page.evaluate('history.back()');expect(page.get_by_role('heading',level=1,name='历史归档',exact=True)).to_be_visible()
    page.evaluate('history.forward()');expect(page.get_by_role('heading',level=1,name='发布月历',exact=True)).to_be_visible()
    row=next(row for row in ui.list_data['tasks'] if row['status']=='pending_review' and not row['hard_alerts'])
    task_id=row['id']; detail=ui.fx.detail(task_id)
    page.goto(ui.fx.base_url+'/review/'+task_id,wait_until='networkidle')
    page.get_by_role('button',name='编辑分类',exact=True).click();page.get_by_role('textbox',name='产品分类',exact=True).fill('Riko，保留选择')
    ui.overrides[('PUT',f'/api/tasks/{task_id}/tags')]=(409,{'detail':'stale tags'})
    page.get_by_role('button',name='保存分类',exact=True).click()
    latest={**detail,'tags_revision':'new-tags-version'};ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,latest)
    page.get_by_role('button',name='载入最新分类',exact=True).click();expect(page.get_by_role('textbox',name='产品分类',exact=True)).to_have_value('Riko，保留选择')
    ui.overrides[('PUT',f'/api/tasks/{task_id}/tags')]=(200,latest)
    page.get_by_role('button',name='保存分类',exact=True).click();expect(page.get_by_role('dialog')).to_have_count(0)
    assert [r['body'] for r in ui.requests if r['method']=='PUT' and r['path'].endswith('/tags')][-1]['tags_revision']=='new-tags-version'
    page.get_by_role('button',name='更多处理动作',exact=True).click();page.get_by_role('menuitem',name='稍后再审',exact=True).click()
    page.get_by_role('textbox',name='理由',exact=True).fill('保留处理理由')
    page.locator('[role="dialog"] input[type="datetime-local"]').fill('2026-09-16T09:00')
    ui.overrides[('POST',f'/api/tasks/{task_id}/review')]=(409,{'detail':'stale review'})
    page.get_by_role('dialog').get_by_role('button',name='确认',exact=True).click()
    latest=copy.deepcopy(latest);latest['review']['revision']='new-review-version';ui.overrides[('GET',f'/api/tasks/{task_id}')]=(200,latest)
    page.get_by_role('button',name='刷新状态，保留填写内容',exact=True).click()
    expect(page.get_by_role('textbox',name='理由',exact=True)).to_have_value('保留处理理由')
    expect(page.get_by_role('button',name='刷新状态，保留填写内容',exact=True)).to_have_count(0)
    ui.overrides[('POST',f'/api/tasks/{task_id}/review')]=(200,latest)
    page.get_by_role('dialog').get_by_role('button',name='确认',exact=True).click();expect(page.get_by_role('dialog')).to_have_count(0)
    body=[r['body'] for r in ui.requests if r['method']=='POST' and r['path'].endswith('/review')][-1]
    assert body['review_revision']=='new-review-version' and body['wake_at']=='2026-09-16T09:00:00+08:00' and body['reason']=='保留处理理由'
    return {'B1':'PASS','B2':'PASS','B7_tags':'PASS','B7_review':'PASS','B13':'PASS'}


def stage_review_menu(page, ui):
    measurements = []

    def open_action(trigger, action, label):
        trigger.click()
        popup = page.locator('.ant-dropdown:visible')
        expect(popup).to_have_count(1)
        item = popup.get_by_role('menuitem', name=action, exact=True)
        # Playwright 的 visible 只检查布局；屏幕外的菜单也会通过，必须验证视口和真实点击。
        try:
            expect(item).to_be_in_viewport(ratio=1)
        except AssertionError:
            print(json.dumps({'case': label, 'popup': popup.bounding_box(),
                              'style': popup.evaluate('(el) => ({top: el.style.top, transition: getComputedStyle(el).transition})')},
                             ensure_ascii=False), flush=True)
            raise
        measurements.append({'case': label, 'popup': popup.bounding_box(), 'trigger': trigger.bounding_box()})
        item.click()
        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        dialog.get_by_role('button', name='取消', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(trigger).to_be_focused()

    data = ui.synthetic_buckets()
    for motion in ('no-preference', 'reduce'):
        page.emulate_media(reduced_motion=motion)
        for width in (827, 1366):
            page.set_viewport_size({'width': width, 'height': 730})
            page.goto(ui.fx.base_url + '/review', wait_until='networkidle')
            for index, tab, action in ((0, '待我审 2', '稍后再审'), (1, '待我审 2', '这篇不发'),
                                       (2, '未就绪 1', '稍后再审'), (3, '已挂起 1', '恢复审校')):
                page.get_by_role('tab', name=tab, exact=True).click()
                row = data['tasks'][index]
                trigger = page.locator(f'tr[data-task-id="{row["id"]}"]').get_by_role('button', name='更多处理动作')
                open_action(trigger, action, f'{motion}/{width}/list/{row["status"]}')
                # 关闭后重新展开同一实例，验证不残留屏幕外的定位或隐藏状态。
                trigger.click()
                expect(page.get_by_role('menuitem', name=action, exact=True)).to_be_in_viewport(ratio=1)
                page.get_by_role('heading', level=1).click()
                expect(page.locator('.ant-dropdown:visible')).to_have_count(0)
                assert urlparse(page.url).path == '/review/facebook'
            # 同时走真实夹具详情与优化能力 GET；菜单测试不替换该接口，也不提交处理动作。
            for task_id in (ui.fx.fb_id, ui.fx.ig_id):
                page.goto(ui.fx.base_url + '/review/' + task_id, wait_until='networkidle')
                trigger = page.get_by_role('button', name='更多处理动作', exact=True)
                open_action(trigger, '稍后再审', f'{motion}/{width}/detail/{task_id}')
            if motion == 'reduce' and width == 827:
                trigger.click()
                item = page.get_by_role('menuitem', name='稍后再审', exact=True)
                expect(item).to_be_in_viewport(ratio=1)
                item.hover()
                page.screenshot(path=str(EVIDENCE / 'review-menu-reduced-motion.png'))
                page.get_by_role('heading', level=1).click()
    assert not [r for r in ui.requests if r['method'] not in {'GET', 'HEAD'}], ui.requests
    return {'review_menu': 'PASS', 'measurements': measurements, 'cancel_without_mutation': True}


STAGES={'C':stage_c,'E':stage_e,'D1':stage_d1,'D2':stage_d2,'D3':stage_d3,'D4':stage_d4,'D5':stage_d5,'D':stage_d,'F':stage_f,'G':stage_g,'H':stage_h,'I':stage_i,'REVIEW_MENU':stage_review_menu}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--stage",default="C")
    args=parser.parse_args()
    if args.stage=='ALL':
        # 每个场景独立的临时归档与宿主，避免前一页面尚在结束的请求进入后一场景的 stub。
        results={};requests=[]
        for stage in STAGES:
            run=subprocess.run([sys.executable,__file__,'--stage',stage],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=150)
            if run.returncode:
                print(run.stdout);print(run.stderr);raise SystemExit(run.returncode)
            report=json.loads((EVIDENCE/f'browser-stage-{stage.lower()}.json').read_text('utf-8'))
            results[stage]=report['results'];requests.extend(report['requests'])
            print('PASS '+stage,flush=True)
        output={'stage':'ALL','results':results,'requests':requests,'real_external_actions':False,'page_errors':[],'server_denials':[]}
        (EVIDENCE/'browser-stage-all.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'{len(results)} browser scenario groups passed',flush=True)
        return
    with BrowserFixture() as fx:
        ui=UIFixture(fx)
        with sync_playwright() as driver:
            context=driver.chromium.launch_persistent_context(str(fx.root/"react-profile"),executable_path=fx.chrome_exe,
                headless=True,viewport={"width":1366,"height":768},timezone_id="America/New_York")
            try:
                page=context.new_page(); page.set_default_timeout(8000)
                # 导航等待缩略图请求，预算独立于交互断言。
                page.set_default_navigation_timeout(30000)
                ui.attach(page)
                result=STAGES[args.stage](page,ui)
                assert not ui.errors, ui.errors
                assert not fx.denied_backend_requests, fx.denied_backend_requests
                report={"stage":args.stage,"results":result,"requests":ui.requests,"page_errors":ui.errors,
                    "real_external_actions":False,"server_denials":fx.denied_backend_requests}
                output=EVIDENCE/f"browser-stage-{args.stage.lower()}.json"
                output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
                print(json.dumps(result,ensure_ascii=False))
            except Exception:
                print('PAGE ERRORS: '+str(ui.errors),flush=True)
                print('LAST REQUESTS: '+json.dumps(ui.requests[-8:],ensure_ascii=False),flush=True)
                if not page.is_closed():
                    (EVIDENCE/'browser-failure.txt').write_text(page.locator('body').aria_snapshot(),encoding='utf-8')
                    page.screenshot(path=str(EVIDENCE/'browser-failure.png'))
                raise
            finally:
                context.close()


if __name__ == "__main__":
    main()

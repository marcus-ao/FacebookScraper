"""Same temporary task and same operator inputs, driven through both built interfaces."""
from __future__ import annotations
import copy
import json
import re
from playwright.sync_api import sync_playwright, expect
from ui_fixture import BrowserFixture, ROOT, UIFixture
from browser_regression import choose

WORKFLOWS=['save','snooze','wake','skip','handoff','handoff_link','tags','export','approve','calendar_failure','settings','initial','refine_text','refine_image','recover_job','hashtags']

def run(page,ui,engine,workflow,original):
    react=engine=='react'; detail=copy.deepcopy(original); task_id=detail['id']
    detail['status']='snoozed' if workflow=='wake' else 'handed_off' if workflow=='handoff_link' else 'pending_review'
    detail['review']['status']=detail['status']; detail['read_only']=False
    path=f'/api/tasks/{task_id}'
    cap={'available':True,'reason':'','source_fingerprint':'a'*64,'needs_consent':True,'third_party':workflow in ['initial','recover_job'],'job':None}
    job={'job_id':'compare-job','status':'interrupted' if workflow=='recover_job' else 'pending','recorded_at':'2026-09-13T00:00:00Z','kind':'text','source_text_sha256':detail['text']['source_text_sha256']}
    if workflow=='recover_job': cap['job']=job
    caps={'max_refine_per_media':3,'image_attempts':{},'estimated_image_usd':0.045,'estimate_basis':'fixture','estimate_samples':1,'jobs':[]}
    options=ui.fx.client.get(path+'/approval-options').json();options.update(available=True,reason='',fingerprint='same-fingerprint',earliest='2026-09-14T08:00:00+02:00',latest='2026-10-01T20:00:00+02:00')
    settings=ui.fx.client.get('/api/settings').json()
    calendar=ui.fx.client.get('/api/calendar').json();calendar['refresh_available']=True
    ui.overrides={('GET',path):(200,detail),('GET',path+'/approval-options'):(200,options),
        ('GET',f'/api/initial-translation/task/{task_id}'):(200,cap),('GET',f'/api/refinements/task/{task_id}'):(200,caps),
        ('POST',f'/api/initial-translation/task/{task_id}'):(202,job),('POST',f'/api/refinements/task/{task_id}'):(202,job),
        ('GET','/api/initial-translation/jobs/compare-job'):(200,{**job,'status':'interrupted' if workflow=='recover_job' else 'failed'}),
        ('GET','/api/refinements/jobs/compare-job'):(200,{**job,'status':'failed'}),
        ('POST','/api/content-jobs/compare-job/recover'):(200,{**job,'status':'failed'}),
        ('POST',path+'/review'):(200,detail),('POST',path+'/export'):(200,{'fixture_zip':True}),
        ('POST',path+'/approve'):(200,{'ok':False,'status':'approved'}),
        ('PUT',path+'/localization'):(200,{**detail,'localization':{**detail['localization'],'body_de':'Manuell geprüft 😀 $219.99'}}),
        ('PUT',path+'/tags'):(200,{**detail,'tags':['Riko','促销']}),
        ('GET','/api/calendar'):(200,calendar),('POST','/api/calendar/refresh'):(503,{**calendar,'error':'fixture failure'}),
        ('GET','/api/settings'):(200,settings),('PUT','/api/settings'):(200,{**settings,'editable':{'default_times':['11:00','18:30'],'snooze_default_days':5}}),
        ('POST',f'/api/hashtags/task/{task_id}'):(200,{'source_text_sha256':detail['text']['source_text_sha256'],'generated_at':'2026-09-13T00:00:00Z','notice':'未采样','selected':['#Katzen'],'groups':[],'sampling':{'status':'unavailable','reason':'fixture offline'}})}
    start=len(ui.requests)
    if workflow in ['calendar_failure','settings']:
        view='calendar' if workflow=='calendar_failure' else 'settings'
        page.goto(ui.fx.base_url+('/'+view if react else '/?view='+view),wait_until='networkidle')
    else:
        page.goto(ui.fx.base_url+('/review/'+task_id if react else '/?task='+task_id),wait_until='networkidle')
        expect(page.get_by_role('button',name='编辑德语',exact=True)).to_have_count(0 if workflow=='handoff_link' else 1)
    endpoint=''
    if workflow=='save':
        page.get_by_role('button',name='编辑德语',exact=True).click()
        field=page.get_by_role('textbox',name='德语正文') if react else page.get_by_role('textbox',name='德语译文',exact=True)
        field.fill('Manuell geprüft 😀 $219.99');page.wait_for_timeout(350)
        endpoint=path+'/localization';page.get_by_role('button',name='保存',exact=True).click()
    elif workflow in ['snooze','wake','skip','handoff','handoff_link','export']:
        labels={'snooze':'稍后再审','wake':'恢复审校','skip':'这篇不发','handoff':'我已自行处理','handoff_link':'补充发布链接','export':'下载并由我处理'}
        if react:
            page.get_by_role('button',name='更多处理动作',exact=True).click();page.get_by_role('menuitem',name=labels[workflow],exact=True).click()
        else: page.get_by_role('button',name=labels[workflow],exact=True).click()
        modal=page.get_by_role('dialog')
        if workflow=='snooze': modal.locator('input[type="datetime-local"]').fill('2026-09-16T09:00')
        if workflow in ['snooze','skip']: modal.locator('textarea').fill('同一份运营理由')
        if workflow in ['handoff','handoff_link','export']: modal.locator('input[type="url"]').fill('https://example.invalid/handled')
        endpoint=path+('/export' if workflow=='export' else '/review')
        if workflow=='export':
            with page.expect_download() as downloaded: modal.get_by_role('button',name='下载并交给我',exact=True).click()
            assert downloaded.value.suggested_filename=='post_de.zip'
        else: modal.get_by_role('button',name='确认',exact=True).click()
    elif workflow=='tags':
        page.get_by_role('button',name='编辑分类',exact=True).click()
        (page.get_by_role('textbox',name='产品分类',exact=True) if react else page.locator('.classification textarea')).fill('Riko，促销')
        endpoint=path+'/tags';page.get_by_role('button',name='保存分类',exact=True).click()
    elif workflow=='approve':
        (page.get_by_role('textbox',name='发布时间（柏林当地时间）',exact=True) if react else page.locator('.approval input[type="datetime-local"]')).fill('2026-09-15T10:30')
        endpoint=path+'/approve';page.get_by_role('button',name='通过并创建排期',exact=True).click()
        if react: page.get_by_role('button',name='确认通过并创建排期',exact=True).click()
    elif workflow=='calendar_failure':
        endpoint='/api/calendar/refresh';page.get_by_role('button',name='刷新月历',exact=True).click()
    elif workflow=='settings':
        (page.get_by_role('textbox',name='默认排期时间（柏林）') if react else page.locator('.settings form input').first).fill('11:00, 18:30')
        (page.get_by_role('spinbutton',name='默认挂起期限') if react else page.locator('.settings input[type="number"]')).fill('5')
        endpoint='/api/settings';page.get_by_role('button',name='保存设置',exact=True).click()
    elif workflow=='initial':
        page.get_by_role('checkbox',name='我已确认可以处理这篇内容，开始本篇模型处理。').check()
        endpoint=f'/api/initial-translation/task/{task_id}'
        (page.locator('[data-paid-action="翻译这篇"]') if react else page.get_by_role('button',name='翻译这篇',exact=True)).click()
    elif workflow.startswith('refine_'):
        if react: page.get_by_text('单篇优化（可选）',exact=True).click()
        if workflow=='refine_image':
            if react: choose(page,'优化内容','图片')
            else: page.locator('.refinement select').first.select_option('image')
        (page.get_by_role('textbox',name='这一次希望怎样调整') if react else page.locator('.refinement textarea')).fill('保持事实，缩短开头')
        endpoint=f'/api/refinements/task/{task_id}'
        (page.locator('[data-paid-action="生成图片"], [data-paid-action="生成文案候选"]') if react else page.get_by_role('button',name=re.compile('^生成图片|^生成文案候选'))).click()
    elif workflow=='recover_job':
        endpoint='/api/content-jobs/compare-job/recover'
        page.get_by_role('button',name='核对并恢复本地状态（不重新生成）',exact=True).click()
    elif workflow=='hashtags':
        page.get_by_role('button',name='编辑德语',exact=True).click()
        if react: page.get_by_role('tab',name='话题标签与链接',exact=True).click()
        endpoint=f'/api/hashtags/task/{task_id}'
        (page.locator('[data-paid-action="生成德语标签建议"]') if react else page.get_by_role('button',name='生成德语标签建议',exact=True)).click()
    page.wait_for_timeout(500)
    trace=ui.requests[start:]
    writes=[r for r in trace if r['method'] in ['POST','PUT'] and r['path']==endpoint]
    assert len(writes)==1,(engine,workflow,trace)
    return {'write':writes[0],'sequence':[{'method':r['method'],'path':r['path'],'query':r['query']} for r in trace],
        'checks':[r['body'] for r in trace if r['path'].endswith('/check')]}

def main():
    all_results={}
    with BrowserFixture() as fx:
        ui=UIFixture(fx)
        original=fx.detail(next(row['id'] for row in ui.list_data['tasks'] if row['status']=='pending_review' and not row['hard_alerts']))
        with sync_playwright() as driver:
            context=driver.chromium.launch_persistent_context(str(fx.root/'compare-profile'),executable_path=fx.chrome_exe,headless=True,viewport={'width':1366,'height':768},timezone_id='America/New_York',accept_downloads=True)
            try:
                for engine in ['vue','react']:
                    ui.dist=ROOT/'web'/('ui-next' if engine=='react' else 'ui')/'dist'
                    all_results[engine]={}
                    for workflow in WORKFLOWS:
                        page=context.new_page();page.set_default_timeout(8000);ui.attach(page)
                        try: all_results[engine][workflow]=run(page,ui,engine,workflow,original)
                        finally: page.close()
                        print(f'PASS {engine} {workflow}',flush=True)
                differences=[]
                for workflow in WORKFLOWS:
                    old,new=all_results['vue'][workflow],all_results['react'][workflow]
                    assert old['write']==new['write'],(workflow,old['write'],new['write'])
                    if old['sequence']!=new['sequence']: differences.append(workflow)
                assert not ui.errors,ui.errors
                assert not fx.denied_backend_requests,fx.denied_backend_requests
                report={'workflows':all_results,'identical_write_contracts':WORKFLOWS,'read_sequence_differences':differences,
                    'explanation':'React adds shared Header runtime GET, detail calendar GET and query cache reuse; UI confirmation and tab layout change read timing. Image reads now happen only after the images tab is first opened (post-implementation review fix), so the text-only path issues fewer reads than before. Mutation endpoints and complete bodies are identical. /check retains full localization and is debounced.',
                    'external_calls':0,'server_denials':fx.denied_backend_requests}
                (ROOT/'docs/ui-refactor/network-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(f'{len(WORKFLOWS)} workflows: identical mutation method/path/full body; both UIs driven.',flush=True)
            finally: context.close()

if __name__=='__main__': main()

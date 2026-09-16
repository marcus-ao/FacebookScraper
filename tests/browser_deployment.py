"""Isolated built-UI deployment coordination; deployment transport is simulated, settings writes are local."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from playwright.sync_api import expect, sync_playwright
from browser_fixture import BrowserFixture


def main():
    marker = json.loads((ROOT / 'web/ui/dist/runtime.json').read_text(encoding='utf-8'))
    runtime = marker['runtime_id']
    evidence = ROOT / 'state/deployment-implementation'
    evidence.mkdir(parents=True, exist_ok=True)
    observations = []
    with BrowserFixture() as fixture, sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            str(fixture.root / 'deployment-profile'), executable_path=fixture.chrome_exe,
            headless=True, viewport={'width': 1500, 'height': 1000}, service_workers='block',
            args=['--disable-background-networking', '--disable-component-update', '--no-first-run'])
        try:
            page = browser.new_page()
            errors, reports, writes, held = [], [], [], []
            mode = {'hold_session': True, 'offline': False, 'reject_save': False, 'defers': 0, 'documents': 0}
            status = {'managed': True, 'sha': '1' * 40, 'runtime_id': runtime,
                      'maintenance': {'phase': 'open', 'epoch': 'epoch-one', 'blockers': [], 'operations': []},
                      'deployment': {'phase': 'idle', 'observed_sha': '2' * 40}}
            page.on('pageerror', lambda error: errors.append(str(error)))

            def route_request(route):
                request = route.request
                parsed = urlsplit(request.url)
                if parsed.netloc != urlsplit(fixture.base_url).netloc:
                    route.abort()
                    return
                if parsed.path == '/api/deployment/status':
                    if mode['offline']:
                        route.abort()
                    else:
                        route.fulfill(json=status)
                elif parsed.path == '/api/deployment/session':
                    reports.append(request.post_data_json)
                    if mode['hold_session']:
                        held.append(route)
                    else:
                        route.fulfill(json={'accepted': True})
                elif parsed.path == '/api/deployment/defer':
                    mode['defers'] += 1
                    status['maintenance']['phase'] = 'open'
                    status['deployment']['phase'] = 'waiting'
                    route.fulfill(json={'deferred_minutes': 30})
                elif request.method == 'PUT' and parsed.path == '/api/settings':
                    writes.append(request.headers)
                    if mode['reject_save']:
                        route.fulfill(status=409, json={'code': 'runtime_changed', 'detail': '保留草稿'})
                    else:
                        route.continue_()
                elif request.method in {'GET', 'HEAD'}:
                    if request.is_navigation_request():
                        mode['documents'] += 1
                    route.continue_()
                else:
                    raise AssertionError(f'Unexpected mutation {request.method} {parsed.path}')

            page.route('**/*', route_request)
            page.goto(fixture.base_url + '/settings', wait_until='domcontentloaded')
            field = page.get_by_role('textbox', name='默认排期时间（柏林）')
            expect(field).to_be_visible()
            expect(page.locator('[data-deployment-content]')).to_have_attribute('inert', '')
            page.wait_for_function('document.querySelector("[data-deployment-controls]") !== null')
            assert held, 'Managed page must register a session before permitting editing'
            mode['hold_session'] = False
            for route in held:
                route.fulfill(json={'accepted': True})
            expect(page.locator('[data-deployment-content]')).not_to_have_attribute('inert', '')
            observations.append('initial managed registration precedes editable content')

            field.fill('09:30, 18:30')
            page.wait_for_timeout(100)
            status['maintenance']['phase'] = 'announcing'
            page.evaluate('window.dispatchEvent(new Event("focus"))')
            expect(page.get_by_role('status').filter(has_text='准备更新')).to_be_visible()
            expect(field).to_have_value('09:30, 18:30')
            assert reports[-1]['dirty'] and reports[-1]['ack_epoch'] is None
            page.get_by_role('button', name='保存设置', exact=True).click()
            expect(page.get_by_text('设置已保存，下次选期或挂起时生效。')).to_be_visible()
            expect(page.locator('[data-deployment-content]')).to_have_attribute('inert', '')
            page.wait_for_timeout(100)
            assert reports[-1]['ack_epoch'] == 'epoch-one' and not reports[-1]['dirty'] and not reports[-1]['busy']
            assert writes[-1]['x-fbscraper-runtime'] == runtime
            observations.append('dirty editor can save; matching header and clean acknowledgement follow save')

            page.evaluate('''() => { const portal = document.createElement('div'); portal.className = 'ant-modal-root';
                portal.id = 'deployment-test-portal'; portal.innerHTML = '<button>Portal action</button>'; document.body.append(portal); }''')
            expect(page.locator('#deployment-test-portal')).to_have_attribute('inert', '')
            page.get_by_role('button', name='暂缓 30 分钟').click()
            expect(page.locator('[data-deployment-content]')).not_to_have_attribute('inert', '')
            expect(page.locator('#deployment-test-portal')).not_to_have_attribute('inert', '')
            assert mode['defers'] == 1
            observations.append('body portal freeze and accessible defer control restore safely')

            field.fill('11:15, 19:15')
            mode['offline'] = True
            page.evaluate('window.dispatchEvent(new Event("focus"))')
            expect(page.get_by_role('status').filter(has_text='重新')).to_be_visible()
            # Even programmatic clicks cannot escape the capture barrier.
            before = len(writes)
            page.get_by_role('button', name='保存设置', exact=True).evaluate('(button) => button.click()')
            page.wait_for_timeout(100)
            assert len(writes) == before
            expect(field).to_have_value('11:15, 19:15')
            mode['offline'] = False
            page.evaluate('window.dispatchEvent(new Event("online"))')
            expect(page.get_by_role('status').filter(has_text='暂缓时间')).to_be_visible()
            mode['reject_save'] = True
            page.get_by_role('button', name='保存设置', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='草稿保留')).to_be_visible()
            expect(field).to_have_value('11:15, 19:15')
            status['runtime_id'] = 'b' * 64 if runtime != 'b' * 64 else 'c' * 64
            page.evaluate('window.dispatchEvent(new Event("focus"))')
            expect(page.get_by_role('status').filter(has_text='不会自动刷新')).to_be_visible()
            assert mode['documents'] == 1
            observations.append('disconnect and runtime conflict retain draft, block mutations, and never reload dirty page')
            assert [row['sequence'] for row in reports] == sorted(set(row['sequence'] for row in reports))
            page.get_by_role('link', name='查看运行状态', exact=True).click()
            stay = page.get_by_role('button', name='留在本页', exact=True)
            expect(stay).to_be_visible()
            stay.click()
            expect(stay).not_to_be_visible()
            expect(field).to_have_value('11:15, 19:15')
            observations.append('navigation confirmation remains usable while frozen and preserves settings on stay')

            page.close()
            status.update(runtime_id=runtime, deployment={'phase': 'idle'})
            status['maintenance']['phase'] = 'open'
            mode['reject_save'] = False
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', route_request)
            page.goto(fixture.base_url + '/review/' + fixture.fb_id, wait_until='domcontentloaded')
            expect(page.locator('[data-deployment-content]')).not_to_have_attribute('inert', '')
            page.get_by_text('单篇优化（可选）', exact=True).click()
            instruction = page.get_by_role('textbox', name='这一次希望怎样调整')
            instruction.fill('KEEP THIS UNSENT DRAFT')
            status['maintenance']['phase'] = 'quiesced'
            page.evaluate('window.dispatchEvent(new Event("focus"))')
            expect(page.get_by_role('status').filter(has_text='系统正在更新，请稍候')).to_be_visible()
            page.get_by_role('link', name='查看运行状态', exact=True).click()
            stay = page.get_by_role('button', name='留在本页', exact=True)
            expect(stay).to_be_visible()
            stay.click()
            expect(stay).not_to_be_visible()
            expect(instruction).to_have_value('KEEP THIS UNSENT DRAFT')
            assert reports[-1]['dirty'] is True
            page.get_by_role('link', name='查看运行状态', exact=True).click()
            page.get_by_role('button', name='离开', exact=True).click()
            expect(page).to_have_url(fixture.base_url + '/runtime')
            observations.append('aggregate instruction draft blocks navigation; only explicit leave discards it')
            assert not errors, errors
            page.screenshot(path=str(evidence / 'frontend-deployment.png'), full_page=True)
            (evidence / 'frontend-browser.json').write_text(json.dumps({
                'status': 'offline_pass', 'scenarios': observations, 'session_reports': len(reports),
                'runtime_id': runtime, 'console_errors': errors, 'real_external_actions': False,
                'scope': 'Built React in isolated Chromium; simulated deployment API, real temporary settings API writes',
            }, ensure_ascii=False, indent=2), encoding='utf-8', newline='')
            print(json.dumps({'passed': len(observations), 'evidence': str(evidence / 'frontend-browser.json')}, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == '__main__':
    main()

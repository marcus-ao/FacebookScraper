"""Built UI over insecure HTTP with five isolated clients and real temporary APIs."""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from playwright.sync_api import expect, sync_playwright
from browser_fixture import BrowserFixture
from core.maintenance import Gate, SESSION_SECONDS
from web.api import deployment


def wait_for(page, predicate, description):
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(description)
        page.wait_for_timeout(30)


def refresh_status(page):
    page.evaluate('window.dispatchEvent(new Event("focus"))')


def editable(page):
    expect(page.locator('[data-deployment-content]')).not_to_have_attribute('inert', '')
    expect(page.get_by_role('status').filter(has_text='当前服务版本已就绪')).to_be_visible()


def save(page, name, suffix, status=200):
    with page.expect_response(lambda response: response.request.method == 'PUT'
                              and response.url.endswith(suffix)) as result:
        page.get_by_role('button', name=name, exact=True).click()
    assert result.value.status == status, result.value.text()
    if status == 200:
        if suffix == '/api/settings':
            expect(page.get_by_text('设置已保存，下次选期或挂起时生效。', exact=True)).to_be_visible()
        else:
            expect(page.get_by_role('button', name='编辑德语', exact=True)).to_be_visible()
    return result.value.json()


def main():
    runtime = json.loads((ROOT / 'web/ui/dist/runtime.json').read_text(encoding='utf-8'))['runtime_id']
    assert len(runtime) == 64, 'Build with VITE_FBSCRAPER_RUNTIME_ID before the LAN gate'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence = ROOT / 'state' / f'offline-lan-{stamp}-{os.getpid()}'
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'scenarios': [], 'browser_contexts': 5,
              'runtime_id': runtime, 'real_external_actions': False,
              'scope': 'Insecure HTTP Chromium; temporary real settings/localization/session/defer APIs. '
                       'Release identity, delayed transport and offline state are controlled fixtures. '
                       'Loopback hostname mapping does not prove physical LAN reachability or remote CIDR admission.'}
    errors, external, reports, documents, held = [], [], [[] for _ in range(5)], [0] * 5, []
    mode = {'hold_save': False, 'restore_runtime_on_reload': False}
    manifest = {'sha': 'a' * 40, 'runtime_id': runtime}
    try:
        with ExitStack() as resources:
            fixture = resources.enter_context(BrowserFixture(deployment_coordination=True))
            origin = f'http://review-lan.test:{fixture.port}'
            control = fixture.root / 'control'
            gate = Gate(control)
            gate.initialize()
            (control / 'host.json').write_text(json.dumps({
                'web_host': '0.0.0.0', 'web_port': fixture.port, 'public_base_url': origin,
                'allowed_client_cidrs': ['192.168.20.0/24'],
            }), encoding='utf-8', newline='')
            resources.enter_context(patch.dict(os.environ, FBSCRAPER_CONTROL_DIR=str(control)))
            resources.enter_context(patch.object(deployment, 'read_release', side_effect=lambda _: dict(manifest)))
            playwright = resources.enter_context(sync_playwright())
            browser = playwright.chromium.launch(executable_path=fixture.chrome_exe, headless=True, args=[
                '--host-resolver-rules=MAP review-lan.test 127.0.0.1', '--no-proxy-server',
                '--disable-background-networking', '--disable-component-update', '--no-first-run'])
            resources.callback(browser.close)
            contexts, pages = [], []

            def route_for(index):
                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    if parsed.scheme in {'data', 'blob', 'about'}:
                        route.continue_()
                        return
                    if parsed.netloc != urlsplit(origin).netloc:
                        external.append(request.url)
                        route.abort()
                        return
                    if request.is_navigation_request():
                        documents[index] += 1
                        if index == 0 and mode['restore_runtime_on_reload']:
                            # A replacement document now represents the newly loaded matching UI.
                            manifest['runtime_id'] = runtime
                            mode['restore_runtime_on_reload'] = False
                    if parsed.path == '/api/deployment/session':
                        reports[index].append(request.post_data_json)
                    if index == 2 and mode['hold_save'] and request.method == 'PUT' and parsed.path == '/api/settings':
                        held.append(route)
                        return
                    route.continue_()
                return route_request

            for index in range(5):
                context = browser.new_context(viewport={'width': 1500, 'height': 1000}, service_workers='block')
                contexts.append(context)
                context.set_default_timeout(10000)
                context.route('**/*', route_for(index))
                page = context.new_page()
                pages.append(page)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/settings', wait_until='domcontentloaded')
                expect(page.get_by_role('textbox', name='默认排期时间（北京）')).to_be_visible()
                editable(page)
                capability = page.evaluate('({secure: window.isSecureContext, uuid: typeof crypto.randomUUID, '
                                           'random: typeof crypto.getRandomValues, clipboard: typeof navigator.clipboard})')
                assert capability == {'secure': False, 'uuid': 'undefined', 'random': 'function', 'clipboard': 'undefined'}, capability
            wait_for(pages[0], lambda: len(gate.status()['sessions']) == 5, 'Five real sessions were not registered')
            session_ids = [rows[0]['session_id'] for rows in reports]
            assert len(set(session_ids)) == 5, session_ids
            assert all(row['runtime_id'] == runtime for row in gate.status()['sessions'])
            report['origin'] = origin
            report['capabilities'] = capability
            report['session_ids'] = session_ids
            report['scenarios'].append('Five independent clients initialize on insecure HTTP without randomUUID; sessions are distinct')

            first, second, busy, disconnected, closing = pages
            times = lambda page: page.get_by_role('textbox', name='默认排期时间（北京）')
            times(first).fill('09:30, 18:30')
            times(second).fill('11:15, 19:15')
            saved = save(first, '保存设置', '/api/settings')
            save(second, '保存设置', '/api/settings', 409)
            expect(second.get_by_role('alert')).to_contain_text('设置在别处被改过了')
            expect(times(second)).to_have_value('11:15, 19:15')
            assert fixture.client.get('/api/settings').json()['editable'] == saved['editable']
            second.get_by_role('button', name='载入最新设置并保留我的修改', exact=True).click()
            expect(second.get_by_role('alert')).to_have_count(0)
            expect(times(second)).to_have_value('11:15, 19:15')
            save(second, '保存设置', '/api/settings')
            wait_for(first, lambda: all(not row['dirty'] and not row['busy'] for row in gate.status()['sessions']),
                     'Saved settings clients were not clean before navigating')
            report['scenarios'].append('Real settings CAS rejects the second client with 409, preserves its draft, and supports explicit recovery')

            for page in (first, second):
                page.goto(origin + '/review/' + fixture.fb_id, wait_until='domcontentloaded')
                editable(page)
                page.get_by_role('button', name='编辑德语', exact=True).click()
            first.get_by_role('textbox', name='德语正文').fill('LAN erster gespeicherter Entwurf.')
            second.get_by_role('textbox', name='德语正文').fill('LAN zweiter ungespeicherter Entwurf.')
            saved = save(first, '保存', '/localization')
            save(second, '保存', '/localization', 409)
            expect(second.get_by_role('textbox', name='德语正文')).to_have_value('LAN zweiter ungespeicherter Entwurf.')
            expect(second.get_by_role('button', name='载入最新内容并保留我的修改')).to_be_visible()
            assert fixture.detail(fixture.fb_id)['localization']['body_de'] == saved['localization']['body_de']
            ledger = fixture.sources[fixture.fb_id][2] / 'translated_human.jsonl'
            human_rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
            assert len(human_rows) == 1 and human_rows[0]['actor'] is None, human_rows
            report['scenarios'].append('Real same-post CAS preserves the rejected draft and the first stored text; actor remains null')

            # Clipboard absence is native to HTTP; force only the legacy fallback failure.
            first.evaluate('document.execCommand = () => false')
            first.get_by_role('button').filter(has_text='复制发布文案').click()
            expect(first.get_by_role('dialog', name='手动复制发布文案')).to_be_visible()
            expect(first.get_by_role('textbox', name='完整发布文案')).to_have_value(saved['localization_validation']['caption'])
            first.get_by_role('dialog', name='手动复制发布文案').get_by_role('button', name='关闭', exact=True).last.click()
            report['scenarios'].append('HTTP clipboard fallback presents the complete server-rendered caption for manual copying')

            # Each navigation owns a new in-memory session, including a second tab in one context.
            extra = contexts[4].new_page()
            extra.goto(origin + '/settings', wait_until='domcontentloaded')
            editable(extra)
            wait_for(first, lambda: len({row['session_id'] for row in reports[4]}) == 2, 'Same-context tabs reused a session')
            extra_id = reports[4][-1]['session_id']
            assert extra_id != session_ids[4]
            extra.close()
            wait_for(first, lambda: any(row['session_id'] == extra_id and row['closed'] for row in gate.status()['sessions']),
                     'Clean tab close beacon did not reach the real API')

            busy.get_by_role('button', name='重新读取', exact=True).click()
            expect(times(busy)).to_have_value('11:15, 19:15')
            times(busy).fill('12:00, 20:00')
            mode['hold_save'] = True
            busy.get_by_role('button', name='保存设置', exact=True).click()
            wait_for(first, lambda: bool(held) and any(row['busy'] for row in gate.status()['sessions']),
                     'In-flight UI write was not reported as busy')
            times(disconnected).fill('13:30, 21:30')
            wait_for(first, lambda: any(row['session_id'] == session_ids[3] and row['dirty']
                                        for row in gate.status()['sessions']), 'Dirty client was not registered')
            epoch = gate.announce(delay=0)
            for page in pages:
                refresh_status(page)
            expect(first.locator('[data-deployment-content]')).to_have_attribute('inert', '')
            expect(second.get_by_role('textbox', name='德语正文')).to_have_value('LAN zweiter ungespeicherter Entwurf.')
            assert not gate.try_quiesce(), 'Maintenance ignored active edits or pending write'
            closing.close()
            wait_for(first, lambda: any(row['session_id'] == session_ids[4] and row['closed']
                                        for row in gate.status()['sessions']), 'Clean client close was not acknowledged')
            contexts[3].set_offline(True)
            disconnected.evaluate('window.dispatchEvent(new Event("offline"))')
            expect(disconnected.get_by_role('status').filter(has_text='重新确认')).to_be_visible()
            expect(times(disconnected)).to_have_value('13:30, 21:30')
            expired = gate.status(now=time.time() + SESSION_SECONDS + 1)
            assert any(row['session_id'] == session_ids[3] and row['reason'] == 'unsaved'
                       for row in expired['blockers']), expired
            report['scenarios'].append('Same-context tabs have separate sessions; clean close is acknowledged; dirty, busy and expired offline drafts block switching')
            report['maintenance_blockers'] = expired['blockers']

            with first.expect_response(lambda response: response.url.endswith('/api/deployment/defer')) as deferred:
                first.get_by_role('button', name='暂缓 30 分钟', exact=True).click()
            assert deferred.value.status == 200
            assert gate.status()['phase'] == 'open' and gate.status()['deferred_until'] > time.time() + 1700
            mode['hold_save'] = False
            with busy.expect_response(lambda response: response.request.method == 'PUT' and response.url.endswith('/api/settings')) as released:
                held.pop().continue_()
            assert released.value.status == 200, released.value.text()
            refresh_status(second)
            editable(second)
            second.get_by_role('button', name='放弃修改', exact=True).click()
            contexts[3].set_offline(False)
            refresh_status(disconnected)
            editable(disconnected)
            before_close_sequence = reports[3][-1]['sequence']
            disconnected.close()
            wait_for(first, lambda: any(row['session_id'] == session_ids[3] and row['dirty'] and not row['closed']
                                        and row['sequence'] > before_close_sequence
                                        for row in gate.status()['sessions']), 'Dirty close must retain server blocker')
            # Simulate the explicit local operator resolution required after a lost dirty tab.
            gate.clear_session(session_ids[3])
            future = time.time() + 1801
            epoch = gate.announce(now=future, delay=0)
            for page in (first, second, busy):
                refresh_status(page)
            active_ids = [reports[index][-1]['session_id'] for index in range(3)]
            wait_for(first, lambda: all(any(row['session_id'] == session_id and row['ack_epoch'] == epoch
                                            and not row['dirty'] and not row['busy'] for row in gate.status()['sessions'])
                                       for session_id in active_ids), 'Clean clients did not acknowledge the maintenance epoch')
            report['before_quiescence'] = gate.status()

            def resolved_and_quiesced():
                # ⚠️ clear_session 连同 sequence 水位一起丢弃，关闭途中已经发出的那个状态包
                # 会把这条会话重新注册成 dirty，维护就再也静不下来。操作员遇到会再清一次，
                # 这里同样重试；其余客户端的干净与确认判据不变，照旧由 try_quiesce 把关。
                gate.clear_session(session_ids[3])
                return gate.try_quiesce(now=future)
            wait_for(first, resolved_and_quiesced, 'Maintenance did not quiesce after all clients became safe')
            refresh_status(first)
            expect(first.get_by_role('status').filter(has_text='系统正在更新，请稍候')).to_be_visible()
            gate.reopen()
            report['scenarios'].append('Real defer reopens the gate for 30 minutes; completed writes and explicit abandoned-session resolution allow clean acknowledgements and quiescence')

            for page in (first, second):
                refresh_status(page)
                editable(page)
            second.get_by_role('button', name='编辑德语', exact=True).click()
            second.get_by_role('textbox', name='德语正文').fill('LAN Entwurf beim Versionswechsel behalten.')
            wait_for(first, lambda: any(row['session_id'] == active_ids[1] and row['dirty']
                                        for row in gate.status()['sessions']), 'Runtime-change draft was not reported')
            busy.close()
            contexts[0].set_offline(True)
            before_documents = list(documents)
            manifest['runtime_id'] = 'b' * 64 if runtime != 'b' * 64 else 'c' * 64
            refresh_status(second)
            expect(second.get_by_role('status').filter(has_text='不会自动刷新')).to_be_visible()
            expect(second.get_by_role('textbox', name='德语正文')).to_have_value('LAN Entwurf beim Versionswechsel behalten.')
            assert documents[1] == before_documents[1]
            stale = second.evaluate('''async runtime => {
                const response = await fetch('/api/settings', {method: 'PUT',
                    headers: {'Content-Type': 'application/json', 'X-FBScraper-Runtime': runtime}, body: '{}'});
                return {status: response.status, body: await response.json()};
            }''', runtime)
            assert stale['status'] == 409 and stale['body']['code'] == 'runtime_changed', stale
            mode['restore_runtime_on_reload'] = True
            contexts[0].set_offline(False)
            refresh_status(first)
            wait_for(first, lambda: documents[0] == before_documents[0] + 1, 'Clean page did not refresh after runtime change')
            editable(first)
            assert reports[0][-1]['session_id'] != active_ids[0], 'Reload reused a per-tab in-memory session'
            report['scenarios'].append('Stale page write gets real runtime_changed 409; dirty page retains its draft, while a clean reconnect refreshes and registers anew')
            assert not errors, errors
            assert not external, external
            assert not fixture.denied_backend_requests, fixture.denied_backend_requests
            assert not (fixture.config.state_dir / 'paid_requests.jsonl').exists()
            assert not (fixture.config.state_dir / 'published.jsonl').exists()
            report['session_reports'] = [len(rows) for rows in reports]
            report['status'] = 'offline_pass'
            first.screenshot(path=str(evidence / 'lan-clean.png'), full_page=True)
            second.screenshot(path=str(evidence / 'lan-preserved-draft.png'), full_page=True)
    except BaseException as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report.update(console_errors=errors, blocked_external_requests=external)
        (evidence / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8', newline='')
        print(json.dumps({'status': report['status'], 'scenarios': len(report['scenarios']),
                          'evidence': str(evidence / 'report.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""The built UI recovers an existing receipt; every remote response is isolated."""
import copy

from playwright.sync_api import expect
from browser_fixture import approval_options, publish_operation
from ui_fixture import EVIDENCE


def stage_receipt_reconcile(page, ui):
    task_id = ui.fx.fb_id
    endpoint = '/api/tasks/' + task_id
    at = '2026-09-30T23:00:00+08:00'
    detail = copy.deepcopy(ui.fx.detail(task_id))
    op = publish_operation(task_id=task_id)
    op.update(status='uncertain', scheduled_at=at, message='Original readback failed')
    detail.update(status='approved', schedule=None, publish_operation=op,
                  publication={'attempt_id': 'offline-original', 'status': 'submitted_unverified', 'scheduled_at': at})
    detail['review'].update(status='approved')
    ui.overrides[('GET', endpoint)] = (200, detail)
    ui.overrides[('GET', endpoint + '/approval-options')] = (200, approval_options())
    ui.overrides[('POST', endpoint + '/publication/reconcile')] = (409,
        {'detail': '发布浏览器未启动', 'code': 'publication_reconcile_blocked'})
    page.goto(ui.fx.base_url + '/review/' + task_id, wait_until='networkidle')
    panel = page.get_by_role('region', name='审核与排期')
    expect(panel.get_by_text('已收到排期成功信号，待补齐回执', exact=True)).to_be_visible()
    expect(panel.locator('time')).to_have_attribute('datetime', at)
    expect(panel.get_by_text('请先恢复审核并准备好内容', exact=True)).to_have_count(0)
    button = panel.get_by_role('button', name='核对并补齐本地回执', exact=True)
    button.click()
    expect(panel.get_by_text('发布浏览器未启动', exact=True)).to_be_visible()
    expect(panel.get_by_text('这一篇的状态在别处变过了', exact=True)).to_have_count(0)
    ui.overrides[('POST', endpoint + '/publication/reconcile')] = (200,
        {'status': 'submitted_unverified', 'message': '原排期详情尚未读完整，请勿重新提交。'})
    button.click()
    expect(panel.get_by_text('原排期详情尚未读完整，请勿重新提交。', exact=True)).to_be_visible()
    expect(button).to_be_enabled()

    def recovered(_body):
        detail.update(status='scheduled', schedule={'at': at, 'channel': 'facebook'})
        detail['review'].update(status='scheduled', revision='recovered-revision')
        detail['publication'].update(status='scheduled', remote_id='facebook=123456789')
        return 200, {'status': 'scheduled', 'message': '已有排期已确认'}
    ui.overrides[('POST', endpoint + '/publication/reconcile')] = recovered
    button.click()
    expect(panel.get_by_text('排期已确认', exact=True)).to_be_visible()
    expect(panel.locator('time')).to_have_attribute('datetime', at)
    expect(panel.get_by_text('已收到排期成功信号，待补齐回执', exact=True)).to_have_count(0)
    expect(button).to_be_enabled()
    page.screenshot(path=str(EVIDENCE / 'receipt-recovered.png'))
    writes = [r for r in ui.requests if r['method'] not in ('GET', 'HEAD')]
    assert len(writes) == 3 and all(r['path'] == endpoint + '/publication/reconcile' for r in writes), writes
    return {'receipt_recovery': 'PASS', 'original_attempt': 'offline-original',
            'submitted_at': at, 'recovery_requests': len(writes), 'submit_requests': 0}

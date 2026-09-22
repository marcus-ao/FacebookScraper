"""群自建机器人传输层：假 HTTP 验证签名、信封与响应判定；不联系真实群。"""
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.feishu import (FeishuError, FeishuSettings, Outbox, WEBHOOK_PREFIX,
                         WebhookBot, notification_card, webhook_signature)

PUBLISH_URL = WEBHOOK_PREFIX + 'publish-hook-token'
ALERT_URL = WEBHOOK_PREFIX + 'alert-hook-token'
TARGETS = {'detect': WEBHOOK_PREFIX + 'detect-fixture', 'capture': WEBHOOK_PREFIX + 'capture-fixture',
           'publish': PUBLISH_URL, 'alert': ALERT_URL}
SECRETS = {'publish': 'fixture-secret', 'alert': 'zweites-geheimnis'}


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def bot(handle, *, secrets=None):
    return WebhookBot(TARGETS, secrets,
                      http=httpx.Client(transport=httpx.MockTransport(handle)))


def accepted(_request):
    # 真实成功响应同时带新旧两套字段。
    return httpx.Response(200, json={'StatusCode': 0, 'StatusMessage': 'success',
                                     'code': 0, 'msg': 'success', 'data': {}})


class SignatureTests(unittest.TestCase):
    def test_signature_matches_independently_computed_values(self):
        """定值按官方算法另算，不复用实现——否则算错了测试也跟着错。"""
        self.assertEqual(webhook_signature('1599360473', 'fixture-secret'),
                         'HIcEaU1WCovPqjRiRKRolPDXnB9tveoDAg/OMhGINHc=')
        self.assertEqual(webhook_signature('1700000000', 'zweites-geheimnis'),
                         '2SHD3i6c2DbK7yi0Fe1EDD+TnWhDVwJ5EsjdcwzFmt8=')

    def test_envelope_keeps_the_card_as_an_object_and_signs_per_attempt(self):
        client = bot(accepted, secrets=SECRETS)
        self.addCleanup(client.close)
        body = client.envelope('publish', {'header': {'title': 'x'}}, 1599360473.9)
        # ⚠️ card 必须是对象。im/v1/messages 的 content 要 json.dumps，机器人相反。
        self.assertIsInstance(body['card'], dict)
        self.assertEqual(body['msg_type'], 'interactive')
        self.assertEqual(body['timestamp'], '1599360473')
        self.assertEqual(body['sign'], webhook_signature('1599360473', SECRETS['publish']))
        # 同一张卡在下一次尝试用新时间戳重签：信封不冻结，卡片才冻结。
        later = client.envelope('publish', {'header': {'title': 'x'}}, 1599362000.0)
        self.assertEqual(later['card'], body['card'])
        self.assertNotEqual(later['sign'], body['sign'])

    def test_no_secret_means_no_signature_fields(self):
        client = bot(accepted)
        self.addCleanup(client.close)
        body = client.envelope('publish', {'header': {}}, 1599360473.0)
        self.assertNotIn('timestamp', body)
        self.assertNotIn('sign', body)


class TransportTests(unittest.TestCase):
    def test_each_role_posts_to_its_own_group_and_returns_a_local_receipt(self):
        seen = []

        def handle(request):
            seen.append((str(request.url), json.loads(request.content)))
            return accepted(request)

        client = bot(handle, secrets=SECRETS)
        self.addCleanup(client.close)
        self.assertEqual(client.send('publish', {'header': {}}, 'delivery-a'), 'bot-accepted:delivery-a')
        client.send('alert', {'header': {}}, 'delivery-b')
        self.assertEqual([url for url, _ in seen], [PUBLISH_URL, ALERT_URL])
        # 每个群用自己的密钥签名，不共用。
        self.assertEqual(seen[0][1]['sign'], webhook_signature(seen[0][1]['timestamp'], SECRETS['publish']))
        self.assertEqual(seen[1][1]['sign'], webhook_signature(seen[1][1]['timestamp'], SECRETS['alert']))

    def test_legacy_status_code_only_response_still_counts_as_accepted(self):
        client = bot(lambda _r: httpx.Response(200, json={'StatusCode': 0, 'StatusMessage': 'success'}))
        self.addCleanup(client.close)
        self.assertTrue(client.send('publish', {}, 'delivery'))

    def test_rejections_and_broken_responses_all_fail_closed(self):
        cases = {
            '19021': httpx.Response(200, json={'code': 19021, 'msg': 'sign match fail'}),
            '19024': httpx.Response(200, json={'code': 19024, 'msg': 'key words not found'}),
            'http-500': httpx.Response(500, text='upstream down'),
            'not-json': httpx.Response(200, text='<html>proxy</html>'),
            'not-object': httpx.Response(200, json=[0]),
            'no-code': httpx.Response(200, json={'msg': 'success'}),
        }
        for label, response in cases.items():
            with self.subTest(case=label):
                client = bot(lambda _r, response=response: response)
                self.addCleanup(client.close)
                with self.assertRaises(FeishuError):
                    client.send('publish', {}, 'delivery')

    def test_unknown_role_is_refused_instead_of_guessing_a_group(self):
        client = bot(accepted)
        self.addCleanup(client.close)
        with self.assertRaisesRegex(FeishuError, '接收角色'):
            client.send('marketing', {}, 'delivery')

    def test_all_four_bots_are_required_and_addresses_must_be_bot_hooks(self):
        for targets in ({'publish': PUBLISH_URL}, {'alert': ALERT_URL}, {'publish': PUBLISH_URL, 'alert': ''}):
            with self.subTest(targets=sorted(targets)):
                with self.assertRaisesRegex(FeishuError, 'FEISHU_WEBHOOK'):
                    WebhookBot(targets)
        for wrong in ('https://example.invalid/hook/token', WEBHOOK_PREFIX,
                      'http://open.feishu.cn/open-apis/bot/v2/hook/token'):
            with self.subTest(url=wrong):
                with self.assertRaisesRegex(FeishuError, WEBHOOK_PREFIX):
                    WebhookBot(dict(TARGETS, publish=wrong))

    def test_transport_failures_never_carry_the_address_or_secret(self):
        def refuses(request):
            raise httpx.ConnectError('failed to connect to ' + str(request.url))

        client = bot(refuses, secrets=SECRETS)
        self.addCleanup(client.close)
        with self.assertRaises(FeishuError) as caught:
            client.send('publish', {}, 'delivery')
        message = str(caught.exception)
        self.assertNotIn('publish-hook-token', message)
        self.assertNotIn(SECRETS['publish'], message)


class OutboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'outbox.json'
        self.settings = FeishuSettings(True, 'http://review.internal:8765')

    def test_role_labels_route_publish_and_alert_without_crossing(self):
        seen = []

        def handle(request):
            seen.append(str(request.url))
            return accepted(request)

        client = bot(handle, secrets=SECRETS)
        self.addCleanup(client.close)
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('alert', 'system', {'text': '请检查探测会话'}, now)
        outbox.enqueue('task', 'ready', {'text': 'Deutscher Text'}, now)
        self.assertEqual(outbox.dispatch(now, client.send), 2)
        self.assertEqual(sorted(seen), sorted([ALERT_URL, PUBLISH_URL]))

    def test_the_outbox_file_records_role_labels_and_never_the_hook_address(self):
        client = bot(accepted, secrets=SECRETS)
        self.addCleanup(client.close)
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('task', 'ready', {'text': 'Deutscher Text'}, now)
        outbox.dispatch(now, client.send)
        saved = self.path.read_text(encoding='utf-8')
        # 地址带 token，落盘就等于把凭据写进状态文件（红线 10）。
        self.assertNotIn('publish-hook-token', saved)
        self.assertNotIn(SECRETS['publish'], saved)
        self.assertIn('"recipient": "publish"', saved)
        status = json.dumps(outbox.status(), ensure_ascii=False)
        self.assertNotIn('publish-hook-token', status)

    def test_settings_use_stage_roles_without_open_ids(self):
        self.settings.validate()
        self.assertEqual((self.settings.publish_recipients, self.settings.alert_recipients),
                         (('publish',), ('alert',)))


class CardTests(unittest.TestCase):
    def setUp(self):
        self.settings = FeishuSettings(True, 'http://review.internal:8765')

    def test_detection_has_paired_fields_and_distinct_source_and_observation_times(self):
        card = notification_card('monitor_found', [{
            'platform': 'facebook', 'account': '@neakasaofficial', 'counts': {'new': 2},
            'published_at': '2026-09-22T02:03:04Z', 'discovered_at': '2026-09-22T03:04:05Z',
            'text': 'A clean home', 'run_id': 'scan/1'}], self.settings)
        self.assertEqual(card['header']['title']['content'], '[Neakasa 德国] 监测到新发帖')
        fields = card['elements'][0]['fields']
        self.assertEqual([f['text']['content'] for f in fields], [
            '**平台账号：**\nFacebook · @neakasaofficial',
            "**检测状态：**\n<font color='green'>新发布 2 篇</font>",
            '**最新发帖时间：**\n2026-09-22 10:03:04', '**监测时间：**\n2026-09-22 11:04:05'])
        self.assertTrue(all(f['is_short'] is True for f in fields))
        self.assertEqual(card['elements'][-1]['actions'][0]['url'],
                         'http://review.internal:8765/runtime?scan=scan%2F1')

    def test_empty_invalid_and_naive_times_are_safe_and_never_expose_zone_labels(self):
        for value in (None, '', 'not-a-date', 42, {}, '9999-12-31T23:59:59-12:00'):
            with self.subTest(value=value):
                card = notification_card('monitor_found', [{'published_at': value,
                    'discovered_at': value}], self.settings)
                fields = card['elements'][0]['fields']
                self.assertTrue(all(f['text']['content'].endswith('\n-') for f in fields[2:]))
                self.assertNotIn('北京时间', json.dumps(card, ensure_ascii=False))
        card = notification_card('monitor_found', [{'published_at': datetime(2026, 9, 22, 1)}], self.settings)
        self.assertTrue(card['elements'][0]['fields'][2]['text']['content'].endswith('2026-09-22 09:00:00'))

    def test_summary_normalizes_lines_truncates_before_escaping_and_never_mentions_everyone(self):
        for length in (149, 150, 151):
            card = notification_card('ready', [{'text': '文' * length}], self.settings)
            content = next(e['text']['content'] for e in card['elements'] if 'text' in e)
            self.assertEqual(content, '> ' + '文' * min(length, 150) + ('...' if length > 150 else ''))
        card = notification_card('ready', [{'text': 'first\r\n \n\n<at id=all></at> **bold**'}], self.settings)
        content = next(e['text']['content'] for e in card['elements'] if 'text' in e)
        self.assertNotIn('<at', content)
        self.assertNotIn('**bold**', content)
        self.assertEqual(len(content.splitlines()), 2)
        self.assertTrue(all(line.startswith('> ') for line in content.splitlines()))

    def test_risk_green_requires_completed_scan_clean_checks_and_current_preview(self):
        clean = {'risk_status': 'completed', 'risk_count': 0,
                 'checks': {'issues': [], 'warnings': [], 'ready': True}, 'text': 'Deutsch'}
        variants = [({}, True), ({'risk_status': 'not_scanned'}, False),
                    ({'risk_status': 'failed'}, False), ({'risk_status': 'stale'}, False),
                    ({'risk_count': 2}, False), ({'processing_notes': ['价格待核对']}, False),
                    ({'image_variant': 'original'}, False),
                    ({'checks': {'issues': [{'message': '未确认'}], 'warnings': [], 'ready': False}}, False),
                    ({'preview_error': 'content_refresh_failed'}, False)]
        for changes, green in variants:
            card = notification_card('ready', [{**clean, **changes}], self.settings)
            status = card['elements'][0]['fields'][1]['text']['content']
            self.assertEqual("color='green'" in status, green, changes)
            self.assertNotIn('敏感词', status)
            self.assertEqual(card['header']['template'], 'orange')

    def test_merged_review_buttons_are_numbered_and_all_follow_content(self):
        card = notification_card('ready', [{'task_id': 'a/' + str(i), 'text': 'item ' + str(i)}
                                          for i in range(10)], self.settings)
        tags = [e['tag'] for e in card['elements']]
        start = tags.index('action')
        self.assertTrue(all(tag == 'action' for tag in tags[start:]))
        buttons = [b for e in card['elements'][start:] for b in e['actions']]
        self.assertEqual(len(buttons), 10)
        self.assertTrue(all(len(e['actions']) <= 5 for e in card['elements'][start:]))
        self.assertEqual(buttons[-1]['text']['content'], '10. 前往审核入库')

    def test_settings_control_site_and_clock_and_reject_invalid_configuration(self):
        settings = replace(self.settings, site_name='示例站点', timezone='Europe/Berlin')
        card = notification_card('system', [{'occurred_at': '2026-07-01T00:00:00Z',
                                  'module_name': '采集', 'error_reason': '会话失效',
                                  'action_recommendation': '请手动登录'}], settings)
        self.assertEqual(card['header']['title']['content'], '[示例站点] 任务异常告警')
        self.assertIn('2026-07-01 02:00:00', json.dumps(card))
        self.assertEqual(card['elements'][-1]['actions'][0]['type'], 'danger')
        for changes in ({'site_name': ''}, {'timezone': 'invalid/zone'}):
            with self.assertRaises(ValueError):
                replace(self.settings, **changes)

    def test_legacy_capture_fields_keep_only_business_facts_and_correct_wall_time(self):
        card = notification_card('monitor_saved', [{'capture_status': '完整', 'fields': [
            {'label': '来源', 'value': 'Facebook · neakasaofficial'},
            {'label': '原帖发布时间（北京时间）', 'value': '2026-09-22 10:00:00'},
            {'label': '英文摘要', 'value': 'Legacy source'},
            {'label': '正文状态', 'value': '已保存'}, {'label': '产品分类', 'value': '未分类'}]}], self.settings)
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn('2026-09-22 10:00:00', rendered)
        self.assertIn('Legacy source', rendered)
        self.assertTrue(all(word not in rendered for word in ('北京时间', '正文状态', '产品分类')))

    def test_capture_and_detection_titles_never_upgrade_partial_or_historical_results(self):
        for payload, expected in [({'counts': {'historical': 1}}, '监测到历史补获'),
                                  ({'counts': {'new': 1, 'source_updated': 1}}, '监测到帖子变化')]:
            card = notification_card('monitor_found', [payload], self.settings)
            self.assertEqual(card['header']['title']['content'], '[Neakasa 德国] ' + expected)
        for archived, expected in [(True, '原帖抓取部分完成'), (False, '原帖抓取失败')]:
            card = notification_card('monitor_saved', [{'capture_status': '待人工', 'archived': archived}], self.settings)
            self.assertEqual(card['header']['title']['content'], '[Neakasa 德国] ' + expected)

    def test_legacy_detection_without_counts_does_not_claim_new_publication(self):
        card = notification_card('monitor_found', [{'text': '历史补获 1 篇',
                                  'created_at': '2026-09-22 10:00:00 北京时间'}], self.settings)
        self.assertEqual(card['header']['title']['content'], '[Neakasa 德国] 监测到帖子变化')
        self.assertTrue(card['elements'][0]['fields'][2]['text']['content'].endswith('\n-'))

    def test_monitor_cards_offer_the_dashboard_without_a_review_task(self):
        card = notification_card('monitor_found', [{
            'platform': 'Instagram', 'text': '发现 1 篇新帖', 'counts': {'new': 1},
            'permalink': 'https://www.instagram.com/p/abc/'}], self.settings)
        actions = [element for element in card['elements'] if element['tag'] == 'action']
        labels = [button['text']['content'] for element in actions for button in element['actions']]
        self.assertEqual(labels, ['查看运行看板'])
        self.assertEqual(card['header']['template'], 'turquoise')
        self.assertIn('监测到新发帖', card['header']['title']['content'])

    def test_an_untrustworthy_permalink_keeps_only_the_internal_button(self):
        for bad in ('http://www.instagram.com/p/abc/', 'javascript:alert(1)',
                    'https://user:pw@www.instagram.com/p/abc/', ''):
            with self.subTest(permalink=bad):
                card = notification_card('monitor_saved', [{'text': 'x', 'permalink': bad}],
                                         self.settings)
                buttons = [b for e in card['elements'] if e['tag'] == 'action' for b in e['actions']]
                self.assertEqual([b['url'] for b in buttons], ['http://review.internal:8765/runtime'])

    def test_review_cards_link_directly_to_the_review_task(self):
        card = notification_card('ready', [{
            'task_id': 'in_acme/p1', 'text': 'Deutscher Text',
            'permalink': 'https://www.instagram.com/p/abc/'}], self.settings)
        element = next(e for e in card['elements'] if e['tag'] == 'action')
        self.assertEqual([button['text']['content'] for button in element['actions']],
                         ['前往审核入库'])
        self.assertIn('task=in_acme%2Fp1', json.dumps(card, ensure_ascii=False))
        self.assertEqual(card['header']['template'], 'orange')

    def test_the_selftest_card_renders_but_can_never_be_enqueued(self):
        from core.feishu import KINDS, TITLES
        self.assertIn('selftest', TITLES)
        self.assertNotIn('selftest', KINDS)
        card = notification_card('selftest', [{'text': '通道自检'}], self.settings)
        self.assertIn('通道自检', card['header']['title']['content'])
        with tempfile.TemporaryDirectory() as td:
            outbox = Outbox(Path(td) / 'outbox.json', self.settings)
            with self.assertRaises(ValueError):
                outbox.enqueue('selftest-1', 'selftest', {'text': 'x'},
                               at('2026-09-12T09:00:00+08:00'))

    def test_the_two_monitor_kinds_sort_in_the_order_they_should_arrive(self):
        from core.feishu import ALWAYS_DELIVERED, MONITOR_KINDS, PREVIEWED
        # dispatch 按 sorted(KINDS) 建投递、按建立顺序发送，所以字典序就是群里的先后顺序。
        self.assertEqual(sorted(MONITOR_KINDS), ['monitor_found', 'monitor_saved'])
        # 静默豁免与收件人分开：监测卡豁免静默，检测与爬取各自路由。
        self.assertTrue(MONITOR_KINDS <= ALWAYS_DELIVERED)
        self.assertIn('system', ALWAYS_DELIVERED)
        self.assertFalse({'ready', 'backlog', 'morning'} & ALWAYS_DELIVERED)
        # 监测卡的内容在入队时已由抓取事实定稿，不参与发送前重取。
        self.assertEqual(PREVIEWED, {'ready'})


if __name__ == '__main__':
    unittest.main()

"""群自建机器人传输层：假 HTTP 验证签名、信封与响应判定；不联系真实群。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.feishu import (FeishuError, FeishuSettings, Outbox, WEBHOOK_PREFIX,
                         WebhookBot, notification_card, webhook_signature)

OPS_URL = WEBHOOK_PREFIX + 'ops-hook-token'
TECH_URL = WEBHOOK_PREFIX + 'tech-hook-token'
SECRETS = {'ops': 'fixture-secret', 'tech': 'zweites-geheimnis'}


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def bot(handle, *, secrets=None):
    return WebhookBot({'ops': OPS_URL, 'tech': TECH_URL}, secrets,
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
        body = client.envelope('ops', {'header': {'title': 'x'}}, 1599360473.9)
        # ⚠️ card 必须是对象。im/v1/messages 的 content 要 json.dumps，机器人相反。
        self.assertIsInstance(body['card'], dict)
        self.assertEqual(body['msg_type'], 'interactive')
        self.assertEqual(body['timestamp'], '1599360473')
        self.assertEqual(body['sign'], webhook_signature('1599360473', SECRETS['ops']))
        # 同一张卡在下一次尝试用新时间戳重签：信封不冻结，卡片才冻结。
        later = client.envelope('ops', {'header': {'title': 'x'}}, 1599362000.0)
        self.assertEqual(later['card'], body['card'])
        self.assertNotEqual(later['sign'], body['sign'])

    def test_no_secret_means_no_signature_fields(self):
        client = bot(accepted)
        self.addCleanup(client.close)
        body = client.envelope('ops', {'header': {}}, 1599360473.0)
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
        self.assertEqual(client.send('ops', {'header': {}}, 'delivery-a'), 'bot-accepted:delivery-a')
        client.send('tech', {'header': {}}, 'delivery-b')
        self.assertEqual([url for url, _ in seen], [OPS_URL, TECH_URL])
        # 每个群用自己的密钥签名，不共用。
        self.assertEqual(seen[0][1]['sign'], webhook_signature(seen[0][1]['timestamp'], SECRETS['ops']))
        self.assertEqual(seen[1][1]['sign'], webhook_signature(seen[1][1]['timestamp'], SECRETS['tech']))

    def test_legacy_status_code_only_response_still_counts_as_accepted(self):
        client = bot(lambda _r: httpx.Response(200, json={'StatusCode': 0, 'StatusMessage': 'success'}))
        self.addCleanup(client.close)
        self.assertTrue(client.send('ops', {}, 'delivery'))

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
                    client.send('ops', {}, 'delivery')

    def test_unknown_role_is_refused_instead_of_guessing_a_group(self):
        client = bot(accepted)
        self.addCleanup(client.close)
        with self.assertRaisesRegex(FeishuError, '接收角色'):
            client.send('marketing', {}, 'delivery')

    def test_both_groups_are_required_and_addresses_must_be_bot_hooks(self):
        for targets in ({'ops': OPS_URL}, {'tech': TECH_URL}, {'ops': OPS_URL, 'tech': ''}):
            with self.subTest(targets=sorted(targets)):
                with self.assertRaisesRegex(FeishuError, 'FEISHU_WEBHOOK'):
                    WebhookBot(targets)
        for wrong in ('https://example.invalid/hook/token', WEBHOOK_PREFIX,
                      'http://open.feishu.cn/open-apis/bot/v2/hook/token'):
            with self.subTest(url=wrong):
                with self.assertRaisesRegex(FeishuError, WEBHOOK_PREFIX):
                    WebhookBot({'ops': wrong, 'tech': TECH_URL})

    def test_transport_failures_never_carry_the_address_or_secret(self):
        def refuses(request):
            raise httpx.ConnectError('failed to connect to ' + str(request.url))

        client = bot(refuses, secrets=SECRETS)
        self.addCleanup(client.close)
        with self.assertRaises(FeishuError) as caught:
            client.send('ops', {}, 'delivery')
        message = str(caught.exception)
        self.assertNotIn('ops-hook-token', message)
        self.assertNotIn(SECRETS['ops'], message)


class OutboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'outbox.json'
        self.settings = FeishuSettings(True, 'http://review.internal:8765')

    def test_role_labels_route_to_the_two_groups_without_crossing(self):
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
        self.assertEqual(sorted(seen), sorted([TECH_URL, OPS_URL]))

    def test_the_outbox_file_records_role_labels_and_never_the_hook_address(self):
        client = bot(accepted, secrets=SECRETS)
        self.addCleanup(client.close)
        outbox = Outbox(self.path, self.settings)
        now = at('2026-09-12T09:00:00+08:00')
        outbox.enqueue('task', 'ready', {'text': 'Deutscher Text'}, now)
        outbox.dispatch(now, client.send)
        saved = self.path.read_text(encoding='utf-8')
        # 地址带 token，落盘就等于把凭据写进状态文件（红线 10）。
        self.assertNotIn('ops-hook-token', saved)
        self.assertNotIn(SECRETS['ops'], saved)
        self.assertIn('"recipient": "ops"', saved)
        status = json.dumps(outbox.status(), ensure_ascii=False)
        self.assertNotIn('ops-hook-token', status)

    def test_settings_no_longer_need_open_ids_and_keep_the_two_groups_apart(self):
        self.settings.validate()
        self.assertEqual((self.settings.recipients, self.settings.technical_recipients),
                         (('ops',), ('tech',)))


class CardTests(unittest.TestCase):
    def setUp(self):
        self.settings = FeishuSettings(True, 'http://review.internal:8765')

    def test_monitor_cards_offer_the_source_link_without_a_review_task(self):
        card = notification_card('monitor_found', [{
            'platform': 'Instagram', 'text': '发现 1 篇新帖',
            'permalink': 'https://www.instagram.com/p/abc/'}], self.settings)
        actions = [element for element in card['elements'] if element['tag'] == 'action']
        labels = [button['text']['content'] for element in actions for button in element['actions']]
        # 「查看原帖」原来埋在 task_id 分支里；监测卡发生在有审校任务之前。
        self.assertEqual(labels, ['查看原帖'])
        self.assertEqual(card['header']['template'], 'turquoise')
        self.assertIn('监测到新帖', card['header']['title']['content'])

    def test_an_untrustworthy_permalink_produces_no_button(self):
        for bad in ('http://www.instagram.com/p/abc/', 'javascript:alert(1)',
                    'https://user:pw@www.instagram.com/p/abc/', ''):
            with self.subTest(permalink=bad):
                card = notification_card('monitor_saved', [{'text': 'x', 'permalink': bad}],
                                         self.settings)
                self.assertFalse([e for e in card['elements'] if e['tag'] == 'action'])

    def test_review_cards_keep_both_buttons_in_the_review_first_order(self):
        card = notification_card('ready', [{
            'task_id': 'in_acme/p1', 'text': 'Deutscher Text',
            'permalink': 'https://www.instagram.com/p/abc/'}], self.settings)
        element = next(e for e in card['elements'] if e['tag'] == 'action')
        self.assertEqual([button['text']['content'] for button in element['actions']],
                         ['去审校', '查看原帖'])
        self.assertIn('task=in_acme%2Fp1', json.dumps(card, ensure_ascii=False))
        self.assertEqual(card['header']['template'], 'blue')

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
        from core.feishu import MONITOR_KINDS, URGENT_KINDS
        # dispatch 按 sorted(KINDS) 建投递、按建立顺序发送，所以字典序就是群里的先后顺序。
        self.assertEqual(sorted(MONITOR_KINDS), ['monitor_found', 'monitor_saved'])
        self.assertTrue(MONITOR_KINDS <= URGENT_KINDS)
        self.assertIn('system', URGENT_KINDS)
        self.assertFalse({'ready', 'backlog', 'morning'} & URGENT_KINDS)


if __name__ == '__main__':
    unittest.main()

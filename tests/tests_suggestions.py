"""德语文案的只读优化建议：严格解析、越界丢弃与"绝不改人工稿"。不调用网络。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from core import translated
from core.config import cfg
from localize import suggest
from localize import text as translation
from pipeline import engine, refinement

EN = "Free shipping over $50. Shop now! #Neakasa #CatLover"
DE = "Kostenlose Versand ab $50. Kaufen Sie jetzt! #Neakasa #CatLover"


def one(quote, replacement, kind="grammar", why="因为要这样"):
    return ('[{"quote": %s, "replacement": %s, "kind": "%s", "why": "%s"}]'
            % (_json(quote), _json(replacement), kind, why))


def _json(value):
    import json
    return json.dumps(value, ensure_ascii=False)


class ParseTests(unittest.TestCase):
    def parse(self, payload, *, text_de=DE):
        return suggest.parse_suggestions(payload, text_de=text_de)

    def test_empty_list_is_a_real_answer_not_a_failure(self):
        # 挑不出毛病是正常结果，不能当成模型出错。
        self.assertEqual(self.parse("[]"), {"items": [], "dropped": []})

    def test_locatable_suggestion_survives_with_all_four_fields(self):
        result = self.parse(one("Kostenlose Versand", "Kostenloser Versand"))
        self.assertEqual(result["dropped"], [])
        self.assertEqual(result["items"][0]["quote"], "Kostenlose Versand")
        self.assertEqual(result["items"][0]["replacement"], "Kostenloser Versand")

    def test_quote_that_cannot_be_located_is_dropped_not_guessed(self):
        # 模型凭印象重打一遍就会对不上；对不上就没法采用，不许模糊匹配。
        result = self.parse(one("Kostenloser Versand", "Gratis Versand"))
        self.assertEqual(result["items"], [])
        self.assertIn("出现 0 次", result["dropped"][0])

    def test_ambiguous_quote_is_dropped_because_replacement_would_be_arbitrary(self):
        result = self.parse(one("Versand", "Lieferung"), text_de="Versand und Versand")
        self.assertEqual(result["items"], [])
        self.assertIn("出现 2 次", result["dropped"][0])

    def test_suggestions_touching_money_or_hashtags_are_dropped(self):
        for quote, replacement in [("$50", "50 €"), ("#CatLover", "#Katzenliebe")]:
            result = self.parse(one(quote, replacement))
            self.assertEqual(result["items"], [], (quote, replacement))
            self.assertTrue(result["dropped"], (quote, replacement))

    def test_replacement_cannot_smuggle_in_a_link(self):
        result = self.parse(one("Kaufen Sie jetzt", "Jetzt auf https://de.example/p shoppen"))
        self.assertEqual(result["items"], [])
        self.assertIn("链接", result["dropped"][0])

    def test_one_bad_item_does_not_throw_away_the_good_ones(self):
        payload = ('[{"quote": "Kostenlose Versand", "replacement": "Kostenloser Versand",'
                   ' "kind": "grammar", "why": "词尾错了"},'
                   ' {"quote": "$50", "replacement": "50 €", "kind": "wording", "why": "本地化"}]')
        result = self.parse(payload)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["dropped"]), 1)

    def test_broken_contract_fails_loudly_instead_of_returning_nothing(self):
        for payload in ['not json', '{"quote": "x"}', '[{"quote": "Kostenlose Versand",'
                        ' "replacement": "x", "kind": "typo", "why": "y"}]', '[{"quote": ""}]']:
            with self.assertRaises(suggest.SuggestionContractError, msg=payload):
                self.parse(payload)

    def test_more_items_than_the_cap_is_rejected(self):
        items = ",".join('{"quote": "q%d", "replacement": "r", "kind": "wording", "why": "w"}' % n
                         for n in range(suggest.MAX_ITEMS + 1))
        with self.assertRaises(suggest.SuggestionContractError):
            self.parse("[" + items + "]")

    def test_currency_stays_verbatim_so_the_prompt_and_the_gate_agree(self):
        prompt = suggest.build_prompt(translation.Settings())
        self.assertNotIn("{{", prompt)
        self.assertIn("金额与货币符号", prompt)
        self.assertIn("话题标签", prompt)

    def test_human_localized_prices_and_tags_do_not_discard_grammar_advice(self):
        body = 'Kostenlose Versand ab 49,99 €. Jetzt kaufen! #Katzenliebe'
        result = self.parse(one('Kostenlose Versand', 'Kostenloser Versand'), text_de=body)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['dropped'], [])

    def test_request_excludes_urls_from_both_language_inputs(self):
        request = json.loads(suggest.build_request(
            'Shop https://example.invalid/en', 'Hier https://example.invalid/de'))
        self.assertNotIn('https://', json.dumps(request))


class BindingTests(unittest.TestCase):
    def test_suggestions_expire_when_the_translation_changes(self):
        row = suggest.record(job_id="a" * 32, account="fa_example", post_id="123",
                             source_text_sha256_value="s" * 64, text_de=DE,
                             parsed={"items": [], "dropped": []}, paid_request_id=None)
        self.assertTrue(suggest.is_current(row, source_text_sha256_value="s" * 64, text_de=DE))
        self.assertFalse(suggest.is_current(row, source_text_sha256_value="s" * 64,
                                            text_de=DE + " Neu."))
        self.assertFalse(suggest.is_current(row, source_text_sha256_value="t" * 64, text_de=DE))
        self.assertFalse(suggest.is_current(None, source_text_sha256_value="s" * 64, text_de=DE))

    def test_a_newer_prompt_version_expires_old_suggestions(self):
        row = suggest.record(job_id="a" * 32, account="fa_example", post_id="123",
                             source_text_sha256_value="s" * 64, text_de=DE,
                             parsed={"items": [], "dropped": []}, paid_request_id=None)
        row["prompt_version"] = suggest.SUGGEST_PROMPT_VERSION - 1
        self.assertFalse(suggest.is_current(row, source_text_sha256_value="s" * 64, text_de=DE))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.account, self.source = self.fixture.account, self.fixture.source
        self.executor = Mock()
        patch.object(engine, 'budget_preflight', return_value=None).start()
        self.addCleanup(patch.stopall)

    def submit(self):
        body = self.fixture.client.get(self.fixture.url).json()['localization']['body_de']
        return refinement.submit(self.account, self.source, kind='suggest', instruction='',
            source_text_sha256=translated.source_text_sha256(self.source['text']),
            human_revision=None, review_revision=None, body_de=body, executor=self.executor)

    def test_suggest_needs_no_instruction_unlike_the_other_two_kinds(self):
        self.assertEqual(self.submit()['kind'], 'suggest')
        with self.assertRaises(Exception):
            refinement.submit(self.account, self.source, kind='text', instruction='',
                source_text_sha256=translated.source_text_sha256(self.source['text']),
                human_revision=None, review_revision=None, executor=self.executor)

    def test_generating_suggestions_never_touches_the_human_or_machine_text(self):
        human_path = self.account / 'translated_human.jsonl'
        human = translated.append_human_translation(human_path, self.source,
            'Von Hand formuliert. #Neakasa', expected_revision=None)
        before_human = human_path.read_bytes()
        machine_path = self.account / 'translated.jsonl'
        before_machine = machine_path.read_bytes() if machine_path.exists() else None

        row = refinement.submit(self.account, self.source, kind='suggest', instruction='',
            source_text_sha256=translated.source_text_sha256(self.source['text']),
            human_revision=human['revision'], review_revision=None, body_de='Von Hand formuliert.',
            executor=self.executor)
        caller = SimpleNamespace(
            translate=lambda text, system: one('Von Hand', 'Handgeschrieben'),
            set_paid_context=lambda *a: None, finalize_paid=lambda *a: None,
            paid_request_id='offline-test')
        result = refinement.execute(row, self.source, translator=caller)

        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['suggestions'][0]['replacement'], 'Handgeschrieben')
        # ⛔ 红线 6：建议只是清单，后台任务不碰任何一份译文真相源。
        self.assertEqual(human_path.read_bytes(), before_human)
        self.assertEqual(machine_path.read_bytes() if machine_path.exists() else None, before_machine)

    def test_without_a_german_draft_there_is_nothing_to_advise_on(self):
        # 夹具默认带一版机器译文；这里模拟"还没翻过"的帖子。
        (self.account / 'translated.jsonl').unlink()
        with self.assertRaisesRegex(Exception, '德语正文'):
            self.submit()
        self.executor.submit.assert_not_called()

    def test_a_broken_model_reply_closes_the_paid_request_as_failed(self):
        row = self.submit()
        finalize = Mock()
        caller = SimpleNamespace(translate=lambda text, system: 'not json at all',
                                 set_paid_context=lambda *a: None, finalize_paid=finalize,
                                 paid_request_id='offline-test')
        result = refinement.execute(row, self.source, translator=caller)
        self.assertEqual(result['status'], 'failed')
        finalize.assert_called_once()
        self.assertFalse(finalize.call_args[0][0])
        self.assertEqual(suggest.load_suggestions(
            cfg().state_dir / refinement.SUGGESTIONS_FILE), [])

    def test_new_advice_for_a_tagged_post_is_current_in_the_detail_api(self):
        row = self.submit()
        caller = SimpleNamespace(
            translate=lambda *a: one('Ein sauberes', 'Ein gepflegtes'),
            set_paid_context=lambda *a: None, finalize_paid=lambda *a: None)
        result = refinement.execute(row, self.source, translator=caller)
        self.assertEqual(result['status'], 'succeeded')
        stored = self.fixture.client.get(self.fixture.url).json()['text_suggestions']
        self.assertTrue(stored['current'])

    def test_api_freezes_the_unsaved_editor_body_and_never_saves_it(self):
        detail = self.fixture.client.get(self.fixture.url).json()
        body = 'Kostenlose Versand. Ungespeichert.'
        before = (self.account / 'translated.jsonl').read_bytes()
        with patch.object(refinement, '_executor', self.executor):
            response = self.fixture.client.post('/api/refinements/task/' + self.fixture.task_id,
                json={'kind': 'suggest', 'body_de': body, 'instruction': '',
                      'source_text_sha256': detail['text']['source_text_sha256'],
                      'human_revision': None, 'review_revision': None})
        self.assertEqual(response.status_code, 202, response.text)
        caller = SimpleNamespace(
            translate=Mock(return_value=one('Kostenlose Versand', 'Kostenloser Versand')),
            set_paid_context=lambda *a: None, finalize_paid=lambda *a: None)
        result = refinement.execute(response.json(), self.source, translator=caller)
        self.assertEqual(result['status'], 'succeeded')
        sent = json.loads(caller.translate.call_args.args[0])
        self.assertEqual(sent['current_german'], body)
        self.assertEqual(len(result['suggestions']), 1)
        self.assertEqual((self.account / 'translated.jsonl').read_bytes(), before)
        self.assertFalse((self.account / 'translated_human.jsonl').exists())
        stored = self.fixture.client.get(self.fixture.url).json()['text_suggestions']
        self.assertEqual(stored['body_de'], body)
        self.assertFalse(stored['current'])  # 尚未保存；前端使用当前编辑区快照核对。

    def test_a_missing_editor_snapshot_is_rejected_before_accepting_a_job(self):
        with self.assertRaisesRegex(Exception, '德语正文'):
            refinement.submit(self.account, self.source, kind='suggest', instruction='',
                source_text_sha256=translated.source_text_sha256(self.source['text']),
                human_revision=None, review_revision=None, executor=self.executor)
        self.executor.submit.assert_not_called()

    def test_a_later_saved_draft_cannot_change_the_queued_suggestion_input(self):
        row = self.submit()
        original = row['body_de']
        self.fixture.save('Später von Hand gespeichert. #Neakasa')
        caller = SimpleNamespace(translate=Mock(return_value='[]'),
                                 set_paid_context=lambda *a: None, finalize_paid=lambda *a: None)
        result = refinement.execute(row, self.source, translator=caller)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(json.loads(caller.translate.call_args.args[0])['current_german'], original)
        self.assertFalse(self.fixture.client.get(self.fixture.url).json()['text_suggestions']['current'])


if __name__ == '__main__':
    unittest.main()

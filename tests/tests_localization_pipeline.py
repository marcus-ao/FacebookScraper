"""平台本地化必须进入真正的翻译请求和发布组装，不能仅改变编辑器。"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests_web_review as fixtures
from localize import text as translation
from core import localization, translated
from core.config import cfg
from publish import compose


class LocalizationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.account, self.source = self.fixture.account, self.fixture.source

    def test_source_urls_do_not_enter_main_request_examples_or_refine_context(self):
        url = 'https://shop.example/path?price=19#fragment'
        self.source['text'] = 'Visit our store ' + url + ' #Neakasa'
        self.fixture.write_source()
        seen = []
        def generate(text, system):
            seen.append((text, system))
            self.assertNotIn(url, text + system)
            self.assertNotIn('#fragment', text)
            return 'Besuche unseren Shop. #Neakasa'
        result = translation.run_translate(translation.Settings(), SimpleNamespace(translate=generate),
            self.account, 1, True, False, source_rows=[self.source],
            refine_instruction='优化 ' + url, refine_id='1' * 32, current_body='Alter Text ' + url)
        self.assertEqual(result, (1, 0))
        self.assertEqual(len(seen), 1)
        row = translated.load_translated(self.account / 'translated.jsonl')[self.source['post_id']]
        self.assertEqual(row['source_text_sha256'], translated.source_text_sha256(self.source['text']))

    def test_unmapped_facebook_url_blocks_then_confirmed_mapping_reaches_compose(self):
        source_url = 'https://us.example/product'
        self.source['text'] = 'Our product ' + source_url + ' #Neakasa'
        self.fixture.write_source()
        self.fixture.write_machine('Unser Produkt. #Neakasa')
        self.fixture.write_generated_image('Unser Produkt. #Neakasa')
        def build():
            return compose.compose_post(self.source['post_id'], datetime.now(timezone.utc),
                archive_root=cfg().archive_dir, account=self.account.name, warning_sink=None)
        with self.assertRaisesRegex(compose.ComposeError, '落地页'):
            build()
        cfg()._d['publish']['link_map'] = {source_url: 'https://de.example/produkt'}
        with self.assertRaisesRegex(compose.ComposeError, '请确认本篇的链接'):
            build()
        self.assertEqual(self.fixture.save_localization(links_confirmed=True).status_code, 200)
        post = build()
        self.assertIn('https://de.example/produkt', post.text_de)
        self.assertNotIn(source_url, post.text_de)

    def test_confirmed_semantic_tag_selection_survives_price_mapping(self):
        self.source['text'] = 'A clean home. #CatLover #Neakasa'
        self.fixture.write_source()
        self.fixture.write_machine('Ein sauberes Zuhause. #CatLover #Neakasa')
        self.fixture.write_generated_image('Ein sauberes Zuhause. #CatLover #Neakasa')
        machine = translated.load_translated(self.account / 'translated.jsonl')[self.source['post_id']]
        draft = localization.effective_draft(self.account, self.source, machine)
        self.assertFalse(localization.validate(draft)['ready'])
        draft.update(tags=['#Katzenliebe', '#Neakasa'], hashtags_confirmed=True, links_confirmed=True)
        human = translated.append_human_translation(self.account / 'translated_human.jsonl', self.source,
            localization.render(draft), expected_revision=None)
        localization.append_localization(self.account, self.source, draft, human_revision=human['revision'],
            expected_revision=None, expected_source_sha256=translated.source_text_sha256(self.source['text']))
        post = compose.compose_post(self.source['post_id'], datetime.now(timezone.utc),
            archive_root=cfg().archive_dir, account=self.account.name, price_map={}, warning_sink=None)
        self.assertIn('#Katzenliebe', post.text_de)
        self.assertNotIn('#CatLover', post.text_de)


if __name__ == '__main__':
    unittest.main()

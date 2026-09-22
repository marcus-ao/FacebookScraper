"""Replay the retained Facebook-only Story detail page, never a live account."""
import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from month_detail_fixtures import ACCOUNTS, MonthDetailCase

STORY_ID = '1065266012808732'
PAGE_ID = '739367289254011'
# Structure from the 2026-09-21 reload log of the 9/15 19:17 Facebook Story: one
# platform badge, metric tabs only, and no Instagram root anywhere on the page.
FB_ONLY = '''<header><div role="heading" aria-level="3" id="caption">{caption}</div>
  <div id="metadata"><img alt="{platform}"><span>Story &middot; Published on: Tue Sep 15, 7:17pm</span></div></header>
  <button role="tab" aria-selected="true">Total</button>
  <button role="tab" aria-selected="false">Audience</button>
  <aside><h3>Feed preview</h3><div><div><img><div>''' + ACCOUNTS['facebook'] + '''</div></div></div></aside>
  <script>fetch('/api/graphql/',{method:'POST'});</script>'''
# The page is named beside the entity only through lwi_info.page_id; the
# supported_actions owner (61578176852811) is a profile id, not this page.
FB_ONLY_PAYLOAD = {'data': {
    'page': {'name': ACCOUNTS['facebook'], 'id': PAGE_ID},
    'tofu_object_insights': {'__typename': 'BizWebFBStoryObjectInsights', 'entity': {
        'entity_id': STORY_ID, 'entity_info': {
            '__typename': 'TofuFBStoryEntityInfo', 'title': 'Your story',
            'media_type': 'PHOTOS', 'entity_id': STORY_ID,
            'lwi_info': {'__typename': 'XFBTofuFBStoryBoostInfo', 'page_id': PAGE_ID}}}}}}


class FacebookOnlyStoryTests(MonthDetailCase):
    """A Story published to Facebook alone: no Instagram root, no channel tab."""

    content_id = STORY_ID
    day = date(2026, 9, 15)
    clock = '7:17 PM'

    def load_responses(self):
        self.documents = [copy.deepcopy(FB_ONLY_PAYLOAD)]
        self.detail = FB_ONLY.replace('{caption}', 'Your story').replace('{platform}', 'Facebook')

    async def test_facebook_only_story_completes_from_its_own_entity(self):
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual(len(result.cards), 1)
        card = result.cards[0]
        self.assertEqual(dict(card.remote_ids), {'facebook': STORY_ID})
        self.assertEqual(dict(card.accounts), {'facebook': ACCOUNTS['facebook']})
        self.assertEqual((card.placement, card.caption_status, card.rendered), ('story', 'unknown', ''))
        self.assertEqual(card.at.isoformat(), '2026-09-15T19:17:00+08:00')
        # Nothing on this page relates it to Instagram, so nothing may claim it does.
        self.assertEqual(card.relationships, ())
        self.assertEqual(result.occupied_for_channel('facebook'), (card.at,))
        self.assertEqual(result.occupied_for_channel('instagram'), ())
        self.assertEqual(len(self.context.pages), 1)

    async def test_a_page_not_bound_to_this_entity_cannot_name_the_story(self):
        for invalid in ['other_entity', 'inner_entity', 'other_page', 'no_page_id', 'caption', 'no_title']:
            with self.subTest(invalid=invalid):
                self.load_responses()
                entity = self.documents[0]['data']['tofu_object_insights']['entity']
                if invalid == 'other_entity':
                    entity['entity_id'] = '1099999999999999'
                elif invalid == 'inner_entity':
                    entity['entity_info']['entity_id'] = '1099999999999999'
                elif invalid == 'other_page':
                    # A named page that is not the one this Story was boosted from.
                    self.documents[0]['data']['page']['id'] = '739367289254012'
                elif invalid == 'no_page_id':
                    entity['entity_info'].pop('lwi_info')
                elif invalid == 'caption':
                    self.detail = self.detail.replace('>Your story<', '>Another story<')
                else:
                    entity['entity_info'].pop('title')
                result = await self.inventory()
                self.assertFalse(result.decision_complete)
                self.assertFalse(any(c.read_status == 'complete' for c in result.cards))

    async def test_an_instagram_badge_still_waits_for_its_own_channel_tab(self):
        # Only a Facebook-only header may take this path; every other Story keeps
        # the recorded Instagram-rooted reader and its tab.
        self.detail = self.detail.replace('alt="Facebook"', 'alt="Instagram"')
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([d['missing_fields'] for d in result.diagnostics], [['instagram_channel_tab']])

    async def test_late_channel_tabs_are_reported_instead_of_a_facebook_only_story(self):
        self.detail = self.detail.replace("fetch('/api/graphql/',{method:'POST'});", '''
          setTimeout(()=>document.body.insertAdjacentHTML('beforeend',
            '<button role="tab" aria-selected="false">Facebook</button>'+
            '<button role="tab" aria-selected="false">Instagram</button>'),400);
          fetch('/api/graphql/',{method:'POST'});''')
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([d['missing_fields'] for d in result.diagnostics], [['channel_tabs']])


if __name__ == '__main__':
    unittest.main()

"""Replay the retained aggregate Post detail page, never a live account."""
import copy
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from month_detail_fixtures import ACCOUNTS, MonthDetailCase
from publish import published_details

POST_ID = '122187915260939228'
POST_IG_ID = '18129143875786241'
FB_PROFILE_ID = '61578176852811'
IG_ACTOR_ID = '17841475604335349'
# From the 2026-09-21 reload log of the 9/15 19:17 aggregate. No tabpanel exists:
# selecting a channel rewrites the one header, and each channel keeps its own
# minute (Facebook 7:17pm, Instagram 7:18pm) and its own caption length.
AGGREGATE = '''<header><div role="heading" aria-level="3" id="caption">{caption}</div>
  <div id="metadata"><span id="platforms"><img alt="Facebook"><img alt="Instagram"></span>
    <span id="clock">Post &middot; Published on: Tue Sep 15, 7:17pm</span></div></header>
  <div role="tab" aria-selected="true" onclick="select(this)">Total performance</div>
  <div role="tab" aria-selected="false" onclick="select(this)">Facebook</div>
  <div role="tab" aria-selected="false" onclick="select(this)">Instagram</div>
  <div role="tab" aria-selected="true">Total</div>
  <aside><h3>Feed preview</h3><div id="preview"></div></aside>
  <script>function select(tab){
    document.querySelectorAll('[role=tab]').forEach(n=>n.setAttribute('aria-selected','false'));
    tab.setAttribute('aria-selected','true');
    const name=tab.textContent;
    if(name==='Total performance'){
      platforms.innerHTML='<img alt="Facebook"><img alt="Instagram">';
      caption.textContent='Der Neakasa Herbst-Sale ist da! FB';
      clock.textContent='Post \\u00b7 Published on: Tue Sep 15, 7:17pm';
      return;
    }
    platforms.innerHTML='<img alt="'+name+'">';
    caption.textContent=name==='Facebook'?'Der Neakasa Herbst-Sale ist da! FB':'Der Neakasa Herbst-Sale ist da! IG';
    clock.textContent='Post \\u00b7 Published on: Tue Sep 15, '+(name==='Facebook'?'7:17pm':'7:18pm');
    preview.innerHTML=name==='Instagram'
      ? '<div id="caption-body"><span id="caption-author">neakasa.de</span></div>'
      : '<h2><a href="https://www.facebook.com/profile.php?id=''' + FB_PROFILE_ID + '''">Neakasa Deutschland</a></h2>';
  }fetch('/api/graphql/',{method:'POST'});</script>'''
AGGREGATE_ROOT = {'data': {'tofu_entity': {'entity_id': POST_ID, 'entity_info': {
    '__typename': 'TofuFBStoryEntityInfo', 'title': 'Der Neakasa Herbst-Sale ist da! FB',
    'cross_posted_entities': [
        {'entity_id': POST_ID, 'entity_info': {'__typename': 'TofuFBStoryEntityInfo',
            'owner': {'entity_id': FB_PROFILE_ID,
                      'entity_info': {'__typename': 'TofuFBProfileWithBizToolsEntityInfo'}}}},
        {'entity_id': POST_IG_ID, 'entity_info': {'__typename': 'TofuIGPostEntityInfo',
            'owner': {'entity_id': IG_ACTOR_ID,
                      'entity_info': {'__typename': 'TofuIGAccountEntityInfo'}},
            'ig_media': {'id': POST_IG_ID, 'permalink': 'https://www.instagram.com/p/DdTldNxlXi7/'}}}]}}}}
# The author is named on the story that holds this post_id; the viewer actor in
# the same document is the signed-in profile and must never stand in for it.
AGGREGATE_STORY = {'data': {'tofu_entity': {'entity_info': {'story': {
    'post_id': POST_ID, 'creation_time': 1789471077,
    'feedback': {'owning_profile': {'__typename': 'User', 'id': FB_PROFILE_ID,
                                    'name': ACCOUNTS['facebook']},
                 'viewer_actor': {'__typename': 'User', 'id': '61589751128761'}},
    'actors': [{'__typename': 'User', 'id': FB_PROFILE_ID, 'name': ACCOUNTS['facebook']}]}}}}}
AGGREGATE_IG = {'data': {'instagram_post': {'id': POST_IG_ID,
    'bizlink_instagram_actor': {'username': ACCOUNTS['instagram'], 'id': IG_ACTOR_ID}}}}


class AggregatePostTests(MonthDetailCase):
    """A Post published to both channels, read one selected channel at a time."""

    content_id = POST_ID
    day = date(2026, 9, 15)
    clock = '7:17 PM'

    def load_responses(self):
        self.documents = [copy.deepcopy(AGGREGATE_ROOT), copy.deepcopy(AGGREGATE_STORY),
                          copy.deepcopy(AGGREGATE_IG)]
        self.detail = AGGREGATE.replace('{caption}', 'Der Neakasa Herbst-Sale ist da! FB')

    async def test_each_channel_of_an_aggregate_keeps_its_own_identity_and_minute(self):
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual(len(result.cards), 2)
        fb = next(c for c in result.cards if c.channels == ('facebook',))
        ig = next(c for c in result.cards if c.channels == ('instagram',))
        self.assertEqual(dict(fb.remote_ids), {'facebook': POST_ID})
        self.assertEqual(dict(ig.remote_ids), {'instagram': POST_IG_ID})
        self.assertEqual(dict(fb.accounts), {'facebook': ACCOUNTS['facebook']})
        self.assertEqual(dict(ig.accounts), {'instagram': ACCOUNTS['instagram']})
        # Two channels, two minutes: the grid time matches Facebook only.
        self.assertEqual((fb.at.hour, fb.at.minute), (19, 17))
        self.assertEqual((ig.at.hour, ig.at.minute), (19, 18))
        self.assertEqual(fb.rendered, 'Der Neakasa Herbst-Sale ist da! FB')
        self.assertEqual(ig.rendered, 'Der Neakasa Herbst-Sale ist da! IG')
        self.assertEqual(fb.relationships, ('cross_platform',))
        self.assertEqual(ig.relationships, ('cross_platform',))
        self.assertEqual(result.occupied_for_channel('facebook'), (fb.at,))
        self.assertEqual(result.occupied_for_channel('instagram'), (ig.at,))

    async def test_empty_initial_badges_do_not_route_an_aggregate_as_single_channel(self):
        # The 9/24 log has two native members but ends in read_view's empty-channel
        # failure. This isolated hydration sequence reproduces that routing defect;
        # the operator log does not retain the exact DOM arrival times.
        self.detail += '''<script>
          const initialTabs=[...document.querySelectorAll('[role=tab]')]
            .filter(n=>['Total performance','Facebook','Instagram'].includes(n.textContent));
          initialTabs.forEach(n=>n.remove());
          platforms.replaceChildren();
          setTimeout(()=>{
            platforms.innerHTML='<img alt="Facebook"><img alt="Instagram">';
            initialTabs.forEach(n=>document.body.insertBefore(n,document.querySelector('aside')));
          },1000);
        </script>'''
        result = await self.inventory()
        self.assertTrue(result.decision_complete, result.diagnostics)
        self.assertEqual([(card.channels, dict(card.remote_ids), card.at.minute) for card in result.cards],
                         [(('facebook',), {'facebook': POST_ID}, 17),
                          (('instagram',), {'instagram': POST_IG_ID}, 18)])

    async def test_native_members_wait_for_instagram_when_header_only_shows_facebook(self):
        self.detail += '''<script>
          const igTab=[...document.querySelectorAll('[role=tab]')].find(n=>n.textContent==='Instagram');
          igTab.remove();
          platforms.innerHTML='<img alt="Facebook">';
          clock.textContent='Loading';
          setTimeout(()=>clock.textContent='Post \\u00b7 Published on: Tue Sep 15, 7:17pm',300);
          setTimeout(()=>document.body.insertBefore(igTab,document.querySelector('aside')),1400);
        </script>'''
        result = await self.inventory()
        self.assertTrue(result.decision_complete, result.diagnostics)
        self.assertEqual([card.channels for card in result.cards], [('facebook',), ('instagram',)])

    async def test_known_cross_post_cannot_be_complete_with_a_missing_channel_tab(self):
        self.detail += '''<script>
          [...document.querySelectorAll('[role=tab]')].find(n=>n.textContent==='Instagram').remove();
          platforms.innerHTML='<img alt="Facebook">';
          clock.textContent='Loading';
          setTimeout(()=>clock.textContent='Post \\u00b7 Published on: Tue Sep 15, 7:17pm',300);
        </script>'''
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([item['missing_fields'] for item in result.diagnostics], [['channel_tabs']])

    async def test_a_later_header_cannot_remove_an_observed_channel(self):
        # Legacy DOM identity remains supported when no native response arrives.
        # Once both badges are seen, a repaint cannot authorize reading only FB.
        self.documents = []
        self.detail += '''<script>
          document.querySelectorAll('[role=tab]').forEach(n=>n.remove());
          preview.innerHTML='<h2><a href="https://www.facebook.com/profile.php?id=61578176852811">Neakasa Deutschland</a></h2>'
            + '<a href="https://www.facebook.com/permalink.php?story_fbid=122187915260939228&id=61578176852811">View post</a>';
        </script>'''
        original_snapshot = published_details.header_snapshot

        async def shrinking_header(page):
            snapshot = await original_snapshot(page)
            if snapshot and snapshot['platforms'] == ['Facebook', 'Instagram']:
                await page.locator('#platforms img[alt=Instagram]').evaluate('(node)=>node.remove()')
            return snapshot

        with patch.object(published_details, 'header_snapshot', shrinking_header):
            result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([item['missing_fields'] for item in result.diagnostics], [['channel_tabs']])

    async def test_a_disappearing_tab_cannot_remove_an_observed_channel(self):
        self.documents = []
        self.detail += '''<script>
          document.querySelectorAll('[role=tab]').forEach(n=>n.remove());
          platforms.innerHTML='<img alt="Facebook">';
          preview.innerHTML='<h2><a href="https://www.facebook.com/profile.php?id=61578176852811">Neakasa Deutschland</a></h2>'
            + '<a href="https://www.facebook.com/permalink.php?story_fbid=122187915260939228&id=61578176852811">View post</a>';
          const initialTab=document.createElement('button');
          initialTab.setAttribute('role','tab');
          initialTab.textContent='Instagram';
          document.body.append(initialTab);
          setTimeout(()=>{
            initialTab.remove();
            const panel=document.createElement('div');
            panel.id='fb-view';
            panel.setAttribute('role','tabpanel');
            panel.setAttribute('aria-labelledby','fb-tab');
            panel.append(document.querySelector('header'),document.querySelector('aside'));
            document.body.insertAdjacentHTML('beforeend',
              '<button role="tab" id="fb-tab" aria-selected="true" aria-controls="fb-view">Facebook</button>');
            document.body.append(panel);
          },1000);
        </script>'''
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([item['missing_fields'] for item in result.diagnostics], [['channel_tabs']])

    async def test_a_name_not_bound_to_this_members_own_ids_cannot_be_used(self):
        for invalid, unresolved in [('other_root', 'facebook'), ('viewer_only', 'facebook'),
                                    ('other_post', 'facebook'), ('other_actor', 'facebook'),
                                    ('foreign_media', 'instagram'), ('no_members', 'facebook')]:
            with self.subTest(invalid=invalid):
                self.load_responses()
                root = self.documents[0]['data']['tofu_entity']
                story = self.documents[1]['data']['tofu_entity']['entity_info']['story']
                if invalid == 'other_root':
                    root['entity_id'] = '122199999999999999'
                elif invalid == 'viewer_only':
                    # Only the signed-in profile is left; it is not the author.
                    story['feedback'].pop('owning_profile')
                elif invalid == 'other_post':
                    story['post_id'] = '122199999999999999'
                elif invalid == 'other_actor':
                    story['actors'][0]['id'] = '61599999999999'
                elif invalid == 'foreign_media':
                    self.documents[2]['data']['instagram_post']['id'] = '18199999999999999'
                else:
                    root['entity_info'].pop('cross_posted_entities')
                result = await self.inventory()
                self.assertFalse(result.decision_complete)
                self.assertNotIn((unresolved,), [card.channels for card in result.cards
                                                 if card.read_status == 'complete'])

    async def test_a_stale_header_cannot_stand_in_for_the_selected_channel(self):
        # The badge keeps naming both platforms after a channel is selected.
        self.detail = self.detail.replace(
            "platforms.innerHTML='<img alt=\"'+name+'\">';",
            "platforms.innerHTML='<img alt=\"Facebook\"><img alt=\"Instagram\">';")
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([d['missing_fields'] for d in result.diagnostics], [['selected_channel']])


if __name__ == '__main__':
    unittest.main()

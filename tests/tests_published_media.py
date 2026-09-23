"""Replay the retained published-media detail page, never a live account."""
import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from month_detail_fixtures import MonthDetailCase

MEDIA_ID = '18015951041945821'
# Structure from the 2026-09-21 reload logs: no channel tab at all, metric tabs
# only, the collaboration named inside the metadata node, and the whole caption
# in the level 3 heading. The visible ellipsis there is CSS clamping: the logged
# heading length (530/720) tracks the response title length (532/718).
MEDIA = '''<header><div role="heading" aria-level="3" id="caption">{caption}</div>
  <div id="metadata"><img alt="{platform}"><span>{label} &middot; Published on: Sat Sep 5, 11:48am
    &middot; <strong>neakasa.global</strong><strong>{extra}</strong></span></div></header>
  <button role="tab" aria-selected="true">Total</button>
  <button role="tab" aria-selected="false">Audience</button>
  <aside><h3>Feed preview</h3><div><div><img><div>{preview}</div></div>
    <div id="caption-body"><span id="caption-author">{preview}</span><span>{caption}</span></div></div></aside>
  <script>fetch('/api/graphql/',{method:'POST'});</script>'''
MEDIA_PAYLOAD = {'data': {'instagram_post': {'id': MEDIA_ID,
    'bizlink_instagram_actor': {'username': 'neakasa.global', 'id': '17841413032463188'}},
    'viewer': {'username': 'neakasa.global', 'id': '17841413032463188'}}}


class PublishedMediaTests(MonthDetailCase):
    """A collaborator's published post occupies the slot under its own account."""

    content_id = MEDIA_ID
    day = date(2026, 9, 5)
    clock = '11:48 AM'

    def load_responses(self):
        self.documents = [copy.deepcopy(MEDIA_PAYLOAD)]
        self.detail = MEDIA.replace('{caption}', 'Day 1 at IFA').replace('{label}', 'Post') \
            .replace('{platform}', 'Instagram').replace('{preview}', 'neakasa.global') \
            .replace('{extra}', ' in collaboration with neakasa.tech and neakasa.de')

    async def test_collaborator_post_occupies_the_slot_under_its_own_account(self):
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual(len(result.cards), 1)
        card = result.cards[0]
        self.assertEqual(dict(card.remote_ids), {'instagram': MEDIA_ID})
        # The configured account is neakasa.de; this post is authored elsewhere.
        self.assertEqual(dict(card.accounts), {'instagram': 'neakasa.global'})
        self.assertEqual((card.placement, card.media_kind, card.caption_status), ('feed','unknown','present'))
        self.assertEqual(card.rendered, 'Day 1 at IFA')
        self.assertEqual(card.relationships, ('collaboration',))
        self.assertEqual(result.occupied_for_channel('instagram'), (card.at,))
        self.assertEqual(result.occupied_for_channel('facebook'), ())

    async def test_reel_keeps_its_own_placement_and_video_form(self):
        self.detail = self.detail.replace('Post &middot;', 'Reel &middot;')
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual((result.cards[0].placement, result.cards[0].media_kind), ('reel','video'))

    async def test_sole_author_is_not_recorded_as_a_collaboration(self):
        self.detail = self.detail.replace(' in collaboration with neakasa.tech and neakasa.de', '')
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual(result.cards[0].relationships, ())

    async def test_unverified_or_mismatched_identity_keeps_the_slot_unresolved(self):
        original_detail = self.detail
        for invalid in ['no_media_response', 'other_media_id', 'preview_author', 'viewer_only', 'platform']:
            with self.subTest(invalid=invalid):
                self.documents = [copy.deepcopy(MEDIA_PAYLOAD)]
                self.detail = original_detail
                if invalid == 'no_media_response':
                    self.documents = [{'data': {}}]
                elif invalid == 'other_media_id':
                    self.documents[0]['data']['instagram_post']['id'] = '17999999999999999'
                elif invalid == 'preview_author':
                    self.detail = self.detail.replace(
                        '<span id="caption-author">neakasa.global</span>', '<span id="caption-author">someone.else</span>')
                elif invalid == 'viewer_only':
                    # The signed-in viewer is not the author of this media.
                    self.documents[0]['data'].pop('instagram_post')
                else:
                    self.detail = self.detail.replace('alt="Instagram"', 'alt="Facebook"')
                result = await self.inventory()
                self.assertFalse(result.decision_complete)
                self.assertFalse(any(c.read_status == 'complete' for c in result.cards))
                # A loader/navigation failure must not accidentally satisfy an identity rejection.
                missing = {'preview_author': 'instagram_preview_author', 'platform': 'owner'}.get(
                    invalid, 'media_identity')
                self.assertEqual([d['stage'] for d in result.diagnostics], ['published_detail'])
                self.assertEqual([d['missing_fields'] for d in result.diagnostics], [[missing]])

    async def test_late_channel_tabs_are_reported_instead_of_a_single_channel_reading(self):
        self.detail = self.detail.replace("fetch('/api/graphql/',{method:'POST'});", '''
          setTimeout(()=>document.body.insertAdjacentHTML('beforeend',
            '<button role="tab" aria-selected="false">Facebook</button>'+
            '<button role="tab" aria-selected="false">Instagram</button>'),400);
          fetch('/api/graphql/',{method:'POST'});''')
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        self.assertEqual([d['missing_fields'] for d in result.diagnostics], [['channel_tabs']])

    async def test_a_two_platform_header_waits_for_its_own_channel_tabs(self):
        # The 9/15 19:17 aggregate names both platforms in its header while its
        # channel tabs mount afterwards. Counting them once reads the detail as a
        # single channel, which then fails for a channel it was never given.
        self.detail = self.detail.replace('<img alt="Instagram">',
                                          '<img alt="Facebook"><img alt="Instagram">') \
            .replace("fetch('/api/graphql/',{method:'POST'});", '''
          const mount=name=>{const b=document.createElement('button');b.setAttribute('role','tab');
            b.setAttribute('aria-selected','false');b.textContent=name;
            b.onclick=()=>b.setAttribute('aria-selected','true');document.body.append(b)};
          setTimeout(()=>{mount('Facebook');mount('Instagram')},400);
          fetch('/api/graphql/',{method:'POST'});''')
        result = await self.inventory()
        self.assertFalse(result.decision_complete)
        # Selected a channel and asked this page for that channel's identity,
        # instead of reporting the aggregate as a reading with no channel at all.
        self.assertEqual([d['missing_fields'] for d in result.diagnostics], [['aggregate_identity']])


if __name__ == '__main__':
    unittest.main()

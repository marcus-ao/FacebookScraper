"""Replay the retained Story response paths in an isolated browser, never a live account."""
import calendar
import copy
import contextlib
import io
import json
import sys
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import month_inventory as month
from publish.business_suite import ProbeRequired
from publish.month_readback import matching
from tools import probe_calendar_detail as probe


# Paths and IDs are from the redacted 2026-09-20 first-load log, SHA-256
# e8550e119e3a86495b23a0d968783d0b246423182b319d5fcbf7cc56ca64dc93.
# Only relevant retained fields are replayed; this is not an unredacted response.
SOURCE_ID = '18084155825688886'
IG_INFO = {'__typename': 'TofuIGPostEntityInfo', 'title': '', 'media_type': 'PHOTOS',
    'ig_media': {'id': SOURCE_ID,
                 'permalink': 'https://www.instagram.com/stories/neakasa.de/3978703119637304396'}}
ENTITY = {'data': {'tofu_entity': {'entity_info': {**IG_INFO,
    'cross_posted_entities': [
        {'entity_info': {'__typename': 'TofuFBStoryEntityInfo'}},
        {'entity_info': IG_INFO}]}}}}
BUSINESS = {'data': {'tofu_business_content': {'contents': [
    {'__typename': 'BusinessFBStoryContent', 'id': '1068553422207259', 'creation_time': 1788518376,
     'content_owner': {'__typename': 'Page', 'id': '739367289254011'}}]}}}
ACCOUNTS = {'facebook': 'Neakasa Deutschland', 'instagram': 'neakasa.de'}

# Direct relation fields/IDs from story-reader-20260920/probe.txt. The raw title,
# owner title and epoch were redacted; these fixture values exercise validation.
FB_ID = '1781315906229402'
FB_ENTITY = {'entity_id': FB_ID, 'entity_info': {
    '__typename': 'TofuFBStoryEntityInfo', 'title': 'Your story',
    'created_at': int(datetime(2026,9,4,10,39,tzinfo=timezone.utc).timestamp()),
    'owner': {'entity_id': '61578176852811', 'entity_info': {
        '__typename': 'TofuFBProfileWithBizToolsEntityInfo', 'title': ACCOUNTS['facebook']}}}}

# Reconstructed from story-reader-20260920/facebook.png: the Story owner row, then
# the nested cross-posted card with its own account row and a caption mention.
# ⚠️ Do not flatten this back to one bare `<img>` beside one `<div>` author. That
# shape existed only in this fixture, and matching it is what shipped a reader the
# live page could never satisfy.
FB_PREVIEW = ('<h3>Feed preview</h3><div>'
    '<div><div><img></div><div><span>' + ACCOUNTS['facebook'] + '</span></div><i>...</i><i>x</i></div>'
    '<div><div><div><img></div><div>neakasa.global, neakasa.de and neakasa.tech</div></div>'
    '<img><div><span>neakasa.global</span> Back at IFA this year</div></div></div>')

# DOM is a semantic reconstruction of the logged roles/attributes, not raw HTML.
DETAIL = '''<header><div role="heading" aria-level="3" id="caption">This content has no text</div>
  <div id="metadata">Story · Published on: Fri Sep 4, 6:39pm</div><span id="platforms"></span></header>
  <button role="tab" aria-selected="true" onclick="select(this)">Total performance</button>
  <button role="tab" aria-selected="false" onclick="select(this)">Facebook</button>
  <button role="tab" aria-selected="false" onclick="select(this)">Instagram</button>
  <aside><h3>Feed preview</h3><div id="instagram_story_preview_frame"><div>neakasa.de</div>
    <img id="instagram_story_preview_media"></div></aside>
  <div role="progressbar">Metrics loading</div>
  <script>function select(tab){
    document.querySelectorAll('[role=tab]').forEach(n=>n.setAttribute('aria-selected','false'));
    tab.setAttribute('aria-selected','true');
    platforms.innerHTML='<img alt="'+tab.textContent+'">';
  }fetch('/api/graphql/',{method:'POST'});</script>'''


class StoryInsightsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.context = await self.browser.new_context()
        self.payload = copy.deepcopy(ENTITY)
        self.detail_html = DETAIL
        self.requests = []
        async def route(request):
            url = request.request.url
            self.requests.append(url)
            if '/api/graphql/' in url:
                body = json.dumps(self.payload) + '\n' + json.dumps(BUSINESS)
                await request.fulfill(content_type='application/json', body=body)
            elif '/object_insights/' in url:
                await request.fulfill(content_type='text/html; charset=utf-8', body=self.detail_html)
            else:
                await request.fulfill(content_type='text/html', body='<html></html>')
        await self.context.route('**/*', route)
        self.page = await self.context.new_page()
        await self.page.goto('https://business.facebook.com/latest/content_calendar')
        days = calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)
        cells = ''.join(f'<div role="link" draggable="false"><span>{d.day}</span>' + (
            f'<a href="/latest/insights/object_insights/?content_id={SOURCE_ID}">6:39 PM</a>'
            if d == date(2026,9,4) else '') + '</div>' for d in days)
        await self.page.set_content('<h1>September</h1><h1>2026</h1>' + cells)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def inventory(self):
        with patch.object(month, 'prepare', AsyncMock()), patch.object(month, 'accounts', return_value=ACCOUNTS):
            return await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai', timeout=5)

    def linked_facebook(self):
        self.payload['data']['tofu_entity']['entity_info']['cross_posted_entities'][0] = copy.deepcopy(FB_ENTITY)
        self.detail_html = DETAIL.replace("tab.setAttribute('aria-selected','true');", """
            tab.setAttribute('aria-selected','true');
            caption.textContent=tab.textContent==='Facebook'?'Your story':'This content has no text';
            if(tab.textContent==='Facebook') document.querySelector('aside').innerHTML='""" + FB_PREVIEW + """';
        """)

    async def test_direct_story_relation_completes_both_channels_without_inventing_facebook_caption(self):
        self.linked_facebook()
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual(len(result.cards), 2)
        fb = next(c for c in result.cards if c.channels == ('facebook',))
        ig = next(c for c in result.cards if c.channels == ('instagram',))
        self.assertEqual(dict(fb.remote_ids), {'facebook': FB_ID})
        self.assertEqual(dict(ig.remote_ids), {'instagram': SOURCE_ID})
        self.assertEqual((fb.caption_status, fb.rendered, fb.delivery), ('unknown', '', 'published'))
        self.assertEqual((ig.caption_status, ig.rendered), ('empty', ''))
        self.assertEqual(result.occupied_for_channel('facebook'), (fb.at,))
        self.assertEqual(result.occupied_for_channel('instagram'), (ig.at,))
        self.assertEqual(matching(result, fb.at, 'Your Story', ('facebook',)), [])
        self.assertEqual(len(self.context.pages), 1)

    async def test_facebook_has_its_own_publication_minute(self):
        self.linked_facebook()
        info = self.payload['data']['tofu_entity']['entity_info']['cross_posted_entities'][0]['entity_info']
        info['created_at'] += 60
        self.detail_html = self.detail_html.replace("caption.textContent=", """
            metadata.textContent='Story · Published on: Fri Sep 4, '+(tab.textContent==='Facebook'?'6:40pm':'6:39pm');
            caption.textContent=""")
        result = await self.inventory()
        self.assertTrue(result.decision_complete)
        self.assertEqual({c.channels[0]: c.at.minute for c in result.cards}, {'instagram':39, 'facebook':40})

    async def test_inconsistent_facebook_evidence_keeps_instagram_and_blocks_decisions(self):
        for invalid in ['owner', 'time', 'channel', 'duplicate_relation', 'unlinked',
                        'preview_owner', 'preview_frame']:
            with self.subTest(invalid=invalid):
                self.payload = copy.deepcopy(ENTITY)
                self.linked_facebook()
                related = self.payload['data']['tofu_entity']['entity_info']['cross_posted_entities']
                if invalid == 'owner':
                    related[0]['entity_info']['owner']['entity_info']['title'] = 'Other account'
                elif invalid == 'time':
                    related[0]['entity_info']['created_at'] += 3600
                elif invalid == 'channel':
                    self.detail_html = self.detail_html.replace("'+tab.textContent+'", "'+('Instagram')+'")
                elif invalid == 'duplicate_relation':
                    related.append({**copy.deepcopy(FB_ENTITY), 'entity_id': '1781315906229403'})
                elif invalid == 'preview_owner':
                    self.detail_html = self.detail_html.replace(
                        '<span>' + ACCOUNTS['facebook'] + '</span>', '<span>Other account</span>')
                elif invalid == 'preview_frame':
                    # The Instagram preview left in place is not the Facebook view,
                    # however long the Facebook tab has been selected.
                    self.detail_html = self.detail_html.replace(
                        '<h3>Feed preview</h3><div>', '<h3>Feed preview</h3><div id=instagram_story_preview_frame>')
                else:
                    # A standalone FB object cannot be associated by response arrival.
                    related[0] = {'entity_info': {'__typename': 'TofuFBStoryEntityInfo'}}
                    self.payload['unrelated'] = copy.deepcopy(FB_ENTITY)
                result = await self.inventory()
                self.assertFalse(result.decision_complete)
                self.assertEqual([dict(c.remote_ids) for c in result.cards if c.read_status == 'complete'],
                                 [{'instagram': SOURCE_ID}])

    async def test_native_instagram_identity_survives_unresolved_facebook_crosspost(self):
        result = await self.inventory()
        verified = [card for card in result.cards if card.read_status == 'complete']
        self.assertEqual(len(verified), 1)
        card = verified[0]
        self.assertEqual(dict(card.remote_ids), {'instagram': SOURCE_ID})
        self.assertEqual(dict(card.accounts), {'instagram': 'neakasa.de'})
        self.assertEqual(card.at.isoformat(), '2026-09-04T18:39:00+08:00')
        self.assertEqual((card.placement, card.caption_status, card.rendered), ('story','empty',''))
        self.assertEqual(card.relationships, ('cross_platform',))
        self.assertEqual(card.source_content_id, SOURCE_ID)
        self.assertEqual(len(result.cards), 2)
        self.assertTrue(result.diagnostics)
        self.assertFalse(result.decision_complete)
        self.assertFalse(any('1068553422207259' in dict(c.remote_ids).values() for c in result.cards))
        with self.assertRaises(ProbeRequired):
            result.occupied_for_channel('instagram')
        self.assertEqual(len(self.context.pages), 1)

    async def test_another_media_or_permalink_owner_cannot_supply_instagram_identity(self):
        for field,value in [('id','19999999999999'),
                            ('permalink','https://www.instagram.com/stories/another.account/3978703119637304396')]:
            with self.subTest(field=field):
                self.payload = copy.deepcopy(ENTITY)
                self.payload['data']['tofu_entity']['entity_info']['ig_media'][field] = value
                result = await self.inventory()
                self.assertFalse(any(c.read_status == 'complete' for c in result.cards))
                self.assertFalse(result.decision_complete)

    async def test_selected_tab_cannot_accept_a_stale_other_channel_header(self):
        self.detail_html = DETAIL.replace("platforms.innerHTML='<img alt=\"'+tab.textContent+'\">';",
            "platforms.innerHTML='<img alt=\"Facebook\">';")
        result = await self.inventory()
        self.assertFalse(any(c.read_status == 'complete' for c in result.cards))
        self.assertFalse(result.decision_complete)

    async def test_root_title_must_match_visible_caption_not_just_channel_selection(self):
        self.payload['data']['tofu_entity']['entity_info']['title'] = 'Different caption'
        result = await self.inventory()
        self.assertFalse(any(c.read_status == 'complete' for c in result.cards))
        self.assertFalse(result.decision_complete)

    async def test_delayed_instagram_tab_keeps_the_verified_variant(self):
        self.detail_html = DETAIL.replace("fetch('/api/graphql/',{method:'POST'});", """
          const ig=document.querySelectorAll('[role=tab]')[2];ig.remove();
          setTimeout(()=>document.body.append(ig),700);
          fetch('/api/graphql/',{method:'POST'});""")
        result = await self.inventory()
        self.assertEqual([dict(c.remote_ids) for c in result.cards if c.read_status=='complete'],
                         [{'instagram':SOURCE_ID}])
        self.assertFalse(result.decision_complete)

    async def test_preview_loading_and_changed_channel_time_cannot_be_accepted(self):
        for extra in ["<h2>Loading preview</h2>",
                      "<script>const previous=select;select=tab=>{previous(tab);metadata.textContent='Story · Published on: Fri Sep 4, 6:40pm'}</script>"]:
            with self.subTest(extra=extra):
                self.detail_html = DETAIL + extra
                result = await self.inventory()
                self.assertFalse(any(c.read_status=='complete' for c in result.cards))

    async def test_verify_reader_uses_one_temporary_detail_and_keeps_the_original_tab(self):
        await self.page.goto(f'https://business.facebook.com/latest/insights/object_insights/?content_id={SOURCE_ID}')
        self.requests.clear()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            with patch.object(month, 'accounts', return_value=ACCOUNTS):
                complete=await probe.inspect(self.browser,day=date(2026,9,4),clock=time(18,39),
                                             kind='Story',timeout=5,verify_reader=True)
        self.assertFalse(complete)
        rows=[json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
        result=next(row['READER_RESULT'] for row in rows if 'READER_RESULT' in row)
        self.assertEqual(result['variants'][0]['remote_ids'],{'instagram':SOURCE_ID})
        self.assertEqual(result['variants'][0]['caption_length'],0)
        self.assertIn('facebook_story_identity',result['missing_fields'])
        self.assertEqual(len([url for url in self.requests if '/object_insights/' in url]),1)
        self.assertEqual(len(self.context.pages),1)
        self.assertEqual(await self.page.get_by_role('tab',selected=True).inner_text(),'Total performance')


MEDIA_ID = '18015951041945821'
COLLABORATORS = 'neakasa.global in collaboration with neakasa.tech and neakasa.de'
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


class PublishedMediaTests(unittest.IsolatedAsyncioTestCase):
    """A collaborator's published post occupies the slot under its own account."""

    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.context = await self.browser.new_context()
        self.payload = copy.deepcopy(MEDIA_PAYLOAD)
        self.detail = MEDIA.replace('{caption}', 'Day 1 at IFA').replace('{label}', 'Post') \
            .replace('{platform}', 'Instagram').replace('{preview}', 'neakasa.global') \
            .replace('{extra}', ' in collaboration with neakasa.tech and neakasa.de')
        async def route(request):
            url = request.request.url
            if '/api/graphql/' in url:
                await request.fulfill(content_type='application/json', body=json.dumps(self.payload))
            elif '/object_insights/' in url:
                await request.fulfill(content_type='text/html; charset=utf-8', body=self.detail)
            else:
                await request.fulfill(content_type='text/html', body='<html></html>')
        await self.context.route('**/*', route)
        self.page = await self.context.new_page()
        await self.page.goto('https://business.facebook.com/latest/content_calendar')
        days = calendar.Calendar(firstweekday=6).itermonthdates(2026, 9)
        cells = ''.join(f'<div role="link" draggable="false"><span>{d.day}</span>' + (
            f'<a href="/latest/insights/object_insights/?content_id={MEDIA_ID}">11:48 AM</a>'
            if d == date(2026,9,5) else '') + '</div>' for d in days)
        await self.page.set_content('<h1>September</h1><h1>2026</h1>' + cells)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def inventory(self):
        with patch.object(month, 'prepare', AsyncMock()), patch.object(month, 'accounts', return_value=ACCOUNTS):
            return await month.read(self.page, ui_timezone='Asia/Shanghai', business_timezone='Asia/Shanghai', timeout=5)

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
        for invalid in ['no_media_response', 'other_media_id', 'preview_author', 'viewer_only', 'platform']:
            with self.subTest(invalid=invalid):
                self.payload = copy.deepcopy(MEDIA_PAYLOAD)
                if invalid == 'no_media_response':
                    self.payload = {'data': {}}
                elif invalid == 'other_media_id':
                    self.payload['data']['instagram_post']['id'] = '17999999999999999'
                elif invalid == 'preview_author':
                    self.detail = self.detail.replace(
                        '<span id="caption-author">neakasa.global</span>', '<span id="caption-author">someone.else</span>')
                elif invalid == 'viewer_only':
                    # The signed-in viewer is not the author of this media.
                    self.payload['data'].pop('instagram_post')
                else:
                    self.detail = self.detail.replace('alt="Instagram"', 'alt="Facebook"')
                result = await self.inventory()
                self.assertFalse(result.decision_complete)
                self.assertFalse(any(c.read_status == 'complete' for c in result.cards))

    async def test_late_channel_tabs_are_reported_instead_of_a_single_channel_reading(self):
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

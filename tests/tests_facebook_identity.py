"""Synthetic Facebook responses: identity evidence, fragment merging and archive handoff."""
import asyncio
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.capture import download_media
from core.parse import extract, merge_post, partition_by_owner
from core.store import Archive
from core.capture import Collector
from core.monitor_access import AccessController
from routes import backfill, delta
from pipeline import engine
from image_fixtures import image_bytes


ACTOR_ID = '61591381265280'
ACCOUNT = 'neakasaofficial'
PROFILE = 'https://www.facebook.com/neakasaofficial'


def actor(url=None, identifier=ACTOR_ID):
    return {'id': identifier, 'name': 'Neakasa Official', 'url': url}


def story(identifier='122127190695379375', author=None, media=True):
    return {
        'post_id': identifier, 'creation_time': 1789537430,
        'message': {'text': 'Fall sale'},
        'url': PROFILE + '/posts/pfbidFixture',
        'actors': [author] if author is not None else [],
        'attachments': [{'media': {'__typename': 'Photo', 'id': 'photo1',
            'image': {'uri': 'https://cdn.invalid/photo.png', 'width': 20, 'height': 20}}}] if media else [],
    }


def parse(payloads):
    return extract(copy.deepcopy(payloads), 'facebook', ACCOUNT, 'backfill')


class FacebookIdentityTests(unittest.TestCase):
    def test_batch_actor_alias_resolves_numeric_and_missing_urls_in_both_orders(self):
        for url in (None, 'https://www.facebook.com/profile.php?id=' + ACTOR_ID,
                    'https://www.facebook.com/' + ACTOR_ID + '/'):
            for reverse in (False, True):
                with self.subTest(url=url, reverse=reverse):
                    payloads = [story(author=actor(url)), story('other-post', actor(PROFILE))]
                    posts = parse(payloads[::-1] if reverse else payloads)
                    kept, rejected = partition_by_owner(posts, ACCOUNT)
                    self.assertEqual(rejected, [])
                    self.assertEqual({p.owner for p in kept}, {ACCOUNT})
                    self.assertEqual(len(kept[0].media), 1)

    def test_typed_profile_can_supply_alias_without_another_story(self):
        posts = parse([story(author=actor()), {'data': {'node': {
            '__typename': 'Page', 'id': ACTOR_ID, 'url': PROFILE + '/?locale=en_US'}}}])
        self.assertEqual(partition_by_owner(posts, ACCOUNT)[1], [])
        self.assertEqual(posts[0].owner, ACCOUNT)

    def test_supplied_profile_message_action_binds_the_numeric_owner(self):
        # Minimized from the user's capture prefix; the story itself is synthetic.
        profile = json.loads((Path(__file__).parent / 'fixtures' / 'facebook_profile_identity.json').read_text('utf-8'))
        posts = parse([profile, story(author=actor())])
        self.assertEqual(partition_by_owner(posts, ACCOUNT)[1], [])
        self.assertEqual(posts[0].owner, ACCOUNT)
        self.assertTrue(any(e.get('url') == 'https://www.facebook.com/messages/t/neakasaofficial/'
                            for e in posts[0].owner_evidence))

    def test_message_url_only_binds_an_explicit_profile_action_owner(self):
        for payload in (
            {'__typename': 'ProfileActionMessage', 'profile_owner': {'id': '999'},
             'uri': 'https://www.facebook.com/messages/t/neakasaofficial/'},
            {'__typename': 'ProfileActionMessage', 'profile_owner': {'id': ACTOR_ID},
             'uri': 'https://other.invalid/messages/t/neakasaofficial/'},
            {'profile_owner': {'id': ACTOR_ID}, 'uri': 'https://www.facebook.com/messages/t/neakasaofficial/'},
        ):
            self.assertEqual(partition_by_owner(parse([payload, story(author=actor())]), ACCOUNT)[0], [])

    def test_same_post_merge_keeps_named_identity_and_complete_images(self):
        for reverse in (False, True):
            fragments = [story(author=actor(PROFILE), media=False), story(author=actor())]
            if reverse:
                fragments.reverse()
            # Separate extract calls reproduce homepage/detail response boundaries.
            result = merge_post(parse([fragments[0]])[0], parse([fragments[1]])[0])
            self.assertEqual(result.owner, ACCOUNT)
            self.assertEqual([m.url for m in result.media], ['https://cdn.invalid/photo.png'])
            self.assertEqual(partition_by_owner([result], ACCOUNT)[1], [])

    def test_unproven_ids_names_permalinks_and_foreign_urls_do_not_authorize(self):
        for author in (actor(), actor(identifier='999'), actor('https://other.invalid/neakasaofficial'), None):
            with self.subTest(author=author):
                posts = parse([story(author=author)])
                self.assertEqual(partition_by_owner(posts, ACCOUNT)[0], [])
        # An unrelated object with id/url is not an actor or typed profile.
        posts = parse([story(author=actor()), {'id': ACTOR_ID, 'url': PROFILE}])
        self.assertEqual(partition_by_owner(posts, ACCOUNT)[0], [])

    def test_embedded_collaborators_survive_projection_without_secrets(self):
        raw = story(author=actor('https://www.facebook.com/partner.page', '90000000000001'))
        raw['collaborators'] = [{'id': ACTOR_ID, 'name': 'Neakasa Official',
                                 'url': PROFILE, 'access_token': 'fixture-secret'}]
        raw['comet_sections'] = {'context_layout': {'story': {'token': 'fixture-secret',
            'comet_sections': {'title': {'story': {'collaborators': [
                {'id': ACTOR_ID, 'url': PROFILE}]}}}}}}
        collector = Collector()
        collector.add_document_json([json.dumps({'data': {'bootstrap': {
            'DTSGInitialData': {'token': 'fixture-secret'}}, 'node': raw}})])
        posts = extract(collector.payloads, 'facebook', ACCOUNT, 'delta')
        kept, rejected = partition_by_owner(posts, ACCOUNT)
        self.assertEqual(rejected, [])
        self.assertEqual(kept[0].owner, 'partner.page')
        self.assertEqual(kept[0].coauthors, [ACCOUNT])
        self.assertNotIn('fixture-secret', json.dumps(collector.payloads))

    def test_external_actor_is_not_overridden_by_target_post_link(self):
        posts = parse([story(author=actor('https://www.facebook.com/external', '999')),
                       story('target', actor(PROFILE))])
        kept, rejected = partition_by_owner(posts, ACCOUNT)
        self.assertEqual([p.post_id for p in kept], ['target'])
        self.assertEqual(rejected[0]['owner'], 'external')

    def test_conflicting_author_evidence_stays_rejected_after_richer_fragment(self):
        for conflict in (actor('https://www.facebook.com/external'), actor(PROFILE, '999'),
                         actor('https://www.facebook.com/profile.php?id=999')):
            for reverse in (False, True):
                fragments = [story(author=actor(PROFILE)), story(author=conflict, media=False)]
                if reverse:
                    fragments.reverse()
                merged = merge_post(parse([fragments[0]])[0], parse([fragments[1]])[0])
                merged = merge_post(merged, parse([story(author=actor(PROFILE))])[0])
                kept, rejected = partition_by_owner([merged], ACCOUNT)
                self.assertEqual(kept, [])
                self.assertEqual(rejected[0]['reason'], 'owner_conflict')

    def test_alias_does_not_leak_into_a_later_capture(self):
        parse([story(author=actor(PROFILE))])
        self.assertEqual(partition_by_owner(parse([story(author=actor())]), ACCOUNT)[0], [])

    def test_scroll_progress_reuses_alias_and_revisits_earlier_unresolved_posts(self):
        for reverse in (False, True):
            payloads = [story(author=actor()), story('second', actor(PROFILE))]
            if reverse:
                payloads.reverse()
            progress = backfill.ScrollProgress('facebook', ACCOUNT)
            progress.update(payloads[:1])
            progress.update(payloads)
            self.assertEqual(progress.ids, {'122127190695379375', 'second'})

    def test_detail_merges_identity_before_filtering_and_refuses_conflicts(self):
        for identifier in (ACTOR_ID, '999'):
            with self.subTest(identifier=identifier), tempfile.TemporaryDirectory() as temporary:
                arc = Archive(Path(temporary) / 'archive', 'fa_' + ACCOUNT)
                original = parse([story(author=actor(PROFILE), media=False)])[0]
                original.source_media_complete = original.media_complete = False
                detail = Collector()
                detail.payloads = [story(author=actor(identifier=identifier))]
                access = AccessController(Path(temporary) / 'state',
                                          clock=lambda: datetime(2026, 9, 18, 2, tzinfo=timezone.utc))
                access.initialize('isolated fixture')
                access.reserve_homepage('facebook', 'identity-detail')
                config = delta.DeltaConfig(access=access, scan_id='identity-detail', first_screen_seconds=0)
                response = SimpleNamespace(status=200, ok=True, headers={'content-type': 'image/png'},
                                           body=AsyncMock(return_value=image_bytes(format='PNG')))
                ctx = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=response)))
                if identifier == '999':
                    arc.record_rejected([{'post_id': original.post_id, 'owner': 'id:' + ACTOR_ID,
                                          'reason': 'owner_mismatch', 'expected_owner': ACCOUNT}])
                with patch.object(delta, 'scan_page', AsyncMock(return_value=(detail, original.permalink))):
                    result = asyncio.run(delta.capture_post(ctx, 'facebook', ACCOUNT, arc, config, original))
                if identifier == ACTOR_ID:
                    self.assertEqual(result.owner, ACCOUNT)
                    self.assertTrue(result.media_complete)
                    self.assertEqual(len(result.media), 1)
                    self.assertTrue((arc.base / result.media[0].local_path).is_file())
                else:
                    self.assertFalse(result.media_complete)
                    self.assertEqual(ctx.request.get.await_count, 0)
                    self.assertTrue(config.source_failures)
                    rejections = [json.loads(line) for line in (arc.base / '_rejected.jsonl').read_text('utf-8').splitlines()]
                    self.assertEqual([r['reason'] for r in rejections], ['owner_mismatch', 'owner_conflict'])
                    self.assertTrue(any(e.get('id') == '999' for e in rejections[-1]['owner_evidence']))
                    self.assertEqual(arc.record_rejected([rejections[-1]]), 0)

    def test_archive_keeps_identity_evidence_images_and_video_metadata(self):
        video = story('video-post', actor(PROFILE))
        video['attachments'] = [{'media': {'__typename': 'Video', 'id': 'video123'}}]
        posts, rejected = partition_by_owner(parse([story(author=actor()), video]), ACCOUNT)
        self.assertEqual(rejected, [])
        response = SimpleNamespace(status=200, ok=True, headers={'content-type': 'image/png'},
                                   body=AsyncMock(return_value=image_bytes(format='PNG')))
        ctx = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=response)))
        with tempfile.TemporaryDirectory() as temporary:
            arc = Archive(Path(temporary), 'fa_' + ACCOUNT)
            for post in posts:
                asyncio.run(download_media(ctx, arc, post, PROFILE))
                self.assertTrue(arc.append(post))
            rows = {row['post_id']: row for row in arc.rows()}
            saved = rows['122127190695379375']
            self.assertEqual(saved['owner'], ACCOUNT)
            self.assertTrue(any(e.get('id') == ACTOR_ID and e.get('url') == PROFILE
                                for e in saved['owner_evidence']))
            self.assertTrue((arc.base / saved['media'][0]['local_path']).is_file())
            self.assertEqual(rows['video-post']['media'][0]['kind'], 'video')
            self.assertIsNone(rows['video-post']['media'][0]['local_path'])
            self.assertEqual(ctx.request.get.await_count, 1)
            self.assertEqual(json.loads((arc.post_dir(posts[0]) / 'post.json').read_text('utf-8'))['owner'], ACCOUNT)
            source = engine.SourcePost('facebook', arc.base, saved,
                                       datetime(2026, 9, 16, tzinfo=timezone.utc), 'facebook:' + saved['post_id'])
            self.assertIsNone(engine.prepaid_issue(
                engine.Candidate(source, (source,), 'independent'), engine.publish_rules()))


if __name__ == '__main__':
    unittest.main()

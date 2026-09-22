"""Semantic fixtures, not a recording of Meta DOM or evidence of live compatibility."""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish import planner_content as content
from publish.business_suite import RemotePlannerCard, RemoteSlotInventory, ProbeRequired


class ContentTests(unittest.TestCase):
    def observation(self, **changes):
        return dict(metadata='Story · Published on: Fri Sep 4, 6:39pm',
                    caption='This content has no text', channel='instagram',
                    owner='neakasa.de', remote_id='1804155825688886',
                    media_kind='unknown', **changes)

    def parse(self, observation):
        return content.classify_published(observation, date(2026, 9, 4),
            {'facebook': 'Neakasa Deutschland', 'instagram': 'neakasa.de'})

    def test_textless_story_is_empty_content_not_a_loading_failure_or_placeholder_caption(self):
        value = self.parse(self.observation())
        self.assertEqual(value['placement'], 'story')
        self.assertEqual(value['caption_status'], 'empty')
        self.assertEqual(value['text'], '')
        self.assertEqual(value['delivery'], 'published')
        self.assertEqual(value['ui_at'], datetime(2026, 9, 4, 18, 39))

    def test_media_form_and_placement_are_independent_and_unknown_labels_are_not_feed(self):
        for label, placement, form in [('Post','feed','image'), ('Post','feed','carousel'),
                                       ('Post','feed','mixed'), ('Reel','reel','video'),
                                       ('Video','feed','video'), ('Live','live','video')]:
            obs = self.observation()
            obs.update(metadata=f'{label} · Published on: Fri Sep 4, 6:39pm',
                       caption='Caption', media_kind=form)
            value = self.parse(obs)
            self.assertEqual((value['placement'], value['media_kind']), (placement, form))
        obs = self.observation()
        obs['metadata'] = 'New format · Published on: Fri Sep 4, 6:39pm'
        value = self.parse(obs)
        self.assertEqual(value['placement'], 'unknown')
        self.assertEqual(value['read_status'], 'incomplete')

    def test_empty_feed_is_explicit_and_missing_caption_remains_missing(self):
        obs = self.observation()
        obs['metadata'] = 'Post · Published on: Fri Sep 4, 6:39pm'
        self.assertEqual(self.parse(obs)['text'], '')
        obs['caption'] = None
        value = self.parse(obs)
        self.assertEqual(value['caption_status'], 'unknown')
        self.assertEqual(value['read_status'], 'incomplete')
        self.assertEqual(value['channels'], ('instagram',))

    def test_aggregate_icons_cannot_supply_channels_or_copy_an_id(self):
        obs = self.observation()
        obs.update(channel='', platforms=['Facebook','Instagram'])
        with self.assertRaises(content.DetailReadError) as error:
            self.parse(obs)
        self.assertEqual(error.exception.code, 'identity_unverified')

    def test_facebook_your_story_title_is_not_caption_or_empty_caption_proof(self):
        obs = self.observation()
        obs.update(caption='Your Story', channel='facebook', owner='Neakasa Deutschland')
        with self.assertRaises(content.DetailReadError) as error:
            self.parse(obs)
        self.assertIn('caption', error.exception.missing_fields)
        obs['story_entity_verified'] = True
        value = self.parse(obs)
        self.assertEqual((value['text'], value['caption_status'], value['read_status']), ('', 'unknown', 'complete'))

    def test_collaborator_and_caption_mentions_cannot_replace_the_publishing_owner(self):
        obs = self.observation()
        obs.update(owner='neakasa.global', caption='neakasa.de', collaborators=['neakasa.de'])
        with self.assertRaises(content.DetailReadError) as error:
            self.parse(obs)
        self.assertEqual(error.exception.code, 'identity_mismatch')

    def test_wrong_date_and_missing_remote_id_remain_explicit_failures(self):
        obs = self.observation()
        obs['metadata'] = 'Story · Published on: Thu Sep 3, 6:39pm'
        with self.assertRaises(content.DetailReadError) as error:
            self.parse(obs)
        self.assertEqual(error.exception.code, 'time_mismatch')
        obs = self.observation()
        obs['remote_id'] = ''
        with self.assertRaises(content.DetailReadError) as error:
            self.parse(obs)
        self.assertIn('remote_id', error.exception.missing_fields)

    def test_known_story_keeps_same_channel_gap_and_unknown_read_cannot_authorize_slots(self):
        at = datetime(2026,9,4,10,39,tzinfo=timezone.utc)
        card = RemotePlannerCard(at, ('instagram',), (('instagram','1804155825688886'),),
                                 '', '', 'published', placement='story', caption_status='empty')
        inventory = RemoteSlotInventory((at,), 'Asia/Shanghai', date(2026,8,30),date(2026,10,3),(card,),True)
        self.assertEqual(inventory.occupied_for_channel('instagram'), (at,))
        self.assertTrue(inventory.decision_complete)
        unknown = RemotePlannerCard(at, read_status='unsupported')
        partial = RemoteSlotInventory((at,), 'Asia/Shanghai', date(2026,8,30),date(2026,10,3),(unknown,),True)
        self.assertTrue(partial.cards_loaded)
        self.assertFalse(partial.classification_complete)
        self.assertFalse(partial.decision_complete)
        with self.assertRaises(ProbeRequired):
            partial.occupied_for_channel('instagram')


if __name__ == '__main__':
    unittest.main()

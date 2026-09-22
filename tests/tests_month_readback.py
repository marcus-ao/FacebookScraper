"""The production submission path retains grid dates and exact full captions."""
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publish import business_suite as bs, month_inventory, month_readback

WHEN = datetime(2026, 9, 15, 18, tzinfo=timezone.utc)
TEXT = 'Langer Text mit #Neakasa und https://de.example.test\n' + 'Vollständig bleiben. ' * 100


class ReadbackTests(unittest.IsolatedAsyncioTestCase):
    def inventory(self, cards):
        return bs.RemoteSlotInventory(tuple(c.at for c in cards), 'UTC',
                                      WHEN.date(), WHEN.date(), tuple(cards), True)

    def card(self, text=TEXT, channel='facebook', delivery='scheduled'):
        return bs.RemotePlannerCard(WHEN, (channel,), ((channel, '123456789'),), text, 'hash', delivery, placement='feed')

    async def readback(self, inventory, **kwargs):
        with patch.object(month_inventory, 'read', AsyncMock(return_value=inventory)), \
                patch.object(bs, '_readback_screenshot', AsyncMock(return_value='')):
            return await month_readback.verify(None, WHEN, TEXT, ui_timezone='UTC',
                target_channels=('facebook',), expected_image_count=5, **kwargs)

    async def test_production_baseline_uses_structured_grid_dates_and_remote_identity(self):
        with patch.object(month_inventory, 'read', AsyncMock(return_value=self.inventory([self.card()]))):
            baseline = await month_readback.baseline(None, WHEN, TEXT, ui_timezone='UTC',
                                                           target_channels=('facebook',))
        self.assertEqual(baseline.match_count, 1)
        self.assertEqual(baseline.remote_ids, ('facebook=123456789',))

    async def test_complete_caption_and_channel_match_with_images_explicitly_unverified(self):
        result = await self.readback(self.inventory([self.card()]))
        self.assertTrue(result.found)
        self.assertTrue(result.diagnostics['full_caption_equal'])
        self.assertFalse(result.diagnostics['remote_images_verified'])
        self.assertIsNone(result.image_count)
        for card in (self.card(TEXT[:100]), self.card('Added text ' + TEXT),
                     self.card(channel='instagram'), self.card(delivery='published')):
            self.assertFalse((await self.readback(self.inventory([card]))).found)

    async def test_preexisting_remote_id_or_duplicate_cards_cannot_prove_new_submission(self):
        baseline = bs.ScheduledBaseline('observed', 0, ('facebook=123456789',))
        self.assertFalse((await self.readback(self.inventory([self.card()]), pre_submit_baseline=baseline)).found)
        self.assertFalse((await self.readback(self.inventory([self.card()]), expected_remote_id='facebook=other')).found)
        self.assertFalse((await self.readback(self.inventory([self.card(), self.card()]))).found)

    async def test_truncated_caption_failure_keeps_relevant_diagnostics(self):
        result = await self.readback(self.inventory([self.card(TEXT[:100])]))
        self.assertFalse(result.found)
        self.assertEqual(result.diagnostics['caption_mismatch'], 1)
        self.assertEqual(result.diagnostics['expected_caption_length'], len(bs._card_text(TEXT)))
        self.assertEqual(result.diagnostics['samples'][0]['text_length'], len(TEXT[:100]))
        self.assertEqual(result.diagnostics['samples'][0]['text_preview'], TEXT[:100])

    async def test_partial_month_or_unidentified_channel_is_not_a_zero_baseline(self):
        unknown = bs.RemotePlannerCard(WHEN)
        with patch.object(month_inventory, 'read', AsyncMock(return_value=self.inventory([unknown]))):
            with self.assertRaises(bs.PublishStepError):
                await month_readback.baseline(None, WHEN, TEXT, ui_timezone='UTC',
                                                    target_channels=('facebook',))

    async def test_existing_match_missing_id_and_uncovered_time_cannot_certify_submission(self):
        inventory = self.inventory([self.card()])
        baseline = month_readback.baseline_from_inventory(inventory, WHEN, TEXT, ('facebook',))
        self.assertFalse((await self.readback(inventory, pre_submit_baseline=baseline)).found)
        self.assertFalse((await self.readback(self.inventory([replace(self.card(), remote_ids=())]))).found)
        for incomplete in (replace(inventory, visible_start=None),
                           replace(inventory, visible_end=WHEN.date().replace(day=14))):
            self.assertFalse((await self.readback(incomplete)).found)
            with self.assertRaises(bs.PublishStepError):
                month_readback.baseline_from_inventory(incomplete, WHEN, TEXT, ('facebook',))

    async def test_diagnostics_distinguish_time_and_channel_and_bound_samples(self):
        cards = [replace(self.card(), at=WHEN.replace(hour=19)), self.card(channel='instagram')]
        cards.extend(self.card('Wrong caption') for _ in range(30))
        result = await self.readback(self.inventory(cards))
        self.assertFalse(result.found)
        self.assertEqual(result.diagnostics['time_mismatch'], 1)
        self.assertEqual(result.diagnostics['channel_mismatch'], 1)
        self.assertEqual(result.diagnostics['caption_mismatch'], 30)
        self.assertEqual(len(result.diagnostics['samples']), 20)

    async def test_month_reader_preserves_both_dst_occurrences_and_rejects_missing_hour(self):
        material = {'channels': ('facebook',), 'remote_ids': {'facebook': '123456789'},
                    'text': TEXT, 'delivery': 'scheduled', 'placement': 'feed'}
        for date, clock, expected in ((datetime(2026, 11, 1).date(), '1:00 AM', 2),
                                      (datetime(2026, 3, 8).date(), '2:30 AM', None)):
            rows = [{'date': date, 'items': [{'index': 0, 'time': clock}]}]
            with patch.object(month_inventory, 'prepare', AsyncMock()), \
                    patch.object(month_inventory, 'read_grid', AsyncMock(return_value=rows)), \
                    patch.object(month_inventory, 'read_item', AsyncMock(return_value=material)):
                call = month_inventory.read(None, ui_timezone='America/Los_Angeles',
                                            business_timezone='America/Los_Angeles')
                if expected is None:
                    with self.assertRaises(bs.PublishStepError):
                        await call
                else:
                    inventory = await call
                    self.assertEqual(len(inventory.occupied_for_channel('facebook')), 2)
                    self.assertEqual(inventory.occupied[1].timestamp() - inventory.occupied[0].timestamp(), 3600)


if __name__ == '__main__':
    unittest.main()

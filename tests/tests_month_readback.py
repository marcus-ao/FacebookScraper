"""The production submission path retains grid dates and exact full captions."""
import sys
import unittest
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


if __name__ == '__main__':
    unittest.main()

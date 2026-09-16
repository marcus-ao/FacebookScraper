"""Submission causality from the same complete month inventory as conflict checks."""
import hashlib
from datetime import datetime

from publish import business_suite as bs, month_inventory


def matching(inventory, when, final_text, target_channels):
    if not inventory.cards_loaded or not inventory.channels_complete or not inventory.covers((when,)):
        raise bs.PublishStepError('月历范围或渠道未读完整，不能核验本次排期')
    expected = bs._card_text(final_text)
    if not expected:
        raise bs.PublishStepError('待核验文案为空')
    # Bind grid dates to detail captions and IDs before matching time-only links.
    return [card for card in inventory.cards
            if card.delivery == 'scheduled' and card.at.timestamp() == when.timestamp()
            and card.channels == target_channels and bs._card_text(card.rendered) == expected]


def remote_id(card):
    ids = dict(card.remote_ids)
    if set(ids) != set(card.channels) or any(not ids[channel] for channel in card.channels):
        raise bs.PublishStepError('排期详情缺少各目标渠道独立的远端 ID')
    return ';'.join(channel + '=' + ids[channel] for channel in card.channels)


async def baseline(page, when, final_text, *, ui_timezone, target_channels, timeout=30):
    inventory = await month_inventory.read(page, ui_timezone=ui_timezone,
                                          business_timezone=bs.business_timezone(), timeout=timeout)
    cards = matching(inventory, when, final_text, target_channels)
    return bs.ScheduledBaseline(datetime.now().astimezone().isoformat(), len(cards),
                                tuple(remote_id(card) for card in cards),
                                tuple(card.card_sha256 for card in cards))


async def verify(page, when, final_text, *, ui_timezone, target_channels,
                 expected_image_count=None, pre_submit_baseline=None, expected_remote_id='',
                 timeout=30, screenshot_path=None):
    stamp = datetime.now().astimezone().isoformat()
    base = dict(observed_at=stamp, target_at=when.isoformat(),
                ui_at=when.astimezone(bs.resolve_ui_timezone(ui_timezone)).isoformat(),
                final_text_sha256=hashlib.sha256(final_text.encode('utf-8')).hexdigest(),
                expected_image_count=expected_image_count)
    diagnostics = {'expected_caption_length': len(bs._card_text(final_text)),
                   'remote_images_verified': False, 'failure_stage': 'pre_submit_baseline'}
    try:
        if pre_submit_baseline is not None and pre_submit_baseline.match_count:
            raise bs.PublishStepError('提交前已有同条件排期，不能用旧卡片确认本次提交')
        diagnostics['failure_stage'] = 'inventory_read'
        inventory = await month_inventory.read(page, ui_timezone=ui_timezone,
                                              business_timezone=bs.business_timezone(), timeout=timeout)
        diagnostics.update(inventory_cards=len(inventory.cards),
                           complete_month=inventory.cards_loaded and inventory.channels_complete and inventory.covers((when,)),
                           failure_stage='matching', caption_mismatch=0, time_mismatch=0,
                           channel_mismatch=0, delivery_mismatch=0)
        samples = []
        for candidate in inventory.cards:
            text_equal = bs._card_text(candidate.rendered) == bs._card_text(final_text)
            time_equal = candidate.at.timestamp() == when.timestamp()
            channel_equal = candidate.channels == target_channels
            diagnostics['caption_mismatch'] += int(time_equal and channel_equal and not text_equal)
            diagnostics['time_mismatch'] += int(text_equal and channel_equal and not time_equal)
            diagnostics['channel_mismatch'] += int(time_equal and text_equal and not channel_equal)
            diagnostics['delivery_mismatch'] += int(time_equal and text_equal and channel_equal and candidate.delivery != 'scheduled')
            samples.append({'at': candidate.at.isoformat(), 'channels': list(candidate.channels),
                            'delivery': candidate.delivery, 'text_length': len(candidate.rendered),
                            'text_preview': candidate.rendered[:200], 'caption_matches': text_equal,
                            'time_matches': time_equal, 'channel_matches': channel_equal})
        diagnostics['samples'] = sorted(samples, key=lambda row: not (row['time_matches'] or row['caption_matches']))[:20]
        cards = matching(inventory, when, final_text, target_channels)
        diagnostics['matched_entries'] = len(cards)
        if len(cards) != 1:
            raise bs.PublishStepError('完整月历中未找到唯一的同渠道、同时间、完整正文排期详情')
        card = cards[0]
        diagnostics['failure_stage'] = 'remote_identity'
        identity = remote_id(card)
        if expected_remote_id and identity != expected_remote_id:
            raise bs.PublishStepError('提交成功信号与月历排期的远端 ID 不一致')
        if pre_submit_baseline is not None and identity in pre_submit_baseline.remote_ids:
            raise bs.PublishStepError('月历命中的是提交前已存在的远端 ID')
        shot = await bs._readback_screenshot(page, screenshot_path, timeout)
        diagnostics.update(failure_stage=None, full_caption_equal=True)
        return bs.ScheduledReadback(found=True, **base, channels=card.channels,
            remote_id=identity, card_sha256=card.card_sha256, screenshot=shot,
            success_signal='planner_complete_month_and_scheduled_detail',
            diagnostics=diagnostics)
    except Exception as exc:
        shot = await bs._readback_screenshot(page, screenshot_path, timeout)
        return bs.ScheduledReadback(found=False, **base, screenshot=shot, error=str(exc), diagnostics=diagnostics)

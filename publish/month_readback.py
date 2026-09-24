"""Submission causality from a live month inventory scoped to the target time."""
import hashlib
from dataclasses import replace
from datetime import datetime

from core.config import cfg
from publish import business_suite as bs, month_inventory, scheduled_media, snapshots
from publish.channel_evidence import accounts


def matching(inventory, when, final_text, target_channels):
    # 网格对齐不证明目标卡片详情；按目标时刻核验，范围外的详情缺口不参与本次回读。
    if len(target_channels) != 1:
        raise bs.PublishStepError('排期回读必须且只能有一个目标渠道')
    try:
        relevant = inventory.cards_in_range(target_channels[0], when, when)
    except bs.ProbeRequired as exc:
        raise bs.PublishStepError('月历范围或相关条目未读完整，不能核验本次排期') from exc
    if any(card.read_status != 'complete' or card.placement == 'unknown'
           or card.delivery == 'unknown' or card.caption_status == 'unknown' for card in relevant):
        raise bs.PublishStepError('目标时刻的详情未完整，不能当作没有旧卡')
    expected = bs._card_text(final_text)
    if not expected:
        raise bs.PublishStepError('待核验文案为空')
    # Bind grid dates to detail captions and IDs before matching time-only links.
    matched = [card for card in relevant
            if card.delivery == 'scheduled' and card.placement == 'feed' and card.at.timestamp() == when.timestamp()
            and card.channels == target_channels and bs._card_text(card.rendered) == expected]
    expected_accounts = accounts()
    if any(dict(card.accounts) != {channel: expected_accounts[channel] for channel in target_channels}
           for card in matched):
        raise bs.PublishStepError('排期详情账号与本次目标不一致或缺失')
    return matched


def remote_id(card):
    ids = dict(card.remote_ids)
    if set(ids) != set(card.channels) or any(not ids[channel] for channel in card.channels):
        raise bs.PublishStepError('排期详情缺少各目标渠道独立的远端 ID')
    return ';'.join(channel + '=' + ids[channel] for channel in card.channels)


def baseline_from_inventory(inventory, when, final_text, target_channels):
    """用已经读到的整月结果做提交前基线，避免紧接着再读一遍。"""
    cards = matching(inventory, when, final_text, target_channels)
    return bs.ScheduledBaseline(datetime.now().astimezone().isoformat(), len(cards),
                                tuple(remote_id(card) for card in cards),
                                tuple(card.card_sha256 for card in cards))


async def baseline(page, when, final_text, *, ui_timezone, target_channels, timeout=30, run=None):
    inventory = await month_inventory.read(page, ui_timezone=ui_timezone,
                                          business_timezone=bs.business_timezone(), timeout=timeout, run=run)
    return baseline_from_inventory(inventory, when, final_text, target_channels)


async def verify(page, when, final_text, *, ui_timezone, target_channels,
                 expected_image_count=None, pre_submit_baseline=None, expected_remote_id='',
                 timeout=30, screenshot_path=None, run=None, frozen_attempt=None, verify_images=False):
    """Confirm the scheduled object; extra image evidence is an explicit read-only revisit."""
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
                                              business_timezone=bs.business_timezone(), timeout=timeout, run=run)
        diagnostics.update(inventory_cards=len(inventory.cards),
                           complete_month=inventory.decision_complete and inventory.covers((when,)),
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
            raise bs.PublishStepError('目标时刻未找到唯一的同渠道、同时间、完整正文排期详情')
        card = cards[0]
        diagnostics['failure_stage'] = 'remote_identity'
        identity = remote_id(card)
        if expected_remote_id and identity != expected_remote_id:
            raise bs.PublishStepError('提交成功信号与月历排期的远端 ID 不一致')
        if pre_submit_baseline is not None and identity in pre_submit_baseline.remote_ids:
            raise bs.PublishStepError('月历命中的是提交前已存在的远端 ID')
        diagnostics['remote_media'] = {'image_count': None, 'order_verified': False,
                                       'error': 'frozen_context_missing' if verify_images else 'not_requested'}
        if verify_images and frozen_attempt is not None:
            try:
                metadata, _, files, directory = snapshots.load_for_attempt(frozen_attempt)
                if (files['text_de.txt'].decode('utf-8') != final_text
                        or snapshots.require_bound(metadata) != when
                        or tuple(frozen_attempt['target_channels']) != target_channels):
                    raise bs.PublishStepError('取图参数与冻结 attempt 不一致')
                paths = [directory / name for name in metadata['images']]
                if expected_image_count is not None and expected_image_count != len(paths):
                    raise bs.PublishStepError('期望图数与冻结图片不一致')
                base['expected_image_count'] = len(paths)
            except Exception:
                diagnostics['remote_media']['error'] = 'frozen_snapshot_invalid'
            else:
                async def observe(dialog):
                    # Pure image failure must not erase the independently verified scheduled fact.
                    try:
                        return await scheduled_media.collect(dialog, timeout=timeout)
                    except Exception:
                        return scheduled_media.MediaCapture((), {}, error='media_read_failed')
                diagnostics['failure_stage'] = 'media_target_identity'
                capture = await month_inventory.read_scheduled_target(page, card, ui_timezone=ui_timezone,
                    timeout=timeout, card_spec=run.planner_card if run is not None else None,
                    observe_detail=observe)
                binding = scheduled_media.binding_for(dict(frozen_attempt, remote_id=identity))
                try:
                    capture = replace(capture, structure={**capture.structure, 'target': {
                        'accounts': dict(card.accounts), 'remote_ids': dict(card.remote_ids),
                        'scheduled_at': card.at.isoformat(), 'ui_at': base['ui_at'],
                        'final_text_sha256': base['final_text_sha256'],
                        'card_sha256': card.card_sha256, 'method': 'reacquired_scheduled_dialog'}})
                    diagnostics.update(scheduled_media.verify_capture(capture, paths,
                        cfg().state_dir / 'publish_attempts' / 'remote_media', binding))
                except Exception:
                    diagnostics['remote_media']['error'] = 'media_verification_failed'
        shot = await bs._readback_screenshot(page, screenshot_path, timeout)
        diagnostics.update(failure_stage=None, full_caption_equal=True)
        return bs.ScheduledReadback(found=True, **base, channels=card.channels,
            remote_id=identity, card_sha256=card.card_sha256, screenshot=shot,
            image_count=diagnostics['remote_media'].get('image_count'),
            success_signal='planner_target_range_and_scheduled_detail',
            diagnostics=diagnostics)
    except Exception as exc:
        shot = await bs._readback_screenshot(page, screenshot_path, timeout)
        return bs.ScheduledReadback(found=False, **base, screenshot=shot, error=str(exc), diagnostics=diagnostics)


def verification_text(readback):
    if not readback.found:
        return '本次未能重新核实原排期对象；保留历史排期事实和防重，请人工核对。'
    prefix = '目标时刻、完整最终正文、账号和唯一渠道已从内容日历回读；'
    if readback.diagnostics.get('remote_images_verified') is True:
        return prefix + '已核验排期详情 %d 张图片的数量、顺序及视觉对应，证据已保全。' % readback.image_count
    reason = (readback.diagnostics.get('remote_media') or {}).get('error') or 'media_unverified'
    if reason == 'not_requested':
        return prefix + '图片未做额外远端核验，可另行只读复验。'
    return prefix + '排期详情图片未核验（%s），G8 尚未通过。' % reason

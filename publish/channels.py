"""Choose exactly one evidenced feed channel and verify the target before submission."""
from __future__ import annotations

from publish.business_suite import ProbeRequired
from publish import channel_evidence as controls


def require_independent_channel_evidence(target_channels: tuple[str, ...]) -> None:
    """附着前核验独立渠道、账号、表单及资产证据；历史双选记录不能替代。"""
    if len(target_channels) != 1 or target_channels[0] not in {"facebook", "instagram"}:
        raise ProbeRequired("每篇来源只允许发布到对应的一个渠道；请重新生成独立审校项。")
    controls.require(target_channels[0])


async def select(page, target_channels, *, timeout=30):
    require_independent_channel_evidence(target_channels)
    channel = target_channels[0]
    controls.assert_context(page, channel)
    names = controls.accounts()
    chosen = await controls.selected(page, timeout=timeout)
    if not chosen[channel]:
        await page.get_by_role('option', name=names[channel], exact=True).click(timeout=timeout * 1000)
    for other in names:
        if other == channel:
            continue
        chosen = await controls.selected(page, timeout=timeout)
        if chosen[other]:
            await page.get_by_role('option', name=names[other], exact=True).click(timeout=timeout * 1000)
    chosen = await controls.selected(page, timeout=timeout)
    if chosen != {name: name == channel for name in names}:
        raise ProbeRequired('渠道选择回读不一致；没有上传或提交')
    await page.keyboard.press('Escape')
    story = page.get_by_role('switch', name=controls.STORY, exact=True)
    if await story.count() == 1 and await story.get_attribute('aria-checked') == 'true':
        await story.click(timeout=timeout * 1000)
        dialog = page.get_by_role('dialog', name='Stop sharing to Facebook Story', exact=True)
        await dialog.wait_for(state='visible', timeout=timeout * 1000)
        only_this = dialog.get_by_role('radio', name="Don't share this post", exact=True)
        if await only_this.get_attribute('aria-checked') != 'true':
            raise ProbeRequired('关闭 Story 的选项不是仅此帖，请人工核对')
        await dialog.get_by_role('button', name='Confirm', exact=True).click(timeout=timeout * 1000)
        await dialog.wait_for(state='hidden', timeout=timeout * 1000)
    for name in (controls.STORY, controls.THREADS, 'Boost', 'Make this an ad post'):
        switch = page.get_by_role('switch', name=name, exact=True)
        if await switch.count() == 1 and await switch.get_attribute('aria-checked') != 'false':
            raise ProbeRequired('附加发布或广告开关尚未关闭，请人工核对：' + name)
    controls.assert_context(page, channel)
    return {'channel': channel, 'account': names[channel], 'selection_verified': True}


async def verify_before_submit(page, target_channels):
    require_independent_channel_evidence(target_channels)
    controls.assert_context(page, target_channels[0])
    return await controls.observe(page, target_channels[0])

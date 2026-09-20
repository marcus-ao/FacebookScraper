r"""由 tools._scaffolding.probe_signals --emit 生成，勿手改。
来源：publish_probe_20260920_100921_378853.json；生成时间：2026-09-19T20:31:17-07:00。
"""
from __future__ import annotations

from publish.locator_types import EvidenceSignal, Locator

LOCATORS: dict[str, Locator] = {item.key: item for item in (
    Locator(
        key='composer_submit_button',
        step='G6 提交（推导自 v2 可信交互）',
        surface='business.facebook.com/latest/composer',
        role='button',
        name='Schedule',
        name_source='visible-text',
        source_dump='publish_probe_20260920_100921_378853.json',
        sequences=(205,),
        breaks_when='按钮改名（Schedule / Veröffentlichen 随界面语言走）或 role 从 button 变成别的',
        inferred='按因果位置 + 标签措辞推导：账号上下文之后、composer 上标签最像定时提交的那一次可信点击。',
        attributes={
            'tag': 'div',
        },
        evidence_kind='interaction',
    ),
)}

SIGNALS: dict[str, EvidenceSignal] = {item.key: item for item in (
    EvidenceSignal(
        key='composer_account_context',
        step='G2 目标主页核对（推导自 v2 被动语义）',
        kind='semantic',
        surface='business.facebook.com/latest/composer',
        source_dump='publish_probe_20260920_100921_378853.json',
        sequences=(17,),
        breaks_when='composer 预览不再显示目标主页显示名；或主页改名（那时应改 [publish].facebook_page_name）',
        role='heading',
        name='Neakasa Deutschland',
        url_prefix='',
        attributes={
            'facebook_account_token': 'Neakasa Deutschland',
            'facebook_account_regex': '@?(?P<account>.+)',
            'instagram_not_provable_here': '本信号只核验 FB 预览；IG 须独立核验渠道与回读',
        },
    ),
    EvidenceSignal(
        key='composer_success_signal',
        step='G6 提交成功（推导自 v2 被动语义）',
        kind='semantic',
        surface='business.facebook.com/latest/composer',
        source_dump='publish_probe_20260920_100921_378853.json',
        sequences=(136,),
        breaks_when='成功提示改文案、改 role，或不再经过 aria-live 区域宣告',
        role='heading',
        name='Your post is scheduled',
        url_prefix='',
        attributes={},
    ),
    EvidenceSignal(
        key='planner_scheduled_card',
        step='G6c 排期回读（推导自 v2 被动语义）',
        kind='semantic',
        surface='business.facebook.com/latest/content_calendar',
        source_dump='publish_probe_20260920_100921_378853.json',
        sequences=(141, 180, 184),
        breaks_when='日历条目不再把正文与完整时刻放在同一条可访问名里；或详情弹窗不再显示 `ID: <数字>` 与渠道标记',
        role='link',
        name='This is a manual test.',
        url_prefix='',
        attributes={
            'date_format': '%B %d, %Y',
            'time_format': '%I:%M %p',
            'datetime_regex': '(?P<date>[A-Z][a-z]{2,8} \\d{1,2}, \\d{4})\\D{0,10}?(?P<time>\\d{1,2}:\\d{2} [AaPp][Mm])',
            'entry_role': 'link',
            'entry_probe_text': 'This is a manual test.',
            'dialog_role': 'dialog',
            'dialog_name': 'Post details',
            'remote_id_regex': 'ID:\\s*(?P<remote_id>\\d{6,})',
            'facebook_marker': "Facebook's Feed",
            'facebook_account_token': 'Neakasa Deutschland',
            'instagram_marker': 'Instagram feed',
            'instagram_account_token': 'neakasa.de',
            'instagram_detail_basis': 'shared_facebook_structure',
            'visible_month_role': 'heading',
            'visible_month_format': '%B',
            'visible_year_role': 'heading',
            'visible_year_format': '%Y',
        },
    ),
    EvidenceSignal(
        key='planner_loaded_signal',
        step='G6c Planner 数据就绪（推导自 v2 被动语义）',
        kind='semantic',
        surface='business.facebook.com/latest/content_calendar',
        source_dump='publish_probe_20260920_100921_378853.json',
        sequences=(141,),
        breaks_when="日历不再渲染月份标题，或它在数据到达前就出现了（那样它证明不了'数据已就绪'）",
        role='heading',
        name='September',
        url_prefix='',
        attributes={},
    ),
)}

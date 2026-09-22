"""审校台单篇提交的本次运行条件。不读取历史录证文件。"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from core.config import cfg
from publish import business_suite as bs
from publish.selectors import COMPOSER, SIGNALS

_CARD_ATTRS = (
    "datetime_regex", "remote_id_regex", "dialog_role", "dialog_name",
    "facebook_marker", "instagram_marker",
)


class ManualRunError(bs.PublishStepError):
    """单篇提交在打开浏览器之前就能确定的运行条件问题。"""


@dataclass(frozen=True)
class ManualRun:
    """一次确认过的目标。共享函数收到它就按它执行，不退回录证文件。"""

    channel: str
    account: str
    asset_id: str
    business_id: str
    ui_timezone: str
    submit_button: object
    success_signal: object
    planner_card: object

    @property
    def asset_context(self) -> dict:
        return {"asset_id": self.asset_id, "business_id": self.business_id}

    def target(self) -> dict:
        return {"channel": self.channel, "account": self.account,
                "asset_id": self.asset_id, "business_id": self.business_id}


def _account(channel: str) -> str:
    key = "facebook_page_name" if channel == "facebook" else "instagram_account"
    value = str(cfg().get("publish", key, "") or "").strip()
    if not value:
        raise ManualRunError("发布目标账号未配置：[publish].%s" % key)
    return value


def load(channel: str) -> ManualRun:
    """用当前配置和已实现的控件组装本次运行条件。"""
    if channel not in {"facebook", "instagram"}:
        raise ManualRunError("每篇仅接受一个来源对应的发布渠道")
    try:
        cfg().assert_publish_chrome_isolated()
    except SystemExit as exc:
        raise ManualRunError("发布浏览器身份未隔离：" + str(exc)) from exc
    account = _account(channel)
    asset_id = str(cfg().get("publish", "asset_id", "") or "").strip()
    business_id = str(cfg().get("publish", "business_id", "") or "").strip()
    missing = [name for name, value in (("asset_id", asset_id), ("business_id", business_id))
               if not re.fullmatch(r"\d+", value)]
    if missing:
        raise ManualRunError("提交前缺少业务资产配置：" + "、".join(
            "[publish].%s" % name for name in missing))
    ui_timezone = str(cfg().get("publish", "ui_timezone", "") or "").strip()
    if not ui_timezone:
        raise ManualRunError("[publish].ui_timezone 为空；无法核对排期时刻")
    try:
        bs.resolve_ui_timezone(ui_timezone)
    except (ValueError, bs.PublishStepError, bs.ProbeRequired) as exc:
        raise ManualRunError(str(exc)) from exc
    button = COMPOSER.get("composer_submit_button")
    success = SIGNALS.get("composer_success_signal")
    card = SIGNALS.get("planner_scheduled_card")
    if (button is None or success is None or card is None or not button.role or not button.name
            or not success.role or not success.name):
        raise ManualRunError("已实现的提交或回读控件不完整，不能创建排期")
    missing_attrs = [key for key in _CARD_ATTRS if not (card.attributes or {}).get(key)]
    if missing_attrs:
        raise ManualRunError("已实现的回读控件缺少：" + "、".join(missing_attrs))
    # 页面上的账号必须对当前配置，不能对录制当时写进信号里的显示名。
    attributes = dict(card.attributes)
    attributes["facebook_account_token"] = str(cfg().get("publish", "facebook_page_name", "") or "").strip()
    attributes["instagram_account_token"] = str(cfg().get("publish", "instagram_account", "") or "").strip()
    return ManualRun(channel, account, asset_id, business_id, ui_timezone, button, success,
                     replace(card, attributes=attributes))


def confirm_target(confirmed, run: ManualRun) -> None:
    """弹窗确认的目标必须和提交时重新读取的配置一致。"""
    expected = run.target()
    if not isinstance(confirmed, dict) or any(str(confirmed.get(key) or "") != value
                                              for key, value in expected.items()):
        raise ManualRunError("发布目标已变化，请重新确认")

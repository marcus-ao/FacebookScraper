"""渠道选择的证据边界。真实勾选控件尚未录入，不使用默认双发兜底。"""
from __future__ import annotations

from publish.business_suite import ProbeRequired


def require_independent_channel_evidence(target_channels: tuple[str, ...]) -> None:
    """在附着浏览器之前拒绝没有实测依据的单渠道操作。

    当前已录制的 dump 只有默认双选及 FB 预览，不能证明取消另一渠道、
    单渠道目标账号和排期控件之间的关系。补录后在这一边界实现选择与回读；
    不能只修改 journal 中的 target_channels 就宣称完成渠道选择。
    """
    if len(target_channels) != 1 or target_channels[0] not in {"facebook", "instagram"}:
        raise ProbeRequired("每篇来源只允许发布到对应的一个渠道；请重新生成独立审校项。")
    raise ProbeRequired(
        "单渠道发布尚缺真实勾选证据：请录制 FB/IG 渠道切换、目标账号显示名及"
        "单渠道排期回读。当前没有打开浏览器，也没有使用默认双渠道发布。")

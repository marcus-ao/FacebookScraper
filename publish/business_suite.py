"""Business Suite UI 操作契约（G2–G6 的 G1 前骨架）。

这些函数故意在接触 ``page`` 前失败。G1 的真实 DOM、时区、定时窗口与成功信号
尚未记录，当前实现任何点击/填写都会违反项目的“不得猜选择器”红线。
G7 目前只保留在任务书中，不声称已有代码骨架。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


class ProbeRequired(RuntimeError):
    """G1 尚未完成，真实 UI 操作必须保持关闭。"""


def _blocked(step: str) -> ProbeRequired:
    return ProbeRequired(
        "%s 尚未实现：先由用户运行 tools/probe_publish.py 完成 G1，"
        "再根据 state/publish_probe_<时间戳>.json 回填真实定位；不得猜选择器。"
        % step)


async def ensure_logged_in(page) -> bool:
    """确认已登录且当前选中的 Page/IG 账号都正确；不得自动登录。"""
    raise _blocked("G2 登录态与目标 Page 检查")


async def upload_images(page, paths: list[Path]) -> None:
    """上传图片并回读缩略图数量；不使用固定 sleep。"""
    raise _blocked("G3 图片上传")


async def fill_caption(page, text: str) -> None:
    """填写文案并逐字符回读；换行策略由 G1 的真实控件类型决定。"""
    raise _blocked("G4 文案填写")


async def set_schedule(page, when: datetime) -> None:
    """显式转换到 Page UI 时区并回读；时区必须来自 G1。"""
    raise _blocked("G5 定时设置")


async def submit(page) -> str | None:
    """等待 G1 确认的成功信号；失败绝不自动删除草稿。"""
    raise _blocked("G6 提交与结果确认")

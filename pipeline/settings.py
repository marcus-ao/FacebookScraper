r"""`[pipeline]` 配置的严格读取。**这个模块只认识 config.toml，不认识流水线。**

它被单独拆出来，是因为有三个不同高度的调用方都要它：

- ``pipeline/cli.py``（L 组 CLI）—— 最顶层，读 autonomy / dead_man_days；
- ``pipeline/engine.py``（执行引擎）—— 预算判据要读日/月上限；
- 各阶段 CLI 的 ``main()`` 组装 ``RequestController`` 时经由上面那条。

放回 ``cli.py`` 会让引擎反过来 import CLI，那就是一条环。
放进 ``core/config.py`` 又不合适：那里是通用配置层，不该知道 `[pipeline]`
这一段的业务语义。所以它自己成一层：**只依赖 core.config，谁都能用。**
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from core.config import cfg

# `[pipeline]` 的全部合法键。CR-40 立的规矩：不许有改了不生效的死旋钮，
# 也不许有拼错了却静默被忽略的键。所以这张表是双向的 ——
# 表外的键报错，表里标 False 的键在 status 里显式说明"还没接上"。
PIPELINE_CONFIG_KEYS: dict[str, bool] = {
    "autonomy": True,
    "dead_man_days": True,         # ← cli.py 的 check-alive 就在消费它
    "monthly_budget_usd": True,
    "daily_budget_usd": True,
}
AUTONOMY_LEVELS = ("manual", "assisted", "supervised", "autonomous")


class PipelineConfigError(RuntimeError):
    """`[pipeline]` 配置本身不合法。失败闭合，不猜默认值。"""


def pipeline_settings(raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """严格读 `[pipeline]`；未知键、错类型都失败闭合。

    形状照抄 ``localize_images.Settings.validate`` 的双向对账，
    但这里只有四个键，不值得为它建一个类。
    """
    if raw is None:
        raw = cfg()._d.get("pipeline", {}) or {}
    if not isinstance(raw, Mapping):
        raise PipelineConfigError("[pipeline] 不是一张表")

    unknown = sorted(set(raw) - set(PIPELINE_CONFIG_KEYS))
    if unknown:
        raise PipelineConfigError(
            "[pipeline] 有代码不认识的键：" + "、".join(unknown)
            + "\n    （拼错的键会静默失效，所以这里直接拒绝；"
              "真要加新键，请同时改 pipeline/settings.py 的 PIPELINE_CONFIG_KEYS）")
    missing = sorted(set(PIPELINE_CONFIG_KEYS) - set(raw))
    if missing:
        raise PipelineConfigError(
            "[pipeline] 缺少必需键：" + "、".join(missing)
            + "\n    （config.toml 里那一整段是 L 组的骨架，不要删键）")

    autonomy = raw["autonomy"]
    if autonomy not in AUTONOMY_LEVELS:
        raise PipelineConfigError(
            "[pipeline].autonomy 必须是 %s 之一，实得 %r"
            % ("/".join(AUTONOMY_LEVELS), autonomy))

    days = raw["dead_man_days"]
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise PipelineConfigError(
            "[pipeline].dead_man_days 必须是 >=1 的整数，实得 %r" % (days,))

    budgets = {}
    for key in ("monthly_budget_usd", "daily_budget_usd"):
        value = raw[key]
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(float(value)) or value < 0):
            raise PipelineConfigError(
                "[pipeline].%s 必须是非负数，实得 %r" % (key, value))
        budgets[key] = float(value)

    return {"autonomy": autonomy, "dead_man_days": days, **budgets}

"""严格读取 [pipeline] 配置，仅依赖 core.config。"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from core.config import cfg

# 支持的键及其接入状态；未知键拒绝，未接入项在状态中说明。
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
    """校验 pipeline 配置；未知键或错类型报错。"""
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

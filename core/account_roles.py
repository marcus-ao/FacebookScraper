"""品牌自有账号的两种角色：来源只认当前监测目标，合作者并入付费信任。"""
from __future__ import annotations

from typing import Mapping

PLATFORMS = ("facebook", "instagram")


class AccountRoleError(ValueError):
    """品牌名单或冻结名单写坏，或监测目标指到了已冻结账号。"""


def collaborator_trust(trusted: Mapping, brand: Mapping, platform: str) -> frozenset[str]:
    """付费闸与「是否第三方」共用的信任集合。第三方仍只来自 trusted。"""
    return _as_names(trusted, platform) | _as_names(brand, platform)


def lenient_names(table, platform: str) -> set[str]:
    """告警口径。⚠️ 写坏时当空集，不能在监测轮次里抛。"""
    if not isinstance(table, Mapping):
        return set()
    owners = table.get(platform, [])
    if not isinstance(owners, list):
        return set()
    return {item.strip().lower() for item in owners if isinstance(item, str) and item.strip()}


def validate_roles(publish, targets) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """付费入口的结构校验。缺表当空；写坏或目标落在冻结名单上则报错。"""
    if not isinstance(publish, Mapping):
        raise AccountRoleError("[publish] 不是表")
    if not isinstance(targets, Mapping):
        raise AccountRoleError("[targets] 必须是表")
    brand = _strict_table(publish, "brand_accounts")
    frozen = _strict_table(publish, "frozen_sources")
    for platform in PLATFORMS:
        outside = frozen[platform] - brand[platform]
        if outside:
            names = "、".join(sorted(outside))
            raise AccountRoleError(
                "[publish.frozen_sources].%s 的 %s 不在 [publish.brand_accounts].%s 中"
                % (platform, names, platform))
        account = str(targets.get(platform) or "").strip().lower()
        if account and account in frozen[platform]:
            raise AccountRoleError(freeze_message(platform, account))
    return brand, frozen


def frozen_target_message(config) -> str | None:
    """监测和回填在打开浏览器前调用。名单读不出来时不在这里中止轮次。"""
    try:
        getter = getattr(config, "get", None)
        frozen_raw = getter("publish", "frozen_sources", {}) if callable(getter) else {}
        targets = config["targets"]
    except (AttributeError, KeyError, TypeError):
        return None
    if not isinstance(targets, Mapping):
        return None
    for platform in PLATFORMS:
        account = str(targets.get(platform) or "").strip().lower()
        if account and account in lenient_names(frozen_raw, platform):
            return freeze_message(platform, account)
    return None


def freeze_message(platform: str, account: str) -> str:
    return ("[targets].%s 的 %s 已在 [publish.frozen_sources] 中冻结，"
            "不能作为抓取来源。它仍可留在 [publish.brand_accounts] 中作为合作者。"
            % (platform, account))


def _strict_table(publish: Mapping, key: str) -> dict[str, frozenset[str]]:
    if key not in publish or publish.get(key) is None:
        return {platform: frozenset() for platform in PLATFORMS}
    table = publish.get(key)
    if not isinstance(table, Mapping):
        raise AccountRoleError("[publish.%s] 必须是表" % key)
    parsed: dict[str, frozenset[str]] = {}
    for platform in PLATFORMS:
        owners = table.get(platform, [])
        if not isinstance(owners, list) or not all(
                isinstance(owner, str) and owner.strip() for owner in owners):
            raise AccountRoleError("[publish.%s].%s 必须是非空字符串数组" % (key, platform))
        parsed[platform] = frozenset(owner.strip().lower() for owner in owners)
    return parsed


def _as_names(table: Mapping, platform: str) -> frozenset[str]:
    if not isinstance(table, Mapping):
        return frozenset()
    value = table.get(platform, ())
    if isinstance(value, (set, frozenset)):
        return frozenset(str(item).strip().lower() for item in value if str(item).strip())
    if isinstance(value, list):
        return frozenset(item.strip().lower() for item in value
                         if isinstance(item, str) and item.strip())
    return frozenset()

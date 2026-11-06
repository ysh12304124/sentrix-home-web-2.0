"""地点候选与确定归属。place_text_matches保留粗召回线索；place_admin_matches要求完整查询由已确定行政层级组成。附近居民点和上位区县不能证明具体道路或店铺。"""

from __future__ import annotations

from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.models import Place

# 1.0 参与第 2 条规则的槽位。``country`` 不在其中：拿国名去匹配会命中一切。
_ADMIN_KEYS = ("province", "city", "district")


def admin_text_matches(value: str, admin: tuple[str, str, str], aliases: PlaceAliases) -> bool:
    """只有整个查询由已确定的行政层级组成，才作为确定归属。"""
    remaining = value.strip().casefold()
    if not remaining:
        return False
    names = {part.casefold() for part in admin if part}
    names.update(aliases.strip_admin_suffix(part).casefold() for part in admin if part)
    names = {name for name in names if len(name) >= 2}
    # 别名只替换完整行政名，不把“清迈某酒店”整体缩成“清迈”。
    for key, targets in aliases.table.items():
        if any(target.casefold() in names for target in targets):
            names.add(key.casefold())
    matched = False
    while remaining:
        hit = next(
            (name for name in sorted(names, key=len, reverse=True) if remaining.startswith(name)), None
        )
        if hit is None:
            return False
        matched = True
        remaining = remaining[len(hit) :].lstrip(" ·,，/")
    return matched


def place_admin_matches(value: str, place: Place | None, aliases: PlaceAliases) -> bool:
    return place is not None and admin_text_matches(
        value, (place.admin.province, place.admin.city, place.admin.district), aliases
    )


def place_label(place: Place) -> str:
    """参与第 1 条规则的整串。与 1.0 的 ``label`` 拼接范围一致（含国名）。"""
    parts = [
        place.admin.province,
        place.admin.city,
        place.admin.district,
        place.nearest.township,
        place.nearest.locality,
    ]
    return " ".join(part for part in parts if part)


def place_text_matches(value: str, place: Place | None, aliases: PlaceAliases) -> bool:
    """地点约束 ``value`` 是否被这次解析结果支持。

    ``place`` 为空一律返回 False：1.0 在这一步返回 False 之后，调用方
    (``MetadataRetriever``) 把「没有 geocode」当成「预筛通过」放行，于是一个纯地点
    查询会把整个 scope 灌进 metadata 通道。2.0 把「没解析出地点」与「地点不匹配」
    分开表达——前者由调用方显式决定要不要放行，不再藏在这个布尔值里。
    """
    text = str(value or "").strip()
    if not text or place is None:
        return False

    label = place_label(place)
    if not label:
        return False
    if text in label:
        return True

    for key in _ADMIN_KEYS:
        part = aliases.strip_admin_suffix(getattr(place.admin, key, ""))
        if len(part) >= 2 and part in text:
            return True

    lower_label = label.lower()
    return any(alias.lower() in lower_label for alias in aliases.names_for(text))

"""Typed item property-set shapes used by the Items desk editor.

Managed kinds (weapon, armor, container, money) get checkbox + tab editors.
Other tags and blocks remain in the free-form Other JSON pane.
"""

from __future__ import annotations

import json

# Canonical kind tag == block key for each managed set.
MANAGED_KINDS = ("weapon", "armor", "container", "money")
# Legacy aliases folded into a canonical kind on load/save.
KIND_ALIASES = {
    "currency": "money",
}

DEFAULT_WEAPON = {
    "category": "",
    "damage": "",
    "properties": [],
    "range": "melee",
}

DEFAULT_ARMOR = {
    "category": "",
    "ac": "",
    "stealth_disadvantage": False,
    "strength": None,
}

DEFAULT_CONTAINER = {
    "capacity": "",
    "role": "",
}

DEFAULT_MONEY = {
    "value": "",
}


def _as_dict(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _as_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, str) and tag.strip()]


def canonical_kind_tag(tag: str) -> str:
    return KIND_ALIASES.get(tag, tag)


def kind_enabled(properties: dict | None, kind: str) -> bool:
    props = properties or {}
    tags = {canonical_kind_tag(tag) for tag in normalize_tags(props.get("tags"))}
    if kind in tags:
        return True
    if kind in props and isinstance(props.get(kind), dict):
        return True
    # Legacy currency block/tag → money.
    if kind == "money":
        if "currency" in props and isinstance(props.get("currency"), dict):
            return True
    return False


def weapon_from_properties(properties: dict | None) -> dict:
    block = _as_dict((properties or {}).get("weapon"))
    return {
        "category": str(block.get("category") or ""),
        "damage": str(block.get("damage") or ""),
        "properties": _as_str_list(block.get("properties")),
        "range": str(block.get("range") or "melee"),
    }


def armor_from_properties(properties: dict | None) -> dict:
    block = _as_dict((properties or {}).get("armor"))
    strength = block.get("strength", None)
    if strength is None or strength == "":
        strength_value = None
    else:
        strength_value = strength
    return {
        "category": str(block.get("category") or ""),
        "ac": str(block.get("ac") or ""),
        "stealth_disadvantage": bool(block.get("stealth_disadvantage", False)),
        "strength": strength_value,
    }


def container_from_properties(properties: dict | None) -> dict:
    block = _as_dict((properties or {}).get("container"))
    return {
        "capacity": str(block.get("capacity") or ""),
        "role": str(block.get("role") or ""),
    }


def money_from_properties(properties: dict | None) -> dict:
    props = properties or {}
    block = _as_dict(props.get("money"))
    if not block:
        block = _as_dict(props.get("currency"))
    return {
        "value": str(block.get("value") or ""),
    }


def other_properties(properties: dict | None) -> dict:
    """Properties excluding managed kind blocks; tags drop managed/alias labels."""
    props = dict(properties or {})
    for key in MANAGED_KINDS:
        props.pop(key, None)
    props.pop("currency", None)
    tags = [
        tag
        for tag in normalize_tags(props.get("tags"))
        if canonical_kind_tag(tag) not in MANAGED_KINDS
    ]
    if tags:
        props["tags"] = tags
    else:
        props.pop("tags", None)
    return props


def parse_weapon_properties_text(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if "," in text:
        parts = text.split(",")
    else:
        parts = text.split()
    return [part.strip() for part in parts if part.strip()]


def format_weapon_properties(properties: list[str]) -> str:
    return ", ".join(properties)


def parse_strength_text(raw: str):
    text = (raw or "").strip()
    if not text or text.casefold() in {"none", "null"}:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def format_strength(value) -> str:
    if value is None:
        return ""
    return str(value)


def assemble_properties(
    *,
    is_weapon: bool = False,
    is_armor: bool = False,
    is_container: bool = False,
    is_money: bool = False,
    weapon: dict | None = None,
    armor: dict | None = None,
    container: dict | None = None,
    money: dict | None = None,
    other: dict | None = None,
) -> dict:
    """Build the stored properties object from kind editors + Other JSON."""
    props = dict(other or {})
    if not isinstance(props, dict):
        raise ValueError("Other properties must be a JSON object.")
    tags = [
        tag
        for tag in normalize_tags(props.get("tags"))
        if canonical_kind_tag(tag) not in MANAGED_KINDS
    ]
    props.pop("currency", None)

    if is_weapon:
        tags.append("weapon")
        block = dict(DEFAULT_WEAPON)
        block.update(_as_dict(weapon))
        block["properties"] = _as_str_list(block.get("properties"))
        props["weapon"] = block
    else:
        props.pop("weapon", None)

    if is_armor:
        tags.append("armor")
        block = dict(DEFAULT_ARMOR)
        block.update(_as_dict(armor))
        block["stealth_disadvantage"] = bool(block.get("stealth_disadvantage", False))
        if block.get("strength", None) in ("",):
            block["strength"] = None
        props["armor"] = block
    else:
        props.pop("armor", None)

    if is_container:
        tags.append("container")
        block = dict(DEFAULT_CONTAINER)
        block.update(_as_dict(container))
        props["container"] = block
    else:
        props.pop("container", None)

    if is_money:
        tags.append("money")
        block = dict(DEFAULT_MONEY)
        block.update(_as_dict(money))
        props["money"] = block
    else:
        props.pop("money", None)

    if tags:
        props["tags"] = tags
    else:
        props.pop("tags", None)
    return props


def properties_to_json(properties: dict | None) -> str:
    data = properties if isinstance(properties, dict) else {}
    if not data:
        return "{}"
    return json.dumps(data, ensure_ascii=False, indent=2)


def properties_from_json(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Property sets must be valid JSON ({exc}).") from exc
    if not isinstance(data, dict):
        raise ValueError("Property sets must be a JSON object.")
    return data

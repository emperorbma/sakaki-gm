"""Deterministic 5e-style checks resolved from bottle data (no LLM math).

The model may decide that a check is needed and propose ``roll_check``.
This module computes modifiers, rolls, and success from character JSON,
skills, and resolved weapon/armor property sets.
"""

from __future__ import annotations

import random
import re
from typing import Any


ABILITY_NAMES = ("str", "dex", "con", "int", "wis", "cha")

_DAMAGE_RE = re.compile(
    r"(?P<count>\d+)\s*d\s*(?P<sides>\d+)\s*(?P<mod>[+-]\s*\d+)?",
    re.IGNORECASE,
)
_AC_FLAT_RE = re.compile(r"^\s*\+?\s*(?P<value>-?\d+)\s*$")
_AC_DEX_RE = re.compile(
    r"^\s*(?P<base>\d+)\s*\+\s*Dex(?:\s*\(\s*max\s*(?P<cap>\d+)\s*\))?\s*$",
    re.IGNORECASE,
)


def ability_modifier(score) -> int | None:
    try:
        return (int(score) - 10) // 2
    except (TypeError, ValueError):
        return None


def roll_die(sides: int, *, rng: random.Random | None = None) -> int:
    if sides < 1:
        raise ValueError("Die sides must be >= 1")
    roller = rng or random
    return roller.randint(1, sides)


def roll_d20(
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    rng: random.Random | None = None,
) -> dict:
    """Roll a d20, optionally with advantage/disadvantage."""
    roller = rng or random
    first = roller.randint(1, 20)
    if advantage and not disadvantage:
        second = roller.randint(1, 20)
        chosen = max(first, second)
        return {
            "rolls": [first, second],
            "value": chosen,
            "mode": "advantage",
        }
    if disadvantage and not advantage:
        second = roller.randint(1, 20)
        chosen = min(first, second)
        return {
            "rolls": [first, second],
            "value": chosen,
            "mode": "disadvantage",
        }
    return {"rolls": [first], "value": first, "mode": "normal"}


def parse_damage_expression(text: str) -> dict | None:
    """Parse ``1d8+2 piercing``-style expressions into dice parts."""
    if not isinstance(text, str) or not text.strip():
        return None
    match = _DAMAGE_RE.search(text)
    if not match:
        return None
    count = int(match.group("count"))
    sides = int(match.group("sides"))
    mod_raw = match.group("mod")
    modifier = int(re.sub(r"\s+", "", mod_raw)) if mod_raw else 0
    remainder = (text[: match.start()] + text[match.end() :]).strip(" ,;")
    damage_type = remainder or None
    return {
        "count": count,
        "sides": sides,
        "modifier": modifier,
        "damage_type": damage_type,
        "expression": text.strip(),
    }


def roll_damage(expression: str, *, rng: random.Random | None = None) -> dict | None:
    parsed = parse_damage_expression(expression)
    if parsed is None:
        return None
    roller = rng or random
    dice = [roller.randint(1, parsed["sides"]) for _ in range(parsed["count"])]
    total = sum(dice) + parsed["modifier"]
    return {
        **parsed,
        "dice": dice,
        "total": total,
    }


def parse_armor_ac(formula: str, dex_modifier: int) -> dict | None:
    """Resolve an armor ``ac`` string against a Dexterity modifier.

    Supports flat values (``18``, ``+2``), ``11 + Dex``, and
    ``14 + Dex (max 2)``.
    """
    if not isinstance(formula, str) or not formula.strip():
        return None
    text = formula.strip()
    flat = _AC_FLAT_RE.match(text)
    if flat:
        value = int(flat.group("value"))
        return {
            "formula": text,
            "base": value,
            "dex_applied": 0,
            "ac": value,
            "kind": "flat" if not text.lstrip().startswith("+") else "bonus",
        }
    dexed = _AC_DEX_RE.match(text)
    if dexed:
        base = int(dexed.group("base"))
        cap = dexed.group("cap")
        applied = dex_modifier
        if cap is not None:
            applied = min(applied, int(cap))
        # Negative Dex still applies unless capped above (max N only caps the top).
        return {
            "formula": text,
            "base": base,
            "dex_applied": applied,
            "ac": base + applied,
            "kind": "dex",
        }
    return None


def weapon_attack_ability(weapon: dict | None) -> str:
    """Choose the attack ability from weapon category/properties."""
    block = weapon if isinstance(weapon, dict) else {}
    props = {
        str(item).casefold()
        for item in (block.get("properties") or [])
        if isinstance(item, str)
    }
    category = str(block.get("category") or "").casefold()
    if "finesse" in props:
        return "finesse"
    if "ranged" in category or any(
        token in props for token in ("ammunition", "thrown")
    ):
        # Thrown-only melee weapons still allow Str; prefer Dex for pure ranged.
        if "thrown" in props and "melee" in category and "ranged" not in category:
            return "str"
        return "dex"
    return "str"


def resolve_attack_ability_mod(character: dict, weapon: dict | None) -> tuple[str, int | None]:
    choice = weapon_attack_ability(weapon)
    str_mod = ability_modifier(character.get("str"))
    dex_mod = ability_modifier(character.get("dex"))
    if choice == "finesse":
        if str_mod is None and dex_mod is None:
            return "finesse", None
        if str_mod is None:
            return "dex", dex_mod
        if dex_mod is None:
            return "str", str_mod
        if dex_mod >= str_mod:
            return "dex", dex_mod
        return "str", str_mod
    if choice == "dex":
        return "dex", dex_mod
    return "str", str_mod


def compute_armor_class(
    character: dict,
    armor: dict | None = None,
    *,
    shield_bonus: int = 0,
) -> dict:
    """Compute AC from armor property set + Dex, else sheet ``ac``."""
    dex_mod = ability_modifier(character.get("dex")) or 0
    armor_block = armor if isinstance(armor, dict) else {}
    formula = armor_block.get("ac")
    parsed = parse_armor_ac(str(formula), dex_mod) if formula is not None else None
    if parsed is not None and parsed["kind"] != "bonus":
        total = parsed["ac"] + int(shield_bonus)
        return {
            "ac": total,
            "armor": parsed,
            "shield_bonus": int(shield_bonus),
            "source": "armor",
        }
    bonus_only = parsed["ac"] if parsed and parsed["kind"] == "bonus" else 0
    try:
        sheet = int(character.get("ac"))
    except (TypeError, ValueError):
        sheet = 10 + dex_mod
    total = sheet + bonus_only + int(shield_bonus)
    return {
        "ac": total,
        "armor": parsed,
        "shield_bonus": int(shield_bonus),
        "source": "sheet",
        "sheet_ac": sheet,
    }


def skill_check_bonus(character: dict, skill_row) -> dict | None:
    """Return bonus details for a campaign skill row + character scores."""
    from sakaki_gm.skill_db import skill_bonus

    ability = (skill_row["ability"] or "").strip().lower()
    if ability not in ABILITY_NAMES:
        return None
    proficient = bool(skill_row["proficient"])
    expertise = bool(skill_row["expertise"])
    bonus = skill_bonus(
        character.get(ability),
        character.get("proficiency_bonus", 2),
        proficient,
        expertise,
    )
    if bonus is None:
        return None
    return {
        "skill": skill_row["name"],
        "ability": ability,
        "proficient": proficient,
        "expertise": expertise,
        "modifier": ability_modifier(character.get(ability)),
        "bonus": bonus,
    }


def ability_check_bonus(character: dict, ability: str) -> dict | None:
    ability = ability.strip().lower()
    if ability not in ABILITY_NAMES:
        return None
    mod = ability_modifier(character.get(ability))
    if mod is None:
        return None
    return {"ability": ability, "bonus": mod, "modifier": mod}


def attack_bonus(
    character: dict,
    weapon: dict | None,
    *,
    proficient: bool = False,
) -> dict | None:
    ability_name, ability_mod = resolve_attack_ability_mod(character, weapon)
    if ability_mod is None:
        return None
    try:
        proficiency = int(character.get("proficiency_bonus", 2))
    except (TypeError, ValueError):
        return None
    bonus = ability_mod + (proficiency if proficient else 0)
    return {
        "ability": ability_name,
        "ability_modifier": ability_mod,
        "proficient": bool(proficient),
        "proficiency_bonus": proficiency if proficient else 0,
        "bonus": bonus,
        "weapon": weapon or {},
    }


def resolve_success(total: int, target: int) -> dict:
    return {
        "total": total,
        "target": target,
        "success": total >= target,
        "margin": total - target,
    }


def perform_check(
    *,
    kind: str,
    bonus: int,
    target: int | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
    damage_expression: str | None = None,
    rng: random.Random | None = None,
) -> dict:
    """Roll d20 + bonus, optionally vs a target, optionally with damage on hit."""
    d20 = roll_d20(advantage=advantage, disadvantage=disadvantage, rng=rng)
    total = d20["value"] + int(bonus)
    result: dict[str, Any] = {
        "kind": kind,
        "d20": d20,
        "bonus": int(bonus),
        "total": total,
    }
    if target is not None:
        outcome = resolve_success(total, int(target))
        result["target"] = outcome["target"]
        result["success"] = outcome["success"]
        result["margin"] = outcome["margin"]
    if damage_expression and result.get("success", True):
        # Only auto-roll damage when attacking and (hit or no target given).
        if kind == "attack" and target is not None and not result.get("success"):
            result["damage"] = None
        else:
            result["damage"] = roll_damage(damage_expression, rng=rng)
    return result


def format_check_result(result: dict, *, character: str, label: str) -> str:
    """Human log line for a resolved check."""
    d20 = result["d20"]
    roll_bit = str(d20["value"])
    if d20["mode"] != "normal":
        roll_bit = f"{d20['value']} ({d20['mode']} {d20['rolls']})"
    parts = [
        f"{character}: {label}",
        f"d20 {roll_bit} {result['bonus']:+d} = {result['total']}",
    ]
    if "target" in result:
        verdict = "success" if result["success"] else "failure"
        parts.append(f"vs {result['target']} → {verdict}")
    damage = result.get("damage")
    if isinstance(damage, dict) and damage.get("total") is not None:
        dtype = f" {damage['damage_type']}" if damage.get("damage_type") else ""
        parts.append(f"damage {damage['total']}{dtype}")
    return " — ".join(parts)

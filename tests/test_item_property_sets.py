"""Typed property-set helpers for the Items kind editors."""
from sakaki_gm.item_property_sets import (
    assemble_properties,
    kind_enabled,
    money_from_properties,
    other_properties,
)


def test_assemble_weapon_armor_money_and_container():
    props = assemble_properties(
        is_weapon=True,
        is_armor=True,
        is_container=True,
        is_money=True,
        weapon={
            "category": "simple melee",
            "damage": "1d4 piercing",
            "properties": ["finesse", "light"],
            "range": "20/60",
        },
        armor={
            "category": "light",
            "ac": "11 + Dex",
            "stealth_disadvantage": False,
            "strength": None,
        },
        container={"capacity": "1 cu ft", "role": "pack"},
        money={"value": "1 gp"},
        other={"tags": ["focus", "weapon"], "charges": 3},
    )
    assert set(props["tags"]) == {"focus", "weapon", "armor", "container", "money"}
    assert props["weapon"]["damage"] == "1d4 piercing"
    assert props["armor"]["stealth_disadvantage"] is False
    assert props["armor"]["strength"] is None
    assert props["container"]["role"] == "pack"
    assert props["money"]["value"] == "1 gp"
    assert props["charges"] == 3


def test_currency_alias_counts_as_money():
    legacy = {"tags": ["currency"]}
    assert kind_enabled(legacy, "money") is True
    assert money_from_properties({"currency": {"value": "1 cp"}})["value"] == "1 cp"
    assert "currency" not in other_properties(legacy).get("tags", [])


def test_unchecking_kind_removes_block():
    props = assemble_properties(
        is_weapon=False,
        is_money=False,
        other={
            "tags": ["weapon", "money", "gear"],
            "weapon": {"damage": "1d8"},
            "money": {"value": "1 gp"},
        },
    )
    assert props.get("tags") == ["gear"]
    assert "weapon" not in props
    assert "money" not in props

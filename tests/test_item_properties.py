"""Archetype / instance typed property-set visibility and persistence."""
import json

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.gm import _short_character_payload
from sakaki_gm.item_db import (
    create_item,
    create_item_instance,
    get_character_items,
    get_items,
    resolve_item,
    set_character_items,
    update_item,
    update_item_instance,
)
from sakaki_gm.rules_5e_equipment import ensure_5e_equipment_archetypes


def test_update_item_preserves_typed_property_sets(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    item_id = create_item(
        db,
        "Longsword",
        "A blade.",
        properties={
            "tags": ["weapon"],
            "weapon": {"damage": "1d8 slashing", "range": "melee"},
        },
    )
    update_item(db, item_id, "Longsword", "A well-kept blade.")
    row = get_items(db)[0]
    assert row["description"] == "A well-kept blade."
    assert row["properties"]["weapon"]["damage"] == "1d8 slashing"
    assert row["is_container"] is False

    update_item(
        db,
        item_id,
        "Longsword",
        "A well-kept blade.",
        is_container=True,
    )
    row = get_items(db)[0]
    assert "container" in row["properties"]["tags"]
    assert "weapon" in row["properties"]["tags"]
    assert row["properties"]["weapon"]["damage"] == "1d8 slashing"
    db.close()


def test_update_item_can_replace_property_sets(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    item_id = create_item(db, "Shield", "Wood.", properties={"tags": ["armor"]})
    update_item(
        db,
        item_id,
        "Shield",
        "Reinforced.",
        properties={
            "tags": ["armor"],
            "armor": {"category": "shield", "ac_bonus": 2},
        },
    )
    props = get_items(db)[0]["properties"]
    assert props["armor"]["ac_bonus"] == 2
    db.close()


def test_instance_properties_save_as_override_diff(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    archetype_id = create_item(
        db,
        "Dagger",
        "A simple dagger.",
        properties={
            "tags": ["weapon"],
            "weapon": {
                "damage": "1d4 piercing",
                "properties": ["finesse", "light"],
                "range": "20/60",
            },
        },
    )
    instance_id = create_item_instance(db, archetype_id, commit=True)
    update_item_instance(
        db,
        instance_id,
        properties={
            "tags": ["weapon"],
            "weapon": {
                "damage": "1d4+1 piercing",
                "properties": ["finesse", "light"],
                "range": "20/60",
            },
        },
    )
    stored = json.loads(
        db.execute(
            "SELECT properties FROM objects WHERE object_id=?",
            (instance_id,),
        ).fetchone()["properties"]
    )
    assert stored == {"weapon": {"damage": "1d4+1 piercing"}}
    resolved = resolve_item(db, instance_id)
    assert resolved["properties"]["weapon"]["damage"] == "1d4+1 piercing"
    assert resolved["properties"]["weapon"]["range"] == "20/60"
    db.close()


def test_character_items_and_scene_gear_include_properties(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_5e_equipment_archetypes(db, commit=True)
    longsword = next(item for item in get_items(db) if item["name"] == "Longsword")
    character_id = create_character(db, "Serina", {"name": "Serina", "ac": 16})
    set_character_items(db, character_id, [(longsword["id"], 1, False, "hand")])
    owned = get_character_items(db, character_id)
    assert len(owned) == 1
    assert owned[0]["properties"]["weapon"]["damage"] == "1d8 slashing"

    row = db.execute(
        "SELECT id, name, campaign_role, object_id, data FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    payload = _short_character_payload(db, row, "Serina swings her longsword")
    gear = payload["gear"]
    assert gear[0]["properties"]["weapon"]["damage"] == "1d8 slashing"
    db.close()

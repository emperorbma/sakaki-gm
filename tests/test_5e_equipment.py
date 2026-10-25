"""Standard 5e equipment archetype seeding."""
import json

from sakaki_gm.db import get_db
from sakaki_gm.item_db import create_item, get_items
from sakaki_gm.rules_5e_equipment import (
    DEFAULT_5E_EQUIPMENT,
    ensure_5e_equipment_archetypes,
)


def test_ensure_adds_full_catalog_on_empty_bottle(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    result = ensure_5e_equipment_archetypes(db, commit=True)
    assert result["added"] == len(DEFAULT_5E_EQUIPMENT)
    assert result["skipped"] == 0
    assert len(result["ids"]) == len(DEFAULT_5E_EQUIPMENT)
    names = {item["name"] for item in get_items(db)}
    for entry in DEFAULT_5E_EQUIPMENT:
        assert entry["name"] in names
    db.close()


def test_ensure_skips_existing_names_without_overwrite(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    dagger_id = create_item(db, "Dagger", "Custom campaign dagger.")
    db.execute(
        "UPDATE objects SET properties=? WHERE object_id=?",
        (json.dumps({"tags": ["weapon"], "custom": True}), dagger_id),
    )
    db.commit()

    first = ensure_5e_equipment_archetypes(db, commit=True)
    assert first["skipped"] == 1
    assert first["added"] == len(DEFAULT_5E_EQUIPMENT) - 1

    row = db.execute(
        "SELECT description, properties FROM objects WHERE object_id=?",
        (dagger_id,),
    ).fetchone()
    assert row["description"] == "Custom campaign dagger."
    assert json.loads(row["properties"])["custom"] is True

    second = ensure_5e_equipment_archetypes(db, commit=True)
    assert second["added"] == 0
    assert second["skipped"] == len(DEFAULT_5E_EQUIPMENT)
    assert len(get_items(db)) == len(DEFAULT_5E_EQUIPMENT)
    db.close()


def test_ensure_preserves_typed_property_sets(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_5e_equipment_archetypes(db, commit=True)
    longsword = db.execute(
        "SELECT properties FROM objects WHERE object_type='item' AND name=?",
        ("Longsword",),
    ).fetchone()
    props = json.loads(longsword["properties"])
    assert "weapon" in props["tags"]
    assert props["weapon"]["damage"] == "1d8 slashing"
    assert "versatile (1d10)" in props["weapon"]["properties"]

    backpack = db.execute(
        "SELECT properties FROM objects WHERE object_type='item' AND name=?",
        ("Backpack",),
    ).fetchone()
    bag = json.loads(backpack["properties"])
    assert "container" in bag["tags"]
    assert bag["container"]["role"] == "pack"
    db.close()


def test_default_catalog_names_are_unique():
    keys = [entry["name"].casefold() for entry in DEFAULT_5E_EQUIPMENT]
    assert len(keys) == len(set(keys))
    assert all(entry["name"].strip() for entry in DEFAULT_5E_EQUIPMENT)

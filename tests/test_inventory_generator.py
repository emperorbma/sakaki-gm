"""Class inventory kits with irrelevant-dupe skipping."""
import json
import shutil
from pathlib import Path

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.inventory_generator import (
    choose_kit_for_character,
    generate_character_inventory,
    generate_inventories_for_campaign,
)
from sakaki_gm.item_db import (
    create_item_instance,
    get_character_inventory_container,
    get_character_inventory_tree,
    place_owned_copy_in_container,
)
from sakaki_gm.rules_5e_equipment import ensure_5e_equipment_archetypes


def _names(tree):
    found = []

    def walk(node):
        if not str(node.get("object_id", "")).startswith("inventory:"):
            found.append(node.get("name"))
        for child in node.get("children") or []:
            walk(child)

    walk(tree)
    return found


def test_choose_kit_skips_spirits_and_picks_wizard():
    assert choose_kit_for_character({"class": "Wizard"}, campaign_role="Ally") == "wizard"
    assert choose_kit_for_character({"class": "Ranger"}, campaign_role="NPC") == "ranger"
    assert (
        choose_kit_for_character({"class": "Dream Guardian"}, campaign_role="NPC")
        == "none"
    )
    assert choose_kit_for_character({"class": "Deity Avatar"}, campaign_role="Cameo") == "none"


def test_wizard_kit_adds_missing_and_skips_covered(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_5e_equipment_archetypes(db, commit=True)
    character_id = create_character(
        db, "Elara", {"name": "Elara", "class": "Wizard", "level": 12}
    )
    catalog = {
        row["name"]: row["object_id"]
        for row in db.execute(
            "SELECT object_id, name FROM objects WHERE object_type='item'"
        )
    }
    root = get_character_inventory_container(db, character_id)
    # Existing spellbook + unique skirt (covers clothes family).
    book = create_item_instance(db, catalog["Spellbook"], commit=False)
    place_owned_copy_in_container(db, root, book, commit=False)
    skirt = create_item_instance(db, catalog["Traveler's Clothes"], commit=False)
    db.execute(
        "UPDATE objects SET name=?, properties=? WHERE object_id=?",
        (
            "Lunarglow Skirt",
            json.dumps({"name": "Lunarglow Skirt"}, ensure_ascii=False),
            skirt,
        ),
    )
    place_owned_copy_in_container(db, root, skirt, commit=True)

    first = generate_character_inventory(db, character_id, commit=True)
    assert first["kit"] == "wizard"
    added_names = {item["name"] for item in first["added"]}
    assert "Spellbook" not in added_names
    assert "Traveler's Clothes" not in added_names
    assert "Backpack" in added_names
    assert "Arcane Focus" in added_names

    names = _names(get_character_inventory_tree(db, character_id))
    assert "Spellbook" in names
    assert "Lunarglow Skirt" in names
    assert names.count("Traveler's Clothes") == 0
    assert "Backpack" in names

    second = generate_character_inventory(db, character_id, commit=True)
    assert second["added"] == []
    assert any(row["name"] == "Spellbook" for row in second["skipped"])
    db.close()


def test_campaign_generate_skips_empty_spirits(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    wiz = create_character(db, "Koren", {"name": "Koren", "class": "Wizard", "level": 1})
    spirit = create_character(
        db,
        "Aelun",
        {"name": "Aelun", "class": "Light Serpent", "level": 1},
        campaign_role="Ally",
    )
    summary = generate_inventories_for_campaign(db, commit=True)
    by_name = {row["character"]: row for row in summary["results"]}
    assert by_name["Koren"]["kit"] == "wizard"
    assert by_name["Koren"]["added"]
    assert by_name["Aelun"]["kit"] == "none"
    assert by_name["Aelun"]["added"] == []
    assert _names(get_character_inventory_tree(db, wiz))
    assert _names(get_character_inventory_tree(db, spirit)) == []
    db.close()


def test_generator_on_eldenweave_snapshot_is_conservative(tmp_path):
    """Serina already rich — generator should add little/nothing relevant."""
    source = Path(
        "/home/ubuntu/.cursor/projects/workspace/uploads/6175732bd8a9_57d4.db"
    )
    if not source.exists():
        source = Path(
            "/home/ubuntu/.cursor/projects/workspace/uploads/6175732bd8a9_a6fb.db"
        )
    if not source.exists():
        return
    dest = tmp_path / "eldenweave.db"
    shutil.copy(source, dest)
    db = get_db(dest)
    serina = db.execute(
        "SELECT id FROM characters WHERE name='Serina'"
    ).fetchone()["id"]
    before = set(_names(get_character_inventory_tree(db, serina)))
    result = generate_character_inventory(db, serina, commit=True)
    after = set(_names(get_character_inventory_tree(db, serina)))
    added = {item["name"] for item in result["added"]}
    # Skirt/focus/stick/scepter cover clothes, focus, and melee — no filler dupes.
    assert "Traveler's Clothes" not in added
    assert "Arcane Focus" not in added
    assert "Dagger" not in added
    assert "Lunarglow Skirt" in after
    assert before <= after
    db.close()


def test_stick_or_scepter_covers_starter_dagger(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_5e_equipment_archetypes(db, commit=True)
    character_id = create_character(
        db, "Serina", {"name": "Serina", "class": "Wizard", "level": 2}
    )
    catalog = {
        row["name"]: row["object_id"]
        for row in db.execute(
            "SELECT object_id, name FROM objects WHERE object_type='item'"
        )
    }
    root = get_character_inventory_container(db, character_id)
    stick = create_item_instance(db, catalog["Club"], commit=False)
    db.execute(
        "UPDATE objects SET name=?, properties=? WHERE object_id=?",
        (
            "Starbloom-Infused Lunar Stick",
            json.dumps({"name": "Starbloom-Infused Lunar Stick"}, ensure_ascii=False),
            stick,
        ),
    )
    place_owned_copy_in_container(db, root, stick, commit=True)
    result = generate_character_inventory(db, character_id, commit=True)
    assert "Dagger" not in {item["name"] for item in result["added"]}
    assert any(
        row["name"] == "Dagger" and row["reason"] == "already covered"
        for row in result["skipped"]
    )
    db.close()

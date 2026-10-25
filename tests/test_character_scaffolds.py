import random

import pytest

from sakaki_gm.character_db import (
    create_character,
    get_all_characters,
    get_character,
    spawn_character_from_scaffold,
)
from sakaki_gm.db import get_db
from sakaki_gm.item_db import (
    create_item_instance,
    get_character_inventory_tree,
    get_items,
    place_owned_copy_in_container,
)
from sakaki_gm.name_gen import generate_given_name, resolve_spawn_name
from sakaki_gm.skill_db import get_character_skills, get_skills, set_character_skills
from sakaki_gm.spell_db import get_character_spells, get_spells, set_character_spells
from sakaki_gm.ui.modules.characters import (
    DEFAULT_STAT_BLOCK_SCAFFOLD,
    CharactersModule,
    _is_stat_block_scaffold,
    _optional_int,
    normalize_stat_block_status,
)


def test_stat_block_scaffold_detection():
    assert _is_stat_block_scaffold({
        "stat_block_status": "Design scaffold; mechanics unassigned.",
    })
    assert not _is_stat_block_scaffold({"stat_block_status": "Complete"})
    assert not _is_stat_block_scaffold({})


def test_normalize_stat_block_status_for_ui():
    assert normalize_stat_block_status(False, "Design scaffold; x") is None
    assert normalize_stat_block_status(True, "") == DEFAULT_STAT_BLOCK_SCAFFOLD
    assert normalize_stat_block_status(True, "Design scaffold; Okina") == (
        "Design scaffold; Okina"
    )
    assert normalize_stat_block_status(True, "Deity avatar only") == (
        "Design scaffold; Deity avatar only"
    )
    assert _is_stat_block_scaffold({
        "stat_block_status": normalize_stat_block_status(True, "custom note"),
    })


def test_optional_integer_preserves_unassigned_fields():
    assert _optional_int("") is None
    assert _optional_int("  ") is None
    assert _optional_int("18") == 18
    with pytest.raises(ValueError):
        _optional_int("not a number")


def test_character_list_separates_scaffolds_from_roster(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(
        db,
        "Serina",
        {"name": "Serina", "class": "Wizard", "level": 2},
        campaign_role="Protagonist",
    )
    create_character(
        db,
        "Voryn Acolyte",
        {
            "name": "Voryn Acolyte",
            "class": "Cultist",
            "stat_block_status": "Design scaffold; role template.",
        },
        campaign_role="Antagonist",
    )
    module = CharactersModule.__new__(CharactersModule)
    module.db = db
    module.list_mode = CharactersModule.LIST_CHARACTERS
    roster = module._visible_characters()
    assert [char["name"] for char in roster] == ["Serina"]

    module.list_mode = CharactersModule.LIST_SCAFFOLDS
    scaffolds = module._visible_characters()
    assert [char["name"] for char in scaffolds] == ["Voryn Acolyte"]
    assert len(get_all_characters(db)) == 2
    db.close()


def test_resolve_spawn_name_respects_generate_option():
    scaffold = {
        "name": "Voryn Acolyte",
        "campaign_role": "Antagonist",
        "class": "Cultist",
        "spawn_generate_name": True,
        "spawn_name_pattern": "{given} ({scaffold})",
    }
    name = resolve_spawn_name(scaffold, rng=random.Random(7))
    assert name.endswith(" (Voryn Acolyte)")
    assert not name.startswith("Voryn Acolyte")
    assert resolve_spawn_name(
        {"name": "Voryn Acolyte"},
        rng=random.Random(1),
    ) == "Voryn Acolyte (spawned)"
    assert resolve_spawn_name(
        scaffold,
        explicit_name="Named One",
        rng=random.Random(1),
    ) == "Named One"
    assert generate_given_name(rng=random.Random(3))


def test_spawn_clones_scaffold_without_removing_it(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    scaffold_id = create_character(
        db,
        "Voryn Acolyte",
        {
            "name": "Voryn Acolyte",
            "class": "Cultist",
            "level": 1,
            "stat_block_status": "Design scaffold; Voryn role.",
            "spawn_generate_name": True,
            "spawn_name_pattern": "{given}",
            "notes": "Temple recruit",
        },
        campaign_role="Antagonist",
    )
    skills = get_skills(db)
    stealth = next(s for s in skills if s["name"] == "Stealth")
    set_character_skills(db, scaffold_id, [(stealth["id"], True, False)])
    spells = get_spells(db)
    if spells:
        set_character_spells(
            db,
            scaffold_id,
            [(spells[0]["id"], True, 0, "")],
        )
    items = get_items(db)
    dagger = next((item for item in items if item["name"] == "Dagger"), None)
    if dagger is not None:
        instance_id = create_item_instance(db, dagger["object_id"], commit=False)
        place_owned_copy_in_container(
            db,
            f"inventory:{scaffold_id}",
            instance_id,
            quantity=1,
            commit=True,
        )

    new_id = spawn_character_from_scaffold(
        db, scaffold_id, name=None
    )
    scaffold = get_character(db, scaffold_id)
    spawned = get_character(db, new_id)
    assert scaffold is not None
    assert scaffold["name"] == "Voryn Acolyte"
    assert _is_stat_block_scaffold(scaffold)
    assert scaffold.get("spawn_generate_name") is True
    assert spawned is not None
    assert spawned["id"] != scaffold_id
    assert spawned["name"] != "Voryn Acolyte"
    assert not _is_stat_block_scaffold(spawned)
    assert "spawn_generate_name" not in spawned
    assert "spawn_name_pattern" not in spawned
    assert spawned["notes"] == "Temple recruit"
    assert spawned["campaign_role"] == "Antagonist"
    assert spawned["class"] == "Cultist"

    skill_names = {
        row["name"]
        for row in get_character_skills(db, new_id)
        if row["proficient"]
    }
    assert "Stealth" in skill_names
    if spells:
        assert get_character_spells(db, new_id)
    if dagger is not None:
        tree = get_character_inventory_tree(db, new_id)
        names = [child["name"] for child in tree.get("children") or []]
        assert "Dagger" in names
        source_tree = get_character_inventory_tree(db, scaffold_id)
        source_ids = {
            child["object_id"] for child in source_tree.get("children") or []
        }
        spawned_ids = {
            child["object_id"] for child in tree.get("children") or []
        }
        assert source_ids.isdisjoint(spawned_ids)

    assert len(get_all_characters(db)) == 2
    db.close()


def test_spawn_rejects_non_scaffold(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    char_id = create_character(db, "Serina", {"name": "Serina", "class": "Wizard"})
    with pytest.raises(ValueError, match="not a design scaffold"):
        spawn_character_from_scaffold(db, char_id)
    db.close()

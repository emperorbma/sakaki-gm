import pytest

from sakaki_gm.character_db import create_character, get_all_characters
from sakaki_gm.db import get_db
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

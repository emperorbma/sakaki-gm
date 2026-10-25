"""Campaign spell catalog CRUD and conservative free-text linking."""
import json

from sakaki_gm.db import get_db
from sakaki_gm.rules_5e_spells import DEFAULT_5E_SPELLS, ensure_5e_spells
from sakaki_gm.spell_db import (
    delete_campaign_spell,
    find_spell_names_in_text,
    get_character_spells,
    get_spells,
    link_character_spells_from_free_text,
    migrate_character_spell_free_text,
    set_character_spells,
    spell_casting_mode,
    spell_notes_blurb,
    upsert_campaign_spell,
)


def test_fresh_db_seeds_stock_spells(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 17
    names = {row["name"] for row in get_spells(db)}
    assert "Fireball" in names
    assert "Mage Hand" in names
    assert len(names) >= len(DEFAULT_5E_SPELLS)
    db.close()


def test_upsert_and_delete_campaign_spell(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    spell_id = upsert_campaign_spell(
        db,
        "Moonbeam Custom",
        level=2,
        school="evocation",
        casting_time="1 action",
        range="120 feet",
        components="V, S, M",
        duration="Concentration, up to 1 minute",
        description="A silver beam of moonlight.",
        concentration=True,
    )
    names = {row["name"] for row in get_spells(db)}
    assert "Moonbeam Custom" in names

    upsert_campaign_spell(
        db,
        "Moonbeam Custom",
        level=3,
        school="evocation",
        description="Brighter moonlight.",
        concentration=True,
        spell_id=spell_id,
    )
    row = next(spell for spell in get_spells(db) if spell["id"] == spell_id)
    assert row["level"] == 3
    assert "Brighter" in row["description"]

    try:
        upsert_campaign_spell(db, "Fireball", level=3, school="evocation")
        assert False, "expected duplicate name error"
    except ValueError:
        pass

    delete_campaign_spell(db, spell_id)
    assert "Moonbeam Custom" not in {row["name"] for row in get_spells(db)}
    db.close()


def test_ensure_5e_spells_is_idempotent(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    first = ensure_5e_spells(db, commit=True)
    assert first["added"] == 0
    second = ensure_5e_spells(db, commit=True)
    assert second["added"] == 0
    assert second["skipped"] == len(DEFAULT_5E_SPELLS)
    db.close()


def test_find_spell_names_prefers_longer_matches():
    catalog = ["Fire", "Fire Bolt", "Protection from Evil and Good"]
    found = find_spell_names_in_text(
        "She casts Fire Bolt then Protection from Evil and Good.",
        catalog,
    )
    assert found == ["Fire Bolt", "Protection from Evil and Good"]
    assert "Fire" not in found


def test_free_text_linking_does_not_rewrite_character_data(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    prose = "Cantrips: Fire Bolt, Mage Hand\n1st: Shield, Detect Magic"
    cursor = db.execute(
        "INSERT INTO characters (name, data) VALUES (?, ?)",
        ("Wizard", json.dumps({"spells": prose, "notes": "keep me"}, ensure_ascii=False)),
    )
    character_id = int(cursor.lastrowid)
    db.commit()

    result = link_character_spells_from_free_text(db, character_id, prose)
    assert "Fire Bolt" in result["linked"]
    assert "Mage Hand" in result["linked"]
    assert "Shield" in result["linked"]
    assert "Detect Magic" in result["linked"]

    linked = get_character_spells(db, character_id)
    linked_names = {row["name"] for row in linked}
    assert linked_names >= {"Fire Bolt", "Mage Hand", "Shield", "Detect Magic"}
    for row in linked:
        assert row["known"] == 1
        assert row["prepared"] == 0

    data = json.loads(
        db.execute("SELECT data FROM characters WHERE id=?", (character_id,)).fetchone()[
            "data"
        ]
    )
    assert data["spells"] == prose
    assert data["notes"] == "keep me"
    db.close()


def test_migration_links_existing_free_text_without_surprises(tmp_path):
    """Simulate a pre-v17 bottle: create at v16-shaped content then remigrate.

    Fresh get_db already seeds v17. Here we insert a character with free-text
    spells after open and re-run the conservative linker, asserting prose stays.
    """
    db = get_db(tmp_path / "campaign.db")
    prose = "Prepared: Cure Wounds, Healing Word. Also Fireball someday."
    cursor = db.execute(
        "INSERT INTO characters (name, data) VALUES (?, ?)",
        ("Cleric", json.dumps({"spells": prose}, ensure_ascii=False)),
    )
    character_id = int(cursor.lastrowid)
    db.commit()

    summary = migrate_character_spell_free_text(db)
    assert summary["characters_scanned"] >= 1
    assert summary["links_created"] >= 2

    data = json.loads(
        db.execute("SELECT data FROM characters WHERE id=?", (character_id,)).fetchone()[
            "data"
        ]
    )
    assert data["spells"] == prose
    names = {row["name"] for row in get_character_spells(db, character_id)}
    assert "Cure Wounds" in names
    assert "Healing Word" in names
    assert "Fireball" in names
    # Unrecognized prose must not invent catalog rows.
    assert "someday" not in {row["name"].casefold() for row in get_spells(db)}
    db.close()


def test_linking_skips_partial_word_matches(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    # "Light" is a cantrip; "lightning" must not count as Light.
    found = find_spell_names_in_text("A bolt of lightning strikes.", ["Light", "Fireball"])
    assert found == []
    found = find_spell_names_in_text("She casts Light on the torch.", ["Light", "Fireball"])
    assert found == ["Light"]
    db.close()


def test_stock_spells_default_to_vancian_casting_mode(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    fireball = next(spell for spell in get_spells(db) if spell["name"] == "Fireball")
    assert spell_casting_mode(fireball) == "vancian"
    db.close()


def test_hybrid_loadout_prepared_count_for_vancian(tmp_path):
    """Hybrid campaigns mix Vancian and mana spells; prepared is a count."""
    db = get_db(tmp_path / "campaign.db")
    fireball = next(spell for spell in get_spells(db) if spell["name"] == "Fireball")
    mana_id = upsert_campaign_spell(
        db,
        "Arc Surge",
        level=2,
        school="evocation",
        casting_mode="mana",
        description="Spend mana to release a surge of force.",
    )
    cursor = db.execute(
        "INSERT INTO characters (name, data) VALUES (?, ?)",
        ("Hybrid Caster", json.dumps({"spells": "old list: Fireball"}, ensure_ascii=False)),
    )
    character_id = int(cursor.lastrowid)
    db.commit()

    set_character_spells(
        db,
        character_id,
        [
            (fireball["id"], True, 3, ""),  # multi-slot Vancian preparations
            (mana_id, True, 5, ""),  # prepared count ignored for mana
        ],
    )
    by_name = {row["name"]: row for row in get_character_spells(db, character_id)}
    assert by_name["Fireball"]["prepared"] == 3
    assert by_name["Arc Surge"]["prepared"] == 0
    assert spell_casting_mode(
        next(spell for spell in get_spells(db) if spell["id"] == mana_id)
    ) == "mana"

    blurb = spell_notes_blurb(
        json.loads(
            db.execute(
                "SELECT data FROM characters WHERE id=?", (character_id,)
            ).fetchone()["data"]
        )
    )
    assert "Fireball" in blurb
    db.close()

"""Campaign skill catalog CRUD."""
from sakaki_gm.db import get_db
from sakaki_gm.skill_db import (
    DEFAULT_5E_SKILLS,
    delete_campaign_skill,
    get_skills,
    replace_campaign_skills,
    upsert_campaign_skill,
)


def test_upsert_and_delete_campaign_skill(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    replace_campaign_skills(db, DEFAULT_5E_SKILLS)
    skill_id = upsert_campaign_skill(
        db, "Lunar Lore", "int", "Recall rites of the Lunar Court."
    )
    names = {row["name"] for row in get_skills(db)}
    assert "Lunar Lore" in names

    upsert_campaign_skill(
        db,
        "Lunar Lore",
        "wis",
        "Sense the pull of moon rites.",
        skill_id=skill_id,
    )
    row = next(skill for skill in get_skills(db) if skill["id"] == skill_id)
    assert row["ability"] == "wis"
    assert "moon rites" in row["description"]

    try:
        upsert_campaign_skill(db, "Stealth", "dex")
        assert False, "expected duplicate name error"
    except ValueError:
        pass

    delete_campaign_skill(db, skill_id)
    assert "Lunar Lore" not in {row["name"] for row in get_skills(db)}
    db.close()


def test_migration_backfills_5e_skill_descriptions(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    stealth = next(skill for skill in get_skills(db) if skill["name"] == "Stealth")
    assert stealth["description"]
    assert "sneak" in stealth["description"].casefold() or "hide" in stealth[
        "description"
    ].casefold()
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 16
    db.close()


def test_replace_campaign_skills_writes_descriptions(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    replace_campaign_skills(db, DEFAULT_5E_SKILLS)
    arcana = next(skill for skill in get_skills(db) if skill["name"] == "Arcana")
    assert "spells" in arcana["description"].casefold()
    db.close()

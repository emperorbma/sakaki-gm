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
    skill_id = upsert_campaign_skill(db, "Lunar Lore", "int")
    names = {row["name"] for row in get_skills(db)}
    assert "Lunar Lore" in names

    upsert_campaign_skill(db, "Lunar Lore", "wis", skill_id=skill_id)
    row = next(skill for skill in get_skills(db) if skill["id"] == skill_id)
    assert row["ability"] == "wis"

    try:
        upsert_campaign_skill(db, "Stealth", "dex")
        assert False, "expected duplicate name error"
    except ValueError:
        pass

    delete_campaign_skill(db, skill_id)
    assert "Lunar Lore" not in {row["name"] for row in get_skills(db)}
    db.close()

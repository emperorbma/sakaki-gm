"""Bottle-side deterministic checks (no LLM math)."""
import json
import random

from sakaki_gm.checks import (
    ability_modifier,
    attack_bonus,
    compute_armor_class,
    format_check_result,
    parse_armor_ac,
    parse_damage_expression,
    perform_check,
    resolve_attack_ability_mod,
    roll_damage,
    weapon_attack_ability,
)
from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.gm import apply_proposals, normalize_proposals
from sakaki_gm.item_db import create_item, set_character_items
from sakaki_gm.skill_db import DEFAULT_5E_SKILLS, replace_campaign_skills
from sakaki_gm.skill_db import set_character_skills
from sakaki_gm.skill_db import get_skills


def test_armor_ac_formulas():
    assert parse_armor_ac("18", 3)["ac"] == 18
    assert parse_armor_ac("11 + Dex", 3)["ac"] == 14
    assert parse_armor_ac("14 + Dex (max 2)", 5)["ac"] == 16
    assert parse_armor_ac("14 + Dex (max 2)", 1)["ac"] == 15
    assert parse_armor_ac("+2", 0)["kind"] == "bonus"
    plate = compute_armor_class({"dex": 16, "ac": 12}, {"ac": "18"})
    assert plate["ac"] == 18
    assert plate["source"] == "armor"
    shield = compute_armor_class({"dex": 16, "ac": 14}, None, shield_bonus=2)
    assert shield["ac"] == 16


def test_weapon_ability_and_attack_bonus():
    dagger = {
        "category": "simple melee",
        "damage": "1d4 piercing",
        "properties": ["finesse", "light", "thrown"],
        "range": "20/60",
    }
    assert weapon_attack_ability(dagger) == "finesse"
    character = {"str": 10, "dex": 16, "proficiency_bonus": 2}
    ability, mod = resolve_attack_ability_mod(character, dagger)
    assert ability == "dex"
    assert mod == 3
    details = attack_bonus(character, dagger, proficient=True)
    assert details["bonus"] == 5
    longbow = {
        "category": "martial ranged",
        "damage": "1d8 piercing",
        "properties": ["ammunition", "heavy", "two-handed"],
        "range": "150/600",
    }
    assert weapon_attack_ability(longbow) == "dex"


def test_damage_parse_and_roll():
    parsed = parse_damage_expression("1d8 + 2 piercing")
    assert parsed["count"] == 1
    assert parsed["sides"] == 8
    assert parsed["modifier"] == 2
    assert parsed["damage_type"] == "piercing"
    rolled = roll_damage("2d6 slashing", rng=random.Random(0))
    assert rolled["total"] == sum(rolled["dice"])
    assert rolled["damage_type"] == "slashing"


def test_perform_check_success_and_format():
    result = perform_check(
        kind="skill",
        bonus=5,
        target=15,
        rng=random.Random(1),
    )
    assert "total" in result
    assert result["success"] == (result["total"] >= 15)
    line = format_check_result(result, character="Serina", label="Stealth (dex)")
    assert line.startswith("Serina: Stealth")


def test_roll_check_proposal_skill_and_attack(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    replace_campaign_skills(db, DEFAULT_5E_SKILLS)
    character_id = create_character(
        db,
        "Serina",
        {
            "name": "Serina",
            "str": 10,
            "dex": 16,
            "proficiency_bonus": 2,
            "ac": 14,
        },
    )
    skills = {row["name"]: row["id"] for row in get_skills(db)}
    set_character_skills(
        db,
        character_id,
        [(skills["Stealth"], True, False)],
    )
    dagger_id = create_item(
        db,
        "Dagger",
        "A blade.",
        properties={
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d4 piercing",
                "properties": ["finesse", "light"],
                "range": "melee",
            },
        },
    )
    set_character_items(db, character_id, [(dagger_id, 1, False, "hand")])

    proposals = normalize_proposals([
        {
            "op": "roll_check",
            "character": "Serina",
            "check": "skill",
            "skill": "Stealth",
            "dc": 12,
            "summary": "Slip past the wraith",
        },
        {
            "op": "roll_check",
            "character": "Serina",
            "check": "attack",
            "item_object_id": dagger_id,
            "ac": 13,
            "proficient": True,
            "summary": "Stab with dagger",
        },
        {"op": "roll_check", "character": "Serina", "check": "skill"},
    ])
    assert [p["op"] for p in proposals] == ["roll_check", "roll_check"]

    messages = apply_proposals(db, proposals)
    assert any("Stealth" in line or "Slip past" in line for line in messages)
    assert any("dagger" in line.casefold() or "Stab" in line for line in messages)
    data = json.loads(
        db.execute("SELECT data FROM characters WHERE name='Serina'").fetchone()["data"]
    )
    assert len(data["check_results"]) == 2
    assert ability_modifier(16) == 3
    db.close()

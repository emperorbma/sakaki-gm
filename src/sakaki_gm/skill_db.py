"""Campaign skill definitions and character skill proficiency persistence."""
import json


# (name, ability, description) — short play-facing notes for the Skills desk / checks.
DEFAULT_5E_SKILLS = (
    ("Acrobatics", "dex", "Stay on your feet in tricky situations; tumble, balance, and contort."),
    ("Animal Handling", "wis", "Calm, control, or read the intent of animals."),
    ("Arcana", "int", "Recall lore about spells, magic items, eldritch symbols, and planar lore."),
    ("Athletics", "str", "Climb, jump, swim, or force your way through physical obstacles."),
    ("Deception", "cha", "Hide the truth with words, disguise, or misdirection."),
    ("History", "int", "Recall lore about historical events, people, kingdoms, and disputes."),
    ("Insight", "wis", "Determine a creature's true intentions; detect lies or predict moves."),
    ("Intimidation", "cha", "Influence someone through threats, hostile presence, or violence."),
    ("Investigation", "int", "Look for clues, deduce how things work, and search for hidden objects."),
    ("Medicine", "wis", "Stabilize the dying, diagnose illness, and treat wounds."),
    ("Nature", "int", "Recall lore about terrain, plants, animals, weather, and natural cycles."),
    ("Perception", "wis", "Spot, hear, or otherwise notice something; general awareness."),
    ("Performance", "cha", "Entertain an audience with music, dance, acting, or storytelling."),
    ("Persuasion", "cha", "Influence someone with tact, social grace, or good nature."),
    ("Religion", "int", "Recall lore about deities, rites, prayers, cults, and holy symbols."),
    ("Sleight of Hand", "dex", "Pick pockets, plant an object, or conceal what you are holding."),
    ("Stealth", "dex", "Conceal yourself from enemies; sneak, hide, and slip past notice."),
    ("Survival", "wis", "Follow tracks, hunt game, guide a group, and predict the weather."),
)


ABILITY_SCORES = frozenset({"str", "dex", "con", "int", "wis", "cha"})


def skill_entry_parts(entry) -> tuple[str, str, str]:
    """Normalize a skill definition tuple to (name, ability, description)."""
    name = entry[0]
    ability = entry[1] if len(entry) > 1 else ""
    description = entry[2] if len(entry) > 2 else ""
    return name, ability, description


def get_skills(db):
    return db.execute(
        """
        SELECT id, name, ability, description
        FROM campaign_skills
        ORDER BY name COLLATE NOCASE
        """
    ).fetchall()


def _normalize_skill(
    name: str, ability: str, description: str = ""
) -> tuple[str, str, str]:
    name = (name or "").strip()
    ability = (ability or "").strip().lower()
    description = (description or "").strip()
    if not name:
        raise ValueError("Skill names cannot be empty.")
    if ability and ability not in ABILITY_SCORES:
        raise ValueError("Skill ability must be a standard ability score.")
    return name, ability, description


def upsert_campaign_skill(
    db,
    name: str,
    ability: str,
    description: str = "",
    *,
    skill_id: int | None = None,
) -> int:
    """Create or update one campaign skill. Returns the skill id."""
    name, ability, description = _normalize_skill(name, ability, description)
    with db:
        if skill_id is None:
            conflict = db.execute(
                "SELECT id FROM campaign_skills WHERE name=? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if conflict is not None:
                raise ValueError(f"A skill named {name} already exists.")
            cursor = db.execute(
                """
                INSERT INTO campaign_skills (name, ability, description)
                VALUES (?, ?, ?)
                """,
                (name, ability, description),
            )
            return int(cursor.lastrowid)
        row = db.execute(
            "SELECT id, name FROM campaign_skills WHERE id=?",
            (skill_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown skill id {skill_id}.")
        conflict = db.execute(
            """
            SELECT id FROM campaign_skills
            WHERE name=? COLLATE NOCASE AND id!=?
            """,
            (name, skill_id),
        ).fetchone()
        if conflict is not None:
            raise ValueError(f"A skill named {name} already exists.")
        db.execute(
            """
            UPDATE campaign_skills
            SET name=?, ability=?, description=?
            WHERE id=?
            """,
            (name, ability, description, skill_id),
        )
        return int(skill_id)


def delete_campaign_skill(db, skill_id: int) -> None:
    """Delete a campaign skill (character proficiency rows cascade)."""
    with db:
        deleted = db.execute(
            "DELETE FROM campaign_skills WHERE id=?",
            (skill_id,),
        ).rowcount
        if not deleted:
            raise ValueError(f"Unknown skill id {skill_id}.")


def replace_campaign_skills(db, definitions) -> None:
    normalized = []
    for entry in definitions:
        name, ability, description = skill_entry_parts(entry)
        name, ability, description = _normalize_skill(name, ability, description)
        normalized.append((name, ability, description))
    if len({name.casefold() for name, _ability, _description in normalized}) != len(
        normalized
    ):
        raise ValueError("Skill names must be unique.")
    with db:
        if normalized:
            placeholders = ", ".join("?" for _ in normalized)
            db.execute(
                f"DELETE FROM campaign_skills WHERE name NOT IN ({placeholders})",
                tuple(name for name, _ability, _description in normalized),
            )
        else:
            db.execute("DELETE FROM campaign_skills")
        db.executemany(
            """
            INSERT INTO campaign_skills (name, ability, description)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                ability=excluded.ability,
                description=excluded.description
            """,
            normalized,
        )


def backfill_default_skill_descriptions(db) -> int:
    """Fill empty descriptions for stock 5e skill names. Returns rows updated."""
    updated = 0
    with db:
        for name, _ability, description in (
            skill_entry_parts(entry) for entry in DEFAULT_5E_SKILLS
        ):
            if not description:
                continue
            cursor = db.execute(
                """
                UPDATE campaign_skills
                SET description=?
                WHERE name=? COLLATE NOCASE
                  AND TRIM(COALESCE(description, '')) = ''
                """,
                (description, name),
            )
            updated += cursor.rowcount
    return updated


def get_character_skills(db, character_id: int):
    return db.execute(
        """
        SELECT campaign_skills.id, campaign_skills.name, campaign_skills.ability,
               campaign_skills.description,
               character_skills.proficient, character_skills.expertise
        FROM campaign_skills
        LEFT JOIN character_skills
          ON character_skills.skill_id = campaign_skills.id
         AND character_skills.character_id = ?
        ORDER BY campaign_skills.name COLLATE NOCASE
        """,
        (character_id,),
    ).fetchall()


def set_character_skills(db, character_id: int, assignments) -> None:
    normalized = []
    for skill_id, proficient, expertise in assignments:
        expertise = bool(expertise)
        proficient = bool(proficient) or expertise
        if not proficient:
            continue
        normalized.append(
            (character_id, skill_id, int(proficient), int(expertise))
        )
    with db:
        db.execute("DELETE FROM character_skills WHERE character_id=?", (character_id,))
        db.executemany(
            """
            INSERT INTO character_skills (character_id, skill_id, proficient, expertise)
            VALUES (?, ?, ?, ?)
            """,
            normalized,
        )


def move_legacy_skills_to_notes(data: dict) -> None:
    legacy = data.pop("skills", None)
    if not legacy:
        return
    if isinstance(legacy, list):
        legacy_text = "\n".join(str(line) for line in legacy).strip()
    elif isinstance(legacy, dict):
        legacy_text = json.dumps(legacy, ensure_ascii=False, indent=2)
    else:
        legacy_text = str(legacy).strip()
    if not legacy_text:
        return
    notes = data.get("notes", "")
    if isinstance(notes, list):
        notes_text = "\n".join(str(line) for line in notes).strip()
    elif isinstance(notes, dict):
        notes_text = json.dumps(notes, ensure_ascii=False, indent=2)
    else:
        notes_text = str(notes or "").strip()
    section = f"Previous skill details:\n{legacy_text}"
    data["notes"] = f"{notes_text}\n\n{section}".strip() if notes_text else section


def skill_bonus(ability_score, proficiency_bonus, proficient, expertise) -> int | None:
    try:
        modifier = (int(ability_score) - 10) // 2
        proficiency = int(proficiency_bonus)
    except (TypeError, ValueError):
        return None
    if expertise:
        return modifier + 2 * proficiency
    if proficient:
        return modifier + proficiency
    return modifier

"""Campaign skill definitions and character skill proficiency persistence."""
import json


DEFAULT_5E_SKILLS = (
    ("Acrobatics", "dex"),
    ("Animal Handling", "wis"),
    ("Arcana", "int"),
    ("Athletics", "str"),
    ("Deception", "cha"),
    ("History", "int"),
    ("Insight", "wis"),
    ("Intimidation", "cha"),
    ("Investigation", "int"),
    ("Medicine", "wis"),
    ("Nature", "int"),
    ("Perception", "wis"),
    ("Performance", "cha"),
    ("Persuasion", "cha"),
    ("Religion", "int"),
    ("Sleight of Hand", "dex"),
    ("Stealth", "dex"),
    ("Survival", "wis"),
)


ABILITY_SCORES = frozenset({"str", "dex", "con", "int", "wis", "cha"})


def get_skills(db):
    return db.execute(
        "SELECT id, name, ability FROM campaign_skills ORDER BY name COLLATE NOCASE"
    ).fetchall()


def _normalize_skill(name: str, ability: str) -> tuple[str, str]:
    name = (name or "").strip()
    ability = (ability or "").strip().lower()
    if not name:
        raise ValueError("Skill names cannot be empty.")
    if ability and ability not in ABILITY_SCORES:
        raise ValueError("Skill ability must be a standard ability score.")
    return name, ability


def upsert_campaign_skill(db, name: str, ability: str, *, skill_id: int | None = None) -> int:
    """Create or update one campaign skill. Returns the skill id."""
    name, ability = _normalize_skill(name, ability)
    with db:
        if skill_id is None:
            conflict = db.execute(
                "SELECT id FROM campaign_skills WHERE name=? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if conflict is not None:
                raise ValueError(f"A skill named {name} already exists.")
            cursor = db.execute(
                "INSERT INTO campaign_skills (name, ability) VALUES (?, ?)",
                (name, ability),
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
            "UPDATE campaign_skills SET name=?, ability=? WHERE id=?",
            (name, ability, skill_id),
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
    normalized = [(name.strip(), ability.strip().lower()) for name, ability in definitions]
    if any(not name for name, _ability in normalized):
        raise ValueError("Skill names cannot be empty.")
    if any(ability and ability not in ABILITY_SCORES for _name, ability in normalized):
        raise ValueError("Skill ability must be a standard ability score.")
    if len({name.casefold() for name, _ability in normalized}) != len(normalized):
        raise ValueError("Skill names must be unique.")
    with db:
        if normalized:
            placeholders = ", ".join("?" for _ in normalized)
            db.execute(
                f"DELETE FROM campaign_skills WHERE name NOT IN ({placeholders})",
                tuple(name for name, _ability in normalized),
            )
        else:
            db.execute("DELETE FROM campaign_skills")
        db.executemany(
            """
            INSERT INTO campaign_skills (name, ability)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET ability=excluded.ability
            """,
            normalized,
        )


def get_character_skills(db, character_id: int):
    return db.execute(
        """
        SELECT campaign_skills.id, campaign_skills.name, campaign_skills.ability,
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

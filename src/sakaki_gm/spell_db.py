"""Campaign spell catalog and character spell links."""
from __future__ import annotations

import json
import re


SPELL_SCHOOLS = frozenset({
    "abjuration",
    "conjuration",
    "divination",
    "enchantment",
    "evocation",
    "illusion",
    "necromancy",
    "transmutation",
})


def get_spells(db):
    return db.execute(
        """
        SELECT id, name, level, school, casting_time, range, components,
               duration, description, ritual, concentration, properties
        FROM campaign_spells
        ORDER BY level, name COLLATE NOCASE, id
        """
    ).fetchall()


def get_spell(db, spell_id: int):
    return db.execute(
        """
        SELECT id, name, level, school, casting_time, range, components,
               duration, description, ritual, concentration, properties
        FROM campaign_spells WHERE id=?
        """,
        (spell_id,),
    ).fetchone()


def _normalize_spell_fields(
    name: str,
    *,
    level: int = 0,
    school: str = "",
    casting_time: str = "",
    range: str = "",
    components: str = "",
    duration: str = "",
    description: str = "",
    ritual: bool = False,
    concentration: bool = False,
    properties: dict | None = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Spell names cannot be empty.")
    try:
        level = int(level)
    except (TypeError, ValueError) as exc:
        raise ValueError("Spell level must be an integer.") from exc
    if level < 0 or level > 9:
        raise ValueError("Spell level must be between 0 and 9.")
    school = (school or "").strip().lower()
    if school and school not in SPELL_SCHOOLS:
        raise ValueError(f"Unknown spell school {school!r}.")
    props = properties if isinstance(properties, dict) else {}
    return {
        "name": name,
        "level": level,
        "school": school,
        "casting_time": (casting_time or "").strip(),
        "range": (range or "").strip(),
        "components": (components or "").strip(),
        "duration": (duration or "").strip(),
        "description": (description or "").strip(),
        "ritual": 1 if ritual else 0,
        "concentration": 1 if concentration else 0,
        "properties": json.dumps(props, ensure_ascii=False),
    }


def upsert_campaign_spell(
    db,
    name: str,
    *,
    level: int = 0,
    school: str = "",
    casting_time: str = "",
    range: str = "",
    components: str = "",
    duration: str = "",
    description: str = "",
    ritual: bool = False,
    concentration: bool = False,
    properties: dict | None = None,
    spell_id: int | None = None,
) -> int:
    """Create or update one campaign spell. Returns the spell id."""
    fields = _normalize_spell_fields(
        name,
        level=level,
        school=school,
        casting_time=casting_time,
        range=range,
        components=components,
        duration=duration,
        description=description,
        ritual=ritual,
        concentration=concentration,
        properties=properties,
    )
    with db:
        if spell_id is None:
            conflict = db.execute(
                "SELECT id FROM campaign_spells WHERE name=? COLLATE NOCASE",
                (fields["name"],),
            ).fetchone()
            if conflict is not None:
                raise ValueError(f"A spell named {fields['name']} already exists.")
            cursor = db.execute(
                """
                INSERT INTO campaign_spells (
                    name, level, school, casting_time, range, components,
                    duration, description, ritual, concentration, properties
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["name"],
                    fields["level"],
                    fields["school"],
                    fields["casting_time"],
                    fields["range"],
                    fields["components"],
                    fields["duration"],
                    fields["description"],
                    fields["ritual"],
                    fields["concentration"],
                    fields["properties"],
                ),
            )
            return int(cursor.lastrowid)
        row = db.execute(
            "SELECT id FROM campaign_spells WHERE id=?",
            (spell_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown spell id {spell_id}.")
        conflict = db.execute(
            """
            SELECT id FROM campaign_spells
            WHERE name=? COLLATE NOCASE AND id!=?
            """,
            (fields["name"], spell_id),
        ).fetchone()
        if conflict is not None:
            raise ValueError(f"A spell named {fields['name']} already exists.")
        db.execute(
            """
            UPDATE campaign_spells SET
                name=?, level=?, school=?, casting_time=?, range=?,
                components=?, duration=?, description=?, ritual=?,
                concentration=?, properties=?
            WHERE id=?
            """,
            (
                fields["name"],
                fields["level"],
                fields["school"],
                fields["casting_time"],
                fields["range"],
                fields["components"],
                fields["duration"],
                fields["description"],
                fields["ritual"],
                fields["concentration"],
                fields["properties"],
                spell_id,
            ),
        )
        return int(spell_id)


def upsert_campaign_spell_if_missing(db, entry: dict, *, commit: bool = True) -> int | None:
    """Insert a spell definition when the name is absent. Returns new id or None.

    Does not overwrite an existing same-name row. Pass ``commit=True`` when the
    caller will not commit (e.g. Spells tab seed button).
    """
    name = (entry.get("name") or "").strip()
    if not name:
        raise ValueError("Spell names cannot be empty.")
    existing = db.execute(
        "SELECT id FROM campaign_spells WHERE name=? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if existing is not None:
        return None
    fields = _normalize_spell_fields(
        name,
        level=entry.get("level", 0),
        school=entry.get("school", ""),
        casting_time=entry.get("casting_time", ""),
        range=entry.get("range", ""),
        components=entry.get("components", ""),
        duration=entry.get("duration", ""),
        description=entry.get("description", ""),
        ritual=bool(entry.get("ritual")),
        concentration=bool(entry.get("concentration")),
        properties=entry.get("properties") if isinstance(entry.get("properties"), dict) else {},
    )
    cursor = db.execute(
        """
        INSERT INTO campaign_spells (
            name, level, school, casting_time, range, components,
            duration, description, ritual, concentration, properties
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields["name"],
            fields["level"],
            fields["school"],
            fields["casting_time"],
            fields["range"],
            fields["components"],
            fields["duration"],
            fields["description"],
            fields["ritual"],
            fields["concentration"],
            fields["properties"],
        ),
    )
    if commit:
        db.commit()
    return int(cursor.lastrowid)


def delete_campaign_spell(db, spell_id: int) -> None:
    with db:
        deleted = db.execute(
            "DELETE FROM campaign_spells WHERE id=?",
            (spell_id,),
        ).rowcount
        if not deleted:
            raise ValueError(f"Unknown spell id {spell_id}.")


def get_character_spells(db, character_id: int):
    return db.execute(
        """
        SELECT campaign_spells.id, campaign_spells.name, campaign_spells.level,
               campaign_spells.school, campaign_spells.description,
               character_spells.known, character_spells.prepared,
               character_spells.notes
        FROM character_spells
        JOIN campaign_spells ON campaign_spells.id = character_spells.spell_id
        WHERE character_spells.character_id = ?
        ORDER BY campaign_spells.level, campaign_spells.name COLLATE NOCASE
        """,
        (character_id,),
    ).fetchall()


def set_character_spell(
    db,
    character_id: int,
    spell_id: int,
    *,
    known: bool = True,
    prepared: bool = False,
    notes: str = "",
) -> None:
    with db:
        db.execute(
            """
            INSERT INTO character_spells (
                character_id, spell_id, known, prepared, notes
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(character_id, spell_id) DO UPDATE SET
                known=excluded.known,
                prepared=excluded.prepared,
                notes=excluded.notes
            """,
            (
                character_id,
                spell_id,
                1 if known else 0,
                1 if prepared else 0,
                (notes or "").strip(),
            ),
        )


def find_spell_names_in_text(text: str, catalog_names: list[str]) -> list[str]:
    """Return catalog names that appear as whole phrases in ``text``.

    Matching is case-insensitive. Longer names win first so
    ``Protection from Evil and Good`` is preferred over a hypothetical
    shorter overlap. Apostrophes in names (Hunter's Mark) are supported.
    """
    if not isinstance(text, str) or not text.strip() or not catalog_names:
        return []
    # Sort longest first to prefer specific multi-word titles.
    ordered = sorted({name.strip() for name in catalog_names if name.strip()}, key=len, reverse=True)
    found = []
    remaining = text
    for name in ordered:
        pattern = re.compile(rf"(?<![A-Za-z0-9']){re.escape(name)}(?![A-Za-z0-9'])", re.IGNORECASE)
        if pattern.search(remaining):
            found.append(name)
            # Remove matched spans so overlapping shorter names are less likely.
            remaining = pattern.sub(" ", remaining)
    # Stable alpha order for callers.
    return sorted(found, key=str.casefold)


def link_character_spells_from_free_text(
    db,
    character_id: int,
    text: str,
    *,
    mark_prepared: bool = False,
) -> dict:
    """Link catalog spells named in free text. Never edits the free-text field.

    Only exact catalog title matches become ``character_spells`` rows.
    Unrecognized prose is left alone. Defaults to known=True, prepared=False
    so migration does not invent preparation state.
    """
    catalog = {row["name"]: row["id"] for row in get_spells(db)}
    matched_names = find_spell_names_in_text(text, list(catalog))
    linked = []
    for name in matched_names:
        spell_id = catalog[name]
        existing = db.execute(
            """
            SELECT 1 FROM character_spells
            WHERE character_id=? AND spell_id=?
            """,
            (character_id, spell_id),
        ).fetchone()
        if existing is not None:
            continue
        set_character_spell(
            db,
            character_id,
            spell_id,
            known=True,
            prepared=bool(mark_prepared),
            notes="",
        )
        linked.append(name)
    return {"matched": matched_names, "linked": linked}


def migrate_character_spell_free_text(db) -> dict:
    """Conservatively link free-text character spells to the catalog.

    Does not modify ``characters.data`` spell prose. Returns summary counts.
    """
    characters = db.execute("SELECT id, name, data FROM characters").fetchall()
    linked_total = 0
    matched_total = 0
    scanned = 0
    for row in characters:
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        text = data.get("spells")
        if not isinstance(text, str) or not text.strip():
            continue
        scanned += 1
        result = link_character_spells_from_free_text(db, row["id"], text)
        matched_total += len(result["matched"])
        linked_total += len(result["linked"])
    return {
        "characters_scanned": scanned,
        "names_matched": matched_total,
        "links_created": linked_total,
    }

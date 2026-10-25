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

# Per-spell casting paradigm. Characters may mix both in one loadout.
CASTING_MODES = frozenset({"vancian", "mana"})
DEFAULT_CASTING_MODE = "vancian"


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


def spell_casting_mode(spell_row_or_props) -> str:
    """Return ``vancian`` or ``mana`` for a campaign spell row or properties dict.

    Stock 5e titles default to Vancian (known/prepared). Mana-mode spells are
    loadout membership only — prepared does not apply. Mode is per spell so a
    character can carry both kinds.
    """
    props = spell_row_or_props
    if not isinstance(props, dict):
        raw = None
        try:
            raw = spell_row_or_props["properties"]
        except (TypeError, KeyError, IndexError):
            raw = None
        if isinstance(raw, str):
            try:
                props = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                props = {}
        elif isinstance(raw, dict):
            props = raw
        else:
            props = {}
    mode = str(props.get("casting_mode") or DEFAULT_CASTING_MODE).strip().casefold()
    return mode if mode in CASTING_MODES else DEFAULT_CASTING_MODE


def spell_uses_prepared(spell_row_or_props) -> bool:
    """True when this spell's casting mode uses Vancian preparation."""
    return spell_casting_mode(spell_row_or_props) == "vancian"


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
    casting_mode: str | None = None,
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
    props = dict(properties) if isinstance(properties, dict) else {}
    mode_source = casting_mode if casting_mode is not None else props.get("casting_mode")
    mode = str(mode_source or DEFAULT_CASTING_MODE).strip().casefold()
    if mode not in CASTING_MODES:
        raise ValueError(
            f"Unknown casting mode {mode_source!r}; expected vancian or mana."
        )
    props["casting_mode"] = mode
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
    casting_mode: str | None = None,
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
        casting_mode=casting_mode,
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
    entry_props = (
        entry.get("properties") if isinstance(entry.get("properties"), dict) else {}
    )
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
        casting_mode=entry.get("casting_mode"),
        properties=entry_props,
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
    """Return this character's spellbook loadout (catalog rows they know)."""
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


def _normalize_prepared_count(prepared, *, vancian: bool) -> int:
    """Coerce prepared to a non-negative integer slot count.

    5e-style lists typically use 0/1. Classic Vancian may store multiple
    preparations of the same title. Mana-mode spells always store 0.
    """
    if not vancian:
        return 0
    try:
        count = int(prepared)
    except (TypeError, ValueError):
        count = 1 if prepared else 0
    return max(0, count)


def set_character_spell(
    db,
    character_id: int,
    spell_id: int,
    *,
    known: bool = True,
    prepared: int | bool = 0,
    notes: str = "",
) -> None:
    mode = spell_casting_mode(get_spell(db, spell_id) or {})
    prepared_count = _normalize_prepared_count(
        prepared, vancian=(mode == "vancian")
    )
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
                prepared_count,
                (notes or "").strip(),
            ),
        )


def set_character_spells(db, character_id: int, assignments) -> None:
    """Replace a character's spellbook loadout.

    Each assignment is ``(spell_id, known, prepared, notes)``. Rows with
    ``known`` false are omitted (not in the loadout). ``prepared`` is an
    integer preparation count for Vancian spells (0/1 for typical 5e lists;
    higher for multi-slot Vancian). Mana-mode spells store prepared=0.
    """
    mode_by_id = {
        int(row["id"]): spell_casting_mode(row) for row in get_spells(db)
    }
    normalized = []
    for entry in assignments:
        if len(entry) == 3:
            spell_id, known, prepared = entry
            notes = ""
        else:
            spell_id, known, prepared, notes = entry
        spell_id = int(spell_id)
        known = bool(known)
        if not known:
            continue
        vancian = mode_by_id.get(spell_id, DEFAULT_CASTING_MODE) == "vancian"
        prepared_count = _normalize_prepared_count(prepared, vancian=vancian)
        normalized.append(
            (
                character_id,
                spell_id,
                1,
                prepared_count,
                (notes or "").strip(),
            )
        )
    with db:
        db.execute(
            "DELETE FROM character_spells WHERE character_id=?",
            (character_id,),
        )
        db.executemany(
            """
            INSERT INTO character_spells (
                character_id, spell_id, known, prepared, notes
            ) VALUES (?, ?, ?, ?, ?)
            """,
            normalized,
        )


def spell_notes_blurb(data: dict | None) -> str:
    """Return free-text spell notes kept beside the structured loadout."""
    if not isinstance(data, dict):
        return ""
    text = data.get("spells")
    if isinstance(text, list):
        return "\n".join(str(line) for line in text).strip()
    if isinstance(text, dict):
        return json.dumps(text, ensure_ascii=False, indent=2)
    return str(text or "").strip()


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
    mark_prepared: int | bool = 0,
) -> dict:
    """Link catalog spells named in free text. Never edits the free-text field.

    Only exact catalog title matches become ``character_spells`` rows.
    Unrecognized prose is left alone. Defaults to known with prepared=0 so
    migration does not invent preparation counts.
    """
    catalog = {row["name"]: row["id"] for row in get_spells(db)}
    matched_names = find_spell_names_in_text(text, list(catalog))
    linked = []
    prepared_count = _normalize_prepared_count(mark_prepared, vancian=True)
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
            prepared=prepared_count,
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

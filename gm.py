# gm.py — prompt, reply parsing, and campaign updates
import json

from item_db import get_character_items, get_items
from quest_db import get_quest_characters, get_quests

HP_CURRENT = "hp_current"
HP_MAX = "hp_max"

JSON_CONTRACT = """Reply with one JSON object and no other text:
{
  "narration": "what the table hears",
  "updates": {
    "characters": {"Exact Name": {"hp_current": 8}},
    "world": {"location": "place", "events": ["what just happened"]}
  }
}
Use the exact character names as keys. Omit a character to leave them unchanged.
Hit points are hp_current and hp_max. For world.events, a string is appended and a list replaces the log.
"""

DEFAULT_INSTRUCTIONS = (
    "You are an expert Game Master.\n"
    "Instructions prefaced with [Play] continue the story. "
    "Other instructions are bookkeeping."
)


def stored_system_prompt(db) -> str:
    row = db.execute(
        "SELECT value FROM campaign_settings WHERE key='system_prompt'"
    ).fetchone()
    if row is None or not isinstance(row["value"], str):
        return ""
    return row["value"].strip()


def bootstrap_instructions(db) -> str:
    """Imported instruction note, or the built-in GM line when the campaign has none."""
    rows = db.execute("SELECT title, content FROM notes").fetchall()
    for row in rows:
        title = (row["title"] or "").lower()
        if "instruction" in title and row["content"]:
            return row["content"].strip()
    return DEFAULT_INSTRUCTIONS


def campaign_instructions(db) -> str:
    """Campaign text wins. An empty setting leaves the bootstrap in place."""
    return stored_system_prompt(db) or bootstrap_instructions(db)


def world_value(db, key, default):
    row = db.execute("SELECT value FROM world WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    raw = row["value"]
    if key != "events":
        return raw
    try:
        value = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return [raw] if raw else []
    if isinstance(value, list):
        return value
    return [value]


def build_messages(db, action: str):
    """Return (system, user) for one GM turn. The user text is prefixed with [Play]."""
    characters = []
    for row in db.execute("SELECT id, name, data FROM characters ORDER BY name"):
        try:
            data = json.loads(row["data"])
        except json.JSONDecodeError:
            data = {"error": "unreadable"}
        if isinstance(data, dict):
            data.pop("inventory", None)
            data.pop("attuned", None)
        assignments = get_character_items(db, row["id"])
        characters.append({
            "name": row["name"],
            "data": data,
            "items": [
                {
                    "name": item["name"],
                    "description": item["description"],
                    "quantity": item["quantity"],
                    "attuned": bool(item["attuned"]),
                    "equipment_location": item["equipment_location"],
                }
                for item in assignments
            ],
        })

    notes = [
        {"title": row["title"], "content": row["content"]}
        for row in db.execute("SELECT title, content FROM notes ORDER BY title")
    ]
    item_catalog = [
        {"name": item["name"], "description": item["description"]}
        for item in get_items(db)
    ]
    quests = [
        {
            "name": quest["name"],
            "description": quest["description"],
            "status": quest["status"],
            "characters": [
                character["name"]
                for character in get_quest_characters(db, quest["id"])
            ],
        }
        for quest in get_quests(db)
    ]
    log_rows = db.execute(
        "SELECT speaker, message FROM log ORDER BY id DESC LIMIT 12"
    ).fetchall()
    recent = [
        {"speaker": row["speaker"], "message": row["message"]}
        for row in reversed(log_rows)
    ]
    played = action.strip()
    if not played.startswith("[Play]"):
        played = "[Play]\n" + played

    prompt = world_value(db, "prompt", "")
    if not isinstance(prompt, str):
        prompt = ""
    prompt = prompt.strip()
    system_parts = [campaign_instructions(db).rstrip()]
    if prompt:
        system_parts.append("WORLD\n" + prompt)
    system_parts.append(JSON_CONTRACT.strip())

    user = "\n".join([
        "CURRENT STATE",
        "Characters:",
        json.dumps(characters, ensure_ascii=False),
        f"Location: {world_value(db, 'location', 'Unknown')}",
        "Events:",
        json.dumps(world_value(db, "events", []), ensure_ascii=False),
        "Notes:",
        json.dumps(notes, ensure_ascii=False),
        "Item catalog:",
        json.dumps(item_catalog, ensure_ascii=False),
        "Quests:",
        json.dumps(quests, ensure_ascii=False),
        "Recent log:",
        json.dumps(recent, ensure_ascii=False),
        "",
        "PLAYER ACTION",
        played,
    ])
    system = "\n\n".join(system_parts)
    return system, user


def format_clipboard(system: str, user: str) -> str:
    return system.rstrip() + "\n\n" + user


def parse_reply(text: str):
    """Return (narration, updates or None)."""
    raw = (text or "").strip()
    if not raw:
        return "", None
    obj = _extract_object(raw)
    if not isinstance(obj, dict):
        return raw, None
    updates = obj.get("updates")
    if not isinstance(updates, dict) and ("characters" in obj or "world" in obj):
        updates = {key: obj[key] for key in ("characters", "world") if key in obj}
    if not isinstance(updates, dict):
        updates = None
    narration = obj.get("narration")
    if not isinstance(narration, str) or not narration.strip():
        narration = raw
    return narration.strip(), updates


def _extract_object(text: str):
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return None
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict) and (
            "updates" in value or "narration" in value or "characters" in value or "world" in value
        ):
            return value
        index = start + 1


def canonicalize_character_patch(patch: dict) -> dict:
    out = dict(patch)
    if HP_CURRENT not in out and "hp" in out:
        out[HP_CURRENT] = out["hp"]
    out.pop("hp", None)
    if HP_MAX not in out and "max_hp" in out:
        out[HP_MAX] = out["max_hp"]
    out.pop("max_hp", None)
    return out


def apply_updates(db, updates: dict) -> list:
    """Merge updates into the open campaign. Returns log lines."""
    if not isinstance(updates, dict):
        return ["No state updates in the reply"]
    messages = []
    changed = False
    characters = updates.get("characters")
    if isinstance(characters, dict):
        for name, patch in characters.items():
            if not isinstance(patch, dict):
                messages.append(f"Skipped {name}: update was not an object")
                continue
            row = db.execute(
                "SELECT id, data FROM characters WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                messages.append(f"Unknown character: {name}")
                continue
            try:
                current = json.loads(row["data"])
            except json.JSONDecodeError:
                current = {}
            current.update(canonicalize_character_patch(patch))
            new_name = current.get("name") or name
            db.execute(
                "UPDATE characters SET name=?, data=? WHERE id=?",
                (new_name, json.dumps(current, ensure_ascii=False), row["id"]),
            )
            changed = True
    world = updates.get("world")
    if isinstance(world, dict) and world:
        for key, value in world.items():
            if key == "events":
                _write_events(db, value)
            else:
                stored = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                db.execute(
                    "INSERT OR REPLACE INTO world (key, value) VALUES (?, ?)",
                    (key, stored),
                )
        changed = True
    if changed:
        db.commit()
        messages.append("State updated")
    elif not messages:
        return ["No state updates in the reply"]
    return messages


def _write_events(db, value):
    if isinstance(value, list):
        stored = value
    elif isinstance(value, str):
        current = world_value(db, "events", [])
        current.append(value)
        stored = current
    else:
        current = world_value(db, "events", [])
        current.append(str(value))
        stored = current
    db.execute(
        "INSERT OR REPLACE INTO world (key, value) VALUES ('events', ?)",
        (json.dumps(stored, ensure_ascii=False),),
    )

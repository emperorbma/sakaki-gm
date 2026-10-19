# gm.py — prompt, reply parsing, and campaign updates
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sakaki_gm.item_db import (
    get_character_inventory_container,
    get_character_items,
    get_items,
)
from sakaki_gm.quest_db import get_quest_characters, get_quests
from sakaki_gm.skill_db import get_character_skills, skill_bonus

HP_CURRENT = "hp_current"
HP_MAX = "hp_max"
OBSOLETE_REFERENCE_PREFIX = "[OBSOLETE REFERENCE]"
SUPPORTED_PROPOSAL_OPS = frozenset({"spend_use"})

JSON_CONTRACT = """Reply with one JSON object and no other text:
{
  "narration": "what the table hears",
  "updates": {
    "characters": {"Exact Name": {"hp_current": 8}},
    "world": {"location": "place", "events": ["what just happened"]}
  },
  "cited_record_ids": ["document:11", "object:catalog-item:4"],
  "proposals": [
    {
      "op": "spend_use",
      "character": "Exact Name",
      "item_object_id": "catalog-item:4",
      "use": "detect_magic",
      "summary": "Ward Detect Magic spent until long rest"
    }
  ]
}
Use the exact character names as keys. Omit a character to leave them unchanged.
Hit points are hp_current and hp_max. For world.events, a string is appended and a list replaces the log.
cited_record_ids lists scratch record_id values you relied on; use [] if none.
proposals are suggested writes that need approval; never put them inside updates.
Supported proposal ops: spend_use.
"""


@dataclass
class ParsedReply:
    narration: str
    updates: dict | None = None
    cited_record_ids: list[str] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)

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
        if (
            not title.startswith(OBSOLETE_REFERENCE_PREFIX.lower())
            and "instruction" in title
            and row["content"]
        ):
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


def build_messages(db, action: str, *, include_reference_material: bool = True):
    """Return (system, user) for one GM turn. The user text is prefixed with [Play]."""
    characters = []
    for row in db.execute(
        "SELECT id, name, data, campaign_role, object_id "
        "FROM characters ORDER BY name"
    ):
        try:
            data = json.loads(row["data"])
        except json.JSONDecodeError:
            data = {"error": "unreadable"}
        if isinstance(data, dict):
            data.pop("inventory", None)
            data.pop("attuned", None)
            data.pop("skills", None)
        assignments = get_character_items(db, row["id"])
        skills = []
        if isinstance(data, dict):
            proficiency_bonus = data.get("proficiency_bonus", 2)
            for skill in get_character_skills(db, row["id"]):
                proficient = bool(skill["proficient"])
                expertise = bool(skill["expertise"])
                if not proficient and not expertise:
                    continue
                ability = skill["ability"]
                skills.append({
                    "id": skill["id"],
                    "name": skill["name"],
                    "ability": ability,
                    "proficient": proficient,
                    "expertise": expertise,
                    "bonus": skill_bonus(
                        data.get(ability) if ability else None,
                        proficiency_bonus,
                        proficient,
                        expertise,
                    ),
                })
        characters.append({
            "name": row["name"],
            "campaign_role": row["campaign_role"],
            "object_id": row["object_id"],
            "inventory_container_object_id": get_character_inventory_container(
                db, row["id"]
            ),
            "data": data,
            "skills": skills,
            "items": [
                {
                    "object_id": item["id"],
                    "name": item["name"],
                    "description": item["description"],
                    "quantity": item["quantity"],
                    "attuned": bool(item["attuned"]),
                    "equipment_location": item["equipment_location"],
                }
                for item in assignments
            ],
        })

    notes = []
    item_catalog = []
    if include_reference_material:
        notes = [
            {"title": row["title"], "content": row["content"]}
            for row in db.execute("SELECT title, content FROM notes ORDER BY title")
            if not (row["title"] or "").startswith(OBSOLETE_REFERENCE_PREFIX)
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

    user_lines = [
        "CURRENT STATE",
        "Characters:",
        json.dumps(characters, ensure_ascii=False),
        f"Location: {world_value(db, 'location', 'Unknown')}",
        "Events:",
        json.dumps(world_value(db, "events", []), ensure_ascii=False),
    ]
    if include_reference_material:
        user_lines.extend([
            "Notes:",
            json.dumps(notes, ensure_ascii=False),
            "Item catalog:",
            json.dumps(item_catalog, ensure_ascii=False),
        ])
    else:
        user_lines.append(
            "Campaign reference documents and catalog objects can be searched "
            "with the lookup tools."
        )
    user_lines.extend([
        "Quests:",
        json.dumps(quests, ensure_ascii=False),
        "Recent log:",
        json.dumps(recent, ensure_ascii=False),
        "",
        "PLAYER ACTION",
        played,
    ])
    user = "\n".join(user_lines)
    system = "\n\n".join(system_parts)
    return system, user


def format_clipboard(system: str, user: str) -> str:
    return system.rstrip() + "\n\n" + user


def parse_reply(text: str) -> ParsedReply:
    """Parse a GM JSON reply into narration, updates, citations, and proposals."""
    raw = (text or "").strip()
    if not raw:
        return ParsedReply(narration="")
    obj = _extract_object(raw)
    if not isinstance(obj, dict):
        return ParsedReply(narration=raw)
    updates = obj.get("updates")
    if not isinstance(updates, dict) and ("characters" in obj or "world" in obj):
        updates = {key: obj[key] for key in ("characters", "world") if key in obj}
    if not isinstance(updates, dict):
        updates = None
    narration = obj.get("narration")
    if not isinstance(narration, str) or not narration.strip():
        narration = raw
    return ParsedReply(
        narration=narration.strip(),
        updates=updates,
        cited_record_ids=_normalize_record_ids(obj.get("cited_record_ids")),
        proposals=normalize_proposals(obj.get("proposals")),
    )


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
            "updates" in value
            or "narration" in value
            or "characters" in value
            or "world" in value
            or "cited_record_ids" in value
            or "proposals" in value
        ):
            return value
        index = start + 1


def _normalize_record_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    ids = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in ids:
            ids.append(item.strip())
    return ids


def normalize_proposals(value) -> list[dict]:
    """Keep well-formed proposal objects; drop unknown ops and malformed rows."""
    if not isinstance(value, list):
        return []
    proposals = []
    for item in value:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        if not isinstance(op, str) or op.strip() not in SUPPORTED_PROPOSAL_OPS:
            continue
        proposal = {"op": op.strip()}
        for key in ("character", "item_object_id", "use", "summary", "record_id"):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                proposal[key] = raw.strip()
        if proposal["op"] == "spend_use":
            if "character" not in proposal or "use" not in proposal:
                continue
        proposals.append(proposal)
    return proposals


def citation_messages(scratch, cited_record_ids) -> list[str]:
    """Return System log lines for citation validation against turn scratch."""
    if not cited_record_ids:
        return []
    if scratch is None:
        return [
            "Cited record IDs were present but no scratch workspace was available "
            "to verify them."
        ]
    unknown = scratch.unknown_citations(cited_record_ids)
    known = [record_id for record_id in cited_record_ids if record_id not in unknown]
    messages = []
    if known:
        messages.append(
            f"Cited {len(known)} scratch record"
            f"{'' if len(known) == 1 else 's'}."
        )
    if unknown:
        messages.append(
            "Unknown cited record IDs (ignored for evidence): "
            + ", ".join(repr(item) for item in unknown)
        )
    return messages


def proposal_summary_lines(proposals: list[dict]) -> list[str]:
    lines = []
    for proposal in proposals:
        summary = proposal.get("summary")
        if summary:
            lines.append(f"{proposal['op']}: {summary}")
            continue
        if proposal["op"] == "spend_use":
            target = proposal.get("item_object_id") or "item"
            lines.append(
                f"spend_use: {proposal['character']} / {proposal['use']} ({target})"
            )
        else:
            lines.append(proposal["op"])
    return lines


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


def apply_proposals(db, proposals: list[dict]) -> list:
    """Apply approved proposals. Returns log lines."""
    if not proposals:
        return ["No proposals to apply"]
    messages = []
    changed = False
    for proposal in normalize_proposals(proposals):
        if proposal["op"] == "spend_use":
            line = _apply_spend_use(db, proposal)
            messages.append(line)
            if not line.startswith("Skipped"):
                changed = True
        else:
            messages.append(f"Skipped unsupported proposal op {proposal['op']!r}")
    if changed:
        db.commit()
        messages.append("Proposals applied")
    elif not messages:
        return ["No proposals to apply"]
    return messages


def _apply_spend_use(db, proposal: dict) -> str:
    name = proposal["character"]
    use = proposal["use"]
    row = db.execute(
        "SELECT id, data FROM characters WHERE name=?", (name,)
    ).fetchone()
    if row is None:
        return f"Skipped spend_use for unknown character: {name}"
    try:
        current = json.loads(row["data"])
    except json.JSONDecodeError:
        current = {}
    if not isinstance(current, dict):
        current = {}
    spent = current.get("spent_uses")
    if not isinstance(spent, list):
        spent = []
    entry = {
        "use": use,
        "spent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if "item_object_id" in proposal:
        entry["item_object_id"] = proposal["item_object_id"]
    if "summary" in proposal:
        entry["summary"] = proposal["summary"]
    if "record_id" in proposal:
        entry["record_id"] = proposal["record_id"]
    spent.append(entry)
    current["spent_uses"] = spent
    db.execute(
        "UPDATE characters SET data=? WHERE id=?",
        (json.dumps(current, ensure_ascii=False), row["id"]),
    )
    label = proposal.get("summary") or f"{name}: {use}"
    return f"Recorded spent use — {label}"


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

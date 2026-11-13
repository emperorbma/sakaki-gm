# gm.py — prompt, reply parsing, and campaign updates
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sakaki_gm.checks import (
    ability_check_bonus,
    attack_bonus,
    format_check_result,
    perform_check,
    skill_check_bonus,
)
from sakaki_gm.item_db import (
    get_character_inventory_container,
    get_character_items,
    get_items,
    resolve_item,
)
from sakaki_gm.object_graph_db import get_navigation_exits
from sakaki_gm.quest_db import get_quest_characters, get_quests
from sakaki_gm.scratch_context import snapshot_revision
from sakaki_gm.skill_db import get_character_skills, skill_bonus

HP_CURRENT = "hp_current"
HP_MAX = "hp_max"
OBSOLETE_REFERENCE_PREFIX = "[OBSOLETE REFERENCE]"
SUPPORTED_PROPOSAL_OPS = frozenset({
    "spend_use",
    "inventory_add",
    "inventory_update",
    "inventory_remove",
    "note_append",
    "link_create",
    "roll_check",
})
ALLOWED_LINK_RELATIONS = frozenset({"located_at", "contains"})
ROLL_CHECK_KINDS = frozenset({"skill", "ability", "attack"})

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
    },
    {
      "op": "roll_check",
      "character": "Exact Name",
      "check": "skill",
      "skill": "Stealth",
      "dc": 15,
      "summary": "Slip past the wraith"
    }
  ]
}
Use the exact character names as keys. Omit a character to leave them unchanged.
Hit points are hp_current and hp_max. For world.events, a string is appended and a list replaces the log.
cited_record_ids lists scratch record_id values you relied on; use [] if none.
proposals are suggested writes that need approval; never put them inside updates.
Supported proposal ops: spend_use, inventory_add, inventory_update, inventory_remove,
note_append, link_create (located_at or contains only), roll_check.
roll_check: you decide that a check is needed; the app rolls and scores it from
bottle stats and item property sets (no LLM math). check is skill, ability, or
attack. skill needs skill+dc; ability needs ability+dc; attack needs
item_object_id (owned weapon copy or archetype) and ac (or dc), optional
proficient/advantage/disadvantage.
narration is table-facing story only. Never echo app or prompt bookkeeping
(seat, desk, controller, operator, muse, model, scene pack, payload, bottle,
or similar). In-world wording is fine when the fiction itself uses it.
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


SCENE_LOG_LIMIT = 6
FULL_LOG_LIMIT = 12


def build_messages(
    db,
    action: str,
    *,
    include_reference_material: bool = True,
    seat_id: int | None = None,
):
    """Return (system, user) for one GM turn. The user text is prefixed with [Play].

    Ask GM passes include_reference_material=False and receives a scene pack:
    acting character short state, place plus routes, and a short log tail.
    Clipboard copy keeps the full bottle dump when the flag is True.

    ``seat_id`` selects whose bound PC is the acting character when the action
    does not name someone (multi-player: each player seat owns a character_id;
    GM temporary lease uses the GM seat's character_id). Pass the *desk* seat,
    not the shared campaign ``active_seat_id``, so multi-window tables keep
    each Ask lens on the correct controller.
    """
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
    system = "\n\n".join(system_parts)

    if include_reference_material:
        user = _full_reference_user_message(db, played)
    else:
        user = _scene_user_message(db, action, played, seat_id=seat_id)
    return system, user


def _full_reference_user_message(db, played: str) -> str:
    characters = []
    for row in db.execute(
        "SELECT id, name, data, campaign_role, object_id "
        "FROM characters ORDER BY name"
    ):
        characters.append(_full_character_payload(db, row))
    notes = [
        {"title": row["title"], "content": row["content"]}
        for row in db.execute("SELECT title, content FROM notes ORDER BY title")
        if not (row["title"] or "").startswith(OBSOLETE_REFERENCE_PREFIX)
    ]
    item_catalog = [
        {"name": item["name"], "description": item["description"]}
        for item in get_items(db)
    ]
    quests = _quest_payloads(db)
    recent = _recent_log(db, FULL_LOG_LIMIT)
    return "\n".join([
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


def _scene_user_message(
    db, action: str, played: str, *, seat_id: int | None = None
) -> str:
    from sakaki_gm.seat_db import get_active_seat, get_seat

    scene = build_scene_pack(db, action, seat_id=seat_id)
    seat = get_seat(db, seat_id) if seat_id is not None else None
    if seat is None:
        try:
            seat = get_active_seat(db)
        except Exception:
            seat = None
    gm_lease = (
        seat is not None
        and seat.get("role") == "gm"
        and seat.get("character_id") is not None
        and scene.get("acting_character")
    )
    parts = ["CURRENT SCENE"]
    # GM temporary control: cut prior possession narration; "you" is this body only.
    # Keep bookkeeping out of table-facing words the model might echo.
    if gm_lease:
        acting_name = scene["acting_character"].get("name") or "the acting character"
        scene_place = (scene.get("place") or {}).get("name") or scene.get(
            "location_text"
        )
        viewpoint = (
            f"Viewpoint: you are {acting_name} for this turn. "
            "Second-person \"you\", worn gear, and body refer ONLY to that "
            "character's state below. Earlier turns about other characters are "
            "omitted on purpose — do not carry them over, and do not explain "
            "how this viewpoint was chosen."
        )
        if scene_place:
            viewpoint += f" Play them at {scene_place}."
        table_focus = scene.get("table_focus")
        if (
            isinstance(table_focus, dict)
            and table_focus.get("name")
            and scene_place
            and str(table_focus["name"]).casefold() != str(scene_place).casefold()
        ):
            viewpoint += (
                f" Another party is focused on {table_focus['name']}; "
                "stay where you are unless the action says otherwise, and do "
                "not narrate teleport or map gaps."
            )
        parts.append(viewpoint)
    parts.extend([
        "Acting character:",
        json.dumps(scene["acting_character"], ensure_ascii=False),
        f"Location: {scene['location_text']}",
        "Place:",
        json.dumps(scene["place"], ensure_ascii=False),
    ])
    if scene.get("focus_companions"):
        parts.extend([
            "Focus companions (same GM Ask group; not separate Actors):",
            json.dumps(scene["focus_companions"], ensure_ascii=False),
        ])
    if scene.get("table_focus"):
        parts.extend([
            "Other party focus (may differ from this viewpoint):",
            json.dumps(scene["table_focus"], ensure_ascii=False),
        ])
    parts.extend([
        "Routes:",
        json.dumps(scene["routes"], ensure_ascii=False),
        "Recent log:",
        json.dumps(scene["recent_log"], ensure_ascii=False),
        "Campaign reference documents and the item catalog are omitted here. "
        "In tools mode, search them with the lookup tools. In context mode, "
        "the app attaches lore only for names outside this scene.",
        "",
    ])
    if seat is not None and seat.get("role") == "gm":
        parts.append("ACTION")
    else:
        parts.append("PLAYER ACTION")
    parts.append(played)
    return "\n".join(parts)


def build_scene_pack(db, action: str, *, seat_id: int | None = None) -> dict:
    """Selective scene for Ask GM: one actor, place, routes, short log."""
    from sakaki_gm.seat_db import (
        GM_CONTROL_SCENE_TABLE,
        get_active_seat,
        get_gm_control_scene,
        get_seat,
    )

    table_location_text = world_value(db, "location", "Unknown")
    if not isinstance(table_location_text, str) or not table_location_text.strip():
        table_location_text = "Unknown"
    table_place = _resolve_location_object(db, table_location_text)

    acting = _select_acting_character(db, action, seat_id=seat_id)
    seat = get_seat(db, seat_id) if seat_id is not None else None
    if seat is None:
        try:
            seat = get_active_seat(db)
        except Exception:
            seat = None

    location_text = table_location_text
    place = table_place
    table_focus = None
    gm_lease = (
        seat is not None
        and seat.get("role") == "gm"
        and seat.get("character_id") is not None
        and acting is not None
    )
    if gm_lease:
        scene_choice = get_gm_control_scene(db)
        if scene_choice != GM_CONTROL_SCENE_TABLE:
            chosen = _place_object_by_id(db, scene_choice)
            if chosen is not None:
                place = chosen
                location_text = chosen["name"] or table_location_text
        # When framing away from the party, keep table focus visible.
        if table_place is not None and (
            place is None
            or place.get("object_id") != table_place.get("object_id")
        ):
            table_focus = {
                "object_id": table_place["object_id"],
                "name": table_place["name"],
                "object_type": table_place["object_type"],
                "geography_kind": table_place["geography_kind"],
                "description": (table_place["description"] or "")[:1200],
                "location_text": table_location_text,
            }
        elif place is None and table_location_text != location_text:
            table_focus = {"location_text": table_location_text}

    routes = []
    if place is not None:
        try:
            for exit_row in get_navigation_exits(db, place["object_id"]):
                target = exit_row["target"]
                routes.append({
                    "relation": exit_row["relation"],
                    "inference": exit_row.get("inference"),
                    "target_object_id": target["object_id"],
                    "target_name": target["name"],
                    "target_geography_kind": target.get("geography_kind"),
                })
        except KeyError:
            routes = []

    include_gm_private = not (seat and seat.get("role") == "player")
    after_log_id = None
    focus_companions: list[dict] = []
    if gm_lease:
        after_log_id = _gm_focus_log_anchor_id(db, acting["name"])
        focus_companions = _gm_focus_companions(
            db, seat, acting_id=int(acting["id"]), action=action
        )
    pack = {
        "acting_character": (
            _short_character_payload(db, acting, action) if acting else None
        ),
        "focus_companions": focus_companions,
        "location_text": location_text,
        "place": (
            {
                "object_id": place["object_id"],
                "name": place["name"],
                "object_type": place["object_type"],
                "geography_kind": place["geography_kind"],
                "description": (place["description"] or "")[:1200],
            }
            if place else None
        ),
        "routes": routes,
        "recent_log": _recent_log(
            db,
            SCENE_LOG_LIMIT,
            include_gm_private=include_gm_private,
            after_log_id=after_log_id,
        ),
        "coverage_names": _scene_coverage_names(
            acting, place, routes, db, action, companions=focus_companions
        ),
    }
    if table_focus is not None:
        pack["table_focus"] = table_focus
    return pack


def _place_object_by_id(db, object_id: str):
    """Return a geography/object row by object_id, or None."""
    oid = (object_id or "").strip()
    if not oid:
        return None
    try:
        row = db.execute(
            """
            SELECT object_id, name, object_type, geography_kind, description
            FROM objects WHERE object_id=?
            """,
            (oid,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return dict(row)


def out_of_scene_lore_query(db, action: str, coverage_names=None) -> str | None:
    """Return a lore search string when the action names something outside the scene.

    Full notes/catalog search remains available via campaign lookup tools.
    """
    if coverage_names is None:
        coverage_names = build_scene_pack(db, action)["coverage_names"]
    covered = {
        name.casefold()
        for name in coverage_names
        if isinstance(name, str) and name.strip()
    }
    action_cf = (action or "").casefold()
    mentions = []

    def consider(label: str):
        text = (label or "").strip()
        if len(text) < 3:
            return
        key = text.casefold()
        if key in covered:
            return
        if not _label_mentioned_in_action(key, action_cf):
            return
        if text not in mentions:
            mentions.append(text)

    for row in db.execute("SELECT name FROM characters ORDER BY name"):
        consider(row["name"])
    for row in db.execute(
        "SELECT name FROM objects ORDER BY length(name) DESC, name"
    ):
        consider(row["name"])
    for row in db.execute("SELECT title FROM notes ORDER BY title"):
        title = row["title"] or ""
        if title.startswith(OBSOLETE_REFERENCE_PREFIX):
            continue
        consider(title)
    for quest in get_quests(db):
        consider(quest["name"])
    if not mentions:
        return None
    return " ".join(mentions[:12])


def _scene_coverage_names(
    acting, place, routes, db, action: str, *, companions: list | None = None
) -> list[str]:
    names = []
    if acting is not None:
        names.append(acting["name"])
        for item in _character_item_assignments(db, acting["id"]):
            if item.get("name"):
                names.append(item["name"])
    for companion in companions or []:
        if companion.get("name"):
            names.append(companion["name"])
    if place is not None and place.get("name"):
        names.append(place["name"])
    for route in routes:
        if route.get("target_name"):
            names.append(route["target_name"])
    location_text = world_value(db, "location", "")
    if isinstance(location_text, str) and location_text.strip():
        names.append(location_text.strip())
    return names


def _gm_focus_companions(
    db, seat, *, acting_id: int, action: str
) -> list[dict]:
    """Other members of the GM seat's Ask focus party (lease has none)."""
    from sakaki_gm.party_db import (
        _is_gm_control_party,
        get_party,
        party_member_ids,
    )

    if seat is None or seat.get("party_id") is None:
        return []
    party = get_party(db, int(seat["party_id"]))
    if party is None or not _is_gm_control_party(party):
        return []
    companions = []
    for member_id in party_member_ids(db, party["id"]):
        if int(member_id) == int(acting_id):
            continue
        row = db.execute(
            "SELECT id, name, data, campaign_role, object_id "
            "FROM characters WHERE id=?",
            (int(member_id),),
        ).fetchone()
        if row is None:
            continue
        companions.append(_short_character_payload(db, row, action))
    return companions


def note_gm_ask_for_actors(
    db, narration: str, *, seat_id: int | None = None
) -> list[int]:
    """Feed GM Ask narration into linked Actors for the current focus.

    Actors remain AI-driven; this only records what the GM just portrayed so
    later inferences can follow the table.
    """
    from sakaki_gm.actor_db import observe_gm_play_for_actors
    from sakaki_gm.party_db import (
        _is_gm_control_party,
        get_party,
        party_member_ids,
    )
    from sakaki_gm.seat_db import get_active_seat, get_seat

    text = (narration or "").strip()
    if not text:
        return []
    seat = get_seat(db, seat_id) if seat_id is not None else None
    if seat is None:
        try:
            seat = get_active_seat(db)
        except Exception:
            seat = None
    if seat is None or seat.get("role") != "gm":
        return []
    character_ids: list[int] = []
    if seat.get("party_id") is not None:
        party = get_party(db, int(seat["party_id"]))
        if party is not None and _is_gm_control_party(party):
            character_ids = [int(mid) for mid in party_member_ids(db, party["id"])]
    if not character_ids and seat.get("character_id") is not None:
        character_ids = [int(seat["character_id"])]
    if not character_ids:
        return []
    return observe_gm_play_for_actors(
        db, narration=text, character_ids=character_ids, commit=True
    )


def _select_acting_character(
    db, action: str, *, seat_id: int | None = None
):
    from sakaki_gm.campaign_roles import (
        campaign_role_tokens,
        is_playerish_campaign_role,
    )
    from sakaki_gm.seat_db import get_active_seat, get_seat

    rows = db.execute(
        "SELECT id, name, data, campaign_role, object_id "
        "FROM characters ORDER BY name"
    ).fetchall()
    if not rows:
        return None
    by_id = {int(row["id"]): row for row in rows}
    action_cf = (action or "").casefold()
    for row in rows:
        name = (row["name"] or "").strip()
        if len(name) >= 2 and name.casefold() in action_cf:
            return row

    # Multi-player: the seated controller's bound character is the default lens.
    bound_id = None
    seat = get_seat(db, seat_id) if seat_id is not None else None
    if seat is None:
        try:
            seat = get_active_seat(db)
        except Exception:
            seat = None
    if seat is not None and seat.get("character_id") is not None:
        bound_id = int(seat["character_id"])
        if bound_id in by_id:
            return by_id[bound_id]

    # Prefer an explicit protagonist, then any other playerish role.
    # Tokenize roles so "pc" does not match inside "NPC".
    protagonists = [
        row
        for row in rows
        if "protagonist" in campaign_role_tokens(row["campaign_role"])
    ]
    if protagonists:
        return protagonists[0]
    for row in rows:
        if is_playerish_campaign_role(row["campaign_role"]):
            return row
    return rows[0]


def _character_item_assignments(db, character_id: int) -> list:
    """Return inventory rows; empty when the bottle is read-only or unset."""
    try:
        return get_character_items(db, character_id)
    except sqlite3.OperationalError:
        return []


def _short_character_payload(db, row, action: str) -> dict:
    try:
        data = json.loads(row["data"])
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    assignments = _character_item_assignments(db, row["id"])
    action_cf = (action or "").casefold()
    named = []
    equipped = []
    for item in assignments:
        payload = {
            "object_id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "quantity": item["quantity"],
            "attuned": bool(item["attuned"]),
            "equipment_location": item["equipment_location"],
        }
        props = item.get("properties") or {}
        if props:
            payload["properties"] = props
        name = (item["name"] or "").casefold()
        if name and name in action_cf:
            named.append(payload)
        elif item["attuned"] or (item["equipment_location"] or "").strip():
            equipped.append(payload)
    gear = named or equipped or [
        {
            "object_id": item["id"],
            "name": item["name"],
            "quantity": item["quantity"],
            "attuned": bool(item["attuned"]),
            "equipment_location": item["equipment_location"],
            **(
                {"properties": item["properties"]}
                if item.get("properties")
                else {}
            ),
        }
        for item in assignments[:8]
    ]
    recorded_places = _character_recorded_places(db, row["object_id"])
    payload = {
        "name": row["name"],
        "campaign_role": row["campaign_role"],
        "object_id": row["object_id"],
        "hp_current": data.get("hp_current"),
        "hp_max": data.get("hp_max"),
        "ac": data.get("ac"),
        "gear": gear,
    }
    if recorded_places:
        payload["recorded_places"] = recorded_places
    return payload


def _character_recorded_places(db, character_object_id) -> list[dict]:
    """Compact located_at targets for scene packs (name + object_id)."""
    from sakaki_gm.seat_db import character_recorded_places

    return [
        {
            "object_id": place["object_id"],
            "name": place["name"],
            "geography_kind": place.get("geography_kind"),
        }
        for place in character_recorded_places(db, character_object_id)
    ]


def _full_character_payload(db, row) -> dict:
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
    return {
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
                **(
                    {"properties": item["properties"]}
                    if item.get("properties")
                    else {}
                ),
            }
            for item in assignments
        ],
    }


def _quest_payloads(db) -> list[dict]:
    return [
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


def _gm_lease_log_anchor_id(db, character_name: str) -> int | None:
    """Back-compat alias for :func:`_gm_focus_log_anchor_id`."""
    return _gm_focus_log_anchor_id(db, character_name)


def _gm_focus_log_anchor_id(db, character_name: str) -> int | None:
    """Latest System GM-focus marker; scene packs start after it."""
    from sakaki_gm.seat_db import gm_control_party_message

    name = (character_name or "").strip()
    markers = []
    if name:
        markers.extend(
            [
                gm_control_party_message(name),
                # Older bottles may still have the pre-party lease wording.
                f"GM controlling {name} (temporary lease)",
                f"GM temporary party: {name}",
            ]
        )
    try:
        if markers:
            placeholders = ", ".join("?" for _ in markers)
            row = db.execute(
                f"""
                SELECT id FROM log
                WHERE speaker = 'System' AND (
                    message IN ({placeholders})
                    OR message LIKE 'GM focus (%'
                )
                ORDER BY id DESC LIMIT 1
                """,
                markers,
            ).fetchone()
        else:
            row = db.execute(
                """
                SELECT id FROM log
                WHERE speaker = 'System' AND message LIKE 'GM focus (%'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return int(row["id"])


def _recent_log(
    db,
    limit: int,
    *,
    include_gm_private: bool = True,
    after_log_id: int | None = None,
) -> list[dict]:
    """Recent log lines for prompts. GM-private System ops stay off player packs.

    ``after_log_id`` (inclusive of later rows only) cuts context at a GM lease
    boundary so prior possession narration does not enter the new lens.
    """
    after_clause = ""
    params: list = []
    if after_log_id is not None:
        after_clause = " AND id > ?"
        params.append(int(after_log_id))
    try:
        if include_gm_private:
            log_rows = db.execute(
                f"""
                SELECT speaker, message FROM log
                WHERE 1=1{after_clause}
                ORDER BY id DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        else:
            log_rows = db.execute(
                f"""
                SELECT speaker, message FROM log
                WHERE COALESCE(visibility, 'table') != 'gm'{after_clause}
                ORDER BY id DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
    except Exception:
        if after_log_id is not None:
            log_rows = db.execute(
                """
                SELECT speaker, message FROM log
                WHERE id > ?
                ORDER BY id DESC LIMIT ?
                """,
                (int(after_log_id), limit),
            ).fetchall()
        else:
            log_rows = db.execute(
                "SELECT speaker, message FROM log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {"speaker": row["speaker"], "message": row["message"]}
        for row in reversed(log_rows)
    ]


def _label_mentioned_in_action(label_cf: str, action_cf: str) -> bool:
    """True when the action names this label or a multi-word head of it."""
    if label_cf in action_cf:
        return True
    parts = label_cf.split()
    while len(parts) >= 2:
        head = " ".join(parts)
        if len(head) >= 3 and head in action_cf:
            return True
        parts = parts[:-1]
    return False


def _resolve_location_object(db, location_text: str):
    text = (location_text or "").strip()
    if not text or text == "Unknown":
        return None
    loc = text.casefold()
    rows = db.execute(
        """
        SELECT object_id, name, object_type, geography_kind, description
        FROM objects
        ORDER BY length(name) DESC, name
        """
    ).fetchall()
    for row in rows:
        name = (row["name"] or "").strip()
        if len(name) >= 3 and name.casefold() in loc:
            return dict(row)
    for row in rows:
        name = (row["name"] or "").strip()
        if name and name.casefold() == loc:
            return dict(row)
    return None


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
        for key in (
            "character",
            "item_object_id",
            "use",
            "summary",
            "record_id",
            "text",
            "relation",
            "from_object_id",
            "to_object_id",
            "notes",
            "equipment_location",
            "check",
            "skill",
            "ability",
        ):
            raw = item.get(key)
            if isinstance(raw, str) and raw.strip():
                proposal[key] = raw.strip()
        if "text" not in proposal and isinstance(item.get("content"), str):
            content = item["content"].strip()
            if content:
                proposal["text"] = content
        note_id = item.get("note_id")
        if isinstance(note_id, int) and not isinstance(note_id, bool):
            proposal["note_id"] = note_id
        elif isinstance(note_id, str) and note_id.strip().isdigit():
            proposal["note_id"] = int(note_id.strip())
        quantity = item.get("quantity")
        if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity >= 1:
            proposal["quantity"] = quantity
        if isinstance(item.get("attuned"), bool):
            proposal["attuned"] = item["attuned"]
        for flag in ("proficient", "advantage", "disadvantage"):
            if isinstance(item.get(flag), bool):
                proposal[flag] = item[flag]
        for key in ("dc", "ac"):
            value = item.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                proposal[key] = value
            elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
                proposal[key] = int(value.strip())
        if not _proposal_required_fields(proposal):
            continue
        proposals.append(proposal)
    return proposals


def _proposal_required_fields(proposal: dict) -> bool:
    op = proposal["op"]
    if op == "spend_use":
        return "character" in proposal and "use" in proposal
    if op in {"inventory_add", "inventory_update", "inventory_remove"}:
        return "character" in proposal and "item_object_id" in proposal
    if op == "note_append":
        return "note_id" in proposal and "text" in proposal
    if op == "link_create":
        return (
            proposal.get("relation") in ALLOWED_LINK_RELATIONS
            and "from_object_id" in proposal
            and "to_object_id" in proposal
        )
    if op == "roll_check":
        if "character" not in proposal:
            return False
        kind = (proposal.get("check") or "").casefold()
        if kind not in ROLL_CHECK_KINDS:
            return False
        proposal["check"] = kind
        if kind == "skill":
            return "skill" in proposal and ("dc" in proposal or "ac" in proposal)
        if kind == "ability":
            return "ability" in proposal and ("dc" in proposal or "ac" in proposal)
        # attack
        return "item_object_id" in proposal and ("dc" in proposal or "ac" in proposal)
    return False


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
        op = proposal["op"]
        if op == "spend_use":
            target = proposal.get("item_object_id") or "item"
            lines.append(
                f"spend_use: {proposal['character']} / {proposal['use']} ({target})"
            )
        elif op.startswith("inventory_"):
            lines.append(
                f"{op}: {proposal['character']} / {proposal['item_object_id']}"
            )
        elif op == "note_append":
            preview = proposal["text"]
            if len(preview) > 80:
                preview = preview[:77] + "..."
            lines.append(f"note_append: note {proposal['note_id']} — {preview}")
        elif op == "link_create":
            lines.append(
                f"link_create: {proposal['relation']} "
                f"{proposal['from_object_id']} → {proposal['to_object_id']}"
            )
        elif op == "roll_check":
            kind = proposal.get("check", "check")
            detail = proposal.get("skill") or proposal.get("ability") or proposal.get(
                "item_object_id", ""
            )
            target = proposal.get("dc", proposal.get("ac"))
            lines.append(f"roll_check: {proposal['character']} {kind} {detail} vs {target}")
        else:
            lines.append(op)
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


def apply_proposals(db, proposals: list[dict], *, scratch=None) -> list:
    """Apply approved proposals. Returns log lines."""
    if not proposals:
        return ["No proposals to apply"]
    messages = []
    changed = False
    handlers = {
        "spend_use": _apply_spend_use,
        "inventory_add": _apply_inventory_add,
        "inventory_update": _apply_inventory_update,
        "inventory_remove": _apply_inventory_remove,
        "note_append": _apply_note_append,
        "link_create": _apply_link_create,
        "roll_check": _apply_roll_check,
    }
    for proposal in normalize_proposals(proposals):
        conflict = _revision_conflict(db, scratch, proposal)
        if conflict is not None:
            messages.append(conflict)
            continue
        handler = handlers.get(proposal["op"])
        if handler is None:
            messages.append(f"Skipped unsupported proposal op {proposal['op']!r}")
            continue
        line = handler(db, proposal)
        messages.append(line)
        if not line.startswith("Skipped"):
            changed = True
    if changed:
        db.commit()
        messages.append("Proposals applied")
    elif not messages:
        return ["No proposals to apply"]
    return messages


def live_record_revision(db, record_type: str, canonical_key: str) -> str | None:
    """Fingerprint the live campaign row matching a scratch record."""
    if record_type == "document":
        try:
            note_id = int(canonical_key)
        except (TypeError, ValueError):
            return None
        row = db.execute(
            "SELECT id, title, content FROM notes WHERE id=?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None
        return snapshot_revision({
            "id": row["id"],
            "title": row["title"],
            "content": row["content"] or "",
        })
    if record_type == "object":
        row = db.execute(
            """
            SELECT object_id, name, object_type, geography_kind, description,
                   properties
            FROM objects WHERE object_id=?
            """,
            (canonical_key,),
        ).fetchone()
        if row is None:
            return None
        return snapshot_revision({
            "object_id": row["object_id"],
            "name": row["name"],
            "object_type": row["object_type"],
            "geography_kind": row["geography_kind"],
            "description": row["description"] or "",
            "properties": row["properties"],
        })
    return None


def _revision_conflict(db, scratch, proposal: dict) -> str | None:
    record_id = proposal.get("record_id")
    if not record_id:
        return None
    if scratch is None:
        return (
            f"Skipped {proposal['op']}: record_id {record_id!r} could not be "
            "verified without scratch."
        )
    entry = scratch.get(record_id)
    if entry is None:
        return f"Skipped {proposal['op']}: unknown record_id {record_id!r}"
    live = live_record_revision(db, entry["type"], entry["canonical_key"])
    if live is None:
        return (
            f"Skipped {proposal['op']}: record {record_id!r} no longer exists"
        )
    if live != entry["revision"]:
        return (
            f"Skipped {proposal['op']}: record {record_id!r} changed since "
            "the scratch snapshot"
        )
    return None


def _character_row(db, name: str):
    return db.execute(
        "SELECT id, name, data FROM characters WHERE name=?",
        (name,),
    ).fetchone()


def _catalog_item_exists(db, item_object_id: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM objects WHERE object_id=? AND object_type='item'",
        (item_object_id,),
    ).fetchone()
    return row is not None


def _find_inventory_item_link(db, character_id: int, item_object_id: str):
    """Find a contains link by owned instance id or by archetype catalog id."""
    root_id = get_character_inventory_container(db, character_id)
    return db.execute(
        """
        WITH RECURSIVE inv(object_id) AS (
            SELECT ?
            UNION
            SELECT object_links.to_object_id
            FROM object_links
            JOIN inv ON object_links.from_object_id = inv.object_id
            WHERE object_links.relation = 'contains'
        )
        SELECT object_links.id, object_links.from_object_id,
               object_links.to_object_id, object_links.properties
        FROM object_links
        JOIN inv ON object_links.from_object_id = inv.object_id
        WHERE object_links.relation = 'contains'
          AND (
              object_links.to_object_id = ?
              OR object_links.to_object_id IN (
                  SELECT from_object_id FROM object_links
                  WHERE relation = 'instance_of' AND to_object_id = ?
              )
          )
        ORDER BY object_links.id
        LIMIT 1
        """,
        (root_id, item_object_id, item_object_id),
    ).fetchone()


def _character_data(row) -> dict:
    try:
        data = json.loads(row["data"])
    except (TypeError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _resolve_owned_weapon(db, character_id: int, item_object_id: str) -> dict | None:
    """Return resolved weapon property set for an owned copy or archetype id."""
    owned = get_character_items(db, character_id)
    match = None
    for item in owned:
        if item["id"] == item_object_id or item.get("archetype_id") == item_object_id:
            match = item
            break
    name = item_object_id
    object_id = item_object_id
    if match is not None:
        name = match.get("name") or name
        object_id = match["id"]
        props = match.get("properties") or {}
        live = resolve_item(db, match["id"])
        if live is not None:
            name = live.get("name") or name
            props = live.get("properties") or props
    else:
        resolved = resolve_item(db, item_object_id)
        if resolved is None:
            # Fall back to catalog archetype row even if not currently owned.
            row = db.execute(
                "SELECT name, properties FROM objects "
                "WHERE object_id=? AND object_type='item'",
                (item_object_id,),
            ).fetchone()
            if row is None:
                return None
            name = row["name"]
            try:
                props = json.loads(row["properties"] or "{}")
            except json.JSONDecodeError:
                props = {}
        else:
            name = resolved.get("name") or name
            props = resolved.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    weapon = props.get("weapon")
    if not isinstance(weapon, dict):
        return None
    return {"name": name, "object_id": object_id, "weapon": weapon}


def _apply_roll_check(db, proposal: dict) -> str:
    """LLM requests the check; bottle stats + gear resolve the roll locally."""
    name = proposal["character"]
    row = _character_row(db, name)
    if row is None:
        return f"Skipped roll_check for unknown character: {name}"
    data = _character_data(row)
    kind = proposal["check"]
    target = proposal.get("dc", proposal.get("ac"))
    if target is None:
        return f"Skipped roll_check for {name}: missing dc/ac"
    advantage = bool(proposal.get("advantage"))
    disadvantage = bool(proposal.get("disadvantage"))
    label = proposal.get("summary") or kind
    damage_expression = None

    if kind == "skill":
        skill_name = proposal["skill"]
        skill_row = None
        for candidate in get_character_skills(db, row["id"]):
            if (candidate["name"] or "").casefold() == skill_name.casefold():
                skill_row = candidate
                break
        if skill_row is None:
            return f"Skipped roll_check: unknown skill {skill_name!r}"
        details = skill_check_bonus(data, skill_row)
        if details is None:
            return f"Skipped roll_check: cannot score skill {skill_name!r}"
        bonus = details["bonus"]
        label = proposal.get("summary") or f"{details['skill']} ({details['ability']})"
    elif kind == "ability":
        details = ability_check_bonus(data, proposal["ability"])
        if details is None:
            return f"Skipped roll_check: bad ability {proposal['ability']!r}"
        bonus = details["bonus"]
        label = proposal.get("summary") or f"{details['ability'].upper()} check"
    else:
        weapon_info = _resolve_owned_weapon(db, row["id"], proposal["item_object_id"])
        if weapon_info is None:
            return (
                f"Skipped roll_check: {name} has no weapon "
                f"{proposal['item_object_id']!r}"
            )
        details = attack_bonus(
            data,
            weapon_info["weapon"],
            proficient=bool(proposal.get("proficient", False)),
        )
        if details is None:
            return f"Skipped roll_check: cannot score attack for {name}"
        bonus = details["bonus"]
        damage_expression = weapon_info["weapon"].get("damage")
        label = (
            proposal.get("summary")
            or f"attack with {weapon_info['name']}"
        )

    result = perform_check(
        kind=kind,
        bonus=bonus,
        target=int(target),
        advantage=advantage,
        disadvantage=disadvantage,
        damage_expression=damage_expression if kind == "attack" else None,
    )
    # Persist last bottle-resolved check on the character for follow-up turns.
    history = data.get("check_results")
    if not isinstance(history, list):
        history = []
    history.append({
        "kind": kind,
        "label": label,
        "total": result["total"],
        "target": result.get("target"),
        "success": result.get("success"),
        "summary": proposal.get("summary"),
        "rolled_at": datetime.now(timezone.utc).isoformat(),
    })
    data["check_results"] = history[-20:]
    db.execute(
        "UPDATE characters SET data=? WHERE id=?",
        (json.dumps(data, ensure_ascii=False), row["id"]),
    )
    return format_check_result(result, character=name, label=label)


def _apply_spend_use(db, proposal: dict) -> str:
    name = proposal["character"]
    use = proposal["use"]
    row = _character_row(db, name)
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


def _apply_inventory_add(db, proposal: dict) -> str:
    from sakaki_gm.item_db import create_item_instance

    name = proposal["character"]
    item_id = proposal["item_object_id"]
    row = _character_row(db, name)
    if row is None:
        return f"Skipped inventory_add for unknown character: {name}"
    if not _catalog_item_exists(db, item_id):
        return f"Skipped inventory_add: unknown item {item_id!r}"
    root_id = get_character_inventory_container(db, row["id"])
    properties = {
        "quantity": proposal.get("quantity", 1),
        "attuned": bool(proposal.get("attuned", False)),
        "equipment_location": proposal.get("equipment_location", ""),
    }
    instance_id = create_item_instance(db, item_id)
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES ('contains', ?, ?, ?, '')
        """,
        (root_id, instance_id, json.dumps(properties, ensure_ascii=False)),
    )
    return f"Added {instance_id} to {name}'s inventory"


def _apply_inventory_update(db, proposal: dict) -> str:
    name = proposal["character"]
    item_id = proposal["item_object_id"]
    row = _character_row(db, name)
    if row is None:
        return f"Skipped inventory_update for unknown character: {name}"
    link = _find_inventory_item_link(db, row["id"], item_id)
    if link is None:
        return f"Skipped inventory_update: {name} does not have {item_id}"
    try:
        properties = json.loads(link["properties"])
    except json.JSONDecodeError:
        properties = {}
    if not isinstance(properties, dict):
        properties = {}
    if "quantity" in proposal:
        properties["quantity"] = proposal["quantity"]
    if "attuned" in proposal:
        properties["attuned"] = proposal["attuned"]
    if "equipment_location" in proposal:
        properties["equipment_location"] = proposal["equipment_location"]
    db.execute(
        "UPDATE object_links SET properties=? WHERE id=?",
        (json.dumps(properties, ensure_ascii=False), link["id"]),
    )
    return f"Updated {item_id} in {name}'s inventory"


def _apply_inventory_remove(db, proposal: dict) -> str:
    from sakaki_gm.item_db import _delete_orphan_item_instance

    name = proposal["character"]
    item_id = proposal["item_object_id"]
    row = _character_row(db, name)
    if row is None:
        return f"Skipped inventory_remove for unknown character: {name}"
    link = _find_inventory_item_link(db, row["id"], item_id)
    if link is None:
        return f"Skipped inventory_remove: {name} does not have {item_id}"
    instance_id = link["to_object_id"]
    db.execute("DELETE FROM object_links WHERE id=?", (link["id"],))
    _delete_orphan_item_instance(db, instance_id)
    return f"Removed {instance_id} from {name}'s inventory"


def _apply_note_append(db, proposal: dict) -> str:
    note_id = proposal["note_id"]
    row = db.execute(
        "SELECT id, title, content FROM notes WHERE id=?",
        (note_id,),
    ).fetchone()
    if row is None:
        return f"Skipped note_append: unknown note {note_id}"
    existing = row["content"] or ""
    addition = proposal["text"]
    if existing and not existing.endswith("\n"):
        existing += "\n"
    db.execute(
        "UPDATE notes SET content=? WHERE id=?",
        (existing + addition, note_id),
    )
    title = row["title"] or f"note {note_id}"
    return f"Appended to note {note_id} ({title})"


def _apply_link_create(db, proposal: dict) -> str:
    relation = proposal["relation"]
    from_id = proposal["from_object_id"]
    to_id = proposal["to_object_id"]
    if from_id == to_id:
        return "Skipped link_create: an object cannot link to itself"
    for object_id in (from_id, to_id):
        if db.execute(
            "SELECT 1 FROM objects WHERE object_id=?", (object_id,)
        ).fetchone() is None:
            return f"Skipped link_create: unknown object {object_id!r}"
    existing = db.execute(
        """
        SELECT 1 FROM object_links
        WHERE relation=? AND from_object_id=? AND to_object_id=?
        LIMIT 1
        """,
        (relation, from_id, to_id),
    ).fetchone()
    if existing is not None:
        return (
            f"Skipped link_create: {relation} {from_id} → {to_id} already exists"
        )
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES (?, ?, ?, '{}', ?)
        """,
        (relation, from_id, to_id, proposal.get("notes", "")),
    )
    return f"Created {relation} link {from_id} → {to_id}"


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

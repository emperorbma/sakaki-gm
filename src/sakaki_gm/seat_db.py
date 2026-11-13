"""Multiuser ontology shell: seats, turn phase, and knowledge/visibility.

Entity shape with parties (see ``party_db``)::

    Party → Members (characters, seated or not)
          ↘ Seats (optional controllers; ``seats.party_id`` + ``character_id``)

Solo play is implicitly two seats (GM + one player) on one machine, both
attached to the default party. Seat login chooses the active seat; knowledge
edges gate what a player seat may see; the GM seat sees the full playbook.

``character_id`` is the member this seat controls (if any). Non-seated party
members are normal. The GM seat may attach to a temporary one-member party to
run an NPC/absentee PC for Ask, then return to the main party.

Per-seat ``password_hash``: blank string = open seat (no password). Passwords
are seat-scoped; the controlled PC is a separate binding. Password recovery is
out of scope locally; a future server lobby can own that.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Any, Optional

SEAT_ROLES = ("gm", "player")
TURN_PHASES = ("idle", "awaiting_roll", "model_busy")
KNOWLEDGE_LEVELS = ("rumored", "known", "present")
SUBJECT_TYPES = ("character", "actor")
# Levels that appear on a player roster / awareness list.
VISIBLE_KNOWLEDGE = frozenset(KNOWLEDGE_LEVELS)

# System log markers: Ask scene packs for a GM temporary party start *after*
# the matching line so prior possession narration cannot bleed into the lens.
GM_CONTROL_CLEARED_MESSAGE = "GM cleared temporary party"
# Back-compat with bottles that still have the old lease wording in the log.
GM_LEASE_CLEARED_MESSAGE = GM_CONTROL_CLEARED_MESSAGE
# session_state.gm_control_scene: "table" or a geography object_id.
GM_CONTROL_SCENE_TABLE = "table"


def gm_control_party_message(character_name: str) -> str:
    return f"GM temporary party: {character_name}"


def gm_lease_controlling_message(character_name: str) -> str:
    """Deprecated alias — temporary control is a one-character party."""
    return gm_control_party_message(character_name)


def character_recorded_places(db, character_object_id) -> list[dict[str, Any]]:
    """Compact located_at targets (name + object_id) for control / scene packs."""
    object_id = str(character_object_id or "").strip()
    if not object_id:
        return []
    try:
        rows = db.execute(
            """
            SELECT target.object_id, target.name, target.geography_kind,
                   target.object_type, target.description
            FROM object_links AS link
            JOIN objects AS target ON target.object_id = link.to_object_id
            WHERE link.relation = 'located_at'
              AND link.from_object_id = ?
            ORDER BY target.name COLLATE NOCASE, target.object_id
            """,
            (object_id,),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "object_id": row["object_id"],
            "name": row["name"],
            "geography_kind": row["geography_kind"],
            "object_type": row["object_type"],
            "description": row["description"],
        }
        for row in rows
    ]


def normalize_gm_control_scene(value) -> str:
    text = str(value or "").strip()
    if not text:
        return GM_CONTROL_SCENE_TABLE
    return text


def _append_system_log(db, message: str, *, commit: bool = False) -> None:
    """Write a GM-private System line (lease anchors, etc.)."""
    stamp = datetime.now().isoformat()
    try:
        db.execute(
            """
            INSERT INTO log (timestamp, speaker, message, visibility)
            VALUES (?, 'System', ?, 'gm')
            """,
            (stamp, message),
        )
    except Exception:
        db.execute(
            "INSERT INTO log (timestamp, speaker, message) VALUES (?, 'System', ?)",
            (stamp, message),
        )
    if commit:
        db.commit()


def _normalize_role(role: str) -> str:
    value = str(role or "player").strip().lower() or "player"
    if value not in SEAT_ROLES:
        raise ValueError(f"Unknown seat role {role!r}; expected gm or player.")
    return value


def _normalize_phase(phase: str) -> str:
    value = str(phase or "idle").strip().lower() or "idle"
    if value not in TURN_PHASES:
        raise ValueError(
            f"Unknown turn phase {phase!r}; expected one of {', '.join(TURN_PHASES)}."
        )
    return value


def _normalize_knowledge(level: str) -> str:
    value = str(level or "known").strip().lower() or "known"
    if value not in KNOWLEDGE_LEVELS:
        raise ValueError(
            f"Unknown knowledge level {level!r}; "
            f"expected one of {', '.join(KNOWLEDGE_LEVELS)}."
        )
    return value


def _normalize_subject_type(subject_type: str) -> str:
    value = str(subject_type or "").strip().lower()
    if value not in SUBJECT_TYPES:
        raise ValueError(
            f"Unknown subject type {subject_type!r}; "
            f"expected one of {', '.join(SUBJECT_TYPES)}."
        )
    return value


def _seat_password_hash(row) -> str:
    try:
        return str(row["password_hash"] or "")
    except (KeyError, IndexError):
        return ""


def _seat_party_id(row) -> int | None:
    try:
        value = row["party_id"]
    except (KeyError, IndexError):
        return None
    if value is None:
        return None
    return int(value)


def _seat_row(row) -> dict[str, Any]:
    password_hash = _seat_password_hash(row)
    return {
        "id": row["id"],
        "seat_key": row["seat_key"],
        "display_name": row["display_name"] or "",
        "role": row["role"],
        "character_id": row["character_id"],
        "party_id": _seat_party_id(row),
        "has_password": bool(password_hash.strip()),
    }


def set_seat_party(
    db, seat_id: int, party_id: int | None, *, commit: bool = True
) -> dict[str, Any]:
    """Attach a seat to a party (or clear). Members without seats stay valid."""
    if get_seat(db, seat_id) is None:
        raise ValueError(f"Unknown seat {seat_id}.")
    if party_id is not None:
        party_id = int(party_id)
        row = db.execute(
            "SELECT id FROM parties WHERE id=?", (party_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown party {party_id}.")
    try:
        db.execute(
            "UPDATE seats SET party_id=? WHERE id=?",
            (party_id, int(seat_id)),
        )
    except Exception as exc:
        raise ValueError("Seat party column unavailable; migrate the bottle.") from exc
    if commit:
        db.commit()
    seat = get_seat(db, seat_id)
    assert seat is not None
    return seat


def seats_for_party(db, party_id: int) -> list[dict[str, Any]]:
    """Seats currently attached to this party (controllers, not all members)."""
    try:
        rows = db.execute(
            """
            SELECT * FROM seats
            WHERE party_id=?
            ORDER BY role, seat_key COLLATE NOCASE
            """,
            (int(party_id),),
        ).fetchall()
    except Exception:
        return []
    return [_seat_row(row) for row in rows]


def hash_seat_password(password: str, *, salt: bytes | None = None) -> str:
    """Return ``salt_hex$digest_hex`` for storage in ``seats.password_hash``."""
    raw = str(password or "").encode("utf-8")
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt_bytes, 120_000)
    return f"{salt_bytes.hex()}${digest.hex()}"


def verify_password_hash(stored: str, password: str) -> bool:
    stored = str(stored or "").strip()
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    raw = str(password or "").encode("utf-8")
    actual = hashlib.pbkdf2_hmac("sha256", raw, salt, 120_000)
    return hmac.compare_digest(actual, expected)


def seat_requires_password(db, role_or_seat) -> bool:
    """True when this seat has a password configured."""
    seat = _resolve_seat(db, role_or_seat)
    return bool(seat and seat.get("has_password"))


def authenticate_seat(db, role_or_seat, password: str = "") -> dict[str, Any]:
    """Return the seat if login is allowed; raise ValueError on bad password.

    ``role_or_seat`` may be a role name, seat id, or seat dict. Open seats
    (no hash) always succeed; password may be blank.
    """
    seat = _resolve_seat(db, role_or_seat)
    if seat is None:
        raise ValueError("Unknown seat.")
    row = db.execute("SELECT * FROM seats WHERE id=?", (seat["id"],)).fetchone()
    stored = _seat_password_hash(row)
    if not stored.strip():
        return seat
    if not verify_password_hash(stored, password):
        raise ValueError("Incorrect password for that seat.")
    return seat


def set_seat_character(
    db, seat_id: int, character_id: int | None, *, commit: bool = True
) -> dict[str, Any]:
    """Bind which character this seat controls (multi-player acting lens).

    Player seats keep exclusive ownership among players. The GM seat may bind
    an absentee / uncontrolled character without clearing a player's normal
    claim (absentee GM control).
    """
    seat = get_seat(db, seat_id)
    if seat is None:
        raise ValueError(f"Unknown seat {seat_id}.")
    if character_id is not None:
        row = db.execute(
            "SELECT id FROM characters WHERE id=?", (int(character_id),)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown character {character_id}.")
        character_id = int(character_id)
        if seat.get("role") == "player":
            # One present player claim per character among player seats.
            db.execute(
                """
                UPDATE seats SET character_id=NULL
                WHERE character_id=? AND id!=? AND role='player'
                """,
                (character_id, seat_id),
            )
    db.execute(
        "UPDATE seats SET character_id=? WHERE id=?",
        (character_id, seat_id),
    )
    if seat.get("role") == "player":
        sync_controller_seat_label(db, seat_id, commit=False)
    if commit:
        db.commit()
    bound = get_seat(db, seat_id)
    assert bound is not None
    return bound


def present_player_seat_ids(
    db, *, extra_present_seat_ids: set[int] | list[int] | None = None
) -> set[int]:
    """Player seats currently at the table (local active player + extras).

    Absentee player seats are omitted so the GM may control their characters.
    """
    present = {int(seat_id) for seat_id in (extra_present_seat_ids or [])}
    active = get_active_seat(db)
    if active is not None and active.get("role") == "player":
        present.add(int(active["id"]))
    return present


def player_occupied_character_ids(
    db, *, present_seat_ids: set[int] | list[int] | None = None
) -> set[int]:
    """Characters currently controlled by a present player seat."""
    if present_seat_ids is None:
        present_seat_ids = present_player_seat_ids(db)
    present = {int(seat_id) for seat_id in present_seat_ids}
    occupied: set[int] = set()
    for seat in get_seats(db):
        if seat.get("role") != "player":
            continue
        if int(seat["id"]) not in present:
            continue
        if seat.get("character_id") is not None:
            occupied.add(int(seat["character_id"]))
    return occupied


def characters_available_for_gm_control(
    db, *, present_seat_ids: set[int] | list[int] | None = None
) -> list[dict[str, Any]]:
    """Characters the GM may control: anyone not held by a present player.

    Includes NPCs and absentee PCs (normally player-owned, player not here).
    """
    occupied = player_occupied_character_ids(
        db, present_seat_ids=present_seat_ids
    )
    rows = db.execute(
        """
        SELECT id, name, campaign_role, object_id
        FROM characters
        ORDER BY name COLLATE NOCASE
        """
    ).fetchall()
    available = []
    for row in rows:
        character_id = int(row["id"])
        if character_id in occupied:
            continue
        available.append(
            {
                "id": character_id,
                "name": row["name"],
                "campaign_role": row["campaign_role"] or "",
                "object_id": row["object_id"],
                "absentee_pc": _character_has_player_claim(db, character_id),
            }
        )
    return available


def _character_has_player_claim(db, character_id: int) -> bool:
    row = db.execute(
        """
        SELECT id FROM seats
        WHERE role='player' AND character_id=?
        LIMIT 1
        """,
        (int(character_id),),
    ).fetchone()
    return row is not None


def get_gm_control_scene(db) -> str:
    """Where Ask frames a GM temporary control: ``table`` or a place object_id."""
    ensure_default_session(db, commit=False)
    try:
        row = db.execute(
            "SELECT gm_control_scene FROM session_state WHERE id=1"
        ).fetchone()
    except Exception:
        return GM_CONTROL_SCENE_TABLE
    if row is None:
        return GM_CONTROL_SCENE_TABLE
    return normalize_gm_control_scene(row["gm_control_scene"])


def set_gm_control_scene(db, scene: str, *, commit: bool = True) -> str:
    """Persist Ask scene framing for the current GM temporary control."""
    ensure_default_session(db, commit=False)
    scene = normalize_gm_control_scene(scene)
    try:
        db.execute(
            "UPDATE session_state SET gm_control_scene=? WHERE id=1",
            (scene,),
        )
    except Exception:
        # Pre-v25 bottles mid-migrate: ignore until migrate lands.
        if commit:
            db.commit()
        return GM_CONTROL_SCENE_TABLE
    if commit:
        db.commit()
    return scene


def set_gm_control_character(
    db,
    character_id: int | None,
    *,
    scene: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Attach the GM seat to a temporary party focused on one character.

    Clears with ``character_id=None``. When the player seat later claims the
    same PC, ``claim_player_character`` drops this binding.

    Party → Members; Seat attaches to a Party and may bind one Member.
    Non-seated members remain valid. Temporary control creates/uses a
    one-member party and moves the GM seat onto it. ``scene`` chooses Ask
    framing: ``table`` (main world.location) or a geography ``object_id``.
    Default prefers the first recorded place when present.

    Each change writes a System log marker so Ask scene packs can drop prior
    possession narration from the model context.
    """
    from sakaki_gm.party_db import (
        clear_gm_control_parties,
        ensure_default_party,
        ensure_gm_control_party,
    )

    ensure_default_session(db, commit=False)
    gm = get_seat_by_role(db, "gm")
    if gm is None:
        raise ValueError("GM seat missing.")
    if character_id is not None:
        occupied = player_occupied_character_ids(db)
        if int(character_id) in occupied:
            raise PermissionError(
                "That character is controlled by a player who is present."
            )
        row = db.execute(
            "SELECT id, name, object_id FROM characters WHERE id=?",
            (int(character_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown character {character_id}.")
        marker = gm_control_party_message(row["name"])
        if scene is None:
            recorded = character_recorded_places(db, row["object_id"])
            scene = (
                recorded[0]["object_id"]
                if recorded
                else GM_CONTROL_SCENE_TABLE
            )
        else:
            scene = normalize_gm_control_scene(scene)
        temp_party = ensure_gm_control_party(
            db,
            int(character_id),
            display_name=row["name"],
            commit=False,
        )
        set_seat_party(db, gm["id"], temp_party["id"], commit=False)
    else:
        marker = GM_CONTROL_CLEARED_MESSAGE
        scene = GM_CONTROL_SCENE_TABLE
        clear_gm_control_parties(db, commit=False)
        main = ensure_default_party(db, commit=False)
        set_seat_party(db, gm["id"], main["id"], commit=False)
    seat = set_seat_character(db, gm["id"], character_id, commit=False)
    set_gm_control_scene(db, scene, commit=False)
    _append_system_log(db, marker, commit=False)
    if commit:
        db.commit()
    return seat


def list_controller_seats(db) -> list[dict[str, Any]]:
    """Player-role seats (natural PC controllers), labels refreshed."""
    ensure_default_session(db, commit=False)
    seats = []
    for seat in get_seats(db):
        if seat.get("role") != "player":
            continue
        synced = sync_controller_seat_label(db, seat["id"], commit=False) or seat
        seats.append(synced)
    return seats


def reassign_controller_seat(
    db,
    seat_id: int,
    character_id: int | None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """GM ownership transfer: bind a controller seat to a playerish PC (or clear).

    NPCs cannot be assigned. If the PC already sits on another controller seat,
    that other binding is cleared so ownership stays singular.
    """
    from sakaki_gm.campaign_roles import is_playerish_campaign_role

    ensure_default_session(db, commit=False)
    seat = get_seat(db, seat_id)
    if seat is None or seat.get("role") != "player":
        raise ValueError("Pick a player controller seat to reassign.")
    if character_id is not None:
        character_id = int(character_id)
        row = db.execute(
            "SELECT id, name, campaign_role FROM characters WHERE id=?",
            (character_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown character {character_id}.")
        if not is_playerish_campaign_role(row["campaign_role"]):
            raise PermissionError(
                "Only playerish PCs can own a controller seat "
                "(NPCs / GM cast do not bind)."
            )
        # Singular ownership among controller seats.
        db.execute(
            """
            UPDATE seats SET character_id=NULL
            WHERE role='player' AND character_id=? AND id!=?
            """,
            (character_id, seat_id),
        )
        for other in get_seats(db):
            if other.get("role") == "player" and other["id"] != seat_id:
                sync_controller_seat_label(db, other["id"], commit=False)
    bound = set_seat_character(db, seat_id, character_id, commit=False)
    if seat.get("character_id") is not None or character_id is not None:
        try:
            sync_seat_knowledge_from_world(db, seat_id, commit=False)
        except Exception:
            pass
    if commit:
        db.commit()
    return bound


def list_player_claimable_characters(
    db, *, present_seat_ids: set[int] | list[int] | None = None
) -> list[dict[str, Any]]:
    """PCs a player seat may pick at login (playerish / claimed, not occupied)."""
    from sakaki_gm.campaign_roles import is_playerish_campaign_role

    ensure_default_session(db, commit=False)
    occupied = player_occupied_character_ids(
        db, present_seat_ids=present_seat_ids
    )
    player = get_seat_by_role(db, "player")
    own_id = (
        int(player["character_id"])
        if player is not None and player.get("character_id") is not None
        else None
    )
    rows = db.execute(
        """
        SELECT id, name, campaign_role, object_id
        FROM characters
        ORDER BY name COLLATE NOCASE
        """
    ).fetchall()
    claimable = []
    for row in rows:
        character_id = int(row["id"])
        role = row["campaign_role"] or ""
        if not (
            is_playerish_campaign_role(role)
            or _character_has_player_claim(db, character_id)
        ):
            continue
        if character_id in occupied and character_id != own_id:
            continue
        claimable.append(
            {
                "id": character_id,
                "name": row["name"],
                "campaign_role": role,
                "object_id": row["object_id"],
            }
        )
    return claimable


def bottle_occupied_character_ids(
    db, *, except_holder_id: str | None = None
) -> set[int]:
    """Characters with a live desk holder recorded in this bottle."""
    try:
        rows = db.execute(
            "SELECT character_id, holder_id FROM desk_occupancy"
        ).fetchall()
    except Exception:
        return set()
    occupied = set()
    for row in rows:
        holder = str(row["holder_id"] or "")
        if except_holder_id and holder == except_holder_id:
            continue
        occupied.add(int(row["character_id"]))
    return occupied


def claim_desk_occupancy(
    db,
    *,
    holder_id: str,
    desk_role: str,
    character_id: int | None = None,
    seat_id: int | None = None,
    commit: bool = True,
) -> None:
    """Record exclusive live occupancy for this desk process in the bottle."""
    from datetime import datetime, timezone

    holder_id = str(holder_id or "").strip()
    if not holder_id:
        raise ValueError("holder_id is required for desk occupancy.")
    desk_role = _normalize_role(desk_role) if desk_role in SEAT_ROLES else "player"
    # Drop prior claims by this desk.
    db.execute("DELETE FROM desk_occupancy WHERE holder_id=?", (holder_id,))
    if desk_role == "player" and character_id is not None:
        character_id = int(character_id)
        row = db.execute(
            "SELECT holder_id FROM desk_occupancy WHERE character_id=?",
            (character_id,),
        ).fetchone()
        if row is not None and str(row["holder_id"]) != holder_id:
            raise PermissionError(
                "That character is already seated at another desk."
            )
        db.execute(
            """
            INSERT INTO desk_occupancy (
                character_id, seat_id, holder_id, desk_role, claimed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                character_id,
                seat_id,
                holder_id,
                desk_role,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    if commit:
        db.commit()


def release_desk_occupancy(
    db, holder_id: str, *, commit: bool = True
) -> None:
    holder_id = str(holder_id or "").strip()
    if not holder_id:
        return
    try:
        db.execute("DELETE FROM desk_occupancy WHERE holder_id=?", (holder_id,))
    except Exception:
        return
    if commit:
        db.commit()


def claim_player_character(
    db,
    character_id: int,
    password: str = "",
    *,
    seat_id: int | None = None,
    holder_id: str | None = None,
    extra_occupied_character_ids: set[int] | list[int] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Authenticate a controller seat for its bound PC and end any GM lease."""
    ensure_default_session(db, commit=False)
    character_id = int(character_id)
    seat = None
    if seat_id is not None:
        seat = get_seat(db, int(seat_id))
    if seat is None:
        for candidate in get_seats(db):
            if (
                candidate.get("role") == "player"
                and candidate.get("character_id") is not None
                and int(candidate["character_id"]) == character_id
            ):
                seat = candidate
                break
    if seat is None:
        seat = get_seat_by_role(db, "player")
    if seat is None or seat.get("role") != "player":
        raise ValueError("No player controller seat available.")
    seat = authenticate_seat(db, seat, password)
    elsewhere = bottle_occupied_character_ids(
        db, except_holder_id=holder_id
    )
    if extra_occupied_character_ids:
        elsewhere |= {int(item) for item in extra_occupied_character_ids}
    if character_id in elsewhere:
        raise PermissionError(
            "That character is already seated at another desk."
        )
    own = seat.get("character_id")
    claimable_ids = {
        item["id"] for item in list_player_claimable_characters(db)
    }
    if own is not None:
        claimable_ids.add(int(own))
    if character_id not in claimable_ids:
        row = db.execute(
            "SELECT id FROM characters WHERE id=?", (character_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown character {character_id}.")
        raise PermissionError("That character is not available for player login.")
    gm = get_seat_by_role(db, "gm")
    if gm is not None and gm.get("character_id") is not None:
        if int(gm["character_id"]) == character_id:
            from sakaki_gm.party_db import (
                clear_gm_control_parties,
                ensure_default_party,
            )

            set_seat_character(db, gm["id"], None, commit=False)
            clear_gm_control_parties(db, commit=False)
            main = ensure_default_party(db, commit=False)
            set_seat_party(db, gm["id"], main["id"], commit=False)
            _append_system_log(db, GM_CONTROL_CLEARED_MESSAGE, commit=False)
    bound = set_seat_character(db, seat["id"], character_id, commit=False)
    # Player seat attaches to the character's active party when known.
    from sakaki_gm.party_db import active_party_for_character, ensure_default_party

    member_party = active_party_for_character(db, character_id)
    attach_id = (
        member_party["id"]
        if member_party is not None
        else ensure_default_party(db, commit=False)["id"]
    )
    set_seat_party(db, bound["id"], attach_id, commit=False)
    set_active_seat(db, seat["id"], commit=False)
    if holder_id:
        claim_desk_occupancy(
            db,
            holder_id=holder_id,
            desk_role="player",
            character_id=character_id,
            seat_id=bound["id"],
            commit=False,
        )
    if commit:
        db.commit()
    return bound


def set_seat_password(
    db, role_or_seat, password: str, *, commit: bool = True
) -> dict[str, Any]:
    """Set or clear a seat password. Empty password clears (open seat)."""
    seat = _resolve_seat(db, role_or_seat)
    if seat is None:
        raise ValueError("Unknown seat.")
    password = str(password or "")
    stored = hash_seat_password(password) if password else ""
    db.execute(
        "UPDATE seats SET password_hash=? WHERE id=?",
        (stored, seat["id"]),
    )
    if commit:
        db.commit()
    return get_seat(db, seat["id"])


def _resolve_seat(db, role_or_seat) -> Optional[dict[str, Any]]:
    if isinstance(role_or_seat, dict) and role_or_seat.get("id") is not None:
        return get_seat(db, int(role_or_seat["id"]))
    if isinstance(role_or_seat, int):
        return get_seat(db, role_or_seat)
    return get_seat_by_role(db, str(role_or_seat))


def get_seats(db) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM seats ORDER BY role, seat_key COLLATE NOCASE"
    ).fetchall()
    return [_seat_row(row) for row in rows]


def get_seat(db, seat_id: int) -> Optional[dict[str, Any]]:
    row = db.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
    if row is None:
        return None
    return _seat_row(row)


def get_seat_by_key(db, seat_key: str) -> Optional[dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM seats WHERE seat_key=? COLLATE NOCASE",
        (seat_key.strip(),),
    ).fetchone()
    if row is None:
        return None
    return _seat_row(row)


def get_seat_by_role(db, role: str) -> Optional[dict[str, Any]]:
    role = _normalize_role(role)
    row = db.execute(
        "SELECT * FROM seats WHERE role=? ORDER BY id LIMIT 1",
        (role,),
    ).fetchone()
    if row is None:
        return None
    return _seat_row(row)


def ensure_default_session(db, *, commit: bool = True) -> dict[str, Any]:
    """Ensure GM + player seats and a single session_state row exist."""
    gm = get_seat_by_key(db, "gm")
    if gm is None:
        db.execute(
            """
            INSERT INTO seats (seat_key, display_name, role, character_id)
            VALUES ('gm', 'GM', 'gm', NULL)
            """
        )
        gm = get_seat_by_key(db, "gm")
    player = get_seat_by_key(db, "player")
    if player is None:
        db.execute(
            """
            INSERT INTO seats (seat_key, display_name, role, character_id)
            VALUES ('player', 'Player', 'player', NULL)
            """
        )
        player = get_seat_by_key(db, "player")

    _bootstrap_player_character(db, player["id"])

    state = db.execute("SELECT * FROM session_state WHERE id=1").fetchone()
    if state is None:
        db.execute(
            """
            INSERT INTO session_state (
                id, active_seat_id, turn_phase, hotseat_seat_id, turn_wait
            ) VALUES (1, ?, 'idle', NULL, '{}')
            """,
            (gm["id"],),
        )
    else:
        if state["active_seat_id"] is None:
            db.execute(
                "UPDATE session_state SET active_seat_id=? WHERE id=1",
                (gm["id"],),
            )

    # Attach seats with no party only when a party already exists. Do not
    # bootstrap a party here — that would freeze membership before later PCs
    # are created (Party → Members grows as characters are added).
    try:
        row = db.execute(
            """
            SELECT id FROM parties
            WHERE status='active'
            ORDER BY id LIMIT 1
            """
        ).fetchone()
        if row is not None:
            db.execute(
                "UPDATE seats SET party_id=? WHERE party_id IS NULL",
                (int(row["id"]),),
            )
    except Exception:
        pass

    if commit:
        db.commit()
    return get_session_state(db)


_KNOWLEDGE_RANK = {"rumored": 1, "known": 2, "present": 3}


def upgrade_knowledge(
    db,
    seat_id: int,
    subject_type: str,
    subject_id: int,
    level: str,
    *,
    commit: bool = True,
) -> None:
    """Set knowledge only if missing or strictly higher awareness."""
    level = _normalize_knowledge(level)
    current = get_knowledge(db, seat_id, subject_type, subject_id)
    if current is not None and _KNOWLEDGE_RANK[current] >= _KNOWLEDGE_RANK[level]:
        return
    set_knowledge(
        db,
        seat_id,
        subject_type,
        subject_id,
        level,
        commit=commit,
    )


def controller_seat_label(character_name: str | None) -> str:
    """Human label for a player controller seat bound to a PC (or unbound)."""
    name = str(character_name or "").strip()
    if name:
        return f"{name}'s controller"
    return "Player"


def sync_controller_seat_label(
    db, seat_id: int, *, commit: bool = False
) -> dict[str, Any] | None:
    """Refresh display_name from the seat's leased character (campaign-agnostic)."""
    seat = get_seat(db, seat_id)
    if seat is None or seat.get("role") != "player":
        return seat
    character_id = seat.get("character_id")
    name = None
    if character_id is not None:
        row = db.execute(
            "SELECT name FROM characters WHERE id=?", (int(character_id),)
        ).fetchone()
        if row is not None:
            name = row["name"]
    label = controller_seat_label(name)
    if (seat.get("display_name") or "") != label:
        db.execute(
            "UPDATE seats SET display_name=? WHERE id=?",
            (label, seat_id),
        )
        if commit:
            db.commit()
    return get_seat(db, seat_id)


def ensure_controller_seat_for_character(
    db,
    character_id: int,
    *,
    name: str | None = None,
    campaign_role: str | None = None,
    commit: bool = True,
) -> dict[str, Any] | None:
    """Bind a controller seat when a playerish PC is created/updated.

    NPCs and other GM cast never auto-bind. Singleton campaign reuses the
    unbound ``player`` seat first; otherwise opens ``controller:<id>``.
    """
    from sakaki_gm.campaign_roles import is_playerish_campaign_role

    character_id = int(character_id)
    row = db.execute(
        "SELECT id, name, campaign_role FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown character {character_id}.")
    role = (
        campaign_role
        if campaign_role is not None
        else (row["campaign_role"] or "")
    )
    if not is_playerish_campaign_role(role):
        # Demoted from PC: clear this character from player seats only.
        db.execute(
            """
            UPDATE seats SET character_id=NULL
            WHERE role='player' AND character_id=?
            """,
            (character_id,),
        )
        for seat in get_seats(db):
            if seat.get("role") == "player":
                sync_controller_seat_label(db, seat["id"], commit=False)
        if commit:
            db.commit()
        return None

    ensure_default_session(db, commit=False)
    char_name = (name if name is not None else row["name"]) or ""

    for seat in get_seats(db):
        if seat.get("role") == "player" and seat.get("character_id") == character_id:
            sync_controller_seat_label(db, seat["id"], commit=False)
            if commit:
                db.commit()
            return get_seat(db, seat["id"])

    singleton = get_seat_by_key(db, "player")
    if singleton is not None and singleton.get("character_id") is None:
        bound = set_seat_character(
            db, singleton["id"], character_id, commit=False
        )
        if commit:
            db.commit()
        return bound

    seat_key = f"controller:{character_id}"
    existing = get_seat_by_key(db, seat_key)
    if existing is None:
        db.execute(
            """
            INSERT INTO seats (seat_key, display_name, role, character_id)
            VALUES (?, ?, 'player', ?)
            """,
            (seat_key, controller_seat_label(char_name), character_id),
        )
    else:
        set_seat_character(db, existing["id"], character_id, commit=False)
    seat = get_seat_by_key(db, seat_key)
    assert seat is not None
    sync_controller_seat_label(db, seat["id"], commit=False)
    if commit:
        db.commit()
    return get_seat(db, seat["id"])


def _bootstrap_player_character(db, player_seat_id: int) -> None:
    """Refresh label/awareness for an already-leased controller seat.

    Binding itself happens at playerish character creation — not by scanning
    the cast for a protagonist.
    """
    seat = get_seat(db, player_seat_id)
    if seat is None:
        return
    sync_controller_seat_label(db, player_seat_id, commit=False)
    if seat.get("character_id") is None:
        return
    sync_seat_knowledge_from_world(db, player_seat_id, commit=False)


def sync_seat_knowledge_from_world(
    db,
    seat_id: int,
    *,
    commit: bool = True,
) -> list[dict[str, Any]]:
    """Repair desynced awareness from scene presence, quests, allies, and log.

    Player knowledge should reflect who they are with / know about—not only the
    bound PC. Upgrades only; never strips GM-authored higher awareness.
    """
    seat = get_seat(db, seat_id)
    if seat is None or seat_is_gm(seat):
        return []
    character_id = seat.get("character_id")
    if character_id is None:
        return []

    upgraded = []

    def note(subject_id: int, level: str, reason: str) -> None:
        before = get_knowledge(db, seat_id, "character", subject_id)
        upgrade_knowledge(
            db, seat_id, "character", subject_id, level, commit=False
        )
        after = get_knowledge(db, seat_id, "character", subject_id)
        if after != before:
            upgraded.append(
                {
                    "character_id": subject_id,
                    "level": after,
                    "reason": reason,
                }
            )

    note(int(character_id), "present", "self")

    # Co-located via located_at (same place as the PC).
    object_row = db.execute(
        "SELECT object_id FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if object_row and object_row["object_id"]:
        locations = db.execute(
            """
            SELECT to_object_id FROM object_links
            WHERE relation='located_at' AND from_object_id=?
            """,
            (object_row["object_id"],),
        ).fetchall()
        for location in locations:
            for peer in db.execute(
                """
                SELECT c.id
                FROM object_links AS link
                JOIN characters AS c ON c.object_id = link.from_object_id
                WHERE link.relation='located_at'
                  AND link.to_object_id=?
                  AND c.id != ?
                """,
                (location["to_object_id"], character_id),
            ):
                note(int(peer["id"]), "present", "colocated")

    # Open quest co-participants.
    for peer in db.execute(
        """
        SELECT DISTINCT qc2.character_id AS id
        FROM quest_characters AS qc1
        JOIN quests AS q ON q.id = qc1.quest_id
        JOIN quest_characters AS qc2 ON qc2.quest_id = q.id
        WHERE qc1.character_id = ?
          AND qc2.character_id != ?
          AND lower(coalesce(q.status, '')) NOT IN
              ('done', 'complete', 'completed', 'finished', 'failed', 'fail')
        """,
        (character_id, character_id),
    ):
        note(int(peer["id"]), "known", "shared_quest")

    # Active party mates (split parties keep separate membership → separate awareness).
    try:
        from sakaki_gm.party_db import active_party_for_character, party_member_ids

        party = active_party_for_character(db, int(character_id))
        if party is not None:
            for peer_id in party_member_ids(db, party["id"]):
                if peer_id != int(character_id):
                    note(int(peer_id), "present", "party_member")
    except Exception:
        # Pre-v22 bottles mid-migrate still get role-based allies below.
        pass

    # Standing allies / party (not enemies, not scaffolds).
    from sakaki_gm.campaign_roles import is_allyish_campaign_role

    for peer in db.execute(
        """
        SELECT id, campaign_role, data FROM characters WHERE id != ?
        """,
        (character_id,),
    ):
        if is_allyish_campaign_role(peer["campaign_role"]):
            try:
                data = json.loads(peer["data"] or "{}")
            except json.JSONDecodeError:
                data = {}
            status = data.get("stat_block_status") if isinstance(data, dict) else None
            if isinstance(status, str) and status.lower().startswith("design scaffold"):
                continue
            note(int(peer["id"]), "known", "ally_role")

    # Names mentioned in recent world events / log (scene cast the bottle already recorded).
    blob_parts = []
    events_row = db.execute(
        "SELECT value FROM world WHERE key='events'"
    ).fetchone()
    if events_row and events_row["value"]:
        try:
            events = json.loads(events_row["value"])
        except json.JSONDecodeError:
            events = []
        if isinstance(events, list):
            blob_parts.extend(str(item) for item in events[-20:])
    try:
        log_rows = db.execute(
            """
            SELECT message FROM log
            WHERE COALESCE(visibility, 'table') != 'gm'
            ORDER BY id DESC LIMIT 40
            """
        ).fetchall()
    except Exception:
        log_rows = db.execute(
            """
            SELECT message FROM log
            ORDER BY id DESC LIMIT 40
            """
        ).fetchall()
    for row in log_rows:
        blob_parts.append(str(row["message"] or ""))
    blob = "\n".join(blob_parts)
    if blob.strip():
        for peer in db.execute("SELECT id, name FROM characters WHERE id != ?", (character_id,)):
            name = str(peer["name"] or "").strip()
            if len(name) < 3:
                continue
            if name.casefold() in blob.casefold():
                note(int(peer["id"]), "known", "recent_mention")

    # Places the PC has been / is in (and names that surfaced in recent play).
    place_upgrades = sync_place_knowledge_from_world(
        db, seat_id, character_id=character_id, commit=False
    )
    upgraded.extend(place_upgrades)

    if commit:
        db.commit()
    return upgraded


def get_session_state(db) -> dict[str, Any]:
    ensure_row = db.execute("SELECT * FROM session_state WHERE id=1").fetchone()
    if ensure_row is None:
        return ensure_default_session(db)
    wait_raw = ensure_row["turn_wait"] or "{}"
    try:
        wait = json.loads(wait_raw)
    except json.JSONDecodeError:
        wait = {}
    if not isinstance(wait, dict):
        wait = {}
    scene = GM_CONTROL_SCENE_TABLE
    try:
        scene = normalize_gm_control_scene(ensure_row["gm_control_scene"])
    except (KeyError, IndexError):
        scene = GM_CONTROL_SCENE_TABLE
    return {
        "active_seat_id": ensure_row["active_seat_id"],
        "turn_phase": ensure_row["turn_phase"] or "idle",
        "hotseat_seat_id": ensure_row["hotseat_seat_id"],
        "turn_wait": wait,
        "gm_control_scene": scene,
    }


def get_active_seat(db) -> Optional[dict[str, Any]]:
    state = get_session_state(db)
    seat_id = state.get("active_seat_id")
    if seat_id is None:
        return None
    return get_seat(db, seat_id)


def set_active_seat(db, seat_id: int, *, commit: bool = True) -> dict[str, Any]:
    if get_seat(db, seat_id) is None:
        raise ValueError(f"Unknown seat {seat_id}.")
    ensure_default_session(db, commit=False)
    db.execute(
        "UPDATE session_state SET active_seat_id=? WHERE id=1",
        (seat_id,),
    )
    if commit:
        db.commit()
    return get_session_state(db)


def set_active_seat_by_role(db, role: str, *, commit: bool = True) -> dict[str, Any]:
    """Desk toggle helper: swap active seat by role (gm / player)."""
    ensure_default_session(db, commit=False)
    seat = get_seat_by_role(db, role)
    if seat is None:
        raise ValueError(f"No seat with role {role!r}.")
    return set_active_seat(db, seat["id"], commit=commit)


def set_turn_phase(db, phase: str, *, commit: bool = True) -> dict[str, Any]:
    ensure_default_session(db, commit=False)
    phase = _normalize_phase(phase)
    db.execute(
        "UPDATE session_state SET turn_phase=? WHERE id=1",
        (phase,),
    )
    if commit:
        db.commit()
    return get_session_state(db)


def request_player_roll(
    db,
    *,
    hotseat_seat_id: int,
    wait: dict[str, Any],
    commit: bool = True,
) -> dict[str, Any]:
    """Enter awaiting_roll with a wait payload (check kind, character, etc.)."""
    if get_seat(db, hotseat_seat_id) is None:
        raise ValueError(f"Unknown hotseat seat {hotseat_seat_id}.")
    ensure_default_session(db, commit=False)
    payload = dict(wait or {})
    db.execute(
        """
        UPDATE session_state
        SET turn_phase='awaiting_roll',
            hotseat_seat_id=?,
            turn_wait=?
        WHERE id=1
        """,
        (hotseat_seat_id, json.dumps(payload, ensure_ascii=False)),
    )
    if commit:
        db.commit()
    return get_session_state(db)


def clear_turn_wait(db, *, phase: str = "idle", commit: bool = True) -> dict[str, Any]:
    ensure_default_session(db, commit=False)
    phase = _normalize_phase(phase)
    db.execute(
        """
        UPDATE session_state
        SET turn_phase=?, hotseat_seat_id=NULL, turn_wait='{}'
        WHERE id=1
        """,
        (phase,),
    )
    if commit:
        db.commit()
    return get_session_state(db)


def set_knowledge(
    db,
    seat_id: int,
    subject_type: str,
    subject_id: int,
    level: str,
    *,
    commit: bool = True,
) -> None:
    if get_seat(db, seat_id) is None:
        raise ValueError(f"Unknown seat {seat_id}.")
    subject_type = _normalize_subject_type(subject_type)
    level = _normalize_knowledge(level)
    db.execute(
        """
        INSERT INTO knowledge_edges (seat_id, subject_type, subject_id, level)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(seat_id, subject_type, subject_id)
        DO UPDATE SET level=excluded.level
        """,
        (seat_id, subject_type, int(subject_id), level),
    )
    if commit:
        db.commit()


def clear_knowledge(
    db,
    seat_id: int,
    subject_type: str,
    subject_id: int,
    *,
    commit: bool = True,
) -> None:
    subject_type = _normalize_subject_type(subject_type)
    db.execute(
        """
        DELETE FROM knowledge_edges
        WHERE seat_id=? AND subject_type=? AND subject_id=?
        """,
        (seat_id, subject_type, int(subject_id)),
    )
    if commit:
        db.commit()


def get_knowledge(
    db,
    seat_id: int,
    subject_type: str,
    subject_id: int,
) -> Optional[str]:
    subject_type = _normalize_subject_type(subject_type)
    row = db.execute(
        """
        SELECT level FROM knowledge_edges
        WHERE seat_id=? AND subject_type=? AND subject_id=?
        """,
        (seat_id, subject_type, int(subject_id)),
    ).fetchone()
    if row is None:
        return None
    return row["level"]


def list_knowledge(db, seat_id: int, *, subject_type: str | None = None) -> list[dict]:
    if subject_type is None:
        rows = db.execute(
            """
            SELECT seat_id, subject_type, subject_id, level
            FROM knowledge_edges
            WHERE seat_id=?
            ORDER BY subject_type, subject_id
            """,
            (seat_id,),
        ).fetchall()
    else:
        subject_type = _normalize_subject_type(subject_type)
        rows = db.execute(
            """
            SELECT seat_id, subject_type, subject_id, level
            FROM knowledge_edges
            WHERE seat_id=? AND subject_type=?
            ORDER BY subject_id
            """,
            (seat_id, subject_type),
        ).fetchall()
    return [dict(row) for row in rows]


def seat_is_gm(seat: dict[str, Any] | None) -> bool:
    return bool(seat) and seat.get("role") == "gm"


def character_ids_known_to_seat(db, seat_id: int) -> set[int]:
    rows = list_knowledge(db, seat_id, subject_type="character")
    return {
        int(row["subject_id"])
        for row in rows
        if row["level"] in VISIBLE_KNOWLEDGE
    }


def filter_characters_for_seat(
    db,
    characters: list[dict[str, Any]],
    seat: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """GM seat sees all; player seat sees knowledge-visible characters only."""
    if seat_is_gm(seat):
        return list(characters)
    if seat is None:
        return []
    # Keep awareness aligned with scene/quests/allies/log before filtering.
    sync_seat_knowledge_from_world(db, seat["id"], commit=True)
    seat = get_seat(db, seat["id"]) or seat
    known = character_ids_known_to_seat(db, seat["id"])
    bound = seat.get("character_id")
    if bound is not None:
        known.add(int(bound))
    return [char for char in characters if char.get("id") in known]


def get_place_knowledge(db, seat_id: int, object_id: str) -> Optional[str]:
    row = db.execute(
        """
        SELECT level FROM place_knowledge
        WHERE seat_id=? AND object_id=?
        """,
        (seat_id, object_id),
    ).fetchone()
    if row is None:
        return None
    return row["level"]


def set_place_knowledge(
    db,
    seat_id: int,
    object_id: str,
    level: str,
    *,
    commit: bool = True,
) -> None:
    if get_seat(db, seat_id) is None:
        raise ValueError(f"Unknown seat {seat_id}.")
    object_id = str(object_id or "").strip()
    if not object_id:
        raise ValueError("Place object_id cannot be empty.")
    level = _normalize_knowledge(level)
    db.execute(
        """
        INSERT INTO place_knowledge (seat_id, object_id, level)
        VALUES (?, ?, ?)
        ON CONFLICT(seat_id, object_id) DO UPDATE SET level=excluded.level
        """,
        (seat_id, object_id, level),
    )
    if commit:
        db.commit()


def upgrade_place_knowledge(
    db,
    seat_id: int,
    object_id: str,
    level: str,
    *,
    commit: bool = True,
) -> None:
    level = _normalize_knowledge(level)
    current = get_place_knowledge(db, seat_id, object_id)
    if current is not None and _KNOWLEDGE_RANK[current] >= _KNOWLEDGE_RANK[level]:
        return
    set_place_knowledge(db, seat_id, object_id, level, commit=commit)


def list_place_knowledge(db, seat_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT seat_id, object_id, level
        FROM place_knowledge
        WHERE seat_id=?
        ORDER BY object_id
        """,
        (seat_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def place_ids_known_to_seat(db, seat_id: int) -> set[str]:
    return {
        row["object_id"]
        for row in list_place_knowledge(db, seat_id)
        if row["level"] in VISIBLE_KNOWLEDGE
    }


def sync_place_knowledge_from_world(
    db,
    seat_id: int,
    *,
    character_id: int | None = None,
    commit: bool = True,
) -> list[dict[str, Any]]:
    """Mark places the seat's PC has visited / currently occupies."""
    table = db.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='place_knowledge'
        """
    ).fetchone()
    if table is None:
        # Pre-v20 migrate path may call ensure before place_knowledge exists.
        return []
    seat = get_seat(db, seat_id)
    if seat is None or seat_is_gm(seat):
        return []
    if character_id is None:
        character_id = seat.get("character_id")
    if character_id is None:
        return []

    upgraded = []

    def note_place(object_id: str, level: str, reason: str) -> None:
        object_id = str(object_id or "").strip()
        if not object_id:
            return
        before = get_place_knowledge(db, seat_id, object_id)
        upgrade_place_knowledge(db, seat_id, object_id, level, commit=False)
        after = get_place_knowledge(db, seat_id, object_id)
        if after != before:
            upgraded.append(
                {
                    "object_id": object_id,
                    "level": after,
                    "reason": reason,
                }
            )

    catalog = _geography_place_catalog(db)

    object_row = db.execute(
        "SELECT object_id FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    known_place_ids: set[str] = set()
    if object_row and object_row["object_id"]:
        for location in db.execute(
            """
            SELECT to_object_id FROM object_links
            WHERE relation='located_at' AND from_object_id=?
            """,
            (object_row["object_id"],),
        ):
            known_place_ids.add(location["to_object_id"])
            note_place(location["to_object_id"], "present", "located_at")

    # world.location may list several places ("Academy…, ritual hall, Eldenport").
    world_loc = db.execute(
        "SELECT value FROM world WHERE key='location'"
    ).fetchone()
    location_text = str(world_loc["value"] if world_loc else "").strip()
    if location_text:
        for segment in _split_location_segments(location_text):
            for object_id in _match_places_in_text(segment, catalog):
                known_place_ids.add(object_id)
                note_place(object_id, "present", "world_location")

    # Full play history — early areas often only appear here / in quests, not
    # in the short "recent" window.
    blob_parts: list[str] = []
    events_row = db.execute(
        "SELECT value FROM world WHERE key='events'"
    ).fetchone()
    if events_row and events_row["value"]:
        try:
            events = json.loads(events_row["value"])
        except json.JSONDecodeError:
            events = []
        if isinstance(events, list):
            blob_parts.extend(str(item) for item in events)
    for row in db.execute("SELECT message FROM log ORDER BY id"):
        blob_parts.append(str(row["message"] or ""))

    # Done/open quests encode shrine arcs (Stone Circle, Starfall's End, …).
    for quest in db.execute("SELECT name, description, status FROM quests"):
        blob_parts.append(str(quest["name"] or ""))
        blob_parts.append(str(quest["description"] or ""))

    # PC sheet notes — not the campaign atlas note (that lists every place).
    char_row = db.execute(
        "SELECT name, data FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if char_row is not None:
        try:
            data = json.loads(char_row["data"] or "{}")
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            for key in ("notes", "features", "proficiencies"):
                blob_parts.append(str(data.get(key) or ""))
        pc_name = str(char_row["name"] or "").strip()
        if pc_name:
            for note in db.execute(
                "SELECT title, content FROM notes WHERE content != ''"
            ):
                title = str(note["title"] or "")
                title_cf = title.casefold()
                if "geography of" in title_cf or title_cf.startswith("geography"):
                    continue
                if pc_name.casefold() in title_cf or "quest log" in title_cf:
                    blob_parts.append(title)
                    blob_parts.append(str(note["content"] or ""))

    blob = "\n".join(blob_parts)
    if blob.strip():
        for object_id in _match_places_in_text(blob, catalog):
            known_place_ids.add(object_id)
            note_place(object_id, "known", "history_mention")

    # Containment parents of known places (ritual hall → academy → …).
    for object_id in list(known_place_ids):
        for parent_id in _containing_place_ids(db, object_id):
            note_place(parent_id, "known", "contains_parent")

    if commit:
        db.commit()
    return upgraded


def _geography_place_catalog(db) -> list[tuple[str, str, set[str]]]:
    catalog = []
    for place in db.execute(
        """
        SELECT object_id, name FROM objects
        WHERE geography_kind NOT IN ('', 'unspecified')
        """
    ):
        name = str(place["name"] or "").strip()
        if not name:
            continue
        aliases = {name.casefold()}
        if " - " in name:
            left, right = name.split(" - ", 1)
            aliases.add(left.strip().casefold())
            aliases.add(right.strip().casefold())
        # Drop empty / tiny aliases ("end", "star" alone are too vague).
        aliases = {alias for alias in aliases if len(alias) >= 5}
        if aliases:
            catalog.append((place["object_id"], name, aliases))
    return catalog


def _split_location_segments(text: str) -> list[str]:
    parts = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    if text.strip() and text.strip() not in parts:
        parts.insert(0, text.strip())
    return parts


def _match_places_in_text(
    text: str, catalog: list[tuple[str, str, set[str]]]
) -> list[str]:
    text_cf = text.casefold()
    hits = []
    # Longer aliases first so "Academy of Starlight Ritual Hall" wins over
    # "Academy of Starlight" when both match.
    ranked = sorted(
        catalog,
        key=lambda item: max(len(alias) for alias in item[2]),
        reverse=True,
    )
    for object_id, _name, aliases in ranked:
        if any(alias in text_cf for alias in aliases):
            hits.append(object_id)
    return hits


def _containing_place_ids(db, object_id: str) -> list[str]:
    """Walk contains-parents that are geographic places."""
    parents = []
    seen = {object_id}
    frontier = [object_id]
    while frontier:
        current = frontier.pop()
        for row in db.execute(
            """
            SELECT link.from_object_id AS parent_id, obj.geography_kind
            FROM object_links AS link
            JOIN objects AS obj ON obj.object_id = link.from_object_id
            WHERE link.relation='contains' AND link.to_object_id=?
            """,
            (current,),
        ):
            parent_id = row["parent_id"]
            kind = str(row["geography_kind"] or "")
            if parent_id in seen:
                continue
            seen.add(parent_id)
            if kind and kind not in {"", "unspecified"}:
                parents.append(parent_id)
                frontier.append(parent_id)
    return parents


def filter_places_for_seat(
    db,
    places: list[dict[str, Any]],
    seat: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """GM seat sees all geography; player seat sees visited/known places only."""
    if seat_is_gm(seat):
        return list(places)
    if seat is None:
        return []
    sync_seat_knowledge_from_world(db, seat["id"], commit=True)
    known = place_ids_known_to_seat(db, seat["id"])
    return [
        place
        for place in places
        if place.get("object_id") in known
    ]

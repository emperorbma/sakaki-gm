"""Multiuser ontology shell: seats, turn phase, and knowledge/visibility.

Solo play is implicitly two seats (GM + one player) on one machine. The desk
toggle is an active-seat swap. Knowledge edges gate what a player seat may see;
the GM seat sees the full playbook.
"""

from __future__ import annotations

import json
from typing import Any, Optional

SEAT_ROLES = ("gm", "player")
TURN_PHASES = ("idle", "awaiting_roll", "model_busy")
KNOWLEDGE_LEVELS = ("rumored", "known", "present")
SUBJECT_TYPES = ("character", "actor")
# Levels that appear on a player roster / awareness list.
VISIBLE_KNOWLEDGE = frozenset(KNOWLEDGE_LEVELS)


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


def _seat_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "seat_key": row["seat_key"],
        "display_name": row["display_name"] or "",
        "role": row["role"],
        "character_id": row["character_id"],
    }


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


def _bootstrap_player_character(db, player_seat_id: int) -> None:
    """Link protagonist (if any) and sync scene/ally awareness."""
    seat = get_seat(db, player_seat_id)
    if seat is None:
        return
    character_id = seat["character_id"]
    if character_id is None:
        rows = db.execute(
            "SELECT id, name, campaign_role FROM characters ORDER BY id"
        ).fetchall()
        pick = None
        for row in rows:
            role = str(row["campaign_role"] or "").casefold()
            if any(token in role for token in ("protagonist", "player", "pc")):
                pick = row
                break
        if pick is not None:
            db.execute(
                "UPDATE seats SET character_id=? WHERE id=?",
                (pick["id"], player_seat_id),
            )
            character_id = pick["id"]
    if character_id is None:
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

    # Standing allies / party (not enemies, not scaffolds).
    for peer in db.execute(
        """
        SELECT id, campaign_role, data FROM characters WHERE id != ?
        """,
        (character_id,),
    ):
        role = str(peer["campaign_role"] or "").casefold()
        if any(token in role for token in ("ally", "party", "companion")):
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
    for row in db.execute(
        """
        SELECT message FROM log
        ORDER BY id DESC LIMIT 40
        """
    ):
        blob_parts.append(str(row["message"] or ""))
    blob = "\n".join(blob_parts)
    if blob.strip():
        for peer in db.execute("SELECT id, name FROM characters WHERE id != ?", (character_id,)):
            name = str(peer["name"] or "").strip()
            if len(name) < 3:
                continue
            if name.casefold() in blob.casefold():
                note(int(peer["id"]), "known", "recent_mention")

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
    return {
        "active_seat_id": ensure_row["active_seat_id"],
        "turn_phase": ensure_row["turn_phase"] or "idle",
        "hotseat_seat_id": ensure_row["hotseat_seat_id"],
        "turn_wait": wait,
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

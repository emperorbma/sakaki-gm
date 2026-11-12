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


def _bootstrap_player_character(db, player_seat_id: int) -> None:
    """Link protagonist (if any) and seed minimal self-knowledge."""
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
        if pick is None and rows:
            # No role hint: leave unlinked; knowledge stays empty until planned.
            pick = None
        if pick is not None:
            db.execute(
                "UPDATE seats SET character_id=? WHERE id=?",
                (pick["id"], player_seat_id),
            )
            character_id = pick["id"]
    if character_id is None:
        return
    set_knowledge(
        db,
        player_seat_id,
        "character",
        character_id,
        "present",
        commit=False,
    )


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
    known = character_ids_known_to_seat(db, seat["id"])
    # Always include the seat's bound character if present in the list.
    bound = seat.get("character_id")
    if bound is not None:
        known.add(int(bound))
    return [char for char in characters if char.get("id") in known]

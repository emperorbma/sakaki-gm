"""Actor / brain shell: minds with drive, goals, traits, and affect.

Actors are the meta-architecture for **automatic AI control** of persons,
factions, pantheon entities, and political bodies — not the GM's direct Ask
focus. GM direct control uses seat/party focus (singleton lease or multi-member
group). When the GM portrays those characters, call
``observe_gm_play_for_actors`` so later AI inferences can follow the table.

Desk edits are design/debug access (a logical "cheat" outside initial
population or Dreamtime); live play should prefer plot-generated actors and
between-session refresh.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

ACTOR_KINDS = (
    "person",
    "org",
    "pantheon",
    "political",
    "other",
)

PROVENANCE = (
    "authored",
    "derived_from_history",
    "plot_generated",
)

GOAL_STATUSES = (
    "active",
    "paused",
    "done",
    "failed",
)

AFFECT_AXES = (
    "fear",
    "love",
    "loyalty",
    "trust",
)

MORTALITY = (
    "mortal",
    "unkillable",
    "banish_only",
)


def _normalize_kind(kind: str) -> str:
    value = (kind or "person").strip().lower() or "person"
    if value not in ACTOR_KINDS:
        raise ValueError(
            f"Unknown actor kind {kind!r}; expected one of {', '.join(ACTOR_KINDS)}."
        )
    return value


def _normalize_provenance(provenance: str) -> str:
    value = (provenance or "authored").strip().lower() or "authored"
    if value not in PROVENANCE:
        raise ValueError(
            f"Unknown provenance {provenance!r}; "
            f"expected one of {', '.join(PROVENANCE)}."
        )
    return value


def _normalize_mortality(mortality: Optional[str]) -> Optional[str]:
    if mortality is None:
        return None
    value = str(mortality).strip().lower()
    if not value:
        return None
    if value not in MORTALITY:
        raise ValueError(
            f"Unknown mortality {mortality!r}; "
            f"expected one of {', '.join(MORTALITY)} or empty."
        )
    return value


def _row_to_actor(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "character_id": row["character_id"],
        "drive": row["drive"] or "",
        "methods": row["methods"] or "",
        "mortality": row["mortality"],
        "provenance": row["provenance"] or "authored",
        "confidence": row["confidence"],
        "notes": row["notes"] or "",
    }


def get_actors(db) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM actors ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [_row_to_actor(row) for row in rows]


def get_actor(db, actor_id: int) -> Optional[dict[str, Any]]:
    row = db.execute("SELECT * FROM actors WHERE id=?", (actor_id,)).fetchone()
    if row is None:
        return None
    return _row_to_actor(row)


def get_actor_by_name(db, name: str) -> Optional[dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM actors WHERE name=? COLLATE NOCASE",
        (name.strip(),),
    ).fetchone()
    if row is None:
        return None
    return _row_to_actor(row)


def get_actor_for_character(db, character_id: int) -> Optional[dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM actors WHERE character_id=? ORDER BY id LIMIT 1",
        (character_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_actor(row)


def create_actor(
    db,
    name: str,
    *,
    kind: str = "person",
    character_id: Optional[int] = None,
    drive: str = "",
    methods: str = "",
    mortality: Optional[str] = None,
    provenance: str = "authored",
    confidence: Optional[float] = None,
    notes: str = "",
    traits: Iterable[str] = (),
    commit: bool = True,
) -> int:
    """Create an actor. Prefer plot_generated / Dreamtime over desk cheats in play."""
    name = name.strip()
    if not name:
        raise ValueError("Actor name cannot be empty.")
    kind = _normalize_kind(kind)
    provenance = _normalize_provenance(provenance)
    mortality = _normalize_mortality(mortality)
    trait_tags = sorted({tag.strip() for tag in traits if tag and tag.strip()})
    cur = db.execute(
        """
        INSERT INTO actors (
            name, kind, character_id, drive, methods, mortality,
            provenance, confidence, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            kind,
            character_id,
            drive.strip(),
            methods.strip(),
            mortality,
            provenance,
            confidence,
            notes.strip(),
        ),
    )
    actor_id = cur.lastrowid
    for tag in trait_tags:
        db.execute(
            "INSERT OR IGNORE INTO actor_traits (actor_id, tag) VALUES (?, ?)",
            (actor_id, tag),
        )
    if commit:
        db.commit()
    return actor_id


def update_actor(
    db,
    actor_id: int,
    *,
    name: Optional[str] = None,
    kind: Optional[str] = None,
    character_id: Any = ...,
    drive: Optional[str] = None,
    methods: Optional[str] = None,
    mortality: Any = ...,
    provenance: Optional[str] = None,
    confidence: Any = ...,
    notes: Optional[str] = None,
    commit: bool = True,
) -> None:
    current = get_actor(db, actor_id)
    if current is None:
        raise ValueError(f"Unknown actor {actor_id}.")
    new_name = current["name"] if name is None else name.strip()
    if not new_name:
        raise ValueError("Actor name cannot be empty.")
    new_kind = current["kind"] if kind is None else _normalize_kind(kind)
    if character_id is ...:
        new_character_id = current["character_id"]
    else:
        new_character_id = character_id
    new_drive = current["drive"] if drive is None else drive.strip()
    new_methods = current["methods"] if methods is None else methods.strip()
    if mortality is ...:
        new_mortality = current["mortality"]
    else:
        new_mortality = _normalize_mortality(mortality)
    new_provenance = (
        current["provenance"]
        if provenance is None
        else _normalize_provenance(provenance)
    )
    if confidence is ...:
        new_confidence = current["confidence"]
    else:
        new_confidence = confidence
    new_notes = current["notes"] if notes is None else notes.strip()
    db.execute(
        """
        UPDATE actors SET
            name=?, kind=?, character_id=?, drive=?, methods=?,
            mortality=?, provenance=?, confidence=?, notes=?
        WHERE id=?
        """,
        (
            new_name,
            new_kind,
            new_character_id,
            new_drive,
            new_methods,
            new_mortality,
            new_provenance,
            new_confidence,
            new_notes,
            actor_id,
        ),
    )
    if commit:
        db.commit()


def delete_actor(db, actor_id: int, *, commit: bool = True) -> None:
    db.execute("DELETE FROM actors WHERE id=?", (actor_id,))
    if commit:
        db.commit()


def create_actor_for_plot(
    db,
    name: str,
    *,
    kind: str = "person",
    drive: str = "",
    methods: str = "",
    mortality: Optional[str] = None,
    traits: Iterable[str] = (),
    notes: str = "",
    character_id: Optional[int] = None,
    commit: bool = True,
) -> int:
    """Spawn an actor because the plot demands a new mind (not a desk cheat)."""
    return create_actor(
        db,
        name,
        kind=kind,
        character_id=character_id,
        drive=drive,
        methods=methods,
        mortality=mortality,
        provenance="plot_generated",
        confidence=None,
        notes=notes,
        traits=traits,
        commit=commit,
    )


def get_brain_goals(db, actor_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, actor_id, summary, status, priority, quest_id
        FROM brain_goals
        WHERE actor_id=?
        ORDER BY priority DESC, id
        """,
        (actor_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_brain_goals(
    db,
    actor_id: int,
    goals: Iterable[dict[str, Any]],
    *,
    commit: bool = True,
) -> None:
    """Replace an actor's goal list. Each goal: summary, status, priority, quest_id?"""
    if get_actor(db, actor_id) is None:
        raise ValueError(f"Unknown actor {actor_id}.")
    normalized = []
    for goal in goals:
        summary = str(goal.get("summary") or "").strip()
        if not summary:
            continue
        status = str(goal.get("status") or "active").strip().lower() or "active"
        if status not in GOAL_STATUSES:
            raise ValueError(
                f"Unknown goal status {status!r}; "
                f"expected one of {', '.join(GOAL_STATUSES)}."
            )
        priority = int(goal.get("priority") or 0)
        quest_id = goal.get("quest_id")
        if quest_id is not None:
            quest_id = int(quest_id)
        normalized.append((actor_id, summary, status, priority, quest_id))
    db.execute("DELETE FROM brain_goals WHERE actor_id=?", (actor_id,))
    db.executemany(
        """
        INSERT INTO brain_goals (actor_id, summary, status, priority, quest_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        normalized,
    )
    if commit:
        db.commit()


def add_brain_goal(
    db,
    actor_id: int,
    summary: str,
    *,
    status: str = "active",
    priority: int = 0,
    quest_id: Optional[int] = None,
    commit: bool = True,
) -> int:
    summary = summary.strip()
    if not summary:
        raise ValueError("Goal summary cannot be empty.")
    status = (status or "active").strip().lower() or "active"
    if status not in GOAL_STATUSES:
        raise ValueError(
            f"Unknown goal status {status!r}; "
            f"expected one of {', '.join(GOAL_STATUSES)}."
        )
    if get_actor(db, actor_id) is None:
        raise ValueError(f"Unknown actor {actor_id}.")
    cur = db.execute(
        """
        INSERT INTO brain_goals (actor_id, summary, status, priority, quest_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (actor_id, summary, status, int(priority), quest_id),
    )
    if commit:
        db.commit()
    return cur.lastrowid


def sync_goals_from_quests(
    db,
    actor_id: int,
    character_id: int,
    *,
    commit: bool = True,
) -> list[dict[str, Any]]:
    """Attach quest rows for a character as brain goals (reuse quest metadata)."""
    if get_actor(db, actor_id) is None:
        raise ValueError(f"Unknown actor {actor_id}.")
    quests = db.execute(
        """
        SELECT q.id, q.name, q.description, q.status
        FROM quests q
        JOIN quest_characters qc ON qc.quest_id = q.id
        WHERE qc.character_id = ?
        ORDER BY q.name COLLATE NOCASE
        """,
        (character_id,),
    ).fetchall()
    existing = {
        (goal["quest_id"], goal["summary"])
        for goal in get_brain_goals(db, actor_id)
        if goal["quest_id"] is not None
    }
    added = []
    for quest in quests:
        status_raw = (quest["status"] or "").strip().lower()
        if status_raw in {"done", "complete", "completed", "finished"}:
            goal_status = "done"
        elif status_raw in {"failed", "fail", "abandoned"}:
            goal_status = "failed"
        elif status_raw in {"paused", "hold", "waiting"}:
            goal_status = "paused"
        else:
            goal_status = "active"
        summary = quest["name"]
        key = (quest["id"], summary)
        if key in existing:
            continue
        goal_id = add_brain_goal(
            db,
            actor_id,
            summary,
            status=goal_status,
            priority=1 if goal_status == "active" else 0,
            quest_id=quest["id"],
            commit=False,
        )
        added.append(
            {
                "id": goal_id,
                "summary": summary,
                "status": goal_status,
                "quest_id": quest["id"],
            }
        )
    if commit:
        db.commit()
    return added


def get_actor_traits(db, actor_id: int) -> list[str]:
    rows = db.execute(
        """
        SELECT tag FROM actor_traits
        WHERE actor_id=?
        ORDER BY tag COLLATE NOCASE
        """,
        (actor_id,),
    ).fetchall()
    return [row["tag"] for row in rows]


def set_actor_traits(
    db,
    actor_id: int,
    traits: Iterable[str],
    *,
    commit: bool = True,
) -> None:
    if get_actor(db, actor_id) is None:
        raise ValueError(f"Unknown actor {actor_id}.")
    tags = sorted({tag.strip() for tag in traits if tag and tag.strip()})
    db.execute("DELETE FROM actor_traits WHERE actor_id=?", (actor_id,))
    db.executemany(
        "INSERT INTO actor_traits (actor_id, tag) VALUES (?, ?)",
        [(actor_id, tag) for tag in tags],
    )
    if commit:
        db.commit()


def get_affect_edges(db, actor_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, from_actor_id, to_actor_id, to_character_id, axis, level
        FROM affect_edges
        WHERE from_actor_id=?
        ORDER BY axis, id
        """,
        (actor_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_affect_edge(
    db,
    from_actor_id: int,
    axis: str,
    level: int,
    *,
    to_actor_id: Optional[int] = None,
    to_character_id: Optional[int] = None,
    commit: bool = True,
) -> int:
    axis = (axis or "").strip().lower()
    if axis not in AFFECT_AXES:
        raise ValueError(
            f"Unknown affect axis {axis!r}; expected one of {', '.join(AFFECT_AXES)}."
        )
    if (to_actor_id is None) == (to_character_id is None):
        raise ValueError("Affect edge needs exactly one of to_actor_id or to_character_id.")
    if get_actor(db, from_actor_id) is None:
        raise ValueError(f"Unknown actor {from_actor_id}.")
    if to_actor_id is not None:
        existing = db.execute(
            """
            SELECT id FROM affect_edges
            WHERE from_actor_id=? AND axis=? AND to_actor_id=?
            """,
            (from_actor_id, axis, to_actor_id),
        ).fetchone()
    else:
        existing = db.execute(
            """
            SELECT id FROM affect_edges
            WHERE from_actor_id=? AND axis=? AND to_character_id=?
            """,
            (from_actor_id, axis, to_character_id),
        ).fetchone()
    if existing is not None:
        db.execute(
            "UPDATE affect_edges SET level=? WHERE id=?",
            (int(level), existing["id"]),
        )
        edge_id = existing["id"]
    else:
        cur = db.execute(
            """
            INSERT INTO affect_edges (
                from_actor_id, to_actor_id, to_character_id, axis, level
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (from_actor_id, to_actor_id, to_character_id, axis, int(level)),
        )
        edge_id = cur.lastrowid
    if commit:
        db.commit()
    return edge_id


def bind_role_to_actor(
    db,
    role_key: str,
    actor_id: int,
    *,
    commit: bool = True,
) -> None:
    role_key = role_key.strip()
    if not role_key:
        raise ValueError("Role key cannot be empty.")
    if get_actor(db, actor_id) is None:
        raise ValueError(f"Unknown actor {actor_id}.")
    db.execute(
        """
        INSERT INTO actor_role_bindings (role_key, actor_id)
        VALUES (?, ?)
        ON CONFLICT(role_key) DO UPDATE SET actor_id=excluded.actor_id
        """,
        (role_key, actor_id),
    )
    if commit:
        db.commit()


def get_actor_for_role(db, role_key: str) -> Optional[dict[str, Any]]:
    row = db.execute(
        """
        SELECT a.*
        FROM actor_role_bindings b
        JOIN actors a ON a.id = b.actor_id
        WHERE b.role_key=? COLLATE NOCASE
        """,
        (role_key.strip(),),
    ).fetchone()
    if row is None:
        return None
    return _row_to_actor(row)


def get_brain(db, actor_id: int) -> Optional[dict[str, Any]]:
    """Full mind snapshot for GM prompt / Dreamtime."""
    actor = get_actor(db, actor_id)
    if actor is None:
        return None
    return {
        **actor,
        "traits": get_actor_traits(db, actor_id),
        "goals": get_brain_goals(db, actor_id),
        "affect": get_affect_edges(db, actor_id),
    }


def ensure_derived_character_actor(
    db,
    character_id: int,
    *,
    drive: str = "",
    methods: str = "",
    traits: Iterable[str] = (),
    confidence: float = 0.5,
    notes: str = "",
    sync_quests: bool = True,
    commit: bool = True,
) -> int:
    """Create or refresh a derived_from_history actor linked to a character sheet."""
    row = db.execute(
        "SELECT id, name FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown character {character_id}.")
    existing = get_actor_for_character(db, character_id)
    if existing is None:
        actor_id = create_actor(
            db,
            row["name"],
            kind="person",
            character_id=character_id,
            drive=drive,
            methods=methods,
            provenance="derived_from_history",
            confidence=confidence,
            notes=notes,
            traits=traits,
            commit=False,
        )
    else:
        actor_id = existing["id"]
        update_actor(
            db,
            actor_id,
            drive=drive if drive else None,
            methods=methods if methods else None,
            provenance="derived_from_history",
            confidence=confidence,
            notes=notes if notes else None,
            commit=False,
        )
        if traits:
            set_actor_traits(db, actor_id, traits, commit=False)
    if sync_quests:
        sync_goals_from_quests(db, actor_id, character_id, commit=False)
    if commit:
        db.commit()
    return actor_id


def observe_gm_play_for_actors(
    db,
    *,
    narration: str,
    character_ids: Iterable[int],
    commit: bool = True,
) -> list[int]:
    """Feed GM-portrayed play into linked Actors so AI control can follow the table.

    Does not put Actors under GM Ask control. Appends a short observed-play
    note to each Actor already linked to the given characters. Returns actor
    ids that were updated.
    """
    text = (narration or "").strip()
    if not text:
        return []
    # Keep notes bounded; Actors still own their own goals/drive.
    snippet = text if len(text) <= 900 else text[:897].rstrip() + "…"
    stamp = f"\n\n[Observed GM play]\n{snippet}"
    updated: list[int] = []
    seen: set[int] = set()
    for raw_id in character_ids:
        try:
            character_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        actor = get_actor_for_character(db, character_id)
        if actor is None or actor["id"] in seen:
            continue
        seen.add(actor["id"])
        notes = (actor.get("notes") or "").rstrip()
        # Avoid stacking identical back-to-back observations.
        if notes.endswith(snippet):
            continue
        new_notes = (notes + stamp).strip()
        if len(new_notes) > 8000:
            new_notes = new_notes[-8000:].lstrip()
        update_actor(db, actor["id"], notes=new_notes, commit=False)
        updated.append(actor["id"])
    if commit and updated:
        db.commit()
    return updated

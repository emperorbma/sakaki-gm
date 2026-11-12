"""Party stub: membership, split/merge, and GM-approved proposals.

Knowledge stays seat-scoped for now, but party membership is the grouping
that will drive split-party awareness (different subgroups, different views).
Players may request merge/unmerge; the GM approves or rejects before state
changes. The GM may also apply split/merge directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

PARTY_STATUSES = ("active", "disbanded")
PROPOSAL_OPS = ("split", "merge")
PROPOSAL_STATUSES = ("pending", "approved", "rejected")


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_status(status: str) -> str:
    value = str(status or "active").strip().lower() or "active"
    if value not in PARTY_STATUSES:
        raise ValueError(f"Unknown party status {status!r}.")
    return value


def _normalize_proposal_op(op: str) -> str:
    value = str(op or "").strip().lower()
    if value not in PROPOSAL_OPS:
        raise ValueError(f"Unknown party proposal op {op!r}.")
    return value


def _party_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"] or "",
        "status": row["status"] or "active",
        "notes": row["notes"] or "",
        "created_at": row["created_at"] or "",
    }


def _proposal_row(row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": row["id"],
        "op": row["op"],
        "status": row["status"],
        "requested_by_seat_id": row["requested_by_seat_id"],
        "payload": payload,
        "note": row["note"] or "",
        "created_at": row["created_at"] or "",
        "resolved_at": row["resolved_at"] or "",
    }


def list_parties(db, *, status: str | None = "active") -> list[dict[str, Any]]:
    if status is None:
        rows = db.execute(
            "SELECT * FROM parties ORDER BY id"
        ).fetchall()
    else:
        status = _normalize_status(status)
        rows = db.execute(
            "SELECT * FROM parties WHERE status=? ORDER BY id",
            (status,),
        ).fetchall()
    return [_party_row(row) for row in rows]


def get_party(db, party_id: int) -> Optional[dict[str, Any]]:
    row = db.execute("SELECT * FROM parties WHERE id=?", (party_id,)).fetchone()
    if row is None:
        return None
    return _party_row(row)


def party_member_ids(db, party_id: int) -> list[int]:
    rows = db.execute(
        """
        SELECT character_id FROM party_members
        WHERE party_id=?
        ORDER BY character_id
        """,
        (party_id,),
    ).fetchall()
    return [int(row["character_id"]) for row in rows]


def get_party_with_members(db, party_id: int) -> Optional[dict[str, Any]]:
    party = get_party(db, party_id)
    if party is None:
        return None
    party["member_ids"] = party_member_ids(db, party_id)
    return party


def active_party_for_character(db, character_id: int) -> Optional[dict[str, Any]]:
    row = db.execute(
        """
        SELECT parties.*
        FROM party_members
        JOIN parties ON parties.id = party_members.party_id
        WHERE party_members.character_id=?
          AND parties.status='active'
        ORDER BY parties.id
        LIMIT 1
        """,
        (character_id,),
    ).fetchone()
    if row is None:
        return None
    return _party_row(row)


def create_party(
    db,
    name: str,
    *,
    member_ids: list[int] | None = None,
    notes: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    name = str(name or "").strip() or "Party"
    db.execute(
        """
        INSERT INTO parties (name, status, notes, created_at)
        VALUES (?, 'active', ?, ?)
        """,
        (name, str(notes or ""), _utcnow()),
    )
    party_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    for character_id in member_ids or []:
        add_party_member(db, party_id, int(character_id), commit=False)
    if commit:
        db.commit()
    return get_party_with_members(db, party_id)


def add_party_member(
    db, party_id: int, character_id: int, *, commit: bool = True
) -> None:
    party = get_party(db, party_id)
    if party is None or party["status"] != "active":
        raise ValueError(f"Active party {party_id} not found.")
    char = db.execute(
        "SELECT id FROM characters WHERE id=?", (character_id,)
    ).fetchone()
    if char is None:
        raise ValueError(f"Unknown character {character_id}.")
    existing = active_party_for_character(db, character_id)
    if existing is not None and existing["id"] != party_id:
        raise ValueError(
            f"Character {character_id} is already in active party "
            f"{existing['id']} ({existing['name']}). Split or merge first."
        )
    db.execute(
        """
        INSERT OR IGNORE INTO party_members (party_id, character_id)
        VALUES (?, ?)
        """,
        (party_id, character_id),
    )
    if commit:
        db.commit()


def remove_party_member(
    db, party_id: int, character_id: int, *, commit: bool = True
) -> None:
    db.execute(
        "DELETE FROM party_members WHERE party_id=? AND character_id=?",
        (party_id, character_id),
    )
    if commit:
        db.commit()


def _bootstrap_member_ids(db) -> list[int]:
    member_ids: list[int] = []
    for row in db.execute(
        "SELECT id, campaign_role FROM characters ORDER BY id"
    ):
        role = str(row["campaign_role"] or "").casefold()
        if any(token in role for token in ("protagonist", "player", "pc", "party")):
            member_ids.append(int(row["id"]))
    if member_ids:
        return member_ids
    seat = db.execute(
        "SELECT character_id FROM seats WHERE role='player' ORDER BY id LIMIT 1"
    ).fetchone()
    if seat and seat["character_id"] is not None:
        return [int(seat["character_id"])]
    return []


def ensure_default_party(db, *, commit: bool = True) -> dict[str, Any]:
    """Bootstrap a single active party from protagonists / bound player PCs."""
    active = list_parties(db, status="active")
    if active:
        party = get_party_with_members(db, active[0]["id"])
        assert party is not None
        # Fill an empty default party once characters exist (common after migrate).
        if not party.get("member_ids"):
            for character_id in _bootstrap_member_ids(db):
                try:
                    add_party_member(db, party["id"], character_id, commit=False)
                except ValueError:
                    continue
            party = get_party_with_members(db, party["id"])
            assert party is not None
        if commit:
            db.commit()
        return party

    return create_party(
        db,
        "Party",
        member_ids=_bootstrap_member_ids(db),
        notes="Default table party",
        commit=commit,
    )


def split_party(
    db,
    party_id: int,
    character_ids: list[int],
    *,
    new_party_name: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """Unmerge: move characters into a new active party (GM / approved path)."""
    party = get_party(db, party_id)
    if party is None or party["status"] != "active":
        raise ValueError(f"Active party {party_id} not found.")
    members = set(party_member_ids(db, party_id))
    moving = [int(cid) for cid in character_ids]
    if not moving:
        raise ValueError("Split needs at least one character.")
    unknown = [cid for cid in moving if cid not in members]
    if unknown:
        raise ValueError(f"Characters not in party {party_id}: {unknown}")
    if set(moving) == members:
        raise ValueError("Cannot move every member; leave at least one behind.")
    name = str(new_party_name or "").strip() or f"{party['name']} (split)"
    new_party = create_party(db, name, member_ids=[], commit=False)
    for character_id in moving:
        remove_party_member(db, party_id, character_id, commit=False)
        add_party_member(db, new_party["id"], character_id, commit=False)
    if commit:
        db.commit()
    return {
        "remaining": get_party_with_members(db, party_id),
        "split": get_party_with_members(db, new_party["id"]),
    }


def merge_parties(
    db,
    party_ids: list[int],
    *,
    into_party_id: int | None = None,
    new_name: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """Merge active parties into one (GM / approved path)."""
    ids = list(dict.fromkeys(int(pid) for pid in party_ids))
    if len(ids) < 2:
        raise ValueError("Merge needs at least two parties.")
    parties = []
    for party_id in ids:
        party = get_party(db, party_id)
        if party is None or party["status"] != "active":
            raise ValueError(f"Active party {party_id} not found.")
        parties.append(party)
    target_id = int(into_party_id) if into_party_id is not None else ids[0]
    if target_id not in ids:
        raise ValueError("into_party_id must be one of the merged parties.")
    if new_name.strip():
        db.execute(
            "UPDATE parties SET name=? WHERE id=?",
            (new_name.strip(), target_id),
        )
    for party_id in ids:
        if party_id == target_id:
            continue
        for character_id in party_member_ids(db, party_id):
            remove_party_member(db, party_id, character_id, commit=False)
            add_party_member(db, target_id, character_id, commit=False)
        db.execute(
            "UPDATE parties SET status='disbanded' WHERE id=?",
            (party_id,),
        )
    if commit:
        db.commit()
    result = get_party_with_members(db, target_id)
    assert result is not None
    return result


def list_party_proposals(
    db, *, status: str | None = "pending"
) -> list[dict[str, Any]]:
    if status is None:
        rows = db.execute(
            "SELECT * FROM party_proposals ORDER BY id DESC"
        ).fetchall()
    else:
        value = str(status).strip().lower()
        if value not in PROPOSAL_STATUSES:
            raise ValueError(f"Unknown proposal status {status!r}.")
        rows = db.execute(
            "SELECT * FROM party_proposals WHERE status=? ORDER BY id DESC",
            (value,),
        ).fetchall()
    return [_proposal_row(row) for row in rows]


def get_party_proposal(db, proposal_id: int) -> Optional[dict[str, Any]]:
    row = db.execute(
        "SELECT * FROM party_proposals WHERE id=?", (proposal_id,)
    ).fetchone()
    if row is None:
        return None
    return _proposal_row(row)


def request_party_split(
    db,
    *,
    from_party_id: int,
    character_ids: list[int],
    new_party_name: str = "",
    requested_by_seat_id: int | None = None,
    note: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """Player/GM request to unmerge part of a party (pending until approved)."""
    payload = {
        "from_party_id": int(from_party_id),
        "character_ids": [int(cid) for cid in character_ids],
        "new_party_name": str(new_party_name or "").strip(),
    }
    return _insert_proposal(
        db,
        op="split",
        payload=payload,
        requested_by_seat_id=requested_by_seat_id,
        note=note,
        commit=commit,
    )


def request_party_merge(
    db,
    *,
    party_ids: list[int],
    into_party_id: int | None = None,
    new_name: str = "",
    requested_by_seat_id: int | None = None,
    note: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """Player/GM request to merge parties (pending until approved)."""
    payload = {
        "party_ids": [int(pid) for pid in party_ids],
        "into_party_id": int(into_party_id) if into_party_id is not None else None,
        "new_name": str(new_name or "").strip(),
    }
    return _insert_proposal(
        db,
        op="merge",
        payload=payload,
        requested_by_seat_id=requested_by_seat_id,
        note=note,
        commit=commit,
    )


def _insert_proposal(
    db,
    *,
    op: str,
    payload: dict[str, Any],
    requested_by_seat_id: int | None,
    note: str,
    commit: bool,
) -> dict[str, Any]:
    op = _normalize_proposal_op(op)
    db.execute(
        """
        INSERT INTO party_proposals (
            op, status, requested_by_seat_id, payload, note, created_at, resolved_at
        ) VALUES (?, 'pending', ?, ?, ?, ?, '')
        """,
        (
            op,
            requested_by_seat_id,
            json.dumps(payload, ensure_ascii=False),
            str(note or ""),
            _utcnow(),
        ),
    )
    proposal_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    if commit:
        db.commit()
    proposal = get_party_proposal(db, proposal_id)
    assert proposal is not None
    return proposal


def approve_party_proposal(
    db, proposal_id: int, *, commit: bool = True
) -> dict[str, Any]:
    """GM approval: apply the pending split/merge."""
    proposal = get_party_proposal(db, proposal_id)
    if proposal is None:
        raise ValueError(f"Unknown party proposal {proposal_id}.")
    if proposal["status"] != "pending":
        raise ValueError(f"Proposal {proposal_id} is {proposal['status']}, not pending.")
    payload = proposal["payload"]
    result: Any
    if proposal["op"] == "split":
        result = split_party(
            db,
            int(payload["from_party_id"]),
            list(payload.get("character_ids") or []),
            new_party_name=str(payload.get("new_party_name") or ""),
            commit=False,
        )
    elif proposal["op"] == "merge":
        into = payload.get("into_party_id")
        result = merge_parties(
            db,
            list(payload.get("party_ids") or []),
            into_party_id=int(into) if into is not None else None,
            new_name=str(payload.get("new_name") or ""),
            commit=False,
        )
    else:
        raise ValueError(f"Unsupported proposal op {proposal['op']!r}.")
    db.execute(
        """
        UPDATE party_proposals
        SET status='approved', resolved_at=?
        WHERE id=?
        """,
        (_utcnow(), proposal_id),
    )
    if commit:
        db.commit()
    return {
        "proposal": get_party_proposal(db, proposal_id),
        "result": result,
    }


def reject_party_proposal(
    db, proposal_id: int, *, commit: bool = True
) -> dict[str, Any]:
    proposal = get_party_proposal(db, proposal_id)
    if proposal is None:
        raise ValueError(f"Unknown party proposal {proposal_id}.")
    if proposal["status"] != "pending":
        raise ValueError(f"Proposal {proposal_id} is {proposal['status']}, not pending.")
    db.execute(
        """
        UPDATE party_proposals
        SET status='rejected', resolved_at=?
        WHERE id=?
        """,
        (_utcnow(), proposal_id),
    )
    if commit:
        db.commit()
    rejected = get_party_proposal(db, proposal_id)
    assert rejected is not None
    return rejected

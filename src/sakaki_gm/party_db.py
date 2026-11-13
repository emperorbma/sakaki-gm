"""Party stub: membership, split/merge, and GM-approved proposals.

Entity shape (conceptual)::

    Party
      └── Members (characters)     — seated or not; allies/NPCs need no seat
            └── Seat? (optional)   — controller bound via seats.character_id
      └── Seats attached           — seats.party_id = which party this seat is with

Maps ordinary campaign practice, not a formal bureaucracy:

- **Seated member** — that seat controls them.
- **Unseated NPC in the party** — the table runs them as a small committee
  (agree, or one member steers), unless the GM takes **override**.
- **GM override / Ask focus** — dual membership: still in the home party, also
  on a GM focus party (singleton lease or multi-member group). Clearing Ask
  focus returns the GM seat to the main party; singleton leases dissolve,
  multi-member focus groups may stay dormant for later reactivation.

**Actors** (see ``actor_db``) are separate: automatic AI faction/person minds,
not GM Ask focus. GM portrayal can feed Actors via observed-play notes.

Ordinary (non-GM) parties stay exclusive; real splits/merges still move
exclusive membership.

Knowledge stays seat-scoped for now; party membership drives split-party
awareness. Players may request merge/unmerge; the GM approves or applies
directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

PARTY_STATUSES = ("active", "disbanded")
PROPOSAL_OPS = ("split", "merge")
PROPOSAL_STATUSES = ("pending", "approved", "rejected")
# Notes marker: GM Ask focus parties (singleton lease or multi-member group).
GM_FOCUS_PARTY_NOTE = "gm_focus_party"
GM_CONTROL_PARTY_NOTE = GM_FOCUS_PARTY_NOTE  # back-compat alias
GM_FOCUS_KIND_LEASE = "lease"  # singleton character override
GM_FOCUS_KIND_GROUP = "group"  # faction / subgroup / enemy band
# How a party member is controlled right now.
CONTROL_SEATED = "seated"
# Unseated NPC in the party's midst: table agreement / one member steers.
CONTROL_COMMITTEE = "committee"
CONTROL_JOINT = CONTROL_COMMITTEE  # back-compat alias
CONTROL_GM_OVERRIDE = "gm_override"


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
    # Seats attached to this party (controllers). Many members have no seat.
    from sakaki_gm.seat_db import seats_for_party

    party["seat_ids"] = [seat["id"] for seat in seats_for_party(db, party_id)]
    return party


def active_parties_for_character(
    db, character_id: int
) -> list[dict[str, Any]]:
    """All active parties containing this character (home + optional GM party)."""
    rows = db.execute(
        """
        SELECT parties.*
        FROM party_members
        JOIN parties ON parties.id = party_members.party_id
        WHERE party_members.character_id=?
          AND parties.status='active'
        ORDER BY parties.id
        """,
        (int(character_id),),
    ).fetchall()
    return [_party_row(row) for row in rows]


def active_party_for_character(db, character_id: int) -> Optional[dict[str, Any]]:
    """Home party for a character — prefers a non-GM party when dual-membered."""
    parties = active_parties_for_character(db, character_id)
    for party in parties:
        if not _is_gm_control_party(party):
            return party
    return parties[0] if parties else None


def gm_control_party_for_character(
    db, character_id: int
) -> Optional[dict[str, Any]]:
    """GM implicit party membership, if any."""
    for party in active_parties_for_character(db, character_id):
        if _is_gm_control_party(party):
            return party
    return None


def resolve_member_control(db, character_id: int) -> dict[str, Any]:
    """Resolve how a party member is controlled (seated / committee / GM override).

    Same control idea for everyone: a dedicated seat, a party committee for an
    unseated NPC in their midst, or GM Ask focus via the GM seat's focus party
    (dual membership with the home party). Dormant focus-group membership
    without the GM seat attached is not override.
    """
    from sakaki_gm.seat_db import get_seat_by_role, get_seats

    character_id = int(character_id)
    home = active_party_for_character(db, character_id)
    gm_party = gm_control_party_for_character(db, character_id)
    controllers = [
        seat
        for seat in get_seats(db)
        if seat.get("character_id") is not None
        and int(seat["character_id"]) == character_id
    ]
    player_controllers = [s for s in controllers if s.get("role") == "player"]
    gm_controllers = [s for s in controllers if s.get("role") == "gm"]
    gm_seat = get_seat_by_role(db, "gm")
    active_focus = (
        gm_party is not None
        and gm_seat is not None
        and gm_seat.get("party_id") is not None
        and int(gm_seat["party_id"]) == int(gm_party["id"])
    )

    if gm_controllers or active_focus:
        mode = CONTROL_GM_OVERRIDE
    elif player_controllers:
        mode = CONTROL_SEATED
    else:
        mode = CONTROL_COMMITTEE

    return {
        "character_id": character_id,
        "mode": mode,
        "home_party_id": home["id"] if home else None,
        "gm_party_id": gm_party["id"] if gm_party else None,
        "controller_seat_ids": [int(s["id"]) for s in controllers],
    }


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
    party_id = int(party_id)
    character_id = int(character_id)
    # Ordinary parties stay exclusive. GM control parties may dual-member with
    # a home party (action via GM seat's party; still a player-party member).
    for existing in active_parties_for_character(db, character_id):
        if existing["id"] == party_id:
            break
        joining_gm = _is_gm_control_party(party)
        existing_gm = _is_gm_control_party(existing)
        if joining_gm or existing_gm:
            continue
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


def disband_party(db, party_id: int, *, commit: bool = True) -> None:
    """Mark a party disbanded and clear its membership rows."""
    party_id = int(party_id)
    db.execute(
        "UPDATE parties SET status='disbanded' WHERE id=?",
        (party_id,),
    )
    db.execute("DELETE FROM party_members WHERE party_id=?", (party_id,))
    if commit:
        db.commit()


def _is_gm_control_party(party: dict[str, Any] | None) -> bool:
    """True for GM Ask focus parties (lease or group), including legacy notes."""
    if not party:
        return False
    notes = str(party.get("notes") or "")
    return (
        notes == GM_FOCUS_PARTY_NOTE
        or notes.startswith(GM_FOCUS_PARTY_NOTE + ";")
        or notes == "gm_control_party"
        or notes.startswith("gm_control_party;")
    )


def _gm_focus_kind(party: dict[str, Any] | None) -> str:
    if not party:
        return GM_FOCUS_KIND_LEASE
    notes = str(party.get("notes") or "")
    for part in notes.split(";"):
        if part.startswith("kind="):
            kind = part.split("=", 1)[1].strip().lower()
            if kind in {GM_FOCUS_KIND_LEASE, GM_FOCUS_KIND_GROUP}:
                return kind
    # Legacy one-member control notes count as lease.
    return GM_FOCUS_KIND_LEASE


def _gm_focus_notes(kind: str) -> str:
    kind = str(kind or GM_FOCUS_KIND_LEASE).strip().lower()
    if kind not in {GM_FOCUS_KIND_LEASE, GM_FOCUS_KIND_GROUP}:
        kind = GM_FOCUS_KIND_LEASE
    return f"{GM_FOCUS_PARTY_NOTE};kind={kind}"


def list_gm_focus_parties(db) -> list[dict[str, Any]]:
    """All active GM Ask focus parties (singleton leases and multi-member groups)."""
    parties = []
    for party in list_parties(db, status="active"):
        if not _is_gm_control_party(party):
            continue
        full = get_party_with_members(db, party["id"])
        if full is None:
            continue
        full["focus_kind"] = _gm_focus_kind(party)
        parties.append(full)
    return parties


def dissolve_gm_focus_parties(
    db,
    *,
    party_id: int | None = None,
    keep_character_id: int | None = None,
    commit: bool = True,
) -> None:
    """Disband GM focus parties without touching home-party membership.

    ``party_id`` dissolves one. Otherwise dissolves all except an optional
    one-member lease kept for ``keep_character_id``.
    """
    keep = int(keep_character_id) if keep_character_id is not None else None
    target = int(party_id) if party_id is not None else None
    for party in list_parties(db, status="active"):
        if not _is_gm_control_party(party):
            continue
        if target is not None and int(party["id"]) != target:
            continue
        members = party_member_ids(db, party["id"])
        if (
            target is None
            and keep is not None
            and members == [keep]
            and _gm_focus_kind(party) == GM_FOCUS_KIND_LEASE
        ):
            continue
        disband_party(db, party["id"], commit=False)
    if commit:
        db.commit()


def clear_gm_control_parties(
    db, *, keep_character_id: int | None = None, commit: bool = True
) -> None:
    """Back-compat alias — dissolves GM focus parties (prefer dissolve_gm_focus_parties)."""
    dissolve_gm_focus_parties(
        db, keep_character_id=keep_character_id, commit=commit
    )


def release_character_from_gm_focus(
    db, character_id: int, *, commit: bool = True
) -> None:
    """Drop a character from GM Ask focus when a player claims them.

    Dissolves singleton leases that only contain them. Removes them from
    multi-member focus groups (disbands the group if emptied). Does not
    dissolve unrelated focus groups.
    """
    character_id = int(character_id)
    for party in list(list_gm_focus_parties(db)):
        members = [int(mid) for mid in party.get("member_ids") or []]
        if character_id not in members:
            continue
        if len(members) <= 1 or _gm_focus_kind(party) == GM_FOCUS_KIND_LEASE:
            disband_party(db, party["id"], commit=False)
            continue
        remove_party_member(db, party["id"], character_id, commit=False)
        remaining = party_member_ids(db, party["id"])
        if not remaining:
            disband_party(db, party["id"], commit=False)
    if commit:
        db.commit()


def ensure_gm_focus_party(
    db,
    member_ids: list[int],
    *,
    name: str = "",
    kind: str = GM_FOCUS_KIND_LEASE,
    commit: bool = True,
) -> dict[str, Any]:
    """Create or reuse a GM Ask focus party (lease or multi-member group).

    Does **not** dissolve other focus parties — the GM may keep several groups
    (enemy bands, factions) and switch the GM seat between them. Members with a
    home party gain dual membership; enemies with no home party simply join the
    focus party.
    """
    ids = list(dict.fromkeys(int(cid) for cid in member_ids))
    if not ids:
        raise ValueError("Focus party needs at least one member.")
    kind = str(kind or GM_FOCUS_KIND_LEASE).strip().lower()
    if kind not in {GM_FOCUS_KIND_LEASE, GM_FOCUS_KIND_GROUP}:
        kind = GM_FOCUS_KIND_LEASE
    if kind == GM_FOCUS_KIND_LEASE and len(ids) != 1:
        kind = GM_FOCUS_KIND_GROUP

    labels = []
    for character_id in ids:
        row = db.execute(
            "SELECT id, name FROM characters WHERE id=?", (character_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown character {character_id}.")
        labels.append((row["name"] or f"#{character_id}").strip())

    if kind == GM_FOCUS_KIND_LEASE:
        existing = gm_control_party_for_character(db, ids[0])
        if existing is not None and _gm_focus_kind(existing) == GM_FOCUS_KIND_LEASE:
            members = party_member_ids(db, existing["id"])
            if members == ids:
                if commit:
                    db.commit()
                party = get_party_with_members(db, existing["id"])
                assert party is not None
                party["focus_kind"] = GM_FOCUS_KIND_LEASE
                return party

    if name.strip():
        label = name.strip()
    elif kind == GM_FOCUS_KIND_LEASE:
        label = f"{labels[0]} (GM)"
    else:
        label = f"{labels[0]} band (GM)" if len(labels) == 1 else (
            f"{labels[0]} +{len(labels) - 1} (GM)"
        )

    party = create_party(
        db,
        label,
        member_ids=ids,
        notes=_gm_focus_notes(kind),
        commit=False,
    )
    party["focus_kind"] = kind
    if commit:
        db.commit()
    return party


def ensure_gm_control_party(
    db,
    character_id: int,
    *,
    display_name: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """Singleton GM lease focus — dual membership; other focus groups kept."""
    character_id = int(character_id)
    row = db.execute(
        "SELECT id, name FROM characters WHERE id=?", (character_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown character {character_id}.")
    label = (display_name or row["name"] or "Character").strip() or "Character"
    return ensure_gm_focus_party(
        db,
        [character_id],
        name=f"{label} (GM)",
        kind=GM_FOCUS_KIND_LEASE,
        commit=commit,
    )


def _bootstrap_member_ids(db) -> list[int]:
    from sakaki_gm.campaign_roles import is_playerish_campaign_role

    member_ids: list[int] = []
    for row in db.execute(
        "SELECT id, campaign_role FROM characters ORDER BY id"
    ):
        if is_playerish_campaign_role(row["campaign_role"]):
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

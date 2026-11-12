"""GM-seat tool helpers: dialogs and engine ops the human GM can run directly."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Optional

from sakaki_gm.character_db import get_all_characters
from sakaki_gm.seat_db import (
    TURN_PHASES,
    characters_available_for_gm_control,
    clear_turn_wait,
    ensure_default_session,
    get_seat_by_role,
    get_session_state,
    list_controller_seats,
    list_player_claimable_characters,
    reassign_controller_seat,
    request_player_roll,
    set_gm_control_character,
    set_turn_phase,
    sync_seat_knowledge_from_world,
    upgrade_knowledge,
)
from sakaki_gm.ui.modules.characters import _is_stat_block_scaffold

CHECK_KINDS = (
    "initiative",
    "skill",
    "ability",
    "attack",
    "saving_throw",
    "other",
)


def build_roll_wait_payload(
    *,
    character_name: str,
    kind: str,
    skill: str = "",
    dc: str | int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Normalize a GM 'request roll' payload for session turn_wait."""
    kind = (kind or "other").strip().lower() or "other"
    payload: dict[str, Any] = {
        "kind": kind,
        "character": character_name.strip(),
        "requested_by": "gm_seat",
    }
    if skill and str(skill).strip():
        payload["skill"] = str(skill).strip()
    if dc is not None and str(dc).strip() != "":
        try:
            payload["dc"] = int(dc)
        except (TypeError, ValueError):
            payload["dc"] = str(dc).strip()
    if note and str(note).strip():
        payload["note"] = str(note).strip()
    return payload


def playable_character_names(db) -> list[str]:
    names = []
    for char in get_all_characters(db):
        if "error" in char or _is_stat_block_scaffold(char):
            continue
        names.append(str(char["name"]))
    return sorted(names, key=str.casefold)


class RequestRollDialog(tk.Toplevel):
    def __init__(self, parent, db):
        super().__init__(parent)
        self.db = db
        self.result: Optional[dict[str, Any]] = None
        self.title("Request roll")
        self.transient(parent)
        self.grab_set()

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="Character").grid(row=0, column=0, sticky="w", pady=3)
        self.character = ttk.Combobox(
            body, values=playable_character_names(db), state="readonly", width=36
        )
        self.character.grid(row=0, column=1, sticky="ew", pady=3)
        names = playable_character_names(db)
        if names:
            self.character.set(names[0])

        ttk.Label(body, text="Check").grid(row=1, column=0, sticky="w", pady=3)
        self.kind = ttk.Combobox(
            body, values=CHECK_KINDS, state="readonly", width=36
        )
        self.kind.grid(row=1, column=1, sticky="ew", pady=3)
        self.kind.set("initiative")

        ttk.Label(body, text="Skill / detail").grid(row=2, column=0, sticky="w", pady=3)
        self.skill = ttk.Entry(body, width=38)
        self.skill.grid(row=2, column=1, sticky="ew", pady=3)

        ttk.Label(body, text="DC (optional)").grid(row=3, column=0, sticky="w", pady=3)
        self.dc = ttk.Entry(body, width=38)
        self.dc.grid(row=3, column=1, sticky="ew", pady=3)

        ttk.Label(body, text="Note").grid(row=4, column=0, sticky="w", pady=3)
        self.note = ttk.Entry(body, width=38)
        self.note.grid(row=4, column=1, sticky="ew", pady=3)

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(buttons, text="Request", command=self._ok).pack(side=tk.RIGHT)
        body.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _event: self._ok())
        self.character.focus_set()

    def _ok(self):
        name = self.character.get().strip()
        if not name:
            messagebox.showerror(
                "Request roll", "Choose a character.", parent=self
            )
            return
        self.result = build_roll_wait_payload(
            character_name=name,
            kind=self.kind.get(),
            skill=self.skill.get(),
            dc=self.dc.get(),
            note=self.note.get(),
        )
        self.destroy()


def run_request_roll(parent, db) -> Optional[dict[str, Any]]:
    """Dialog → awaiting_roll for the player seat hotseat."""
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    if player is None:
        messagebox.showerror(
            "Request roll", "No player seat is available.", parent=parent
        )
        return None
    dialog = RequestRollDialog(parent, db)
    parent.wait_window(dialog)
    if not dialog.result:
        return None
    state = request_player_roll(
        db,
        hotseat_seat_id=player["id"],
        wait=dialog.result,
    )
    return state


def run_clear_wait(parent, db) -> dict[str, Any]:
    ensure_default_session(db)
    state = get_session_state(db)
    if state.get("turn_phase") == "idle" and not state.get("turn_wait"):
        messagebox.showinfo("Clear wait", "Nothing is pending.", parent=parent)
        return state
    if not messagebox.askyesno(
        "Clear wait",
        "Clear the pending player wait and return to idle?",
        parent=parent,
    ):
        return state
    return clear_turn_wait(db)


def run_sync_awareness(parent, db) -> list[dict[str, Any]]:
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    if player is None:
        messagebox.showerror(
            "Sync awareness", "No player seat is available.", parent=parent
        )
        return []
    upgraded = sync_seat_knowledge_from_world(db, player["id"], commit=True)
    messagebox.showinfo(
        "Sync awareness",
        f"Updated {len(upgraded)} knowledge edge(s) for the player seat.",
        parent=parent,
    )
    return upgraded


def run_set_character_knowledge(parent, db) -> bool:
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    if player is None:
        messagebox.showerror(
            "Set knowledge", "No player seat is available.", parent=parent
        )
        return False
    names = playable_character_names(db)
    if not names:
        messagebox.showerror("Set knowledge", "No characters available.", parent=parent)
        return False
    # simpledialog can't do combobox; use a tiny chooser.
    chooser = tk.Toplevel(parent)
    chooser.title("Set character knowledge")
    chooser.transient(parent)
    chooser.grab_set()
    frame = ttk.Frame(chooser, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text="Character").grid(row=0, column=0, sticky="w")
    character = ttk.Combobox(frame, values=names, state="readonly", width=32)
    character.grid(row=0, column=1, pady=4)
    character.set(names[0])
    ttk.Label(frame, text="Level").grid(row=1, column=0, sticky="w")
    level = ttk.Combobox(
        frame, values=("rumored", "known", "present"), state="readonly", width=32
    )
    level.grid(row=1, column=1, pady=4)
    level.set("known")
    result = {"ok": False}

    def ok():
        name = character.get().strip()
        row = next(
            (
                char
                for char in get_all_characters(db)
                if str(char.get("name")) == name
            ),
            None,
        )
        if row is None:
            messagebox.showerror("Set knowledge", "Unknown character.", parent=chooser)
            return
        upgrade_knowledge(
            db, player["id"], "character", int(row["id"]), level.get(), commit=True
        )
        result["ok"] = True
        chooser.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
    ttk.Button(buttons, text="Cancel", command=chooser.destroy).pack(
        side=tk.RIGHT, padx=(6, 0)
    )
    ttk.Button(buttons, text="Save", command=ok).pack(side=tk.RIGHT)
    parent.wait_window(chooser)
    return bool(result["ok"])


def format_turn_wait_status(state: dict[str, Any]) -> str:
    phase = state.get("turn_phase") or "idle"
    wait = state.get("turn_wait") or {}
    if phase != "awaiting_roll":
        return f"phase={phase}"
    character = wait.get("character") or "?"
    kind = wait.get("kind") or "check"
    dc = wait.get("dc")
    detail = f"{character} · {kind}"
    if wait.get("skill"):
        detail += f" ({wait['skill']})"
    if dc is not None:
        detail += f" DC {dc}"
    return f"awaiting roll: {detail}"


def run_set_turn_phase(parent, db) -> Optional[dict[str, Any]]:
    """Reassign session turn_phase (idle / awaiting_roll / model_busy)."""
    ensure_default_session(db)
    state = get_session_state(db)
    current = state.get("turn_phase") or "idle"
    chooser = tk.Toplevel(parent)
    chooser.title("Set session phase")
    chooser.transient(parent)
    chooser.grab_set()
    frame = ttk.Frame(chooser, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        frame,
        text="Reassign the table state machine phase. Prefer Clear wait / "
        "Request roll for normal flow; use this when the phase is stuck.",
        wraplength=360,
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
    ttk.Label(frame, text="Phase").grid(row=1, column=0, sticky="w")
    phase = ttk.Combobox(
        frame, values=list(TURN_PHASES), state="readonly", width=28
    )
    phase.grid(row=1, column=1, sticky="ew", pady=4)
    phase.set(current if current in TURN_PHASES else "idle")
    result: dict[str, Any] = {"state": None}

    def ok():
        chosen = phase.get().strip()
        if chosen not in TURN_PHASES:
            messagebox.showerror("Set phase", "Choose a valid phase.", parent=chooser)
            return
        if chosen == "awaiting_roll" and not (state.get("turn_wait") or {}):
            if not messagebox.askyesno(
                "Set phase",
                "No turn_wait payload is set. Enter awaiting_roll anyway?",
                parent=chooser,
            ):
                return
        result["state"] = set_turn_phase(db, chosen)
        chooser.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=chooser.destroy).pack(
        side=tk.RIGHT, padx=(6, 0)
    )
    ttk.Button(buttons, text="Set", command=ok).pack(side=tk.RIGHT)
    parent.wait_window(chooser)
    return result["state"]


def run_gm_control_character(parent, db) -> Optional[str]:
    """Pick a temporary GM lens on any character not held by a present player."""
    ensure_default_session(db)
    available = characters_available_for_gm_control(db)
    gm = get_seat_by_role(db, "gm")
    current_id = gm.get("character_id") if gm else None

    dialog = tk.Toplevel(parent)
    dialog.title("Control character")
    dialog.transient(parent)
    dialog.grab_set()
    body = ttk.Frame(dialog, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        body,
        text=(
            "Temporary GM lease: any character not controlled by a player "
            "who is present (includes absentee PCs)."
        ),
        wraplength=420,
    ).pack(anchor="w")

    labels = ["(none — clear lease)"]
    ids: list[int | None] = [None]
    for item in available:
        tag = "absentee PC" if item.get("absentee_pc") else (
            item.get("campaign_role") or "character"
        )
        labels.append(f"{item['name']} ({tag})")
        ids.append(item["id"])

    pick = tk.StringVar()
    combo = ttk.Combobox(
        body, textvariable=pick, values=labels, state="readonly", width=42
    )
    combo.pack(anchor="w", pady=(10, 0))
    if current_id in ids:
        combo.current(ids.index(current_id))
    else:
        combo.current(0)

    result: dict[str, Optional[str]] = {"summary": None}

    def ok():
        index = combo.current()
        if index < 0:
            return
        character_id = ids[index]
        try:
            set_gm_control_character(db, character_id)
        except (PermissionError, ValueError) as exc:
            messagebox.showerror("Control character", str(exc), parent=dialog)
            return
        if character_id is None:
            result["summary"] = "GM cleared character lease"
        else:
            name = available[index - 1]["name"]
            result["summary"] = f"GM controlling {name} (temporary lease)"
        dialog.destroy()

    buttons = ttk.Frame(body)
    buttons.pack(fill=tk.X, pady=(16, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(
        side=tk.RIGHT, padx=(6, 0)
    )
    ttk.Button(buttons, text="Apply", command=ok).pack(side=tk.RIGHT)
    parent.wait_window(dialog)
    return result["summary"]


def run_reassign_controller_seat(parent, db) -> Optional[str]:
    """GM ownership transfer: which playerish PC a controller seat owns."""
    ensure_default_session(db)
    seats = list_controller_seats(db)
    if not seats:
        messagebox.showerror(
            "Reassign seat", "No player controller seats exist.", parent=parent
        )
        return None
    # Include already-bound PCs so the GM can move ownership freely.
    pcs = list_player_claimable_characters(
        db, present_seat_ids=set()
    )
    # Also any playerish currently on a seat (claimable list may filter occupied).
    seen = {item["id"] for item in pcs}
    for seat in seats:
        cid = seat.get("character_id")
        if cid is None or int(cid) in seen:
            continue
        row = db.execute(
            "SELECT id, name, campaign_role FROM characters WHERE id=?",
            (int(cid),),
        ).fetchone()
        if row is not None:
            pcs.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "campaign_role": row["campaign_role"] or "",
                }
            )
            seen.add(int(row["id"]))
    pcs.sort(key=lambda item: str(item["name"]).casefold())

    dialog = tk.Toplevel(parent)
    dialog.title("Reassign controller seat")
    dialog.transient(parent)
    dialog.grab_set()
    body = ttk.Frame(dialog, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        body,
        text=(
            "Transfer ownership: bind a controller seat to a playerish PC. "
            "NPCs never own seats. This is permanent seat assignment, not a "
            "temporary GM lease."
        ),
        wraplength=440,
    ).pack(anchor="w")

    ttk.Label(body, text="Controller seat").pack(anchor="w", pady=(12, 0))
    seat_labels = [
        f"{seat.get('display_name') or seat['seat_key']} [{seat['seat_key']}]"
        for seat in seats
    ]
    seat_var = tk.StringVar()
    seat_combo = ttk.Combobox(
        body, textvariable=seat_var, values=seat_labels, state="readonly", width=44
    )
    seat_combo.pack(anchor="w", pady=(4, 0))
    seat_combo.current(0)

    ttk.Label(body, text="Player character").pack(anchor="w", pady=(12, 0))
    pc_labels = ["(none — clear binding)"] + [
        f"{item['name']} ({item.get('campaign_role') or 'PC'})" for item in pcs
    ]
    pc_ids: list[int | None] = [None] + [item["id"] for item in pcs]
    pc_var = tk.StringVar()
    pc_combo = ttk.Combobox(
        body, textvariable=pc_var, values=pc_labels, state="readonly", width=44
    )
    pc_combo.pack(anchor="w", pady=(4, 0))

    def _sync_pc_from_seat(_event=None):
        index = seat_combo.current()
        if index < 0:
            return
        bound = seats[index].get("character_id")
        if bound in pc_ids:
            pc_combo.current(pc_ids.index(int(bound)))
        else:
            pc_combo.current(0)

    seat_combo.bind("<<ComboboxSelected>>", _sync_pc_from_seat)
    _sync_pc_from_seat()

    result: dict[str, Optional[str]] = {"summary": None}

    def ok():
        seat_index = seat_combo.current()
        pc_index = pc_combo.current()
        if seat_index < 0 or pc_index < 0:
            return
        seat = seats[seat_index]
        character_id = pc_ids[pc_index]
        try:
            bound = reassign_controller_seat(db, seat["id"], character_id)
        except (PermissionError, ValueError) as exc:
            messagebox.showerror("Reassign seat", str(exc), parent=dialog)
            return
        label = bound.get("display_name") or bound.get("seat_key") or "seat"
        if character_id is None:
            result["summary"] = f"GM cleared controller seat ({label})"
        else:
            name = next(
                item["name"] for item in pcs if item["id"] == character_id
            )
            result["summary"] = f"GM reassigned {label} → {name}"
        dialog.destroy()

    buttons = ttk.Frame(body)
    buttons.pack(fill=tk.X, pady=(16, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(
        side=tk.RIGHT, padx=(6, 0)
    )
    ttk.Button(buttons, text="Transfer", command=ok).pack(side=tk.RIGHT)
    parent.wait_window(dialog)
    return result["summary"]


def run_party_manager(parent, db) -> Optional[str]:
    """GM party stub: view parties, approve/reject merge/split proposals, apply ops."""
    from sakaki_gm.party_db import (
        approve_party_proposal,
        ensure_default_party,
        get_party_with_members,
        list_parties,
        list_party_proposals,
        merge_parties,
        reject_party_proposal,
        request_party_merge,
        request_party_split,
        split_party,
    )

    ensure_default_session(db)
    ensure_default_party(db)
    dialog = tk.Toplevel(parent)
    dialog.title("Parties")
    dialog.transient(parent)
    dialog.grab_set()
    body = ttk.Frame(dialog, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        body,
        text=(
            "Parties merge and unmerge with GM approval. Players may request; "
            "you approve before membership (and later split knowledge) changes."
        ),
        wraplength=480,
    ).pack(anchor="w")

    status = ttk.Label(body, text="")
    status.pack(anchor="w", pady=(8, 4))
    listbox = tk.Listbox(body, height=10, font=("Georgia", 11))
    listbox.pack(fill=tk.BOTH, expand=True, pady=4)
    result: dict[str, Any] = {"summary": None}

    def refresh():
        listbox.delete(0, tk.END)
        parties = list_parties(db, status="active")
        names = {
            int(char["id"]): str(char["name"])
            for char in get_all_characters(db)
            if "error" not in char
        }
        lines = []
        for party in parties:
            full = get_party_with_members(db, party["id"]) or party
            members = [
                names.get(cid, f"#{cid}") for cid in full.get("member_ids", [])
            ]
            lines.append(
                f"Party {party['id']}: {party['name']} — "
                + (", ".join(members) if members else "(empty)")
            )
        pending = list_party_proposals(db, status="pending")
        for proposal in pending:
            lines.append(
                f"Pending #{proposal['id']} {proposal['op']}: "
                f"{proposal['payload']} {proposal['note']}".strip()
            )
        for line in lines:
            listbox.insert(tk.END, line)
        status.configure(
            text=f"{len(parties)} active party(ies), {len(pending)} pending proposal(s)"
        )

    def _selected_party_id() -> Optional[int]:
        parties = list_parties(db, status="active")
        if not parties:
            return None
        selection = listbox.curselection()
        if selection and selection[0] < len(parties):
            return parties[selection[0]]["id"]
        return parties[0]["id"]

    def do_split():
        party_id = _selected_party_id()
        if party_id is None:
            messagebox.showerror("Parties", "No active party.", parent=dialog)
            return
        full = get_party_with_members(db, party_id)
        if not full or len(full.get("member_ids") or []) < 2:
            messagebox.showerror(
                "Parties", "Need at least two members to split.", parent=dialog
            )
            return
        names = {
            int(char["id"]): str(char["name"])
            for char in get_all_characters(db)
            if "error" not in char
        }
        # Move everyone except the first member into a scout party (stub UX).
        stay = full["member_ids"][0]
        moving = full["member_ids"][1:]
        label = ", ".join(names.get(cid, str(cid)) for cid in moving)
        if not messagebox.askyesno(
            "Split party",
            f"Split {full['name']}?\n\n"
            f"Stay: {names.get(stay, stay)}\n"
            f"New party: {label}",
            parent=dialog,
        ):
            return
        split_party(
            db,
            party_id,
            moving,
            new_party_name=f"{full['name']} (split)",
        )
        result["summary"] = f"Split party {party_id}"
        refresh()

    def do_merge():
        parties = list_parties(db, status="active")
        if len(parties) < 2:
            messagebox.showerror(
                "Parties", "Need at least two active parties to merge.", parent=dialog
            )
            return
        ids = [party["id"] for party in parties]
        labels = ", ".join(f"{p['id']}:{p['name']}" for p in parties)
        if not messagebox.askyesno(
            "Merge parties",
            f"Merge all active parties into {parties[0]['name']}?\n\n{labels}",
            parent=dialog,
        ):
            return
        merge_parties(db, ids, into_party_id=ids[0])
        result["summary"] = f"Merged parties {ids}"
        refresh()

    def do_request_split():
        party_id = _selected_party_id()
        if party_id is None:
            return
        full = get_party_with_members(db, party_id)
        if not full or len(full.get("member_ids") or []) < 2:
            messagebox.showerror(
                "Parties", "Need at least two members to request a split.", parent=dialog
            )
            return
        player = get_seat_by_role(db, "player")
        request_party_split(
            db,
            from_party_id=party_id,
            character_ids=full["member_ids"][1:],
            new_party_name=f"{full['name']} (split)",
            requested_by_seat_id=player["id"] if player else None,
            note="Player-requested split (stub)",
        )
        result["summary"] = "Queued split proposal"
        refresh()

    def do_request_merge():
        parties = list_parties(db, status="active")
        if len(parties) < 2:
            messagebox.showerror(
                "Parties", "Need two active parties to request a merge.", parent=dialog
            )
            return
        player = get_seat_by_role(db, "player")
        request_party_merge(
            db,
            party_ids=[party["id"] for party in parties],
            into_party_id=parties[0]["id"],
            requested_by_seat_id=player["id"] if player else None,
            note="Player-requested merge (stub)",
        )
        result["summary"] = "Queued merge proposal"
        refresh()

    def do_approve():
        pending = list_party_proposals(db, status="pending")
        if not pending:
            messagebox.showinfo("Parties", "No pending proposals.", parent=dialog)
            return
        proposal = pending[0]
        if not messagebox.askyesno(
            "Approve proposal",
            f"Approve #{proposal['id']} {proposal['op']}?\n{proposal['payload']}",
            parent=dialog,
        ):
            return
        approve_party_proposal(db, proposal["id"])
        result["summary"] = f"Approved proposal {proposal['id']}"
        refresh()

    def do_reject():
        pending = list_party_proposals(db, status="pending")
        if not pending:
            messagebox.showinfo("Parties", "No pending proposals.", parent=dialog)
            return
        proposal = pending[0]
        reject_party_proposal(db, proposal["id"])
        result["summary"] = f"Rejected proposal {proposal['id']}"
        refresh()

    buttons = ttk.Frame(body)
    buttons.pack(fill=tk.X, pady=(8, 0))
    ttk.Button(buttons, text="Split (GM)", command=do_split).pack(side=tk.LEFT, padx=2)
    ttk.Button(buttons, text="Merge (GM)", command=do_merge).pack(side=tk.LEFT, padx=2)
    ttk.Button(buttons, text="Request split", command=do_request_split).pack(
        side=tk.LEFT, padx=2
    )
    ttk.Button(buttons, text="Request merge", command=do_request_merge).pack(
        side=tk.LEFT, padx=2
    )
    ttk.Button(buttons, text="Approve", command=do_approve).pack(side=tk.LEFT, padx=2)
    ttk.Button(buttons, text="Reject", command=do_reject).pack(side=tk.LEFT, padx=2)
    ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side=tk.RIGHT)
    refresh()
    parent.wait_window(dialog)
    return result["summary"]

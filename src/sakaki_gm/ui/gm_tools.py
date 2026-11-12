"""GM-seat tool helpers: dialogs and engine ops the human GM can run directly."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Optional

from sakaki_gm.character_db import get_all_characters
from sakaki_gm.seat_db import (
    TURN_PHASES,
    clear_turn_wait,
    ensure_default_session,
    get_seat_by_role,
    get_session_state,
    request_player_roll,
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

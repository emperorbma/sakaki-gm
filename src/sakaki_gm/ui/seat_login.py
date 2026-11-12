"""Seat login: choose GM vs player before using the desk.

The active seat role is still stored as ``ui.desk_mode`` and mirrored onto
``session_state.active_seat_id``. Players never configure models; that stays
a GM-seat concern after login.

Password is seat-scoped (blank = open). Player login also picks which PC that
seat controls; GM may later take a temporary lease on an absentee PC.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Optional

from sakaki_gm.seat_db import (
    authenticate_seat,
    claim_player_character,
    ensure_default_session,
    get_seat_by_role,
    list_player_claimable_characters,
    seat_requires_password,
)
from sakaki_gm.ui.desk_audience import DESK_MODES, normalize_desk_mode

SEAT_LOGIN_LABELS = {
    "gm": "Game Master",
    "player": "Player",
}

SEAT_LOGIN_BLURBS = {
    "gm": (
        "Full table: tools, catalogs, model config, and AI manager controls. "
        "You choose which model the table uses. Control an absentee PC from "
        "GM tools when needed."
    ),
    "player": (
        "Play lens: pick your PC, then the seat password. Knowledge-filtered "
        "characters and places. Ask GM uses the configured default model."
    ),
}


@dataclass
class SeatLoginResult:
    role: str
    character_id: int | None = None


def seat_login_label(role: str | None) -> str:
    role = normalize_desk_mode(role)
    return SEAT_LOGIN_LABELS.get(role, SEAT_LOGIN_LABELS["gm"])


def apply_seat_choice(cfg: dict, role: str) -> dict:
    """Persist the chosen seat role into config (``ui.desk_mode``)."""
    if not isinstance(cfg.get("ui"), dict):
        cfg["ui"] = {}
    cfg["ui"]["desk_mode"] = normalize_desk_mode(role)
    return cfg


class SeatLoginDialog(tk.Toplevel):
    """Blocking GM / Player login for the local desk."""

    def __init__(
        self,
        parent,
        db,
        *,
        initial: str = "gm",
        title: str = "Seat login",
        allow_cancel: bool = True,
    ):
        super().__init__(parent)
        self.db = db
        self.result: Optional[SeatLoginResult] = None
        self.allow_cancel = allow_cancel
        self._pc_ids: list[int] = []
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        ensure_default_session(db)
        initial = normalize_desk_mode(initial)
        body = ttk.Frame(self, padding=16)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text="Who is sitting at this desk?",
            font=("Georgia", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "Seat login for the table. Switch later from the header if you "
                "are running both sides on one machine."
            ),
            wraplength=420,
        ).pack(anchor="w", pady=(6, 12))

        self.choice = tk.StringVar(value=initial)
        for role in DESK_MODES:
            row = ttk.Frame(body)
            row.pack(fill=tk.X, pady=4)
            label = SEAT_LOGIN_LABELS[role]
            if seat_requires_password(db, role):
                label = f"{label} (password)"
            ttk.Radiobutton(
                row,
                text=label,
                value=role,
                variable=self.choice,
                command=self._refresh_mode,
            ).pack(anchor="w")
            ttk.Label(
                row,
                text=SEAT_LOGIN_BLURBS[role],
                wraplength=400,
                foreground="#4a5568",
            ).pack(anchor="w", padx=(22, 0))

        self.pc_frame = ttk.LabelFrame(body, text="Player character")
        self.pc_frame.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(self.pc_frame, text="Controlled PC").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )
        self.pc_var = tk.StringVar()
        self.pc_combo = ttk.Combobox(
            self.pc_frame,
            textvariable=self.pc_var,
            state="readonly",
            width=36,
        )
        self.pc_combo.grid(row=0, column=1, sticky="w", padx=8, pady=6)

        pw = ttk.Frame(body)
        pw.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(pw, text="Seat password").pack(side=tk.LEFT)
        self.password = ttk.Entry(pw, show="*", width=28)
        self.password.pack(side=tk.LEFT, padx=(8, 0))
        self.password_hint = ttk.Label(body, text="", wraplength=420)
        self.password_hint.pack(anchor="w", pady=(4, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(16, 0))
        if allow_cancel:
            ttk.Button(buttons, text="Cancel", command=self._cancel).pack(
                side=tk.RIGHT, padx=(6, 0)
            )
        ttk.Button(buttons, text="Sit here", command=self._ok).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _event: self._ok())
        self.bind("<Escape>", lambda _event: self._cancel())
        self._refresh_mode()
        self.grab_set()
        self.focus_set()

    def _refresh_mode(self):
        role = normalize_desk_mode(self.choice.get())
        if role == "player":
            self.pc_frame.pack(fill=tk.X, pady=(12, 0))
            claimable = list_player_claimable_characters(self.db)
            self._pc_ids = [item["id"] for item in claimable]
            labels = [
                f"{item['name']} ({item['campaign_role'] or 'PC'})"
                for item in claimable
            ]
            self.pc_combo["values"] = labels
            player = get_seat_by_role(self.db, "player")
            bound = player.get("character_id") if player else None
            if bound in self._pc_ids:
                self.pc_combo.current(self._pc_ids.index(int(bound)))
            elif labels:
                self.pc_combo.current(0)
            else:
                self.pc_var.set("")
        else:
            self.pc_frame.pack_forget()
        if seat_requires_password(self.db, role):
            self.password_hint.configure(
                text="This seat requires a password."
            )
        else:
            self.password_hint.configure(
                text="No seat password — leave blank."
            )

    def _ok(self):
        role = normalize_desk_mode(self.choice.get())
        password = self.password.get()
        try:
            if role == "player":
                index = self.pc_combo.current()
                if index < 0 or index >= len(self._pc_ids):
                    raise ValueError("Choose which PC this player seat controls.")
                claim_player_character(self.db, self._pc_ids[index], password)
                self.result = SeatLoginResult(
                    role="player", character_id=self._pc_ids[index]
                )
            else:
                authenticate_seat(self.db, "gm", password)
                self.result = SeatLoginResult(role="gm")
        except (ValueError, PermissionError) as exc:
            messagebox.showerror("Seat login", str(exc), parent=self)
            self.password.delete(0, tk.END)
            self.password.focus_set()
            return
        self.destroy()

    def _cancel(self):
        if not self.allow_cancel:
            return
        self.result = None
        self.destroy()


def run_seat_login(
    parent,
    db,
    *,
    initial: str = "gm",
    title: str = "Seat login",
    allow_cancel: bool = True,
) -> Optional[SeatLoginResult]:
    """Show the seat login dialog; return result or None if cancelled."""
    dialog = SeatLoginDialog(
        parent,
        db,
        initial=initial,
        title=title,
        allow_cancel=allow_cancel,
    )
    parent.wait_window(dialog)
    return dialog.result

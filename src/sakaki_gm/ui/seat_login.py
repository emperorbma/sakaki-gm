"""Seat login: choose GM vs player before using the desk.

The active seat role is still stored as ``ui.desk_mode`` and mirrored onto
``session_state.active_seat_id``. Players never configure models; that stays
a GM-seat concern after login.

Password is a plain blank-vs-set check: empty stored hash means open seat
(blank login password). Recovery belongs to a future server lobby, not here.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from sakaki_gm.seat_db import authenticate_seat, ensure_default_session, seat_requires_password
from sakaki_gm.ui.desk_audience import DESK_MODES, normalize_desk_mode

SEAT_LOGIN_LABELS = {
    "gm": "Game Master",
    "player": "Player",
}

SEAT_LOGIN_BLURBS = {
    "gm": (
        "Full table: tools, catalogs, model config, and AI manager controls. "
        "You choose which model the table uses."
    ),
    "player": (
        "Play lens: knowledge-filtered characters and places. Ask GM uses the "
        "configured default model — you do not pick models."
    ),
}


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
        self.result: Optional[str] = None
        self.allow_cancel = allow_cancel
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
                command=self._refresh_password_hint,
            ).pack(anchor="w")
            ttk.Label(
                row,
                text=SEAT_LOGIN_BLURBS[role],
                wraplength=400,
                foreground="#4a5568",
            ).pack(anchor="w", padx=(22, 0))

        pw = ttk.Frame(body)
        pw.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(pw, text="Password").pack(side=tk.LEFT)
        self.password = ttk.Entry(pw, show="*", width=28)
        self.password.pack(side=tk.LEFT, padx=(8, 0))
        self.password_hint = ttk.Label(body, text="", wraplength=420)
        self.password_hint.pack(anchor="w", pady=(4, 0))
        self._refresh_password_hint()

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(16, 0))
        if allow_cancel:
            ttk.Button(buttons, text="Cancel", command=self._cancel).pack(
                side=tk.RIGHT, padx=(6, 0)
            )
        ttk.Button(buttons, text="Sit here", command=self._ok).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _event: self._ok())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.grab_set()
        self.password.focus_set() if seat_requires_password(
            db, initial
        ) else self.focus_set()

    def _refresh_password_hint(self):
        role = normalize_desk_mode(self.choice.get())
        if seat_requires_password(self.db, role):
            self.password_hint.configure(
                text="This seat requires a password."
            )
        else:
            self.password_hint.configure(
                text="No password set — leave blank."
            )

    def _ok(self):
        role = normalize_desk_mode(self.choice.get())
        try:
            authenticate_seat(self.db, role, self.password.get())
        except ValueError as exc:
            messagebox.showerror("Seat login", str(exc), parent=self)
            self.password.delete(0, tk.END)
            self.password.focus_set()
            return
        self.result = role
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
) -> Optional[str]:
    """Show the seat login dialog; return ``gm`` / ``player`` or None if cancelled."""
    dialog = SeatLoginDialog(
        parent,
        db,
        initial=initial,
        title=title,
        allow_cancel=allow_cancel,
    )
    parent.wait_window(dialog)
    return dialog.result

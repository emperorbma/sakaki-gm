# ui/log_panel.py
import tkinter as tk
from tkinter import ttk
from datetime import datetime

# table = every desk; gm = GM-seat ops / meta (leases, tool strip, muse status).
LOG_VISIBILITY_TABLE = "table"
LOG_VISIBILITY_GM = "gm"

# Speakers: human desk ask vs AI narration (not the GM-seat login name).
SPEAKER_PLAYER = "Player"  # human on a player controller desk
SPEAKER_CONTROLLER = "Controller"  # human on the GM desk (lease / ops ask)
SPEAKER_MODEL = "Model"  # AI muse/manager narration
SPEAKER_SYSTEM = "System"
SPEAKER_CHAT = "Chat"

_HUMAN_SPEAKERS = frozenset({SPEAKER_PLAYER, SPEAKER_CONTROLLER})


class LogPanel:
    def __init__(self, parent, db):
        self.db = db
        self._desk_mode = "gm"
        self.frame = ttk.Frame(parent)
        self.log = tk.Text(self.frame, font=("Georgia", 11), bg="#fafaf5", wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log.configure(state="disabled")
        self.reload()

    def set_db(self, db):
        self.db = db
        self.reload()

    def set_desk_audience(self, mode: str):
        from sakaki_gm.ui.desk_audience import normalize_desk_mode

        mode = normalize_desk_mode(mode)
        if mode == self._desk_mode:
            return
        self._desk_mode = mode
        self.reload()

    def reload(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        try:
            rows = self.db.execute(
                "SELECT timestamp, speaker, message, visibility FROM log ORDER BY id"
            ).fetchall()
        except Exception:
            rows = self.db.execute(
                "SELECT timestamp, speaker, message FROM log ORDER BY id"
            ).fetchall()
        for row in rows:
            visibility = "table"
            try:
                visibility = str(row["visibility"] or "table")
            except (KeyError, IndexError):
                visibility = "table"
            if self._desk_mode == "player" and visibility == LOG_VISIBILITY_GM:
                continue
            self._render(row["speaker"], row["message"], row["timestamp"])
        self.log.configure(state="disabled")
        self.log.see(tk.END)

    def add_entry(self, speaker, message, *, visibility: str | None = None):
        """Append a log line. System defaults to GM-private visibility."""
        if visibility is None:
            visibility = (
                LOG_VISIBILITY_GM
                if str(speaker) == SPEAKER_SYSTEM
                else LOG_VISIBILITY_TABLE
            )
        if visibility not in {LOG_VISIBILITY_TABLE, LOG_VISIBILITY_GM}:
            visibility = LOG_VISIBILITY_TABLE
        stamp = datetime.now().isoformat()
        if self._desk_mode != "player" or visibility != LOG_VISIBILITY_GM:
            self._render(speaker, message, stamp)
        try:
            self.db.execute(
                """
                INSERT INTO log (timestamp, speaker, message, visibility)
                VALUES (?, ?, ?, ?)
                """,
                (stamp, speaker, message, visibility),
            )
        except Exception:
            self.db.execute(
                "INSERT INTO log (timestamp, speaker, message) VALUES (?, ?, ?)",
                (stamp, speaker, message),
            )
        self.db.commit()

    def _render(self, speaker, message, timestamp):
        self.log.configure(state="normal")
        try:
            clock = datetime.fromisoformat(timestamp).strftime("%H:%M")
        except (TypeError, ValueError):
            clock = datetime.now().strftime("%H:%M")
        start = self.log.index(tk.END)
        self.log.insert(tk.END, f"[{clock}] {speaker}: {message}\n\n")
        color = {
            SPEAKER_PLAYER: "#1b5e20",
            SPEAKER_CONTROLLER: "#33691e",
            SPEAKER_MODEL: "#0d47a1",
            # Legacy bottles labeled model replies as GM before Model existed.
            "GM": "#0d47a1",
            SPEAKER_SYSTEM: "#5d4037",
            SPEAKER_CHAT: "#6a1b9a",
        }.get(speaker, "black")
        self.log.tag_add(speaker, start, "end-1c")
        self.log.tag_config(
            speaker,
            foreground=color,
            font=(
                "Georgia",
                11,
                "bold" if speaker in _HUMAN_SPEAKERS else "normal",
            ),
        )
        self.log.configure(state="disabled")
        self.log.see(tk.END)

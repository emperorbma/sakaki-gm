# ui/log_panel.py
import tkinter as tk
from tkinter import ttk
from datetime import datetime


class LogPanel:
    def __init__(self, parent, db):
        self.db = db
        self.frame = ttk.Frame(parent)
        self.log = tk.Text(self.frame, font=("Georgia", 11), bg="#fafaf5", wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log.configure(state="disabled")
        self.reload()

    def set_db(self, db):
        self.db = db
        self.reload()

    def reload(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        rows = self.db.execute(
            "SELECT timestamp, speaker, message FROM log ORDER BY id"
        ).fetchall()
        for row in rows:
            self._render(row["speaker"], row["message"], row["timestamp"])
        self.log.configure(state="disabled")
        self.log.see(tk.END)

    def add_entry(self, speaker, message):
        self._render(speaker, message, datetime.now().isoformat())
        self.db.execute(
            "INSERT INTO log (timestamp, speaker, message) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), speaker, message),
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
        color = {"Player": "#1b5e20", "GM": "#0d47a1", "System": "#5d4037"}.get(speaker, "black")
        self.log.tag_add(speaker, start, "end-1c")
        self.log.tag_config(
            speaker,
            foreground=color,
            font=("Georgia", 11, "bold" if speaker == "Player" else "normal"),
        )
        self.log.configure(state="disabled")
        self.log.see(tk.END)

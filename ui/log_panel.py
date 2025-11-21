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
        self.log.configure(state='disabled')

    def add_entry(self, speaker, message):
        self.log.configure(state='normal')
        ts = datetime.now().strftime("%H:%M")
        color = {"Player": "#1b5e20", "GM": "#0d47a1", "System": "#5d4037"}.get(speaker, "black")
        self.log.insert(tk.END, f"[{ts}] {speaker}: {message}\n\n")
        self.log.tag_add(speaker, "end-2l", "end-1l")
        self.log.tag_config(speaker, foreground=color, font=("Georgia", 11, "bold" if speaker=="Player" else ""))
        self.log.configure(state='disabled')
        self.log.see(tk.END)
        self.db.execute("INSERT INTO log (timestamp, speaker, message) VALUES (?, ?, ?)",
                       (datetime.now().isoformat(), speaker, message))
        self.db.commit()
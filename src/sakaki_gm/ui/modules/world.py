# ui/modules/world.py — standing world prompt and the event list
import json
import tkinter as tk
from tkinter import ttk

from sakaki_gm.gm import world_value


class WorldModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="World prompt", font=("Georgia", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Save", command=self.save_prompt).pack(side=tk.RIGHT)
        ttk.Label(
            self.frame,
            text="Sent with every Ask GM and Copy Prompt. Describe the setting, tone, and facts the model must keep.",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        self.text = tk.Text(self.frame, wrap=tk.WORD, font=("Georgia", 12), height=16)
        self.text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        events = ttk.LabelFrame(self.frame, text="Events")
        events.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.event_list = tk.Listbox(events, height=6)
        self.event_list.pack(fill=tk.X, padx=8, pady=(8, 4))
        row = ttk.Frame(events)
        row.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.event_entry = ttk.Entry(row)
        self.event_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.event_entry.bind("<Return>", lambda _event: self.add_event())
        ttk.Button(row, text="Add", command=self.add_event).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Remove", command=self.remove_event).pack(side=tk.LEFT)

        self.reload()

    def set_db(self, db):
        self.db = db
        self.reload()

    def reload(self):
        prompt = world_value(self.db, "prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        self.text.delete("1.0", tk.END)
        if prompt:
            self.text.insert("1.0", prompt)
        self.event_list.delete(0, tk.END)
        for item in world_value(self.db, "events", []):
            self.event_list.insert(tk.END, str(item))

    def save_prompt(self, quiet=False):
        text = self.text.get("1.0", "end").strip()
        self.db.execute(
            "INSERT OR REPLACE INTO world (key, value) VALUES ('prompt', ?)",
            (text,),
        )
        self.db.commit()
        if not quiet:
            from tkinter import messagebox
            messagebox.showinfo("Saved", "World prompt saved.")

    def add_event(self):
        text = self.event_entry.get().strip()
        if not text:
            return
        self.event_list.insert(tk.END, text)
        self.event_entry.delete(0, tk.END)
        self._write_events()

    def remove_event(self):
        selected = list(self.event_list.curselection())
        if not selected:
            return
        for index in reversed(selected):
            self.event_list.delete(index)
        self._write_events()

    def _write_events(self):
        events = [self.event_list.get(index) for index in range(self.event_list.size())]
        self.db.execute(
            "INSERT OR REPLACE INTO world (key, value) VALUES ('events', ?)",
            (json.dumps(events, ensure_ascii=False),),
        )
        self.db.commit()

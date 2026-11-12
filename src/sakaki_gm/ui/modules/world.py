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
        ttk.Label(header, text="World prompt", font=("Georgia", 14, "bold")).pack(
            side=tk.LEFT
        )
        self.save_button = ttk.Button(header, text="Save", command=self.save_prompt)
        self.save_button.pack(side=tk.RIGHT)
        self.hint = ttk.Label(
            self.frame,
            text=(
                "Sent with every Ask GM and Copy Prompt. Describe the setting, "
                "tone, and facts the model must keep."
            ),
        )
        self.hint.pack(anchor="w", padx=12, pady=(0, 6))

        self.text = tk.Text(self.frame, wrap=tk.WORD, font=("Georgia", 12), height=16)
        self.text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        events = ttk.LabelFrame(self.frame, text="Events")
        events.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.event_list = tk.Listbox(events, height=6)
        self.event_list.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.event_edit_row = ttk.Frame(events)
        self.event_edit_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.event_entry = ttk.Entry(self.event_edit_row)
        self.event_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.event_entry.bind("<Return>", lambda _event: self.add_event())
        ttk.Button(self.event_edit_row, text="Add", command=self.add_event).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            self.event_edit_row, text="Remove", command=self.remove_event
        ).pack(side=tk.LEFT)
        self._desk_mode = "gm"

        self.reload()

    def set_db(self, db):
        self.db = db
        self.reload()

    def set_desk_audience(self, mode: str):
        from sakaki_gm.ui.desk_audience import normalize_desk_mode, shared_is_stripped

        self._desk_mode = normalize_desk_mode(mode)
        stripped = shared_is_stripped(self._desk_mode)
        state = "disabled" if stripped else "normal"
        self.text.configure(state=state)
        if stripped:
            if self.save_button.winfo_ismapped():
                self.save_button.pack_forget()
            if self.event_edit_row.winfo_ismapped():
                self.event_edit_row.pack_forget()
            self.hint.configure(text="World prompt and events (read-only in player mode).")
        else:
            if not self.save_button.winfo_ismapped():
                self.save_button.pack(side=tk.RIGHT)
            if not self.event_edit_row.winfo_ismapped():
                self.event_edit_row.pack(fill=tk.X, padx=8, pady=(0, 8))
            self.hint.configure(
                text=(
                    "Sent with every Ask GM and Copy Prompt. Describe the setting, "
                    "tone, and facts the model must keep."
                )
            )

    def reload(self):
        from sakaki_gm.ui.desk_audience import shared_is_stripped

        prompt = world_value(self.db, "prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        previous = str(self.text.cget("state") or "normal")
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        if prompt:
            self.text.insert("1.0", prompt)
        if shared_is_stripped(getattr(self, "_desk_mode", "gm")):
            self.text.configure(state="disabled")
        else:
            self.text.configure(state=previous if previous in {"normal", "disabled"} else "normal")
        self.event_list.delete(0, tk.END)
        for item in world_value(self.db, "events", []):
            self.event_list.insert(tk.END, str(item))

    def save_prompt(self, quiet=False):
        from sakaki_gm.ui.desk_audience import shared_is_stripped

        if shared_is_stripped(getattr(self, "_desk_mode", "gm")):
            return
        # Text may be disabled in player mode; read via temporary enable.
        previous = str(self.text.cget("state") or "normal")
        self.text.configure(state="normal")
        text = self.text.get("1.0", "end").strip()
        self.text.configure(state=previous)
        self.db.execute(
            "INSERT OR REPLACE INTO world (key, value) VALUES ('prompt', ?)",
            (text,),
        )
        self.db.commit()
        if not quiet:
            from tkinter import messagebox
            messagebox.showinfo("Saved", "World prompt saved.")

    def add_event(self):
        from sakaki_gm.ui.desk_audience import shared_is_stripped

        if shared_is_stripped(getattr(self, "_desk_mode", "gm")):
            return
        text = self.event_entry.get().strip()
        if not text:
            return
        self.event_list.insert(tk.END, text)
        self.event_entry.delete(0, tk.END)
        self._write_events()

    def remove_event(self):
        from sakaki_gm.ui.desk_audience import shared_is_stripped

        if shared_is_stripped(getattr(self, "_desk_mode", "gm")):
            return
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

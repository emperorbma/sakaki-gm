"""Actors desk — design / debug access to brains (logical cheat outside Dreamtime)."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.actor_db import (
    ACTOR_KINDS,
    MORTALITY,
    PROVENANCE,
    create_actor,
    create_actor_for_plot,
    delete_actor,
    get_actors,
    get_brain,
    set_actor_traits,
    set_brain_goals,
    update_actor,
)


class ActorsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._actors = []
        self._selected_id = None
        self._loading = False

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="Actors", font=("Georgia", 14, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(
            header,
            text=(
                "Design / debug brains. Live play prefers plot-spawned actors "
                "and Dreamtime refresh — desk edits are a logical cheat."
            ),
            wraplength=640,
        ).pack(side=tk.LEFT, padx=(12, 0))

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
        self.list = tk.Listbox(
            body, width=34, font=("Georgia", 11), exportselection=False
        )
        self.list.pack(side=tk.LEFT, fill=tk.Y)
        self.list.bind("<<ListboxSelect>>", self._on_select)

        editor = ttk.Frame(body)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        meta = ttk.Frame(editor)
        meta.pack(fill=tk.X)
        ttk.Label(meta, text="Name").grid(row=0, column=0, sticky="w")
        self.name = ttk.Entry(meta, font=("Georgia", 11))
        self.name.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=2)
        ttk.Label(meta, text="Kind").grid(row=1, column=0, sticky="w")
        self.kind = ttk.Combobox(meta, values=ACTOR_KINDS, state="readonly")
        self.kind.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=2)
        self.kind.set("person")
        ttk.Label(meta, text="Provenance").grid(row=2, column=0, sticky="w")
        self.provenance = ttk.Combobox(meta, values=PROVENANCE, state="readonly")
        self.provenance.grid(row=2, column=1, sticky="ew", padx=(6, 0), pady=2)
        self.provenance.set("authored")
        ttk.Label(meta, text="Mortality").grid(row=3, column=0, sticky="w")
        self.mortality = ttk.Combobox(
            meta, values=("",) + MORTALITY, state="readonly"
        )
        self.mortality.grid(row=3, column=1, sticky="ew", padx=(6, 0), pady=2)
        self.mortality.set("")
        ttk.Label(meta, text="Character id").grid(row=4, column=0, sticky="w")
        self.character_id = ttk.Entry(meta, font=("Georgia", 11))
        self.character_id.grid(row=4, column=1, sticky="ew", padx=(6, 0), pady=2)
        meta.columnconfigure(1, weight=1)

        ttk.Label(editor, text="Drive (mission)").pack(anchor="w", pady=(8, 0))
        self.drive = tk.Text(editor, wrap=tk.WORD, font=("Georgia", 11), height=3)
        self.drive.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(editor, text="Methods").pack(anchor="w")
        self.methods = tk.Text(editor, wrap=tk.WORD, font=("Georgia", 11), height=2)
        self.methods.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(editor, text="Traits (comma-separated)").pack(anchor="w")
        self.traits = ttk.Entry(editor, font=("Georgia", 11))
        self.traits.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(editor, text="Notes").pack(anchor="w")
        self.notes = tk.Text(editor, wrap=tk.WORD, font=("Georgia", 11), height=3)
        self.notes.pack(fill=tk.BOTH, expand=False, pady=(2, 4))

        ttk.Label(editor, text="Goals (one per line: status|summary)").pack(
            anchor="w"
        )
        self.goals = tk.Text(editor, wrap=tk.WORD, font=("Consolas", 10), height=6)
        self.goals.pack(fill=tk.BOTH, expand=True, pady=(2, 4))

        buttons = ttk.Frame(editor)
        buttons.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(buttons, text="New", command=self.new_actor).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Save", command=self.save_current).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(
            buttons, text="Spawn for plot", command=self.spawn_for_plot
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Delete", command=self.delete_current).pack(
            side=tk.LEFT
        )
        self.reload()

    def set_db(self, db):
        self.db = db
        self._selected_id = None
        self.reload()

    def reload(self, keep_id=None):
        self._loading = True
        self._actors = get_actors(self.db)
        self.list.delete(0, tk.END)
        selected = None
        for index, actor in enumerate(self._actors):
            label = f"{actor['name']} [{actor['kind']}]"
            if actor["provenance"] != "authored":
                label += f" ({actor['provenance']})"
            self.list.insert(tk.END, label)
            if actor["id"] == keep_id:
                selected = index
        self._loading = False
        if selected is not None:
            self.list.selection_set(selected)
            self.list.see(selected)
            self._show(selected)
        else:
            self.new_actor()

    def new_actor(self):
        self._loading = True
        self._selected_id = None
        self.list.selection_clear(0, tk.END)
        self.name.delete(0, tk.END)
        self.kind.set("person")
        self.provenance.set("authored")
        self.mortality.set("")
        self.character_id.delete(0, tk.END)
        self.drive.delete("1.0", tk.END)
        self.methods.delete("1.0", tk.END)
        self.traits.delete(0, tk.END)
        self.notes.delete("1.0", tk.END)
        self.goals.delete("1.0", tk.END)
        self._loading = False
        self.name.focus_set()

    def save_current(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showerror("Actors", "Name cannot be empty.", parent=self.frame)
            return
        char_raw = self.character_id.get().strip()
        character_id = int(char_raw) if char_raw else None
        mortality = self.mortality.get().strip() or None
        traits = [part.strip() for part in self.traits.get().split(",") if part.strip()]
        goals = self._parse_goals()
        try:
            if self._selected_id is None:
                actor_id = create_actor(
                    self.db,
                    name,
                    kind=self.kind.get(),
                    character_id=character_id,
                    drive=self.drive.get("1.0", "end"),
                    methods=self.methods.get("1.0", "end"),
                    mortality=mortality,
                    provenance=self.provenance.get(),
                    notes=self.notes.get("1.0", "end"),
                    traits=traits,
                )
            else:
                actor_id = self._selected_id
                update_actor(
                    self.db,
                    actor_id,
                    name=name,
                    kind=self.kind.get(),
                    character_id=character_id,
                    drive=self.drive.get("1.0", "end"),
                    methods=self.methods.get("1.0", "end"),
                    mortality=mortality,
                    provenance=self.provenance.get(),
                    notes=self.notes.get("1.0", "end"),
                )
                set_actor_traits(self.db, actor_id, traits)
            set_brain_goals(self.db, actor_id, goals)
        except (sqlite3.Error, ValueError) as exc:
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass
            messagebox.showerror("Actors", str(exc), parent=self.frame)
            return
        self.reload(keep_id=actor_id)

    def spawn_for_plot(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showerror("Actors", "Name cannot be empty.", parent=self.frame)
            return
        traits = [part.strip() for part in self.traits.get().split(",") if part.strip()]
        try:
            actor_id = create_actor_for_plot(
                self.db,
                name,
                kind=self.kind.get() or "person",
                drive=self.drive.get("1.0", "end"),
                methods=self.methods.get("1.0", "end"),
                mortality=self.mortality.get().strip() or None,
                traits=traits,
                notes=self.notes.get("1.0", "end"),
            )
            goals = self._parse_goals()
            if goals:
                set_brain_goals(self.db, actor_id, goals)
        except (sqlite3.Error, ValueError) as exc:
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass
            messagebox.showerror("Actors", str(exc), parent=self.frame)
            return
        self.reload(keep_id=actor_id)

    def delete_current(self):
        if self._selected_id is None:
            return
        actor = next(
            (item for item in self._actors if item["id"] == self._selected_id),
            None,
        )
        if actor is None:
            return
        if not messagebox.askyesno(
            "Delete actor",
            f"Delete brain for {actor['name']}?",
            parent=self.frame,
        ):
            return
        delete_actor(self.db, actor["id"])
        self.reload()

    def _parse_goals(self):
        goals = []
        for line in self.goals.get("1.0", "end").splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                status, summary = line.split("|", 1)
                goals.append(
                    {
                        "status": status.strip() or "active",
                        "summary": summary.strip(),
                        "priority": 0,
                    }
                )
            else:
                goals.append({"status": "active", "summary": line, "priority": 0})
        return goals

    def _on_select(self, _event=None):
        if self._loading:
            return
        selected = self.list.curselection()
        if selected:
            self._show(selected[0])

    def _show(self, index):
        actor = self._actors[index]
        brain = get_brain(self.db, actor["id"]) or actor
        self._selected_id = actor["id"]
        self.name.delete(0, tk.END)
        self.name.insert(0, actor["name"])
        self.kind.set(actor["kind"])
        self.provenance.set(actor["provenance"])
        self.mortality.set(actor["mortality"] or "")
        self.character_id.delete(0, tk.END)
        if actor["character_id"] is not None:
            self.character_id.insert(0, str(actor["character_id"]))
        self.drive.delete("1.0", tk.END)
        self.drive.insert("1.0", actor["drive"])
        self.methods.delete("1.0", tk.END)
        self.methods.insert("1.0", actor["methods"])
        self.traits.delete(0, tk.END)
        self.traits.insert(0, ", ".join(brain.get("traits") or []))
        self.notes.delete("1.0", tk.END)
        self.notes.insert("1.0", actor["notes"])
        self.goals.delete("1.0", tk.END)
        lines = []
        for goal in brain.get("goals") or []:
            lines.append(f"{goal['status']}|{goal['summary']}")
        if lines:
            self.goals.insert("1.0", "\n".join(lines))

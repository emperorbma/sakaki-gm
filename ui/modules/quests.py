import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from quest_db import delete_quest, get_quest_characters, get_quests, save_quest


class QuestsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._quests = []
        self._characters = []
        self._assigned_character_ids = []
        self._selected_id = None
        self._loading = False

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="Quests", font=("Georgia", 14, "bold")).pack(side=tk.LEFT)

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
        self.list = tk.Listbox(body, width=34, font=("Georgia", 11), exportselection=False)
        self.list.pack(side=tk.LEFT, fill=tk.Y)
        self.list.bind("<<ListboxSelect>>", self._on_select)

        editor = ttk.Frame(body)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        ttk.Label(editor, text="Name").pack(anchor="w")
        self.name = ttk.Entry(editor, font=("Georgia", 11))
        self.name.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(editor, text="Status").pack(anchor="w")
        self.status = ttk.Entry(editor, font=("Georgia", 11))
        self.status.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(editor, text="Description").pack(anchor="w")
        self.description = tk.Text(editor, wrap=tk.WORD, font=("Georgia", 11), height=10)
        self.description.pack(fill=tk.BOTH, expand=True, pady=(2, 8))

        ttk.Label(editor, text="Participating characters").pack(anchor="w")
        participants = ttk.Frame(editor)
        participants.pack(fill=tk.BOTH, expand=True, pady=(2, 8))
        available = ttk.Frame(participants)
        available.pack(fill=tk.X)
        self.available_character = ttk.Combobox(available, state="readonly")
        self.available_character.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(available, text="Add", command=self.add_character).pack(side=tk.LEFT, padx=(6, 0))
        self.characters = tk.Listbox(
            participants,
            height=6,
            exportselection=False,
        )
        self.characters.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        ttk.Button(
            participants,
            text="Remove selected",
            command=self.remove_character,
        ).pack(anchor="w", pady=(4, 0))

        buttons = ttk.Frame(editor)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="New", command=self.new_quest).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Save", command=self.save_current).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Delete", command=self.delete_current).pack(side=tk.LEFT)
        self.reload()

    def set_db(self, db):
        self.db = db
        self._selected_id = None
        self.reload()

    def reload(self, keep_id=None):
        self._loading = True
        self._quests = get_quests(self.db)
        self.list.delete(0, tk.END)
        selected = None
        for index, quest in enumerate(self._quests):
            self.list.insert(tk.END, quest["name"])
            if quest["id"] == keep_id:
                selected = index
        self._loading = False
        if selected is not None:
            self.list.selection_set(selected)
            self.list.see(selected)
            self._show(selected)
        elif keep_id is None:
            self.new_quest()
        else:
            self.new_quest()

    def new_quest(self):
        self._loading = True
        self._selected_id = None
        self.list.selection_clear(0, tk.END)
        self.name.delete(0, tk.END)
        self.status.delete(0, tk.END)
        self.description.delete("1.0", tk.END)
        self._show_characters(set())
        self._loading = False
        self.name.focus_set()

    def save_current(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showerror("Quest", "Quest name cannot be empty.", parent=self.frame)
            return
        description = self.description.get("1.0", "end").strip()
        try:
            quest_id = save_quest(
                self.db,
                self._selected_id,
                name,
                description,
                self._assigned_character_ids,
                self.status.get(),
            )
        except sqlite3.IntegrityError:
            self.db.rollback()
            messagebox.showerror(
                "Quest",
                f"A quest named {name} already exists or a character assignment is invalid.",
                parent=self.frame,
            )
            return
        self.reload(keep_id=quest_id)

    def delete_current(self):
        if self._selected_id is None:
            return
        quest = next(
            (item for item in self._quests if item["id"] == self._selected_id),
            None,
        )
        if quest is None:
            return
        if not messagebox.askyesno(
            "Delete quest",
            f"Delete {quest['name']} and its character links?",
            parent=self.frame,
        ):
            return
        delete_quest(self.db, quest["id"])
        self.reload()

    def _on_select(self, _event=None):
        if self._loading:
            return
        selected = self.list.curselection()
        if selected:
            self._show(selected[0])

    def _show(self, index):
        quest = self._quests[index]
        self._selected_id = quest["id"]
        self.name.delete(0, tk.END)
        self.name.insert(0, quest["name"])
        self.status.delete(0, tk.END)
        self.status.insert(0, quest["status"])
        self.description.delete("1.0", tk.END)
        self.description.insert("1.0", quest["description"])

        assigned = {
            row["id"] for row in get_quest_characters(self.db, quest["id"])
        }
        self._loading = True
        self._show_characters(assigned)
        self._loading = False

    def _show_characters(self, assigned):
        self._characters = self.db.execute(
            "SELECT id, name FROM characters ORDER BY name COLLATE NOCASE"
        ).fetchall()
        available = [
            row for row in self._characters
            if row["id"] not in assigned
        ]
        self._available_character_ids = {
            f"{row['name']} (#{row['id']})": row["id"]
            for row in available
        }
        self.available_character["values"] = tuple(self._available_character_ids)
        if self._available_character_ids:
            self.available_character.current(0)
        else:
            self.available_character.set("")

        self._assigned_character_ids = [
            row["id"] for row in self._characters if row["id"] in assigned
        ]
        self.characters.delete(0, tk.END)
        for character_id in self._assigned_character_ids:
            row = next(row for row in self._characters if row["id"] == character_id)
            self.characters.insert(tk.END, f"{row['name']} (#{row['id']})")

    def add_character(self):
        character_id = self._available_character_ids.get(
            self.available_character.get()
        )
        if character_id is None or character_id in self._assigned_character_ids:
            return
        self._assigned_character_ids.append(character_id)
        self._render_assigned_characters()
        self._show_available_characters()

    def remove_character(self):
        selected = self.characters.curselection()
        if not selected:
            return
        del self._assigned_character_ids[selected[0]]
        self._render_assigned_characters()
        self._show_available_characters()

    def _render_assigned_characters(self):
        self.characters.delete(0, tk.END)
        rows_by_id = {row["id"]: row for row in self._characters}
        for character_id in self._assigned_character_ids:
            row = rows_by_id[character_id]
            self.characters.insert(tk.END, f"{row['name']} (#{row['id']})")

    def _show_available_characters(self):
        assigned = set(self._assigned_character_ids)
        self._available_character_ids = {
            f"{row['name']} (#{row['id']})": row["id"]
            for row in self._characters
            if row["id"] not in assigned
        }
        self.available_character["values"] = tuple(self._available_character_ids)
        if self._available_character_ids:
            self.available_character.current(0)
        else:
            self.available_character.set("")

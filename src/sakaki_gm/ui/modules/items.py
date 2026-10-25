import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.item_db import create_item, delete_item, get_items, update_item


class ItemsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._items = []
        self._loading = False
        self._selected_id = None

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="Item Archetypes", font=("Georgia", 14, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(
            self.frame,
            text=(
                "Reusable definitions. Owned copies in inventories and on the map "
                "are instances that take these defaults unless a copy overrides them. "
                "Container archetypes spawn a new container instance when added."
            ),
        ).pack(anchor="w", padx=12, pady=(0, 8))

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.list = tk.Listbox(body, width=32, font=("Georgia", 11), exportselection=False)
        self.list.pack(side=tk.LEFT, fill=tk.Y)
        self.list.bind("<<ListboxSelect>>", self._on_select)

        editor = ttk.Frame(body)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        ttk.Label(editor, text="Name").pack(anchor="w")
        self.name = ttk.Entry(editor, font=("Georgia", 11))
        self.name.pack(fill=tk.X, pady=(2, 10))
        ttk.Label(editor, text="Description").pack(anchor="w")
        self.description = tk.Text(editor, wrap=tk.WORD, font=("Georgia", 11), height=12)
        self.description.pack(fill=tk.BOTH, expand=True, pady=(2, 8))
        self.is_container = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            editor,
            text="Container archetype (creates a container instance with its own contents)",
            variable=self.is_container,
        ).pack(anchor="w", pady=(0, 8))

        buttons = ttk.Frame(editor)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="New", command=self.new_item).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Save", command=self.save_item).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Delete", command=self.delete_current).pack(side=tk.LEFT)
        self.reload()

    def set_db(self, db):
        self.db = db
        self._items = []
        self._selected_id = None
        self.reload()

    def reload(self, keep_id=None):
        self._loading = True
        self._items = get_items(self.db)
        self.list.delete(0, tk.END)
        selected = None
        for index, item in enumerate(self._items):
            self.list.insert(tk.END, item["name"])
            if item["id"] == keep_id:
                selected = index
        self._loading = False
        if selected is not None:
            self._selected_id = self._items[selected]["id"]
            self.list.selection_set(selected)
            self.list.see(selected)
            self._show(selected)
        elif keep_id is None:
            self.new_item()
        elif not self._items:
            self.new_item()

    def new_item(self):
        self._loading = True
        self._selected_id = None
        self.list.selection_clear(0, tk.END)
        self.name.delete(0, tk.END)
        self.description.delete("1.0", tk.END)
        self.is_container.set(False)
        self._loading = False
        self.name.focus_set()

    def save_item(self):
        name = self.name.get().strip()
        description = self.description.get("1.0", "end").strip()
        if not name:
            messagebox.showerror(
                "Archetype",
                "Archetype name cannot be empty.",
                parent=self.frame,
            )
            return
        item_id = self._selected_id
        try:
            if item_id is None:
                item_id = create_item(
                    self.db, name, description, self.is_container.get()
                )
            else:
                update_item(
                    self.db,
                    item_id,
                    name,
                    description,
                    self.is_container.get(),
                )
        except sqlite3.IntegrityError:
            self.db.rollback()
            messagebox.showerror(
                "Archetype",
                f"An archetype named {name} already exists.",
                parent=self.frame,
            )
            return
        self.reload(keep_id=item_id)

    def delete_current(self):
        if self._selected_id is None:
            return
        item = next(
            (candidate for candidate in self._items if candidate["id"] == self._selected_id),
            None,
        )
        if item is None:
            return
        if not messagebox.askyesno(
            "Delete archetype",
            f"Delete {item['name']} from this campaign's archetypes?",
            parent=self.frame,
        ):
            return
        try:
            delete_item(self.db, item["id"])
        except sqlite3.IntegrityError:
            self.db.rollback()
            messagebox.showerror(
                "Archetype",
                "Owned instances still link to this archetype. "
                "Remove those copies from inventories first.",
                parent=self.frame,
            )
            return
        self.reload()

    def _on_select(self, _event=None):
        if self._loading:
            return
        selected = self.list.curselection()
        if selected:
            self._selected_id = self._items[selected[0]]["id"]
            self._show(selected[0])

    def _show(self, index):
        item = self._items[index]
        self._selected_id = item["id"]
        self.name.delete(0, tk.END)
        self.name.insert(0, item["name"])
        self.description.delete("1.0", tk.END)
        self.description.insert("1.0", item["description"])
        self.is_container.set(item["is_container"])

import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.item_db import (
    attach_item_instance,
    create_item,
    create_unique_container_instance,
    create_unique_item_instance,
    delete_item,
    detach_item_instance,
    detach_instances_of_archetype,
    get_items,
    list_item_instances,
    update_item,
    update_item_instance,
)


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
        ttk.Button(
            header,
            text="Customize owned copies…",
            command=self.open_instance_utility,
        ).pack(side=tk.RIGHT)
        ttk.Label(
            self.frame,
            text=(
                "Reusable definitions. Owned copies in inventories and on the map "
                "are instances that take these defaults unless a copy overrides them. "
                "Container archetypes spawn a new container instance when added. "
                "Use Customize owned copies to edit, detach, or reattach instances."
            ),
            wraplength=780,
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
        ttk.Button(
            buttons,
            text="Detach all copies",
            command=self.detach_all_copies,
        ).pack(side=tk.LEFT, padx=6)
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
                "Detach or remove those copies first.",
                parent=self.frame,
            )
            return
        self.reload()

    def detach_all_copies(self):
        if self._selected_id is None:
            return
        item = next(
            (candidate for candidate in self._items if candidate["id"] == self._selected_id),
            None,
        )
        if item is None:
            return
        copies = list_item_instances(self.db, archetype_id=item["id"])
        if not copies:
            messagebox.showinfo(
                "Detach",
                f"No owned copies currently link to {item['name']}.",
                parent=self.frame,
            )
            return
        if not messagebox.askyesno(
            "Detach all copies",
            f"Detach {len(copies)} owned copy(ies) of {item['name']}?\n\n"
            "Each copy keeps its resolved text and no longer follows this archetype.",
            parent=self.frame,
        ):
            return
        detach_instances_of_archetype(self.db, item["id"])
        messagebox.showinfo(
            "Detach",
            f"Detached {len(copies)} copy(ies). "
            "You can delete this archetype if it is no longer needed, "
            "or reattach copies from Customize owned copies.",
            parent=self.frame,
        )

    def open_instance_utility(self):
        InstanceCustomizeDialog(
            self.frame,
            self.db,
            initial_archetype_id=self._selected_id,
        )

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


class InstanceCustomizeDialog(tk.Toplevel):
    """Edit owned instances: overrides, detach from archetype, or reattach."""

    def __init__(
        self,
        parent,
        db,
        initial_archetype_id=None,
        initial_instance_id=None,
    ):
        super().__init__(parent)
        self.db = db
        self.title("Customize owned copies")
        self.geometry("820x560")
        self.transient(parent)
        self.grab_set()

        self._instances = []
        self._selected_id = None
        self._loading = False
        self._initial_instance_id = initial_instance_id

        archetypes = get_items(db)
        self._archetypes_by_name = {item["name"]: item["id"] for item in archetypes}
        self._archetypes_by_id = {item["id"]: item for item in archetypes}

        filter_row = ttk.Frame(self)
        filter_row.pack(fill=tk.X, padx=12, pady=(12, 6))
        ttk.Label(filter_row, text="Filter by archetype").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar(value="(all owned copies)")
        filter_values = ["(all owned copies)", "(uniques only)"] + [
            item["name"] for item in archetypes
        ]
        self.filter_combo = ttk.Combobox(
            filter_row,
            textvariable=self.filter_var,
            values=filter_values,
            state="readonly",
            width=36,
        )
        self.filter_combo.pack(side=tk.LEFT, padx=8)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload_list())
        if initial_instance_id:
            # Prefer showing the concrete copy's context when opened from inventory.
            from sakaki_gm.item_db import archetype_id_for_instance, resolve_item

            linked = archetype_id_for_instance(db, initial_instance_id)
            if linked and linked in self._archetypes_by_id:
                self.filter_var.set(self._archetypes_by_id[linked]["name"])
            else:
                resolved = resolve_item(db, initial_instance_id)
                if resolved and resolved.get("archetype_id") is None:
                    self.filter_var.set("(uniques only)")
        elif initial_archetype_id and initial_archetype_id in self._archetypes_by_id:
            self.filter_var.set(self._archetypes_by_id[initial_archetype_id]["name"])

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="Owned copies").pack(anchor="w")
        self.list = tk.Listbox(left, font=("Georgia", 11), exportselection=False)
        self.list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.list.bind("<<ListboxSelect>>", self._on_select)

        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        self.status = ttk.Label(right, text="Select a copy to edit.")
        self.status.pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text="Name").pack(anchor="w")
        self.name = ttk.Entry(right, font=("Georgia", 11))
        self.name.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(right, text="Description").pack(anchor="w")
        self.description = tk.Text(right, wrap=tk.WORD, font=("Georgia", 11), height=10)
        self.description.pack(fill=tk.BOTH, expand=True, pady=(2, 8))
        ttk.Label(right, text="Location").pack(anchor="w")
        self.location = ttk.Label(right, text="—", wraplength=360)
        self.location.pack(anchor="w", pady=(2, 10))

        attach_row = ttk.Frame(right)
        attach_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(attach_row, text="Archetype").pack(side=tk.LEFT)
        self.attach_var = tk.StringVar()
        self.attach_combo = ttk.Combobox(
            attach_row,
            textvariable=self.attach_var,
            values=[item["name"] for item in archetypes],
            state="readonly",
            width=28,
        )
        self.attach_combo.pack(side=tk.LEFT, padx=6)

        buttons = ttk.Frame(right)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Save copy", command=self.save_copy).pack(side=tk.LEFT)
        ttk.Button(
            buttons,
            text="Detach (make unique)",
            command=self.detach_copy,
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            buttons,
            text="Attach to archetype",
            command=self.attach_copy,
        ).pack(side=tk.LEFT)

        create_row = ttk.Frame(right)
        create_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(
            create_row,
            text="Some copies are born unique — no archetype required:",
        ).pack(anchor="w")
        create_buttons = ttk.Frame(create_row)
        create_buttons.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(
            create_buttons,
            text="New unique item…",
            command=self.create_unique_item,
        ).pack(side=tk.LEFT)
        ttk.Button(
            create_buttons,
            text="New unique container…",
            command=self.create_unique_container,
        ).pack(side=tk.LEFT, padx=6)

        keep = self._initial_instance_id
        self.reload_list(keep_id=keep)

    def _filter_archetype_id(self):
        label = self.filter_var.get()
        if label == "(all owned copies)":
            return None, False
        if label == "(uniques only)":
            return None, True
        return self._archetypes_by_name.get(label), False

    def reload_list(self, keep_id=None):
        archetype_id, uniques_only = self._filter_archetype_id()
        if uniques_only:
            self._instances = [
                item for item in list_item_instances(self.db) if item["is_unique"]
            ]
        else:
            self._instances = list_item_instances(self.db, archetype_id=archetype_id)
        self.list.delete(0, tk.END)
        selected = None
        for index, item in enumerate(self._instances):
            marker = "unique" if item["is_unique"] else "linked"
            kind = "container" if item.get("is_container") else "item"
            where = item["locations"][0] if item["locations"] else "unplaced"
            self.list.insert(
                tk.END,
                f"{item['name']}  [{kind}/{marker}]  — {where}",
            )
            if item["id"] == keep_id:
                selected = index
        if selected is not None:
            self.list.selection_set(selected)
            self.list.see(selected)
            self._show(selected)
        elif self._instances:
            self.list.selection_set(0)
            self._show(0)
        else:
            self._selected_id = None
            self.status.config(text="No owned copies match this filter.")
            self.name.delete(0, tk.END)
            self.description.delete("1.0", tk.END)
            self.location.config(text="—")

    def _on_select(self, _event=None):
        selected = self.list.curselection()
        if selected:
            self._show(selected[0])

    def _show(self, index):
        item = self._instances[index]
        self._selected_id = item["id"]
        kind = "Container" if item.get("is_container") else "Item"
        if item["is_unique"]:
            self.status.config(
                text=f"{kind} unique — no archetype (reattach anytime)."
            )
        else:
            arch = self._archetypes_by_id.get(item["archetype_id"], {})
            self.status.config(
                text=(
                    f"{kind} linked to archetype: "
                    f"{arch.get('name', item['archetype_id'])}"
                )
            )
        self.name.delete(0, tk.END)
        self.name.insert(0, item["name"])
        self.description.delete("1.0", tk.END)
        self.description.insert("1.0", item["description"] or "")
        self.location.config(
            text="; ".join(item["locations"]) if item["locations"] else "—"
        )
        if item["archetype_id"] and item["archetype_id"] in self._archetypes_by_id:
            self.attach_var.set(self._archetypes_by_id[item["archetype_id"]]["name"])
        elif self.attach_combo["values"]:
            self.attach_var.set(self.attach_combo["values"][0])

    def save_copy(self):
        if self._selected_id is None:
            return
        name = self.name.get().strip()
        description = self.description.get("1.0", "end").strip()
        if not name:
            messagebox.showerror(
                "Owned copy",
                "Name cannot be empty.",
                parent=self,
            )
            return
        try:
            update_item_instance(
                self.db,
                self._selected_id,
                name=name,
                description=description,
            )
        except ValueError as exc:
            messagebox.showerror("Owned copy", str(exc), parent=self)
            return
        self.reload_list(keep_id=self._selected_id)

    def detach_copy(self):
        if self._selected_id is None:
            return
        item = next(
            (row for row in self._instances if row["id"] == self._selected_id),
            None,
        )
        if item is None:
            return
        if item["is_unique"]:
            messagebox.showinfo(
                "Detach",
                "This copy is already unique.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Detach",
            f"Detach {item['name']} from its archetype?\n\n"
            "Resolved text stays on this copy; archetype edits will no longer apply.",
            parent=self,
        ):
            return
        detach_item_instance(self.db, self._selected_id)
        self.reload_list(keep_id=self._selected_id)

    def attach_copy(self):
        if self._selected_id is None:
            return
        archetype_name = self.attach_var.get()
        archetype_id = self._archetypes_by_name.get(archetype_name)
        if archetype_id is None:
            messagebox.showerror(
                "Attach",
                "Choose an archetype to attach to.",
                parent=self,
            )
            return
        item = next(
            (row for row in self._instances if row["id"] == self._selected_id),
            None,
        )
        label = item["name"] if item else "this copy"
        if not messagebox.askyesno(
            "Attach",
            f"Attach {label} to archetype {archetype_name}?\n\n"
            "Matching values follow the archetype again; only differences stay as overrides.",
            parent=self,
        ):
            return
        try:
            attach_item_instance(self.db, self._selected_id, archetype_id)
        except ValueError as exc:
            messagebox.showerror("Attach", str(exc), parent=self)
            return
        self.reload_list(keep_id=self._selected_id)

    def create_unique_item(self):
        from tkinter import simpledialog

        name = simpledialog.askstring(
            "New unique item",
            "Name for a unique owned item (no archetype):",
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("New unique item", "Name cannot be empty.", parent=self)
            return
        instance_id = create_unique_item_instance(self.db, name)
        self.filter_var.set("(uniques only)")
        self.reload_list(keep_id=instance_id)
        messagebox.showinfo(
            "New unique item",
            f"Created {name}. Place it into an inventory from the character desk "
            "(or leave it listed here until you nest it).",
            parent=self,
        )

    def create_unique_container(self):
        from tkinter import simpledialog

        name = simpledialog.askstring(
            "New unique container",
            "Name for a unique container (no archetype), e.g. a vault:",
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror(
                "New unique container",
                "Name cannot be empty.",
                parent=self,
            )
            return
        instance_id = create_unique_container_instance(self.db, name)
        self.filter_var.set("(uniques only)")
        self.reload_list(keep_id=instance_id)
        messagebox.showinfo(
            "New unique container",
            f"Created {name}. Add it under a character inventory with "
            "Add container / drag, or nest it after placing from inventory save.",
            parent=self,
        )

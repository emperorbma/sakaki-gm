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
from sakaki_gm.item_property_sets import (
    armor_from_properties,
    assemble_properties,
    container_from_properties,
    format_strength,
    format_weapon_properties,
    kind_enabled,
    money_from_properties,
    other_properties,
    parse_strength_text,
    parse_weapon_properties_text,
    properties_from_json,
    properties_to_json,
    weapon_from_properties,
)
from sakaki_gm.rules_5e_equipment import ensure_5e_equipment_archetypes


class ItemsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._items = []
        self._loading = False
        self._selected_id = None
        self._kind_tabs = {}

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
        ttk.Button(
            header,
            text="Add missing 5e equipment",
            command=self.add_missing_5e_equipment,
        ).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Label(
            self.frame,
            text=(
                "Reusable definitions. Owned copies in inventories and on the map "
                "are instances that take these defaults unless a copy overrides them. "
                "Check a kind to attach its property set; Container also spawns a "
                "nestable inventory instance when added to a loadout."
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
        self.name.pack(fill=tk.X, pady=(2, 6))
        ttk.Label(editor, text="Description").pack(anchor="w")
        self.description = tk.Text(editor, wrap=tk.WORD, font=("Georgia", 11), height=5)
        self.description.pack(fill=tk.BOTH, expand=False, pady=(2, 6))

        kinds = ttk.Frame(editor)
        kinds.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(kinds, text="Kinds").pack(side=tk.LEFT, padx=(0, 8))
        self.is_weapon = tk.BooleanVar(value=False)
        self.is_armor = tk.BooleanVar(value=False)
        self.is_container = tk.BooleanVar(value=False)
        self.is_money = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            kinds, text="Weapon", variable=self.is_weapon, command=self._sync_kind_tabs
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(
            kinds, text="Armor", variable=self.is_armor, command=self._sync_kind_tabs
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(
            kinds,
            text="Container",
            variable=self.is_container,
            command=self._sync_kind_tabs,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(
            kinds,
            text="Money / Currency",
            variable=self.is_money,
            command=self._sync_kind_tabs,
        ).pack(side=tk.LEFT)

        self.kind_notebook = ttk.Notebook(editor)
        self.kind_notebook.pack(fill=tk.BOTH, expand=True, pady=(2, 8))
        self._build_kind_tabs()
        self._sync_kind_tabs()

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

    def _build_kind_tabs(self):
        weapon = ttk.Frame(self.kind_notebook, padding=8)
        self.weapon_category = ttk.Entry(weapon, font=("Georgia", 11))
        self.weapon_damage = ttk.Entry(weapon, font=("Georgia", 11))
        self.weapon_range = ttk.Entry(weapon, font=("Georgia", 11))
        self.weapon_properties = ttk.Entry(weapon, font=("Georgia", 11))
        for label, widget in (
            ("Category", self.weapon_category),
            ("Damage", self.weapon_damage),
            ("Range", self.weapon_range),
            ("Properties (comma-separated)", self.weapon_properties),
        ):
            ttk.Label(weapon, text=label).pack(anchor="w")
            widget.pack(fill=tk.X, pady=(2, 8))
        self._kind_tabs["weapon"] = weapon

        armor = ttk.Frame(self.kind_notebook, padding=8)
        self.armor_category = ttk.Entry(armor, font=("Georgia", 11))
        self.armor_ac = ttk.Entry(armor, font=("Georgia", 11))
        self.armor_strength = ttk.Entry(armor, font=("Georgia", 11))
        self.armor_stealth = tk.BooleanVar(value=False)
        for label, widget in (
            ("Category", self.armor_category),
            ("AC", self.armor_ac),
            ("Strength requirement (blank = none)", self.armor_strength),
        ):
            ttk.Label(armor, text=label).pack(anchor="w")
            widget.pack(fill=tk.X, pady=(2, 8))
        ttk.Checkbutton(
            armor,
            text="Stealth disadvantage",
            variable=self.armor_stealth,
        ).pack(anchor="w", pady=(0, 8))
        self._kind_tabs["armor"] = armor

        container = ttk.Frame(self.kind_notebook, padding=8)
        self.container_capacity = ttk.Entry(container, font=("Georgia", 11))
        self.container_role = ttk.Entry(container, font=("Georgia", 11))
        ttk.Label(container, text="Capacity").pack(anchor="w")
        self.container_capacity.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(container, text="Role").pack(anchor="w")
        self.container_role.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(
            container,
            text="Nestable inventory container when added to a character loadout.",
            wraplength=420,
        ).pack(anchor="w")
        self._kind_tabs["container"] = container

        money = ttk.Frame(self.kind_notebook, padding=8)
        self.money_value = ttk.Entry(money, font=("Georgia", 11))
        ttk.Label(money, text="Value").pack(anchor="w")
        self.money_value.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(
            money,
            text='Examples: "1 cp", "1 unit", "100 units".',
            wraplength=420,
        ).pack(anchor="w")
        self._kind_tabs["money"] = money

        other = ttk.Frame(self.kind_notebook, padding=8)
        ttk.Label(
            other,
            text=(
                "Other tags and blocks as JSON (food, vessel, focus, reagent, …). "
                "Managed kind tags/blocks are edited above and stripped from this pane."
            ),
            wraplength=420,
        ).pack(anchor="w", pady=(0, 4))
        self.other_properties = tk.Text(
            other, wrap=tk.NONE, font=("Consolas", 10), height=8
        )
        self.other_properties.pack(fill=tk.BOTH, expand=True)
        self._kind_tabs["other"] = other
        self.kind_notebook.add(other, text="Other")

    def _sync_kind_tabs(self):
        enabled = {
            "weapon": self.is_weapon.get(),
            "armor": self.is_armor.get(),
            "container": self.is_container.get(),
            "money": self.is_money.get(),
        }
        titles = {
            "weapon": "Weapon",
            "armor": "Armor",
            "container": "Container",
            "money": "Money",
        }
        # Preserve Other; rebuild managed tabs in a stable order.
        for kind in ("weapon", "armor", "container", "money"):
            tab = self._kind_tabs[kind]
            try:
                self.kind_notebook.forget(tab)
            except tk.TclError:
                pass
        for kind in ("weapon", "armor", "container", "money"):
            if enabled[kind]:
                # Insert before Other when possible.
                other = self._kind_tabs["other"]
                try:
                    other_index = self.kind_notebook.index(other)
                except tk.TclError:
                    other_index = "end"
                self.kind_notebook.insert(
                    other_index, self._kind_tabs[kind], text=titles[kind]
                )
        # Prefer the first managed tab when one was just enabled.
        for kind in ("weapon", "armor", "container", "money"):
            if enabled[kind]:
                self.kind_notebook.select(self._kind_tabs[kind])
                break

    def set_db(self, db):
        self.db = db
        self._items = []
        self._selected_id = None
        self.reload()

    def add_missing_5e_equipment(self):
        if not messagebox.askyesno(
            "5e equipment",
            "Add any missing generic D&D 5e equipment archetypes?\n\n"
            "Existing archetypes with the same name are left unchanged.",
        ):
            return
        result = ensure_5e_equipment_archetypes(self.db, commit=True)
        self.reload()
        detail = (
            f"Added {result['added']} archetype(s); "
            f"{result['skipped']} already present."
        )
        if result.get("repaired"):
            detail += f"\nRepaired {result['repaired']} stock Waterskin(s) to vessel."
        messagebox.showinfo("5e equipment", detail)

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
        self._load_properties({})
        self._loading = False
        self.name.focus_set()

    def _load_properties(self, properties: dict | None):
        props = properties or {}
        self.is_weapon.set(kind_enabled(props, "weapon"))
        self.is_armor.set(kind_enabled(props, "armor"))
        self.is_container.set(kind_enabled(props, "container"))
        self.is_money.set(kind_enabled(props, "money"))

        weapon = weapon_from_properties(props)
        self.weapon_category.delete(0, tk.END)
        self.weapon_category.insert(0, weapon["category"])
        self.weapon_damage.delete(0, tk.END)
        self.weapon_damage.insert(0, weapon["damage"])
        self.weapon_range.delete(0, tk.END)
        self.weapon_range.insert(0, weapon["range"])
        self.weapon_properties.delete(0, tk.END)
        self.weapon_properties.insert(
            0, format_weapon_properties(weapon["properties"])
        )

        armor = armor_from_properties(props)
        self.armor_category.delete(0, tk.END)
        self.armor_category.insert(0, armor["category"])
        self.armor_ac.delete(0, tk.END)
        self.armor_ac.insert(0, armor["ac"])
        self.armor_strength.delete(0, tk.END)
        self.armor_strength.insert(0, format_strength(armor["strength"]))
        self.armor_stealth.set(bool(armor["stealth_disadvantage"]))

        container = container_from_properties(props)
        self.container_capacity.delete(0, tk.END)
        self.container_capacity.insert(0, container["capacity"])
        self.container_role.delete(0, tk.END)
        self.container_role.insert(0, container["role"])

        money = money_from_properties(props)
        self.money_value.delete(0, tk.END)
        self.money_value.insert(0, money["value"])

        self.other_properties.delete("1.0", tk.END)
        self.other_properties.insert(
            "1.0", properties_to_json(other_properties(props))
        )
        self._sync_kind_tabs()

    def _collect_properties(self) -> dict:
        other = properties_from_json(self.other_properties.get("1.0", "end"))
        return assemble_properties(
            is_weapon=self.is_weapon.get(),
            is_armor=self.is_armor.get(),
            is_container=self.is_container.get(),
            is_money=self.is_money.get(),
            weapon={
                "category": self.weapon_category.get().strip(),
                "damage": self.weapon_damage.get().strip(),
                "range": self.weapon_range.get().strip() or "melee",
                "properties": parse_weapon_properties_text(
                    self.weapon_properties.get()
                ),
            },
            armor={
                "category": self.armor_category.get().strip(),
                "ac": self.armor_ac.get().strip(),
                "stealth_disadvantage": self.armor_stealth.get(),
                "strength": parse_strength_text(self.armor_strength.get()),
            },
            container={
                "capacity": self.container_capacity.get().strip(),
                "role": self.container_role.get().strip(),
            },
            money={
                "value": self.money_value.get().strip(),
            },
            other=other,
        )

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
        try:
            properties = self._collect_properties()
        except ValueError as exc:
            messagebox.showerror("Archetype", str(exc), parent=self.frame)
            return
        item_id = self._selected_id
        try:
            if item_id is None:
                item_id = create_item(
                    self.db,
                    name,
                    description,
                    self.is_container.get(),
                    properties=properties,
                )
            else:
                update_item(
                    self.db,
                    item_id,
                    name,
                    description,
                    self.is_container.get(),
                    properties=properties,
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
        self._load_properties(item.get("properties"))


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
        self.name.pack(fill=tk.X, pady=(2, 6))
        ttk.Label(right, text="Description").pack(anchor="w")
        self.description = tk.Text(right, wrap=tk.WORD, font=("Georgia", 11), height=5)
        self.description.pack(fill=tk.BOTH, expand=True, pady=(2, 6))
        ttk.Label(right, text="Resolved property sets").pack(anchor="w")
        ttk.Label(
            right,
            text=(
                "Practical values after archetype defaults + overrides. "
                "Edits on linked copies store only what differs from the archetype."
            ),
            wraplength=360,
        ).pack(anchor="w", pady=(0, 2))
        self.properties = tk.Text(right, wrap=tk.NONE, font=("Consolas", 10), height=7)
        self.properties.pack(fill=tk.BOTH, expand=True, pady=(2, 6))
        ttk.Label(right, text="Location").pack(anchor="w")
        self.location = ttk.Label(right, text="—", wraplength=360)
        self.location.pack(anchor="w", pady=(2, 8))

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
            self.properties.delete("1.0", tk.END)
            self.properties.insert("1.0", "{}")
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
        self.properties.delete("1.0", tk.END)
        self.properties.insert("1.0", properties_to_json(item.get("properties")))
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
            properties = properties_from_json(
                self.properties.get("1.0", "end")
            )
        except ValueError as exc:
            messagebox.showerror("Owned copy", str(exc), parent=self)
            return
        try:
            update_item_instance(
                self.db,
                self._selected_id,
                name=name,
                description=description,
                properties=properties,
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

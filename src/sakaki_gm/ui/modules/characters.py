# ui/modules/characters.py
import json
import random
import sqlite3
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from sakaki_gm.character_db import create_character, delete_character, get_all_characters, update_character
from sakaki_gm.inventory_generator import (
    choose_kit_for_character,
    generate_character_inventory,
    generate_inventories_for_campaign,
)
from sakaki_gm.item_db import (
    get_character_inventory_tree,
    get_items,
    is_customizable_owned_id,
    resolve_item,
    save_character_inventory,
)
from sakaki_gm.skill_db import get_character_skills, get_skills, set_character_skills
from sakaki_gm.spell_db import (
    get_character_spells,
    get_spells,
    set_character_spells,
    spell_casting_mode,
    spell_notes_blurb,
    spell_uses_prepared,
)
from sakaki_gm.ui.modules.items import InstanceCustomizeDialog

# Free-text sheet tabs. Spells are a structured loadout + blurb (see Spells tab).
TEXT_FIELDS = (
    ("features", "Features"),
    ("saving_throws", "Saves"),
    ("actions", "Actions"),
    ("proficiencies", "Proficiencies"),
    ("notes", "Notes"),
)


class CharactersModule:
    def __init__(self, notebook, db, main_app):
        self.db = db
        self.main_app = main_app
        self.frame = ttk.Frame(notebook)

        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=10, pady=10)
        self.tree = ttk.Treeview(tree_frame, columns=("Summary",), show="headings", height=25)
        self.tree.heading("Summary", text="Character Sheet")
        self.tree.column("Summary", width=420)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.open_editor)

        btn_frame = ttk.Frame(tree_frame)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text="+ New Character", command=self.new_character).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Import from .md", command=self.open_importer).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Import Files", command=self.import_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Delete", command=self.delete_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            btn_frame,
            text="Fill starter kits",
            command=self.fill_starter_kits,
        ).pack(side=tk.LEFT, padx=5)

        self.refresh()

    def set_db(self, db):
        self.db = db
        self.refresh()

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for char in get_all_characters(self.db):
            if "error" in char:
                summary = "(error loading data)"
            else:
                hp = char.get("hp_current", char.get("hp", 0))
                max_hp = char.get("hp_max", char.get("max_hp", hp))
                role = f" — {char['campaign_role']}" if char.get("campaign_role") else ""
                summary = f"{char['name']} — {char.get('class') or '?'} — HP: {hp}/{max_hp}{role}"
            self.tree.insert("", "end", iid=str(char["id"]), text=char["name"], values=(summary,))

    def fill_starter_kits(self):
        if not messagebox.askyesno(
            "Fill starter kits",
            "Add missing class kit gear for every character?\n\n"
            "Spirits/cameos get nothing. Equivalents already owned are skipped "
            "(skirt covers clothes, existing focus covers Arcane Focus, etc.).",
            parent=self.frame,
        ):
            return
        try:
            summary = generate_inventories_for_campaign(self.db, commit=True)
        except (sqlite3.Error, ValueError) as exc:
            messagebox.showerror("Fill starter kits", str(exc), parent=self.frame)
            return
        lines = [
            f"Added {summary['added']} item(s) across {summary['characters']} character(s); "
            f"skipped {summary['skipped']} covered entries."
        ]
        for result in summary["results"]:
            if not result["added"]:
                continue
            names = ", ".join(item["name"] for item in result["added"])
            lines.append(f"• {result['character']} ({result['kit']}): {names}")
        messagebox.showinfo("Fill starter kits", "\n".join(lines), parent=self.frame)
        self.refresh()

    def new_character(self):
        self.open_editor(None)

    def open_editor(self, event=None):
        existing_id = self._char_id_from(event)
        row = None
        if existing_id is not None:
            row = self.db.execute("SELECT * FROM characters WHERE id=?", (existing_id,)).fetchone()
            if row is None:
                existing_id = None

        stats_row = self.db.execute(
            "SELECT value FROM campaign_settings WHERE key='ability_scores'"
        ).fetchone()
        ability_scores = json.loads(stats_row["value"]) if stats_row else ["str", "dex", "con", "int", "wis", "cha"]

        prof_row = self.db.execute(
            "SELECT value FROM campaign_settings WHERE key='proficiency_bonus'"
        ).fetchone()
        default_prof = int(prof_row["value"]) if prof_row else 2

        saved = json.loads(row["data"]) if row else {}
        stat_block_scaffold = _is_stat_block_scaffold(saved)
        defaults = {
            "name": "New Character", "class": "", "level": 1, "race": "",
            "background": "", "alignment": "", "experience": 0,
            "hp_current": 10, "hp_max": 10, "ac": 10, "ac_note": "",
            "speed": 30, "initiative": 0, "proficiency_bonus": default_prof,
            "campaign_role": row["campaign_role"] if row else "", "notes": "",
        }
        for stat in ability_scores:
            defaults.setdefault(stat, 10)
        data = {**defaults, **saved}
        if "hp_current" not in saved and "hp" in saved:
            data["hp_current"] = saved["hp"]
        if "hp_max" not in saved and "max_hp" in saved:
            data["hp_max"] = saved["max_hp"]

        editor = tk.Toplevel(self.main_app.root)
        editor.title(f"Character Editor — {data.get('name', 'New')}")
        editor.geometry("1100x760")
        editor.minsize(900, 640)

        ability_vars = {}
        mod_labels = {}
        fields = {}
        texts = {}

        toolbar = ttk.Frame(editor)
        toolbar.pack(fill=tk.X, pady=5, padx=10)
        ttk.Button(
            toolbar, text="Generate ability scores",
            command=lambda: self._roll_scores(ability_scores, ability_vars, mod_labels),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(toolbar, text=f"Using: {', '.join(ability_scores).upper()}").pack(side=tk.RIGHT)

        save_bar = ttk.Frame(editor)
        save_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=8)

        notebook = ttk.Notebook(editor)
        notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        core = ttk.Frame(notebook)
        notebook.add(core, text="Core")
        left = ttk.LabelFrame(core, text="Ability Scores")
        left.grid(row=0, column=0, padx=15, pady=15, sticky="n")
        for stat in ability_scores:
            row_frame = ttk.Frame(left)
            row_frame.pack(fill=tk.X, pady=4)
            ttk.Label(row_frame, text=stat.upper(), width=6).pack(side=tk.LEFT)
            if stat_block_scaffold:
                var = tk.StringVar(value="" if saved.get(stat) is None else str(saved[stat]))
                widget = ttk.Entry(row_frame, textvariable=var, width=5, justify="center")
            else:
                var = tk.IntVar(value=_as_int(data.get(stat), 10))
                widget = ttk.Entry(row_frame, textvariable=var, width=5, justify="center")
            ability_vars[stat] = var
            widget.pack(side=tk.LEFT, padx=8)
            mod_label = ttk.Label(row_frame, text=_mod_text(var.get()), width=6)
            mod_label.pack(side=tk.LEFT, padx=8)
            mod_labels[stat] = mod_label
            _trace_mod(var, mod_label)

        right = ttk.LabelFrame(core, text="Combat & Identity")
        right.grid(row=0, column=1, padx=15, pady=15, sticky="n")
        spec = (
            ("Name", "name", "str"),
            ("Campaign role", "campaign_role", "str"),
            ("Class", "class", "str"),
            ("Level", "level", "int"),
            ("Experience", "experience", "int"),
            ("Race", "race", "str"),
            ("Background", "background", "str"),
            ("Alignment", "alignment", "str"),
            ("HP", "hp_current", "int"),
            ("Max HP", "hp_max", "int"),
            ("AC", "ac", "int"),
            ("AC note", "ac_note", "str"),
            ("Speed", "speed", "int"),
            ("Initiative", "initiative", "int"),
            ("Proficiency", "proficiency_bonus", "int"),
        )
        for index, (label, key, kind) in enumerate(spec):
            ttk.Label(right, text=label + ":").grid(row=index, column=0, sticky="w", pady=3, padx=5)
            if kind == "str":
                var = tk.StringVar(value="" if data.get(key) is None else str(data.get(key)))
                widget = ttk.Entry(right, textvariable=var, width=32)
            elif stat_block_scaffold:
                var = tk.StringVar(value="" if saved.get(key) is None else str(saved[key]))
                widget = ttk.Entry(right, textvariable=var, width=8)
            else:
                var = tk.IntVar(value=_as_int(data.get(key), 0))
                widget = ttk.Spinbox(right, from_=-20, to=9999, textvariable=var, width=8)
            fields[key] = var
            widget.grid(row=index, column=1, pady=3, padx=5, sticky="w")

        catalog_items = get_items(self.db)
        item_ids = {item["name"]: item["id"] for item in catalog_items}
        items_by_id = {item["id"]: item for item in catalog_items}
        catalog_tab = ttk.Frame(notebook)
        notebook.add(catalog_tab, text="Inventory")
        ttk.Label(
            catalog_tab,
            text=(
                "Owned copies are instances (including inside containers). "
                "Drag an archetype to spawn a new copy. Quantity and wear state "
                "edit here; use Customize copy for name, description, detach, or reattach."
            ),
            wraplength=760,
        ).pack(anchor="w", padx=10, pady=(10, 4))
        inventory_tree = ttk.Treeview(
            catalog_tab,
            columns=("quantity", "attuned", "location"),
            show="tree headings",
            height=12,
            selectmode="browse",
        )
        inventory_body = ttk.Frame(catalog_tab)
        inventory_body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        catalog_source = ttk.LabelFrame(
            inventory_body,
            text="Drag archetypes into inventory",
        )
        catalog_source.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        catalog_list = tk.Listbox(
            catalog_source,
            width=28,
            exportselection=False,
        )
        catalog_list.pack(fill=tk.Y, expand=True, padx=4, pady=4)
        catalog_list_ids = []
        for catalog_item in catalog_items:
            label = catalog_item["name"]
            if catalog_item["is_container"]:
                label += " [container]"
            catalog_list.insert(tk.END, label)
            catalog_list_ids.append(catalog_item["id"])
        inventory_view = ttk.Frame(inventory_body)
        inventory_view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inventory_tree.heading("#0", text="Item")
        inventory_tree.heading("quantity", text="Quantity")
        inventory_tree.heading("attuned", text="Attuned")
        inventory_tree.heading("location", text="Equipment location")
        inventory_tree.column("#0", width=440)
        inventory_tree.column("quantity", width=100, anchor="center")
        inventory_tree.column("attuned", width=100, anchor="center")
        inventory_tree.column("location", width=180)
        inventory_tree.pack(fill=tk.BOTH, expand=True)
        inventory_tree.tag_configure("container", font=("TkDefaultFont", 9, "bold"))
        inventory_tree.tag_configure("drop-target", background="#dceeff")
        inventory_node_data = {}
        root_id = "inventory-root"
        inventory_root = (
            get_character_inventory_tree(self.db, existing_id)
            if existing_id is not None else None
        )
        root_name = inventory_root["name"] if inventory_root else "Inventory"
        inventory_tree.insert(
            "",
            tk.END,
            iid=root_id,
            text=root_name,
            values=("", "", ""),
            tags=("container",),
            open=True,
        )
        inventory_node_data[root_id] = {
            "object_id": (
                inventory_root["object_id"] if inventory_root else None
            ),
            "object_type": "container",
            "root": True,
        }

        def render_inventory(parent_id, children):
            for child in children:
                object_id = child["object_id"]
                link_properties = child["properties"]
                is_container = child["object_type"] == "container"
                inventory_tree.insert(
                    parent_id,
                    tk.END,
                    iid=object_id,
                    text=child["name"],
                    values=(
                        link_properties.get("quantity", 1),
                        "Yes" if link_properties.get("attuned") else "No",
                        link_properties.get("equipment_location", ""),
                    ),
                    tags=("container",) if is_container else (),
                    open=is_container,
                )
                inventory_node_data[object_id] = {
                    **child,
                    "properties": link_properties,
                }
                if is_container:
                    render_inventory(object_id, child["children"])

        if inventory_root:
            render_inventory(root_id, inventory_root["children"])

        inventory_controls = ttk.Frame(catalog_tab)
        inventory_controls.pack(fill=tk.X, padx=10, pady=(0, 10))
        field_row = ttk.Frame(inventory_controls)
        field_row.pack(fill=tk.X, pady=(0, 4))
        action_row = ttk.Frame(inventory_controls)
        action_row.pack(fill=tk.X)
        item_name = tk.StringVar()
        item_combo = ttk.Combobox(
            field_row,
            textvariable=item_name,
            values=list(item_ids),
            state="readonly",
            width=34,
        )
        item_combo.pack(side=tk.LEFT, padx=(0, 6))
        quantity = tk.IntVar(value=1)
        ttk.Spinbox(
            field_row,
            from_=1,
            to=9999,
            textvariable=quantity,
            width=7,
        ).pack(side=tk.LEFT)
        equipment_location = tk.StringVar()
        ttk.Label(field_row, text="Location").pack(side=tk.LEFT, padx=(8, 3))
        ttk.Entry(
            field_row,
            textvariable=equipment_location,
            width=16,
        ).pack(side=tk.LEFT)
        attuned = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            field_row,
            text="Attuned",
            variable=attuned,
        ).pack(side=tk.LEFT, padx=8)

        def selected_parent():
            selected = inventory_tree.selection()
            if not selected:
                return root_id
            current = selected[0]
            if inventory_node_data[current]["object_type"] == "container":
                return current
            return inventory_tree.parent(current) or root_id

        def selected_catalog_id():
            item_id = item_ids.get(item_name.get())
            if item_id is None:
                messagebox.showerror(
                    "Inventory",
                    "Choose an archetype from the campaign list first.",
                    parent=editor,
                )
                return None
            try:
                count = quantity.get()
                if count < 1:
                    raise ValueError
            except (tk.TclError, ValueError):
                messagebox.showerror(
                    "Inventory",
                    "Quantity must be a positive whole number.",
                    parent=editor,
                )
                return None
            return item_id, count

        def add_catalog_item():
            selection = selected_catalog_id()
            if selection is None:
                return
            item_id, count = selection
            catalog_item = items_by_id[item_id]
            is_container = catalog_item["is_container"]
            row_id = (
                f"new-container:{uuid.uuid4().hex}"
                if is_container
                else f"new-item:{uuid.uuid4().hex}"
            )
            props = {
                "quantity": count,
                "attuned": bool(attuned.get()),
                "equipment_location": equipment_location.get().strip(),
            }
            node_data = {
                "object_id": row_id,
                "object_type": "container" if is_container else "item",
                "name": catalog_item["name"],
                "description": catalog_item["description"],
                "properties": props,
                "object_properties": {"catalog_item_id": item_id},
                "children": [],
            }
            if is_container:
                node_data["new_container"] = True
            else:
                node_data["new_item"] = True
            parent_id = selected_parent()
            inventory_tree.insert(
                parent_id,
                tk.END,
                iid=row_id,
                text=item_name.get() + (" (container)" if is_container else ""),
                values=(
                    count,
                    "Yes" if attuned.get() else "No",
                    equipment_location.get().strip(),
                ),
                tags=("container",) if is_container else (),
                open=is_container,
            )
            inventory_node_data[row_id] = node_data

        def update_catalog_item():
            selected = inventory_tree.selection()
            if not selected:
                messagebox.showerror(
                    "Inventory",
                    "Select an inventory item to update.",
                    parent=editor,
                )
                return
            selection = selected_catalog_id()
            if selection is None:
                return
            item_id, count = selection
            old_row_id = selected[0]
            old_type = inventory_node_data[old_row_id]["object_type"]
            if old_type not in ("item", "item_instance"):
                messagebox.showerror(
                    "Inventory",
                    "Containers are separate objects. Remove this one and add the desired container instead.",
                    parent=editor,
                )
                return
            if items_by_id[item_id]["is_container"]:
                messagebox.showerror(
                    "Inventory",
                    "A container archetype creates a new container instance. Add it separately.",
                    parent=editor,
                )
                return
            parent_id = inventory_tree.parent(old_row_id)
            inventory_tree.delete(old_row_id)
            inventory_node_data.pop(old_row_id, None)
            row_id = f"new-item:{uuid.uuid4().hex}"
            inventory_tree.insert(
                parent_id,
                tk.END,
                iid=row_id,
                text=item_name.get(),
                values=(
                    count,
                    "Yes" if attuned.get() else "No",
                    equipment_location.get().strip(),
                ),
            )
            inventory_node_data[row_id] = {
                "object_id": row_id,
                "object_type": "item",
                "new_item": True,
                "name": items_by_id[item_id]["name"],
                "description": items_by_id[item_id]["description"],
                "object_properties": {"catalog_item_id": item_id},
                "properties": {
                    "quantity": count,
                    "attuned": bool(attuned.get()),
                    "equipment_location": equipment_location.get().strip(),
                },
                "children": [],
            }

        def load_inventory_selection(_event=None):
            selected = inventory_tree.selection()
            if not selected:
                return
            node = inventory_node_data[selected[0]]
            catalog_item_id = node.get("object_properties", {}).get(
                "catalog_item_id"
            )
            catalog_item = items_by_id.get(catalog_item_id) if catalog_item_id else None
            if catalog_item is None:
                return
            values = inventory_tree.item(selected[0], "values")
            item_name.set(catalog_item["name"])
            quantity.set(int(values[0] or 1))
            attuned.set(values[1] == "Yes")
            equipment_location.set(values[2])

        def add_container():
            name = simpledialog.askstring(
                "Add container",
                "Container name:",
                parent=editor,
            )
            if name is None:
                return
            name = name.strip()
            if not name:
                messagebox.showerror(
                    "Inventory",
                    "Container name cannot be empty.",
                    parent=editor,
                )
                return
            row_id = f"new-container:{uuid.uuid4().hex}"
            inventory_tree.insert(
                selected_parent(),
                tk.END,
                iid=row_id,
                text=name,
                values=(1, "No", ""),
                tags=("container",),
                open=True,
            )
            inventory_node_data[row_id] = {
                "object_id": row_id,
                "object_type": "container",
                "new_container": True,
                "description": "",
                "object_properties": {},
                "properties": {
                    "quantity": 1,
                    "attuned": False,
                    "equipment_location": "",
                },
                "children": [],
            }

        def remove_selected():
            for selected_id in inventory_tree.selection():
                if selected_id == root_id:
                    continue
                descendants = [selected_id, *inventory_tree.get_children(selected_id)]
                while descendants:
                    current = descendants.pop()
                    descendants.extend(inventory_tree.get_children(current))
                    inventory_node_data.pop(current, None)
                inventory_tree.delete(selected_id)

        remove_drop_target = ttk.Label(
            action_row,
            text="Drop here to remove",
            relief=tk.GROOVE,
            padding=(8, 4),
        )
        remove_drop_target.pack(side=tk.RIGHT, padx=(6, 0))

        drag = {"source": None, "target": None, "highlight": None}
        drag_ghost = ttk.Label(editor, relief=tk.SOLID, padding=(6, 3))
        drag_ghost.place_forget()
        drop_indicator = tk.Frame(editor, height=2, background="#2878c8")
        drop_indicator.place_forget()

        def drag_start(event, source):
            if source == "catalog":
                index = catalog_list.nearest(event.y)
                if 0 <= index < len(catalog_list_ids):
                    drag_ghost.config(
                        text=catalog_list.get(index).removesuffix(" [container]")
                    )
                    drag["source"] = (
                        "catalog",
                        catalog_list_ids[index],
                        event.x_root,
                        event.y_root,
                    )
                return
            row_id = inventory_tree.identify_row(event.y)
            if row_id and row_id != root_id:
                drag["source"] = (
                    "inventory",
                    row_id,
                    event.x_root,
                    event.y_root,
                )
                drag_ghost.config(text=inventory_tree.item(row_id, "text"))

        def drop_target_at(x_root, y_root):
            left = inventory_tree.winfo_rootx()
            top = inventory_tree.winfo_rooty()
            if not (
                left <= x_root <= left + inventory_tree.winfo_width()
                and top <= y_root <= top + inventory_tree.winfo_height()
            ):
                return None
            target = inventory_tree.identify_row(y_root - top)
            if not target:
                return root_id, tk.END, inventory_tree.winfo_height(), root_id
            bbox = inventory_tree.bbox(target)
            if not bbox:
                return root_id, tk.END, inventory_tree.winfo_height(), root_id
            row_y, row_height = bbox[1], bbox[3]
            position = max(0, min(1, (y_root - top - row_y) / row_height))
            if target == root_id:
                return root_id, tk.END, row_y + row_height, root_id

            parent_id = inventory_tree.parent(target) or root_id
            siblings = inventory_tree.get_children(parent_id)
            target_index = siblings.index(target)
            is_container = (
                inventory_node_data[target]["object_type"] == "container"
            )
            if is_container and 0.25 <= position <= 0.75:
                return target, tk.END, row_y + row_height, target
            after = position >= (0.75 if is_container else 0.5)
            return (
                parent_id,
                target_index + int(after),
                row_y + row_height if after else row_y,
                None,
            )

        def clear_drop_highlight():
            highlighted = drag["highlight"]
            if highlighted and inventory_tree.exists(highlighted):
                tags = tuple(
                    tag for tag in inventory_tree.item(highlighted, "tags")
                    if tag != "drop-target"
                )
                inventory_tree.item(highlighted, tags=tags)
            drag["highlight"] = None

        def drag_motion(event):
            if drag["source"] is None:
                return
            drag_ghost.place(
                x=event.x_root - editor.winfo_rootx() + 12,
                y=event.y_root - editor.winfo_rooty() + 12,
            )
            drag_ghost.lift()
            target = drop_target_at(event.x_root, event.y_root)
            drag["target"] = target
            if target is None:
                drop_indicator.place_forget()
                clear_drop_highlight()
                return
            if target[3]:
                drop_indicator.place_forget()
                if drag["highlight"] != target[3]:
                    clear_drop_highlight()
                    tags = inventory_tree.item(target[3], "tags")
                    inventory_tree.item(
                        target[3],
                        tags=(*tags, "drop-target"),
                    )
                    drag["highlight"] = target[3]
                return
            clear_drop_highlight()
            tree_left = inventory_tree.winfo_rootx() - editor.winfo_rootx()
            tree_top = inventory_tree.winfo_rooty() - editor.winfo_rooty()
            drop_indicator.place(
                x=tree_left,
                y=tree_top + target[2] - 1,
                width=inventory_tree.winfo_width(),
            )
            drop_indicator.lift()
            drag_ghost.lift()

        def dropped_on_remove_target(x_root, y_root):
            left = remove_drop_target.winfo_rootx()
            top = remove_drop_target.winfo_rooty()
            return (
                left <= x_root <= left + remove_drop_target.winfo_width()
                and top <= y_root <= top + remove_drop_target.winfo_height()
            )

        def drag_end(event, source):
            drag_source = drag["source"]
            drag["source"] = None
            drag_ghost.place_forget()
            drop_indicator.place_forget()
            clear_drop_highlight()
            if drag_source is None:
                return
            if (
                abs(event.x_root - drag_source[2])
                + abs(event.y_root - drag_source[3]) < 8
            ):
                return
            target = drop_target_at(event.x_root, event.y_root)
            if drag_source[0] == "catalog":
                item_id = drag_source[1]
                if target is None:
                    return
                target_parent = target[0]
                catalog_item = items_by_id[item_id]
                item_name.set(catalog_item["name"])
                quantity.set(1)
                attuned.set(False)
                equipment_location.set("")
                inventory_tree.selection_set(target_parent)
                add_catalog_item()
                return

            dragged_id = drag_source[1]
            if dropped_on_remove_target(event.x_root, event.y_root):
                inventory_tree.selection_set(dragged_id)
                remove_selected()
                return
            if target is None:
                return
            target_parent, target_index, _marker_y, _highlight_id = target
            if target_parent == dragged_id:
                return
            ancestor = target_parent
            while ancestor:
                if ancestor == dragged_id:
                    return
                ancestor = inventory_tree.parent(ancestor)
            old_parent = inventory_tree.parent(dragged_id)
            old_siblings = inventory_tree.get_children(old_parent)
            old_index = old_siblings.index(dragged_id)
            if old_parent == target_parent and old_index < target_index:
                target_index -= 1
            inventory_tree.move(dragged_id, target_parent, target_index)
            if (
                inventory_node_data[dragged_id]["object_type"] == "container"
                and target_parent != old_parent
            ):
                inventory_tree.item(target_parent, open=True)

        catalog_list.bind(
            "<ButtonPress-1>",
            lambda event: drag_start(event, "catalog"),
        )
        catalog_list.bind(
            "<ButtonRelease-1>",
            lambda event: drag_end(event, "catalog"),
        )
        catalog_list.bind("<B1-Motion>", drag_motion)
        inventory_tree.bind(
            "<ButtonPress-1>",
            lambda event: drag_start(event, "inventory"),
            add="+",
        )
        inventory_tree.bind(
            "<ButtonRelease-1>",
            lambda event: drag_end(event, "inventory"),
            add="+",
        )
        inventory_tree.bind("<B1-Motion>", drag_motion)
        inventory_tree.bind("<<TreeviewSelect>>", load_inventory_selection)

        def refresh_owned_copy_labels():
            for row_id, node in list(inventory_node_data.items()):
                if node.get("root"):
                    continue
                object_id = node.get("object_id") or row_id
                if not is_customizable_owned_id(object_id, node.get("object_type")):
                    continue
                if not inventory_tree.exists(row_id):
                    continue
                resolved = resolve_item(self.db, object_id)
                if resolved is None:
                    continue
                node["name"] = resolved["name"]
                node["description"] = resolved["description"]
                object_properties = dict(node.get("object_properties") or {})
                if resolved["archetype_id"]:
                    object_properties["catalog_item_id"] = resolved["archetype_id"]
                else:
                    object_properties.pop("catalog_item_id", None)
                node["object_properties"] = object_properties
                label = resolved["name"]
                if resolved["object_type"] == "container":
                    label = f"{label} (container)"
                tags = ("container",) if resolved["object_type"] == "container" else ()
                inventory_tree.item(row_id, text=label, tags=tags)

        def customize_selected_copy():
            selected = inventory_tree.selection()
            if not selected:
                messagebox.showerror(
                    "Customize copy",
                    "Select an owned item or container copy in the inventory tree.",
                    parent=editor,
                )
                return
            row_id = selected[0]
            node = inventory_node_data[row_id]
            if node.get("root"):
                messagebox.showerror(
                    "Customize copy",
                    "Select an owned copy inside the inventory, not the root.",
                    parent=editor,
                )
                return
            object_id = node.get("object_id") or row_id
            if not is_customizable_owned_id(object_id, node.get("object_type")):
                messagebox.showinfo(
                    "Customize copy",
                    "Save the character first so this entry becomes a real "
                    "owned instance (or use New unique item / Add container), "
                    "then customize it.",
                    parent=editor,
                )
                return
            dialog = InstanceCustomizeDialog(
                editor,
                self.db,
                initial_instance_id=object_id,
            )
            editor.wait_window(dialog)
            refresh_owned_copy_labels()

        def add_unique_item():
            name = simpledialog.askstring(
                "New unique item",
                "Name for this unique owned copy (no archetype):",
                parent=editor,
            )
            if name is None:
                return
            name = name.strip()
            if not name:
                messagebox.showerror(
                    "New unique item",
                    "Name cannot be empty.",
                    parent=editor,
                )
                return
            row_id = f"new-item:{uuid.uuid4().hex}"
            props = {
                "quantity": max(1, int(quantity.get() or 1)),
                "attuned": bool(attuned.get()),
                "equipment_location": equipment_location.get().strip(),
            }
            inventory_tree.insert(
                selected_parent(),
                tk.END,
                iid=row_id,
                text=name,
                values=(
                    props["quantity"],
                    "Yes" if props["attuned"] else "No",
                    props["equipment_location"],
                ),
            )
            inventory_node_data[row_id] = {
                "object_id": row_id,
                "object_type": "item",
                "new_item": True,
                "new_unique": True,
                "name": name,
                "description": "",
                "object_properties": {},
                "properties": props,
                "children": [],
            }

        # Fields above; frequent actions here; less common ones under More.
        ttk.Button(
            action_row,
            text="Add",
            command=add_catalog_item,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            action_row,
            text="Update",
            command=update_catalog_item,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            action_row,
            text="Remove",
            command=remove_selected,
        ).pack(side=tk.LEFT, padx=4)
        def reload_inventory_from_db():
            if existing_id is None:
                return
            for child in inventory_tree.get_children(root_id):
                inventory_tree.delete(child)
            for key in list(inventory_node_data):
                if key != root_id:
                    inventory_node_data.pop(key, None)
            fresh = get_character_inventory_tree(self.db, existing_id)
            inventory_node_data[root_id] = {
                "object_id": fresh["object_id"],
                "object_type": "container",
                "root": True,
            }
            inventory_tree.item(root_id, text=fresh["name"])
            render_inventory(root_id, fresh.get("children") or [])

        def fill_starter_kit():
            if existing_id is None:
                messagebox.showinfo(
                    "Starter kit",
                    "Save the character once before filling a starter kit.",
                    parent=editor,
                )
                return
            row = self.db.execute(
                "SELECT data, campaign_role FROM characters WHERE id=?",
                (existing_id,),
            ).fetchone()
            try:
                data = json.loads(row["data"]) if row else {}
            except (TypeError, json.JSONDecodeError):
                data = {}
            kit = choose_kit_for_character(
                data if isinstance(data, dict) else {},
                campaign_role=(row["campaign_role"] if row else "") or "",
            )
            if kit == "none":
                if not messagebox.askyesno(
                    "Starter kit",
                    "This character looks like a spirit/cameo (no mundane kit).\n\n"
                    "Force a basic adventuring kit anyway?",
                    parent=editor,
                ):
                    return
                kit = "basic"
            elif not messagebox.askyesno(
                "Starter kit",
                f"Add missing {kit} kit gear for this character?\n\n"
                "Existing equivalents are skipped (e.g. a skirt covers "
                "Traveler's Clothes; a focus stand-in covers Arcane Focus).",
                parent=editor,
            ):
                return
            try:
                result = generate_character_inventory(
                    self.db, existing_id, kit=kit, commit=True
                )
            except (sqlite3.Error, ValueError) as exc:
                messagebox.showerror("Starter kit", str(exc), parent=editor)
                return
            reload_inventory_from_db()
            added = ", ".join(item["name"] for item in result["added"]) or "nothing new"
            skipped = len(result["skipped"])
            messagebox.showinfo(
                "Starter kit",
                f"Kit: {result['kit']}\nAdded: {added}\nSkipped covered: {skipped}",
                parent=editor,
            )

        more_menu = tk.Menu(editor, tearoff=0)
        more_menu.add_command(label="Add container", command=add_container)
        more_menu.add_command(label="New unique item…", command=add_unique_item)
        more_menu.add_command(
            label="Customize copy…",
            command=customize_selected_copy,
        )
        more_menu.add_separator()
        more_menu.add_command(label="Fill starter kit…", command=fill_starter_kit)
        more_button = ttk.Menubutton(action_row, text="More")
        more_button["menu"] = more_menu
        more_button.pack(side=tk.LEFT, padx=4)

        skills_tab = ttk.Frame(notebook)
        notebook.add(skills_tab, text="Skills")
        ttk.Label(
            skills_tab,
            text="Choose proficiency and expertise. Bonuses use the character's ability scores and proficiency bonus.",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(10, 6))
        for column, title in enumerate(("Skill", "Ability", "Proficient", "Expertise", "Bonus")):
            ttk.Label(skills_tab, text=title, font=("TkDefaultFont", 9, "bold")).grid(
                row=1, column=column, sticky="w", padx=10, pady=3,
            )
        saved_skill_rows = (
            get_character_skills(self.db, existing_id)
            if existing_id is not None else []
        )
        saved_skills = {
            skill["id"]: skill for skill in saved_skill_rows
            if skill["proficient"] or skill["expertise"]
        }
        skill_vars = {}
        skill_bonus_labels = {}

        def refresh_skill_bonus(skill, proficient_var, expertise_var, label):
            ability_var = ability_vars.get(skill["ability"])
            if ability_var is None:
                label.config(text="—")
                return
            try:
                ability_score = ability_var.get()
                proficiency_bonus = fields["proficiency_bonus"].get()
                ability_score = int(ability_score)
                proficiency_bonus = int(proficiency_bonus)
            except (tk.TclError, TypeError, ValueError):
                label.config(text="—")
                return
            modifier = (ability_score - 10) // 2
            bonus = modifier + proficiency_bonus * (
                2 if expertise_var.get() else 1 if proficient_var.get() else 0
            )
            label.config(text=f"{bonus:+d}")

        for index, skill in enumerate(get_skills(self.db), start=2):
            previous = saved_skills.get(skill["id"])
            proficient_var = tk.BooleanVar(
                value=bool(previous["proficient"]) if previous else False,
            )
            expertise_var = tk.BooleanVar(
                value=bool(previous["expertise"]) if previous else False,
            )
            bonus_label = ttk.Label(skills_tab, width=8, anchor="center")
            skill_vars[skill["id"]] = (proficient_var, expertise_var)
            skill_bonus_labels[skill["id"]] = bonus_label
            ttk.Label(skills_tab, text=skill["name"]).grid(
                row=index, column=0, sticky="w", padx=10, pady=2,
            )
            ttk.Label(skills_tab, text=skill["ability"].upper() or "—").grid(
                row=index, column=1, sticky="w", padx=10, pady=2,
            )

            def proficiency_changed(
                proficient_var=proficient_var,
                expertise_var=expertise_var,
                skill=skill,
                bonus_label=bonus_label,
            ):
                if not proficient_var.get():
                    expertise_var.set(False)
                refresh_skill_bonus(skill, proficient_var, expertise_var, bonus_label)

            def expertise_changed(
                proficient_var=proficient_var,
                expertise_var=expertise_var,
                skill=skill,
                bonus_label=bonus_label,
            ):
                if expertise_var.get():
                    proficient_var.set(True)
                refresh_skill_bonus(skill, proficient_var, expertise_var, bonus_label)

            ttk.Checkbutton(
                skills_tab,
                variable=proficient_var,
                command=proficiency_changed,
            ).grid(row=index, column=2, padx=10)
            ttk.Checkbutton(
                skills_tab,
                variable=expertise_var,
                command=expertise_changed,
            ).grid(row=index, column=3, padx=10)
            bonus_label.grid(row=index, column=4, sticky="w", padx=10)
            refresh_skill_bonus(skill, proficient_var, expertise_var, bonus_label)

        def refresh_all_skill_bonuses(*_args):
            for skill in get_skills(self.db):
                vars_for_skill = skill_vars[skill["id"]]
                refresh_skill_bonus(
                    skill,
                    vars_for_skill[0],
                    vars_for_skill[1],
                    skill_bonus_labels[skill["id"]],
                )

        for variable in (*ability_vars.values(), fields["proficiency_bonus"]):
            variable.trace_add("write", refresh_all_skill_bonuses)

        spells_tab = ttk.Frame(notebook)
        notebook.add(spells_tab, text="Spells")
        ttk.Label(
            spells_tab,
            text=(
                "Spellbook loadout links catalog spells. Prepared is a count of "
                "Vancian preparations (0/1 for typical 5e lists; higher when the "
                "same title fills multiple slots). Mana spells ignore prepared "
                "(hybrid campaigns mix both)."
            ),
            wraplength=720,
        ).pack(anchor="w", padx=10, pady=(10, 6))

        spell_catalog_by_id = {int(row["id"]): row for row in get_spells(self.db)}
        saved_spell_rows = (
            get_character_spells(self.db, existing_id)
            if existing_id is not None else []
        )
        # spell_id -> {"prepared": int count}
        spell_loadout = {
            int(row["id"]): {"prepared": max(0, int(row["prepared"] or 0))}
            for row in saved_spell_rows
        }

        spell_list_frame = ttk.Frame(spells_tab)
        spell_list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        spells_tree = ttk.Treeview(
            spell_list_frame,
            columns=("name", "level", "school", "mode", "prepared"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        spells_tree.heading("name", text="Spell")
        spells_tree.heading("level", text="Level")
        spells_tree.heading("school", text="School")
        spells_tree.heading("mode", text="Casting")
        spells_tree.heading("prepared", text="Prepared")
        spells_tree.column("name", width=180, stretch=True)
        spells_tree.column("level", width=70, stretch=False, anchor="center")
        spells_tree.column("school", width=100, stretch=False)
        spells_tree.column("mode", width=80, stretch=False, anchor="center")
        spells_tree.column("prepared", width=80, stretch=False, anchor="center")
        spell_scroll = ttk.Scrollbar(
            spell_list_frame, orient=tk.VERTICAL, command=spells_tree.yview
        )
        spells_tree.configure(yscrollcommand=spell_scroll.set)
        spells_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        spell_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _spell_level_label(level):
            try:
                level = int(level)
            except (TypeError, ValueError):
                return "—"
            return "Cantrip" if level <= 0 else f"Level {level}"

        def refresh_spell_loadout_view():
            spells_tree.delete(*spells_tree.get_children())
            ordered = sorted(
                (
                    spell_catalog_by_id[spell_id]
                    for spell_id in spell_loadout
                    if spell_id in spell_catalog_by_id
                ),
                key=lambda row: (int(row["level"] or 0), str(row["name"]).casefold()),
            )
            for row in ordered:
                spell_id = int(row["id"])
                mode = spell_casting_mode(row)
                prepared = int(spell_loadout[spell_id]["prepared"] or 0)
                prepared_label = str(prepared) if mode == "vancian" else "—"
                school = (row["school"] or "").strip()
                spells_tree.insert(
                    "",
                    tk.END,
                    iid=str(spell_id),
                    values=(
                        row["name"],
                        _spell_level_label(row["level"]),
                        school.title() if school else "—",
                        mode,
                        prepared_label,
                    ),
                )

        def add_spell_to_loadout():
            owned = set(spell_loadout)
            available = [
                row for row in get_spells(self.db)
                if int(row["id"]) not in owned
            ]
            if not available:
                messagebox.showinfo(
                    "Spells",
                    "Every catalog spell is already in this loadout, or the "
                    "campaign catalog is empty.",
                    parent=editor,
                )
                return
            picker = tk.Toplevel(editor)
            picker.title("Add spell from catalog")
            picker.transient(editor)
            picker.grab_set()
            ttk.Label(
                picker,
                text="Choose a campaign spell to add to this character's loadout.",
            ).pack(anchor="w", padx=10, pady=(10, 6))
            listbox = tk.Listbox(picker, height=16, width=48, font=("Georgia", 11))
            listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
            for row in available:
                mode = spell_casting_mode(row)
                listbox.insert(
                    tk.END,
                    f"{row['name']}  ({_spell_level_label(row['level'])}, {mode})",
                )

            def confirm_add():
                selection = listbox.curselection()
                if not selection:
                    return
                chosen = available[selection[0]]
                spell_catalog_by_id[int(chosen["id"])] = chosen
                spell_loadout[int(chosen["id"])] = {"prepared": 0}
                refresh_spell_loadout_view()
                picker.destroy()

            buttons = ttk.Frame(picker)
            buttons.pack(fill=tk.X, padx=10, pady=(0, 10))
            ttk.Button(buttons, text="Add", command=confirm_add).pack(side=tk.LEFT)
            ttk.Button(buttons, text="Cancel", command=picker.destroy).pack(
                side=tk.LEFT, padx=6
            )
            listbox.bind("<Double-Button-1>", lambda _event: confirm_add())

        def remove_selected_spell():
            selected = spells_tree.selection()
            if not selected:
                return
            spell_loadout.pop(int(selected[0]), None)
            refresh_spell_loadout_view()

        def adjust_prepared_selected(delta: int):
            selected = spells_tree.selection()
            if not selected:
                return
            spell_id = int(selected[0])
            row = spell_catalog_by_id.get(spell_id)
            if row is None or not spell_uses_prepared(row):
                messagebox.showinfo(
                    "Spells",
                    "Prepared counts only apply to Vancian spells. Mana spells "
                    "are available whenever they are in the loadout.",
                    parent=editor,
                )
                return
            current = spell_loadout.get(spell_id)
            if current is None:
                return
            current["prepared"] = max(0, int(current["prepared"] or 0) + delta)
            refresh_spell_loadout_view()
            spells_tree.selection_set(str(spell_id))

        spell_buttons = ttk.Frame(spells_tab)
        spell_buttons.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Button(
            spell_buttons, text="Add from catalog", command=add_spell_to_loadout
        ).pack(side=tk.LEFT)
        ttk.Button(
            spell_buttons, text="Remove", command=remove_selected_spell
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            spell_buttons,
            text="Prepared −",
            command=lambda: adjust_prepared_selected(-1),
        ).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(
            spell_buttons,
            text="Prepared +",
            command=lambda: adjust_prepared_selected(1),
        ).pack(side=tk.LEFT, padx=6)

        ttk.Label(
            spells_tab,
            text="Spell notes (legacy free text / migration blurb)",
        ).pack(anchor="w", padx=10)
        spell_notes = tk.Text(spells_tab, wrap=tk.WORD, font=("Consolas", 10), height=6)
        spell_notes.pack(fill=tk.BOTH, expand=True, padx=10, pady=(2, 10))
        spell_notes.insert("1.0", spell_notes_blurb(data))
        refresh_spell_loadout_view()

        for key, label in TEXT_FIELDS:
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=label)
            widget = tk.Text(tab, wrap=tk.WORD, font=("Consolas", 10))
            widget.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            widget.insert("1.0", _as_text(data.get(key, "")))
            texts[key] = widget

        def save():
            try:
                payload = dict(saved)
                for key, var in fields.items():
                    value = var.get()
                    if stat_block_scaffold and key in {
                        "level", "experience", "hp_current", "hp_max", "ac",
                        "speed", "initiative", "proficiency_bonus",
                    }:
                        parsed = _optional_int(value)
                        if parsed is None:
                            payload.pop(key, None)
                        else:
                            payload[key] = parsed
                    else:
                        payload[key] = value
                for stat, var in ability_vars.items():
                    if stat_block_scaffold:
                        parsed = _optional_int(var.get())
                        if parsed is None:
                            payload.pop(stat, None)
                        else:
                            payload[stat] = parsed
                    else:
                        payload[stat] = var.get()
                payload.pop("inventory", None)
                payload.pop("attuned", None)
                payload.pop("skills", None)
                campaign_role = str(payload.pop("campaign_role", "") or "").strip()
                for key, _label in TEXT_FIELDS:
                    payload[key] = texts[key].get("1.0", "end").strip()
                payload["spells"] = spell_notes.get("1.0", "end").strip()
                payload.pop("hp", None)
                payload.pop("max_hp", None)
            except (tk.TclError, TypeError, ValueError):
                messagebox.showerror("Save", "A number field is empty or invalid.", parent=editor)
                return
            name = str(payload.get("name") or "Unnamed")
            if existing_id is None:
                character_id = create_character(
                    self.db,
                    name,
                    payload,
                    campaign_role=campaign_role,
                )
            else:
                update_character(
                    self.db,
                    existing_id,
                    name,
                    payload,
                    campaign_role=campaign_role,
                )
                character_id = existing_id
            def inventory_children(parent_id):
                children = []
                for item_id in inventory_tree.get_children(parent_id):
                    node = dict(inventory_node_data[item_id])
                    node["object_id"] = node.get("object_id") or item_id
                    node["properties"] = {
                        "quantity": int(inventory_tree.set(item_id, "quantity") or 1),
                        "attuned": inventory_tree.set(item_id, "attuned") == "Yes",
                        "equipment_location": inventory_tree.set(item_id, "location"),
                    }
                    if node["object_type"] == "container":
                        node["children"] = inventory_children(item_id)
                    children.append(node)
                return children

            try:
                save_character_inventory(
                    self.db,
                    character_id,
                    inventory_children(root_id),
                )
            except (sqlite3.Error, ValueError, TypeError) as exc:
                messagebox.showerror("Save", f"Could not save inventory: {exc}", parent=editor)
                return
            skill_assignments = [
                (skill_id, values[0].get(), values[1].get())
                for skill_id, values in skill_vars.items()
            ]
            try:
                set_character_skills(self.db, character_id, skill_assignments)
            except sqlite3.Error as exc:
                messagebox.showerror("Save", f"Could not save character skills: {exc}", parent=editor)
                return
            spell_assignments = [
                (spell_id, True, state["prepared"], "")
                for spell_id, state in spell_loadout.items()
            ]
            try:
                set_character_spells(self.db, character_id, spell_assignments)
            except (sqlite3.Error, ValueError) as exc:
                messagebox.showerror(
                    "Save", f"Could not save character spells: {exc}", parent=editor
                )
                return
            self.refresh()
            messagebox.showinfo("Saved", f"{name} saved!", parent=editor)
            editor.destroy()

        ttk.Button(save_bar, text="Save Character", command=save).pack(pady=4)

    def _roll_scores(self, ability_scores, ability_vars, mod_labels):
        for stat in ability_scores:
            if stat not in ability_vars:
                continue
            rolls = sorted(random.randint(1, 6) for _ in range(4))
            ability_vars[stat].set(sum(rolls[1:]))
            mod_labels[stat].config(text=_mod_text(ability_vars[stat].get()))

    def _char_id_from(self, event):
        if isinstance(event, bool):
            return None
        if isinstance(event, int):
            return event
        if isinstance(event, str) and event.isdigit():
            return int(event)
        selected = self.tree.selection()
        if not selected:
            return None
        return int(selected[0])

    def open_importer(self):
        toplevel = tk.Toplevel(self.main_app.root)
        toplevel.title("Import Character from Markdown")
        toplevel.geometry("560x180")
        toplevel.transient(self.main_app.root)
        toplevel.grab_set()
        from sakaki_gm.ui.modules.importer import ImporterModule
        importer = ImporterModule(toplevel, self.main_app)
        importer.pack(fill="both", expand=True)

    def import_files(self):
        files = filedialog.askopenfilenames(
            title="Import campaign files",
            filetypes=[
                ("Campaign files", "*.md *.txt *.csv"),
                ("All files", "*.*"),
            ],
        )
        if not files:
            return
        from sakaki_gm.ui.modules.importer import import_campaign_files
        try:
            summary = import_campaign_files(self.db, files)
        except Exception as exc:
            messagebox.showerror("Import", str(exc))
            return
        self.refresh()
        messagebox.showinfo("Import", summary)

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        if messagebox.askyesno("Delete", "Permanently delete selected character(s)?"):
            for iid in selected:
                delete_character(self.db, int(iid))
                self.tree.delete(iid)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_stat_block_scaffold(data) -> bool:
    status = data.get("stat_block_status")
    return isinstance(status, str) and status.lower().startswith("design scaffold")


def _optional_int(value):
    if value is None or not str(value).strip():
        return None
    return int(value)


def _mod_text(score) -> str:
    try:
        return f"({(int(score) - 10) // 2:+})"
    except (TypeError, ValueError, tk.TclError):
        if score is None or not str(score).strip():
            return "(—)"
        return "(?)"


def _trace_mod(var, label):
    def update(*_args):
        try:
            label.config(text=_mod_text(var.get()))
        except tk.TclError:
            label.config(text="(?)")
    var.trace_add("write", update)

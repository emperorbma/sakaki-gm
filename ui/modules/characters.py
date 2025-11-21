# ui/modules/characters.py
import tkinter as tk
from tkinter import ttk, messagebox
import json
import random
from character_db import create_character, update_character, get_all_characters, delete_character


class CharactersModule:
    def __init__(self, notebook, db, main_app):
        self.db = db
        self.main_app = main_app
        self.frame = ttk.Frame(notebook)

        # Treeview
        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=10, pady=10)
        self.tree = ttk.Treeview(tree_frame, columns=("Summary"), show="headings", height=25)
        self.tree.heading("Summary", text="Character Sheet")
        self.tree.column("Summary", width=320)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.open_editor)

        btn_frame = ttk.Frame(tree_frame)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text="+ New Character", command=self.new_character).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Import from .md", command=self.open_importer).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Delete", command=self.delete_selected).pack(side=tk.LEFT, padx=5)

        self.refresh()

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        characters = get_all_characters(self.db)
        for char in characters:
            if "error" in char:
                summary = "(error loading data)"
            else:
                hp = char.get("hp", 0)
                max_hp = char.get("max_hp", hp)
                cls = char.get("class", "?")
                summary = f"{char['name']} — {cls} — HP: {hp}/{max_hp}"
            
            self.tree.insert("", "end", iid=char["id"], text=char["name"], values=(summary,))

    def new_character(self):
        self.open_editor(None)

    def open_editor(self, event=None):
        item = self.tree.selection()
        char_id = item[0] if item else None
        row = self.db.execute("SELECT * FROM characters WHERE id=?", (char_id,)).fetchone() if char_id else None

        # Load campaign ability scores
        stats_row = self.db.execute("SELECT value FROM campaign_settings WHERE key='ability_scores'").fetchone()
        ability_scores = json.loads(stats_row["value"]) if stats_row else ["str", "dex", "con", "int", "wis", "cha"]

        prof_bonus_row = self.db.execute("SELECT value FROM campaign_settings WHERE key='proficiency_bonus'").fetchone()
        default_prof_bonus = int(prof_bonus_row["value"]) if prof_bonus_row else 2

        default_data = {
            "name": "New Character", "class": "", "level": 1, "race": "", "background": "", "alignment": "",
            "hp": 10, "max_hp": 10, "temp_hp": 0, "ac": 10, "speed": 30, "initiative": 0,
            "proficiency_bonus": default_prof_bonus,
            "inventory": [], "spells": {}, "features": [], "notes": "",
            "saving_throws": {}, "skills": {}
        }
        for stat in ability_scores:
            default_data.setdefault(stat, 10)

        # FIXED: saved data wins over defaults
        saved_data = json.loads(row["data"]) if row else {}
        data = {**default_data, **saved_data}   # ← saved data overwrites defaults

        editor = tk.Toplevel(self.main_app.root)
        editor.title(f"Character Editor — {data.get('name', 'New')}")
        editor.geometry("1200x800")
        editor.minsize(1100, 700)

        # Toolbar
        toolbar = ttk.Frame(editor)
        toolbar.pack(fill=tk.X, pady=5, padx=10)
        ttk.Button(toolbar, text="Generate 5e Stats", command=self.fill_5e_defaults).pack(side=tk.LEFT, padx=5)
        ttk.Label(toolbar, text=f"Using: {', '.join(ability_scores).upper()}").pack(side=tk.RIGHT)

        nb = ttk.Notebook(editor)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # Core Stats Tab
        core = ttk.Frame(nb)
        nb.add(core, text="Core")

        left = ttk.LabelFrame(core, text="Ability Scores")
        left.grid(row=0, column=0, padx=15, pady=15, sticky="n")
        self.ability_vars = {}
        self.mod_labels = {}

        for i, stat in enumerate(ability_scores):
            frame = ttk.Frame(left)
            frame.pack(fill=tk.X, pady=6)

            ttk.Label(frame, text=stat.upper(), width=6, font=("bold", 10)).pack(side=tk.LEFT)
            val = tk.IntVar(value=data.get(stat, 10))
            self.ability_vars[stat] = val
            entry = ttk.Entry(frame, textvariable=val, width=5, justify="center")
            entry.pack(side=tk.LEFT, padx=8)

            mod = (val.get() - 10) // 2
            mod_label = ttk.Label(frame, text=f"({mod:+})", width=6)
            mod_label.pack(side=tk.LEFT, padx=8)
            self.mod_labels[stat] = mod_label

            def update_mod(var_name, stat=stat):
                mod = (self.ability_vars[stat].get() - 10) // 2
                self.mod_labels[stat].config(text=f"({mod:+})")
            val.trace("w", update_mod)

        right = ttk.LabelFrame(core, text="Combat & Identity")
        right.grid(row=0, column=1, padx=15, pady=15, sticky="n")

        self.vars = {}
        fields = [
            ("Name", "name", tk.StringVar),
            ("Class & Level", "class", tk.StringVar),
            ("Race", "race", tk.StringVar),
            ("Background", "background", tk.StringVar),
            ("Alignment", "alignment", tk.StringVar),
            ("HP", "hp", tk.IntVar),
            ("Max HP", "max_hp", tk.IntVar),
            ("AC", "ac", tk.IntVar),
            ("Speed", "speed", tk.IntVar),
            ("Proficiency Bonus", "proficiency_bonus", tk.IntVar),
        ]

        for i, (label, key, var_type) in enumerate(fields):
            ttk.Label(right, text=label + ":").grid(row=i, column=0, sticky="w", pady=4, padx=5)
            default_val = data.get(key, 10 if "hp" in key or key == "ac" else 30 if key == "speed" else 2 if key == "proficiency_bonus" else "")
            var = var_type(value=default_val)
            self.vars[key] = var
            widget = ttk.Entry(right, textvariable=var, width=25) if var_type == tk.StringVar else ttk.Spinbox(right, from_=0, to=999, textvariable=var, width=8)
            widget.grid(row=i, column=1, pady=4, padx=5)

        # === INVENTORY TAB ===
        inv_tab = ttk.Frame(nb)
        nb.add(inv_tab, text="Inventory")
        inv_list = tk.Listbox(inv_tab, height=22)
        inv_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        inv_scroll = ttk.Scrollbar(inv_tab, orient="vertical", command=inv_list.yview)
        inv_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        inv_list.configure(yscrollcommand=inv_scroll.set)
        entry_inv = ttk.Entry(inv_tab)
        entry_inv.pack(fill=tk.X, pady=5, padx=10)
        ttk.Button(inv_tab, text="Add Item", command=lambda: inv_list.insert(tk.END, entry_inv.get()) or entry_inv.delete(0, tk.END)).pack(pady=2)

        # === ATTUNED TAB ===
        att_tab = ttk.Frame(nb)
        nb.add(att_tab, text="Attuned (●)")
        att_list = tk.Listbox(att_tab, height=8, bg="#fff8d9", font=("Segoe UI", 10))
        att_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        def populate_attuned():
            att_list.delete(0, tk.END)
            for item in data.get("attuned", []):
                att_list.insert(tk.END, f"● {item}")

        editor.after(200, populate_attuned)

        # === NOTES TAB ===
        notes_tab = ttk.Frame(nb)
        nb.add(notes_tab, text="Notes")
        notes_text = tk.Text(notes_tab, wrap=tk.WORD, font=("Consolas", 10))
        notes_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        notes_text.insert("1.0", data.get("notes", "").strip())
        notes_text.config(state="disabled")

        # Populate inventory after window is ready
        def populate_inventory():
            inv_list.delete(0, tk.END)
            # Regular items
            for item in data.get("inventory", []):
                inv_list.insert(tk.END, item)
            # Add attuned items with symbol
            for item in data.get("attuned", []):
                inv_list.insert(tk.END, f"● {item} (attuned)")
        editor.after(200, populate_inventory)

        entry = ttk.Entry(inv_tab)
        entry.pack(fill=tk.X, pady=5, padx=10)
        ttk.Button(inv_tab, text="Add", command=lambda: inv_list.insert(tk.END, entry.get()) or entry.delete(0, tk.END)).pack(pady=5)

        # Populate inventory AFTER everything is built
        def populate_inventory():
            fresh_row = self.db.execute("SELECT data FROM characters WHERE id=?", (char_id,)).fetchone()
            if fresh_row:
                fresh_data = json.loads(fresh_row["data"])
                inv_list.delete(0, tk.END)
                for item in fresh_data.get("inventory", []):
                    inv_list.insert(tk.END, item)

        editor.after(200, populate_inventory)

        # Save function
        def save():
            saved = {
                "name": self.vars["name"].get(),
                "class": self.vars["class"].get(),
                "race": self.vars["race"].get(),
                "background": self.vars["background"].get(),
                "alignment": self.vars["alignment"].get(),
                "hp": self.vars["hp"].get(),
                "max_hp": self.vars["max_hp"].get(),
                "ac": self.vars["ac"].get(),
                "speed": self.vars["speed"].get(),
                "proficiency_bonus": self.vars["proficiency_bonus"].get(),
                "inventory": list(inv_list.get(0, tk.END)),
                **{stat: var.get() for stat, var in self.ability_vars.items()}
            }
            json_data = json.dumps(saved, ensure_ascii=False)
            if char_id:
                update_character(self.db, char_id, saved["name"], saved)
            else:
                char_id = create_character(self.db, saved["name"], saved)
            self.db.commit()
            self.refresh()
            messagebox.showinfo("Saved", f"{saved['name']} saved!")
            editor.destroy()

        ttk.Button(editor, text="Save Character", command=save).pack(pady=15)

    def fill_5e_defaults(self):
        # ... (your existing stat generation code) ...
        pass

    def open_importer(self):
        toplevel = tk.Toplevel(self.main_app.root)
        toplevel.title("Import Character from Markdown")
        toplevel.geometry("560x180")
        toplevel.transient(self.main_app.root)
        toplevel.grab_set()

        from ui.modules.importer import ImporterModule
        importer = ImporterModule(toplevel, self.main_app)
        importer.pack(fill="both", expand=True)

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        if messagebox.askyesno("Delete", "Permanently delete selected character(s)?"):
            for iid in selected:
                char_id = int(iid)
                delete_character(self.db, char_id)
                self.tree.delete(iid)
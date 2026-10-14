# ui/modules/characters.py
import json
import random
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from character_db import create_character, delete_character, get_all_characters, update_character

TEXT_FIELDS = (
    ("spells", "Spells"),
    ("features", "Features"),
    ("skills", "Skills"),
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
                summary = f"{char['name']} — {char.get('class') or '?'} — HP: {hp}/{max_hp}"
            self.tree.insert("", "end", iid=str(char["id"]), text=char["name"], values=(summary,))

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
        defaults = {
            "name": "New Character", "class": "", "level": 1, "race": "",
            "background": "", "alignment": "", "experience": 0,
            "hp_current": 10, "hp_max": 10, "ac": 10, "ac_note": "",
            "speed": 30, "initiative": 0, "proficiency_bonus": default_prof,
            "inventory": [], "attuned": [], "notes": "",
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
            var = tk.IntVar(value=_as_int(data.get(stat), 10))
            ability_vars[stat] = var
            ttk.Entry(row_frame, textvariable=var, width=5, justify="center").pack(side=tk.LEFT, padx=8)
            mod_label = ttk.Label(row_frame, text=_mod_text(var.get()), width=6)
            mod_label.pack(side=tk.LEFT, padx=8)
            mod_labels[stat] = mod_label
            _trace_mod(var, mod_label)

        right = ttk.LabelFrame(core, text="Combat & Identity")
        right.grid(row=0, column=1, padx=15, pady=15, sticky="n")
        spec = (
            ("Name", "name", "str"),
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
            else:
                var = tk.IntVar(value=_as_int(data.get(key), 0))
                widget = ttk.Spinbox(right, from_=-20, to=9999, textvariable=var, width=8)
            fields[key] = var
            widget.grid(row=index, column=1, pady=3, padx=5, sticky="w")

        inv_list = _list_tab(notebook, "Inventory", data.get("inventory") or [])
        att_list = _list_tab(notebook, "Attuned", data.get("attuned") or [])

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
                    payload[key] = var.get()
                for stat, var in ability_vars.items():
                    payload[stat] = var.get()
                payload["inventory"] = _list_values(inv_list)
                payload["attuned"] = [_strip_marker(item) for item in _list_values(att_list)]
                for key, _label in TEXT_FIELDS:
                    payload[key] = texts[key].get("1.0", "end").strip()
                payload.pop("hp", None)
                payload.pop("max_hp", None)
            except tk.TclError:
                messagebox.showerror("Save", "A number field is empty or invalid.", parent=editor)
                return
            name = str(payload.get("name") or "Unnamed")
            if existing_id is None:
                create_character(self.db, name, payload)
            else:
                update_character(self.db, existing_id, name, payload)
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
        from ui.modules.importer import ImporterModule
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
        from ui.modules.importer import import_campaign_files
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


def _list_tab(notebook, title, items):
    tab = ttk.Frame(notebook)
    notebook.add(tab, text=title)
    listing = tk.Listbox(tab, height=18)
    scroll = ttk.Scrollbar(tab, orient="vertical", command=listing.yview)
    listing.configure(yscrollcommand=scroll.set)
    listing.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
    scroll.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)
    tab.rowconfigure(0, weight=1)
    tab.columnconfigure(0, weight=1)
    for item in items:
        listing.insert(tk.END, _strip_marker(str(item)))
    entry = ttk.Entry(tab)
    entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

    def add():
        text = entry.get().strip()
        if text:
            listing.insert(tk.END, _strip_marker(text))
            entry.delete(0, tk.END)

    def remove():
        for index in reversed(listing.curselection()):
            listing.delete(index)

    buttons = ttk.Frame(tab)
    buttons.grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=4)
    ttk.Button(buttons, text="Add", command=add).pack(side=tk.LEFT, padx=4)
    ttk.Button(buttons, text="Remove", command=remove).pack(side=tk.LEFT, padx=4)
    entry.bind("<Return>", lambda _event: add())
    return listing


def _list_values(listing):
    return [listing.get(index) for index in range(listing.size()) if listing.get(index).strip()]


def _strip_marker(item: str) -> str:
    text = item.strip()
    if text.startswith("●"):
        text = text[1:].strip()
    return text


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


def _mod_text(score) -> str:
    try:
        return f"({(int(score) - 10) // 2:+})"
    except (TypeError, ValueError, tk.TclError):
        return "(?)"


def _trace_mod(var, label):
    def update(*_args):
        try:
            label.config(text=_mod_text(var.get()))
        except tk.TclError:
            label.config(text="(?)")
    var.trace_add("write", update)

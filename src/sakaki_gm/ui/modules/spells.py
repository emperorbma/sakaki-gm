"""Campaign spell catalog desk tab."""
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.rules_5e_spells import ensure_5e_spells
from sakaki_gm.spell_db import (
    CASTING_MODES,
    DEFAULT_CASTING_MODE,
    SPELL_SCHOOLS,
    delete_campaign_spell,
    get_spell,
    get_spells,
    spell_casting_mode,
    upsert_campaign_spell,
)


def _level_label(level: int) -> str:
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "—"
    if level <= 0:
        return "Cantrip"
    return f"Level {level}"


class SpellsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._selected_spell_id = None

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="Spells", font=("Georgia", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(
            header,
            text="Add missing 5e spells",
            command=self.add_missing_5e_spells,
        ).pack(side=tk.RIGHT)
        ttk.Label(
            self.frame,
            text=(
                "Campaign spell catalog for character spellbook loadouts. "
                "Casting mode is per spell (Vancian known/prepared, or mana) so "
                "hybrid bottles can mix both. Stock 5e titles seed as Vancian; "
                "matched free-text names link without rewriting character prose."
            ),
            wraplength=780,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        list_frame = ttk.Frame(body)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.spells_list = ttk.Treeview(
            list_frame,
            columns=("name", "level", "school", "mode"),
            show="headings",
            selectmode="browse",
        )
        self.spells_list.heading("name", text="Spell")
        self.spells_list.heading("level", text="Level")
        self.spells_list.heading("school", text="School")
        self.spells_list.heading("mode", text="Casting")
        self.spells_list.column("name", width=200, stretch=True)
        self.spells_list.column("level", width=70, stretch=False, anchor="center")
        self.spells_list.column("school", width=100, stretch=False)
        self.spells_list.column("mode", width=80, stretch=False, anchor="center")
        scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.spells_list.yview
        )
        self.spells_list.configure(yscrollcommand=scroll.set)
        self.spells_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.spells_list.bind("<<TreeviewSelect>>", self._on_spell_select)

        editor = ttk.Frame(body)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        ttk.Label(editor, text="Name").pack(anchor="w")
        self.spell_name = tk.StringVar()
        ttk.Entry(editor, textvariable=self.spell_name, width=28, font=("Georgia", 11)).pack(
            fill=tk.X, pady=(2, 8)
        )

        meta = ttk.Frame(editor)
        meta.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(meta, text="Level").grid(row=0, column=0, sticky="w")
        self.spell_level = tk.StringVar(value="0")
        ttk.Combobox(
            meta,
            textvariable=self.spell_level,
            values=[str(n) for n in range(10)],
            state="readonly",
            width=6,
        ).grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Label(meta, text="School").grid(row=0, column=1, sticky="w")
        self.spell_school = tk.StringVar(value="evocation")
        ttk.Combobox(
            meta,
            textvariable=self.spell_school,
            values=sorted(SPELL_SCHOOLS),
            state="readonly",
            width=14,
        ).grid(row=1, column=1, sticky="w", padx=(0, 10))
        ttk.Label(meta, text="Casting").grid(row=0, column=2, sticky="w")
        self.spell_casting_mode = tk.StringVar(value=DEFAULT_CASTING_MODE)
        ttk.Combobox(
            meta,
            textvariable=self.spell_casting_mode,
            values=sorted(CASTING_MODES),
            state="readonly",
            width=10,
        ).grid(row=1, column=2, sticky="w")

        flags = ttk.Frame(editor)
        flags.pack(fill=tk.X, pady=(0, 8))
        self.spell_ritual = tk.BooleanVar(value=False)
        self.spell_concentration = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags, text="Ritual", variable=self.spell_ritual).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Checkbutton(
            flags, text="Concentration", variable=self.spell_concentration
        ).pack(side=tk.LEFT)

        for label, attr in (
            ("Casting time", "spell_casting_time"),
            ("Range", "spell_range"),
            ("Components", "spell_components"),
            ("Duration", "spell_duration"),
        ):
            ttk.Label(editor, text=label).pack(anchor="w")
            var = tk.StringVar()
            setattr(self, attr, var)
            ttk.Entry(editor, textvariable=var, width=28).pack(fill=tk.X, pady=(2, 6))

        ttk.Label(editor, text="Description").pack(anchor="w")
        self.spell_description = tk.Text(
            editor, wrap=tk.WORD, font=("Georgia", 11), height=8
        )
        self.spell_description.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        buttons = ttk.Frame(editor)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="New", command=self.clear_spell_form).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Add", command=self.add_spell).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Save", command=self.save_selected_spell).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Delete", command=self.delete_selected_spell).pack(
            side=tk.LEFT, padx=6
        )
        self.reload()

    def set_db(self, db):
        self.db = db
        self._selected_spell_id = None
        self.reload()

    def add_missing_5e_spells(self):
        if not messagebox.askyesno(
            "5e spells",
            "Add any missing stock D&D 5e spells?\n\n"
            "Existing spells with the same name are left unchanged.",
            parent=self.frame,
        ):
            return
        result = ensure_5e_spells(self.db, commit=True)
        self.reload()
        messagebox.showinfo(
            "5e spells",
            f"Added {result['added']} spell(s); {result['skipped']} already present.",
            parent=self.frame,
        )

    def reload(self, keep_id=None):
        self.spells_list.delete(*self.spells_list.get_children())
        selected = None
        for spell in get_spells(self.db):
            school = (spell["school"] or "").strip()
            item_id = self.spells_list.insert(
                "",
                tk.END,
                iid=str(spell["id"]),
                values=(
                    spell["name"],
                    _level_label(spell["level"]),
                    school.title() if school else "—",
                    spell_casting_mode(spell),
                ),
            )
            if keep_id is not None and spell["id"] == keep_id:
                selected = item_id
        if selected is not None:
            self.spells_list.selection_set(selected)
            self.spells_list.see(selected)
            self._on_spell_select()
        else:
            self.clear_spell_form()

    def clear_spell_form(self):
        self._selected_spell_id = None
        self.spell_name.set("")
        self.spell_level.set("0")
        self.spell_school.set("evocation")
        self.spell_casting_mode.set(DEFAULT_CASTING_MODE)
        self.spell_ritual.set(False)
        self.spell_concentration.set(False)
        self.spell_casting_time.set("")
        self.spell_range.set("")
        self.spell_components.set("")
        self.spell_duration.set("")
        self.spell_description.delete("1.0", tk.END)
        selection = self.spells_list.selection()
        if selection:
            self.spells_list.selection_remove(*selection)

    def _on_spell_select(self, _event=None):
        selected = self.spells_list.selection()
        if not selected:
            return
        spell_id = int(selected[0])
        row = get_spell(self.db, spell_id)
        if row is None:
            return
        self._selected_spell_id = spell_id
        self.spell_name.set(row["name"])
        self.spell_level.set(str(row["level"] if row["level"] is not None else 0))
        school = (row["school"] or "evocation").lower()
        self.spell_school.set(school if school in SPELL_SCHOOLS else "evocation")
        self.spell_casting_mode.set(spell_casting_mode(row))
        self.spell_ritual.set(bool(row["ritual"]))
        self.spell_concentration.set(bool(row["concentration"]))
        self.spell_casting_time.set(row["casting_time"] or "")
        self.spell_range.set(row["range"] or "")
        self.spell_components.set(row["components"] or "")
        self.spell_duration.set(row["duration"] or "")
        self.spell_description.delete("1.0", tk.END)
        self.spell_description.insert("1.0", row["description"] or "")

    def _description_text(self) -> str:
        return self.spell_description.get("1.0", "end").strip()

    def _form_kwargs(self) -> dict:
        return {
            "level": int(self.spell_level.get() or 0),
            "school": self.spell_school.get(),
            "casting_time": self.spell_casting_time.get(),
            "range": self.spell_range.get(),
            "components": self.spell_components.get(),
            "duration": self.spell_duration.get(),
            "description": self._description_text(),
            "ritual": bool(self.spell_ritual.get()),
            "concentration": bool(self.spell_concentration.get()),
            "casting_mode": self.spell_casting_mode.get() or DEFAULT_CASTING_MODE,
        }

    def add_spell(self):
        try:
            spell_id = upsert_campaign_spell(
                self.db,
                self.spell_name.get(),
                **self._form_kwargs(),
            )
        except ValueError as exc:
            messagebox.showerror("Spells", str(exc), parent=self.frame)
            return
        self.reload(keep_id=spell_id)

    def save_selected_spell(self):
        if self._selected_spell_id is None:
            self.add_spell()
            return
        try:
            spell_id = upsert_campaign_spell(
                self.db,
                self.spell_name.get(),
                spell_id=self._selected_spell_id,
                **self._form_kwargs(),
            )
        except ValueError as exc:
            messagebox.showerror("Spells", str(exc), parent=self.frame)
            return
        self.reload(keep_id=spell_id)

    def delete_selected_spell(self):
        if self._selected_spell_id is None:
            selected = self.spells_list.selection()
            if not selected:
                return
            self._selected_spell_id = int(selected[0])
        name = self.spell_name.get().strip() or "this spell"
        if not messagebox.askyesno(
            "Delete spell",
            f"Delete {name} from this campaign?\n\n"
            "Character known/prepared links for it will be removed.",
            parent=self.frame,
        ):
            return
        try:
            delete_campaign_spell(self.db, self._selected_spell_id)
        except ValueError as exc:
            messagebox.showerror("Spells", str(exc), parent=self.frame)
            return
        self.reload()

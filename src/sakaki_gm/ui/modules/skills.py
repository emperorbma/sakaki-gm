"""Campaign skill catalog desk tab."""
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.skill_db import (
    ABILITY_SCORES,
    delete_campaign_skill,
    get_skills,
    upsert_campaign_skill,
)


class SkillsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._selected_skill_id = None

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="Skills", font=("Georgia", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(
            self.frame,
            text=(
                "Campaign skill list used for character proficiency and bottle-side "
                "roll_check proposals. Add, rename, reassign abilities, or edit "
                "descriptions here."
            ),
            wraplength=780,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        list_frame = ttk.Frame(body)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.skills_list = ttk.Treeview(
            list_frame,
            columns=("name", "ability"),
            show="headings",
            selectmode="browse",
        )
        self.skills_list.heading("name", text="Skill")
        self.skills_list.heading("ability", text="Ability")
        self.skills_list.column("name", width=260, stretch=True)
        self.skills_list.column("ability", width=90, stretch=False, anchor="center")
        scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.skills_list.yview
        )
        self.skills_list.configure(yscrollcommand=scroll.set)
        self.skills_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.skills_list.bind("<<TreeviewSelect>>", self._on_skill_select)

        editor = ttk.Frame(body)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        ttk.Label(editor, text="Name").pack(anchor="w")
        self.skill_name = tk.StringVar()
        ttk.Entry(editor, textvariable=self.skill_name, width=28, font=("Georgia", 11)).pack(
            fill=tk.X, pady=(2, 10)
        )
        ttk.Label(editor, text="Ability").pack(anchor="w")
        self.skill_ability = tk.StringVar(value="dex")
        ttk.Combobox(
            editor,
            textvariable=self.skill_ability,
            values=sorted(ABILITY_SCORES),
            state="readonly",
            width=10,
        ).pack(anchor="w", pady=(2, 10))
        ttk.Label(editor, text="Description").pack(anchor="w")
        self.skill_description = tk.Text(
            editor, wrap=tk.WORD, font=("Georgia", 11), height=8
        )
        self.skill_description.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        buttons = ttk.Frame(editor)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="New", command=self.clear_skill_form).pack(
            side=tk.LEFT
        )
        ttk.Button(buttons, text="Add", command=self.add_skill).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(buttons, text="Save", command=self.save_selected_skill).pack(
            side=tk.LEFT
        )
        ttk.Button(buttons, text="Delete", command=self.delete_selected_skill).pack(
            side=tk.LEFT, padx=6
        )
        self.reload()

    def set_db(self, db):
        self.db = db
        self._selected_skill_id = None
        self.reload()

    def reload(self, keep_id=None):
        self.skills_list.delete(*self.skills_list.get_children())
        selected = None
        for skill in get_skills(self.db):
            item_id = self.skills_list.insert(
                "",
                tk.END,
                iid=str(skill["id"]),
                values=(skill["name"], (skill["ability"] or "").upper() or "—"),
            )
            if keep_id is not None and skill["id"] == keep_id:
                selected = item_id
        if selected is not None:
            self.skills_list.selection_set(selected)
            self.skills_list.see(selected)
            self._on_skill_select()
        else:
            self.clear_skill_form()

    def clear_skill_form(self):
        self._selected_skill_id = None
        self.skill_name.set("")
        self.skill_ability.set("dex")
        self.skill_description.delete("1.0", tk.END)
        selection = self.skills_list.selection()
        if selection:
            self.skills_list.selection_remove(*selection)

    def _on_skill_select(self, _event=None):
        selected = self.skills_list.selection()
        if not selected:
            return
        skill_id = int(selected[0])
        row = next(
            (skill for skill in get_skills(self.db) if skill["id"] == skill_id),
            None,
        )
        if row is None:
            return
        self._selected_skill_id = skill_id
        self.skill_name.set(row["name"])
        self.skill_ability.set((row["ability"] or "dex").lower())
        self.skill_description.delete("1.0", tk.END)
        self.skill_description.insert("1.0", row["description"] or "")

    def _description_text(self) -> str:
        return self.skill_description.get("1.0", "end").strip()

    def add_skill(self):
        try:
            skill_id = upsert_campaign_skill(
                self.db,
                self.skill_name.get(),
                self.skill_ability.get(),
                self._description_text(),
            )
        except ValueError as exc:
            messagebox.showerror("Skills", str(exc), parent=self.frame)
            return
        self.reload(keep_id=skill_id)

    def save_selected_skill(self):
        if self._selected_skill_id is None:
            self.add_skill()
            return
        try:
            skill_id = upsert_campaign_skill(
                self.db,
                self.skill_name.get(),
                self.skill_ability.get(),
                self._description_text(),
                skill_id=self._selected_skill_id,
            )
        except ValueError as exc:
            messagebox.showerror("Skills", str(exc), parent=self.frame)
            return
        self.reload(keep_id=skill_id)

    def delete_selected_skill(self):
        if self._selected_skill_id is None:
            selected = self.skills_list.selection()
            if not selected:
                return
            self._selected_skill_id = int(selected[0])
        name = self.skill_name.get().strip() or "this skill"
        if not messagebox.askyesno(
            "Delete skill",
            f"Delete {name} from this campaign?\n\n"
            "Character proficiency and expertise for it will be removed.",
            parent=self.frame,
        ):
            return
        try:
            delete_campaign_skill(self.db, self._selected_skill_id)
        except ValueError as exc:
            messagebox.showerror("Skills", str(exc), parent=self.frame)
            return
        self.reload()

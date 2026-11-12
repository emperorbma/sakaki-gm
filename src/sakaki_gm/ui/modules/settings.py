# ui/modules/settings.py
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.gm import DEFAULT_INSTRUCTIONS, bootstrap_instructions, stored_system_prompt
from sakaki_gm.llm_config import (
    AI_ASSIST_MODES,
    AI_TOOL_LABELS,
    AI_TOOL_NAMES,
    MODEL_CAPABILITIES,
    RAG_MODES,
    PRESETS,
    active_llm_settings,
    ai_assist_mode,
    allocate_model_id,
    enabled_ai_tools,
    get_model,
    list_model_choices,
    load_config,
    model_display_label,
    model_profile_key,
    remove_model,
    resolve_api_key,
    save_config,
    set_default_model,
    upsert_model,
)
from sakaki_gm.model_capability_tests import run_capability_tests
from sakaki_gm.llm_client import list_models, test_connection
from sakaki_gm.rules_5e_equipment import ensure_5e_equipment_archetypes
from sakaki_gm.rules_5e_spells import ensure_5e_spells
from sakaki_gm.seat_db import (
    ensure_default_session,
    seat_requires_password,
    set_seat_password,
)
from sakaki_gm.skill_db import DEFAULT_5E_SKILLS, replace_campaign_skills
from sakaki_gm.ui.seat_login import SEAT_LOGIN_LABELS


class SettingsModule:
    def __init__(self, notebook, db, main_app):
        self.db = db
        self.main_app = main_app
        self.frame = ttk.Frame(notebook)

        settings_canvas = tk.Canvas(self.frame, highlightthickness=0)
        settings_scrollbar = ttk.Scrollbar(
            self.frame,
            orient=tk.VERTICAL,
            command=settings_canvas.yview,
        )
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = ttk.Frame(settings_canvas)
        content_window = settings_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.bind(
            "<Configure>",
            lambda _event: settings_canvas.configure(
                scrollregion=settings_canvas.bbox("all")
            ),
        )
        settings_canvas.bind(
            "<Configure>",
            lambda event: settings_canvas.itemconfigure(
                content_window, width=event.width
            ),
        )
        settings_canvas.bind(
            "<MouseWheel>",
            lambda event: settings_canvas.yview_scroll(
                -int(event.delta / 120), "units"
            ),
        )

        prompt = ttk.LabelFrame(content, text="System prompt")
        prompt.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        ttk.Label(
            prompt,
            text="Leave this empty to keep the built-in instructions, or an imported Instructions note. Text here replaces them.",
        ).pack(anchor="w", padx=10, pady=(8, 4))
        self.prompt_text = tk.Text(prompt, wrap=tk.WORD, font=("Georgia", 11), height=8)
        self.prompt_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        prompt_buttons = ttk.Frame(prompt)
        prompt_buttons.pack(fill=tk.X, padx=10, pady=(0, 4))
        ttk.Button(prompt_buttons, text="Save", command=self.save_system_prompt).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(prompt_buttons, text="Fill default", command=self.fill_default).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(prompt_buttons, text="Start from current", command=self.fill_from_bootstrap).pack(side=tk.LEFT)
        self.prompt_source = ttk.Label(prompt, text="")
        self.prompt_source.pack(anchor="w", padx=10, pady=(0, 8))
        self.reload_system_prompt()

        rules = ttk.LabelFrame(content, text="Campaign rules")
        rules.pack(fill=tk.X, padx=16, pady=12)
        ttk.Button(rules, text="Load Standard D&D 5e Rules", command=self.load_5e).pack(anchor="w", padx=10, pady=8)
        ttk.Label(
            rules,
            text=(
                "Loads ability scores, proficiency bonus, and 5e skills (replacing "
                "existing skills). Also adds any missing generic 5e equipment "
                "archetypes and stock spells without overwriting names already in "
                "the bottle."
            ),
            wraplength=720,
        ).pack(anchor="w", padx=10, pady=(0, 4))
        self.system_label = ttk.Label(rules, text=self._system_text())
        self.system_label.pack(anchor="w", padx=10, pady=(0, 8))

        llm = ttk.LabelFrame(content, text="Language models")
        llm.pack(fill=tk.X, padx=16, pady=12)
        cfg = load_config(self.main_app.config_path)
        self._selected_model_id = cfg.get("default_model_id")
        self._model_choice_ids = []
        current = get_model(cfg, self._selected_model_id)

        self.model_entry_var = tk.StringVar()
        self.model_label = tk.StringVar(value=current.get("label") or "")
        self.model_purpose = tk.StringVar(value=current.get("purpose") or "")
        self.provider = tk.StringVar(value=current.get("provider") or "spacexai")
        self.base_url = tk.StringVar(value=current.get("base_url") or "")
        self.model = tk.StringVar(value=current.get("model") or "")
        self.api_key = tk.StringVar(value=current.get("api_key") or "")
        rag_mode = current.get("rag_mode", "tools")
        self.rag_mode = tk.StringVar(value=RAG_MODES.get(rag_mode, RAG_MODES["tools"]))
        self.default_hint = tk.StringVar(value="")
        self.model_capabilities = {
            capability: tk.BooleanVar(value=False)
            for capability in MODEL_CAPABILITIES
        }

        picker = ttk.Frame(llm)
        picker.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 4))
        ttk.Label(picker, text="Configured models").pack(side=tk.LEFT)
        self.model_entry_combo = ttk.Combobox(
            picker,
            textvariable=self.model_entry_var,
            state="readonly",
            width=36,
        )
        self.model_entry_combo.pack(side=tk.LEFT, padx=(8, 6))
        self.model_entry_combo.bind(
            "<<ComboboxSelected>>", self.on_model_entry_selected
        )
        ttk.Button(picker, text="Add", command=self.add_model_entry).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(picker, text="Remove", command=self.remove_model_entry).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(
            picker, text="Set default", command=self.set_selected_as_default
        ).pack(side=tk.LEFT, padx=2)
        ttk.Label(llm, textvariable=self.default_hint).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4)
        )
        ttk.Label(
            llm,
            text=(
                "GM seat only. The default model powers Ask / Run as GM. "
                "Players never pick a model — the GM manager uses this config "
                "(and later purpose-based routing). Connectivity and capability "
                "tests are the only checks."
            ),
            wraplength=640,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))

        ttk.Label(llm, text="Label").grid(row=3, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.model_label, width=48).grid(
            row=3, column=1, sticky="w", padx=10, pady=6
        )
        ttk.Label(llm, text="Purpose").grid(row=4, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.model_purpose, width=48).grid(
            row=4, column=1, sticky="w", padx=10, pady=6
        )
        ttk.Label(
            llm,
            text="Optional. Purpose muse / narration forces narration-only (no bottle writes) for this model.",
            wraplength=560,
        ).grid(row=5, column=1, sticky="w", padx=10)

        ttk.Label(llm, text="Preset").grid(row=6, column=0, sticky="w", padx=10, pady=6)
        preset = ttk.Combobox(
            llm, textvariable=self.provider, values=list(PRESETS), state="readonly", width=28,
        )
        preset.grid(row=6, column=1, sticky="w", padx=10, pady=6)
        preset.bind("<<ComboboxSelected>>", self.apply_preset)

        ttk.Label(llm, text="Base URL").grid(row=7, column=0, sticky="w", padx=10, pady=6)
        base_url_entry = ttk.Entry(llm, textvariable=self.base_url, width=48)
        base_url_entry.grid(row=7, column=1, sticky="w", padx=10, pady=6)
        base_url_entry.bind(
            "<FocusOut>",
            lambda _event: self._load_model_capabilities(),
        )
        base_url_entry.bind(
            "<Return>",
            lambda _event: self._load_model_capabilities(),
        )
        ttk.Label(llm, text="Model").grid(row=8, column=0, sticky="w", padx=10, pady=6)
        model_frame = ttk.Frame(llm)
        model_frame.grid(row=8, column=1, sticky="w", padx=10, pady=6)
        self.model_combo = ttk.Combobox(
            model_frame,
            textvariable=self.model,
            values=(),
            state="readonly",
            width=39,
        )
        self.model_combo.pack(side=tk.LEFT)
        ttk.Button(
            model_frame,
            text="Refresh models",
            command=self.refresh_models,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(llm, text="API key").grid(row=9, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.api_key, width=48, show="*").grid(
            row=9, column=1, sticky="w", padx=10, pady=6
        )
        ttk.Label(
            llm,
            text="Leave the key blank to use XAI_API_KEY. A local server does not need a key.",
        ).grid(row=10, column=1, sticky="w", padx=10)

        ttk.Label(llm, text="Campaign RAG").grid(
            row=11, column=0, sticky="w", padx=10, pady=6
        )
        ttk.Combobox(
            llm,
            textvariable=self.rag_mode,
            values=list(RAG_MODES.values()),
            state="readonly",
            width=28,
        ).grid(row=11, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(
            llm,
            text="Choose model-driven tool calls or app-retrieved context.",
        ).grid(row=12, column=1, sticky="w", padx=10)
        tools_frame = ttk.LabelFrame(llm, text="Enable AI tools")
        tools_frame.grid(row=13, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        ttk.Label(
            tools_frame,
            text=(
                "Which RAG tools this model may call as Manager. Uncheck all "
                "(or desk Muse) to run without model-initiated tools."
            ),
            wraplength=560,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 2))
        enabled = set(enabled_ai_tools(current))
        self.enabled_ai_tools = {
            name: tk.BooleanVar(value=name in enabled) for name in AI_TOOL_NAMES
        }
        for index, name in enumerate(AI_TOOL_NAMES):
            ttk.Checkbutton(
                tools_frame,
                text=AI_TOOL_LABELS[name],
                variable=self.enabled_ai_tools[name],
            ).grid(
                row=1 + index // 2,
                column=index % 2,
                sticky="w",
                padx=8,
                pady=2,
            )
        capabilities = ttk.LabelFrame(llm, text="Model capabilities")
        capabilities.grid(row=14, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        ttk.Label(
            capabilities,
            text="User-declared expectations; the model listing does not report these.",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 2))
        for index, (capability, label) in enumerate(MODEL_CAPABILITIES.items()):
            ttk.Checkbutton(
                capabilities,
                text=label,
                variable=self.model_capabilities[capability],
            ).grid(
                row=1 + index // 2,
                column=index % 2,
                sticky="w",
                padx=8,
                pady=2,
            )
        self.model_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._load_model_capabilities(),
        )

        buttons = ttk.Frame(llm)
        buttons.grid(row=15, column=1, sticky="w", padx=10, pady=10)
        ttk.Button(buttons, text="Save", command=self.save_llm).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Test", command=self.test_llm).pack(side=tk.LEFT, padx=4)
        self.capability_test_button = ttk.Button(
            buttons,
            text="Test capabilities",
            command=self.test_capabilities,
        )
        self.capability_test_button.pack(side=tk.LEFT, padx=4)
        self._capability_test_busy = False
        self._refresh_model_entry_picker(cfg)
        self._load_model_capabilities()

        desk = ttk.LabelFrame(content, text="Desk")
        desk.pack(fill=tk.X, padx=16, pady=12)
        ui_cfg = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
        self.show_gm_reply_debug = tk.BooleanVar(
            value=bool(ui_cfg.get("show_gm_reply_debug"))
        )
        ttk.Checkbutton(
            desk,
            text="Peek / intercept AI replies before state changes",
            variable=self.show_gm_reply_debug,
            command=self.save_desk_ui,
        ).pack(anchor="w", padx=10, pady=8)
        ttk.Label(
            desk,
            text=(
                "GM seat only. Parks raw model JSON for rewrite, Apply, or "
                "Discard instead of auto-applying."
            ),
            wraplength=640,
        ).pack(anchor="w", padx=10, pady=(0, 4))
        assist_row = ttk.Frame(desk)
        assist_row.pack(fill=tk.X, padx=10, pady=(4, 4))
        ttk.Label(assist_row, text="AI role").pack(side=tk.LEFT)
        self.ai_assist_mode = tk.StringVar(
            value=AI_ASSIST_MODES[ai_assist_mode(cfg)]
        )
        assist_combo = ttk.Combobox(
            assist_row,
            textvariable=self.ai_assist_mode,
            values=list(AI_ASSIST_MODES.values()),
            state="readonly",
            width=28,
        )
        assist_combo.pack(side=tk.LEFT, padx=(8, 0))
        assist_combo.bind("<<ComboboxSelected>>", lambda _e: self.save_desk_ui())
        ttk.Label(
            desk,
            text=(
                "Muse: narration/inspiration only (no tools, no writes). "
                "Manager: may use enabled RAG tools and propose state changes. "
                "Also toggled from the GM tools strip."
            ),
            wraplength=640,
        ).pack(anchor="w", padx=10, pady=(0, 4))
        ttk.Label(
            desk,
            text=(
                "Seat is a login (startup or header “Switch seat…”): Game Master "
                "vs Player on the solo two-seat table. Player seat hides GM "
                "playbook tabs and filters by knowledge; players never pick models."
            ),
            wraplength=640,
        ).pack(anchor="w", padx=10, pady=(0, 8))

        seats = ttk.LabelFrame(desk, text="Seat passwords")
        seats.pack(fill=tk.X, padx=10, pady=(4, 10))
        ttk.Label(
            seats,
            text=(
                "Blank = open seat. Non-empty password is required at login. "
                "No recovery here — a future lobby can handle that when "
                "tables are serverized."
            ),
            wraplength=600,
        ).pack(anchor="w", padx=8, pady=(6, 4))
        self.seat_password_status = tk.StringVar(value="")
        ttk.Label(seats, textvariable=self.seat_password_status).pack(
            anchor="w", padx=8, pady=(0, 4)
        )
        pw_row = ttk.Frame(seats)
        pw_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(pw_row, text="Seat").pack(side=tk.LEFT)
        self.seat_password_role = tk.StringVar(value=SEAT_LOGIN_LABELS["gm"])
        ttk.Combobox(
            pw_row,
            textvariable=self.seat_password_role,
            values=list(SEAT_LOGIN_LABELS.values()),
            state="readonly",
            width=16,
        ).pack(side=tk.LEFT, padx=(6, 10))
        ttk.Label(pw_row, text="New password").pack(side=tk.LEFT)
        self.seat_password_entry = ttk.Entry(pw_row, show="*", width=20)
        self.seat_password_entry.pack(side=tk.LEFT, padx=(6, 8))
        ttk.Button(
            pw_row, text="Set", command=self.save_seat_password
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            pw_row, text="Clear", command=self.clear_seat_password
        ).pack(side=tk.LEFT, padx=2)
        self._refresh_seat_password_status()

    def set_db(self, db):
        self.db = db
        self.system_label.config(text=self._system_text())
        self.reload_system_prompt()

    def reload_system_prompt(self):
        self.prompt_text.delete("1.0", tk.END)
        stored = stored_system_prompt(self.db)
        if stored:
            self.prompt_text.insert("1.0", stored)
        self.prompt_source.config(text=self._prompt_source_text())

    def _prompt_source_text(self) -> str:
        if stored_system_prompt(self.db):
            return "Using this campaign prompt."
        note = self.db.execute(
            "SELECT title FROM notes WHERE lower(title) LIKE '%instruction%' AND content != '' LIMIT 1"
        ).fetchone()
        if note:
            return f"Empty, so the note “{note['title']}” is used."
        return "Empty, so the built-in GM instructions are used."

    def save_system_prompt(self, quiet=False):
        text = self.prompt_text.get("1.0", "end").strip()
        self.db.execute(
            "INSERT OR REPLACE INTO campaign_settings (key, value) VALUES ('system_prompt', ?)",
            (text,),
        )
        self.db.commit()
        self.prompt_source.config(text=self._prompt_source_text())
        if not quiet:
            messagebox.showinfo("Saved", "System prompt saved.")

    def fill_default(self):
        if not self._confirm_replace("Replace the text in the box with the built-in instructions?"):
            return
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", DEFAULT_INSTRUCTIONS.strip())

    def fill_from_bootstrap(self):
        if not self._confirm_replace("Replace the text in the box with the instructions this campaign is using now?"):
            return
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", bootstrap_instructions(self.db))

    def _confirm_replace(self, question: str) -> bool:
        current = self.prompt_text.get("1.0", "end").strip()
        if not current:
            return True
        return messagebox.askyesno("System prompt", question)

    def _system_text(self) -> str:
        current = self.db.execute(
            "SELECT value FROM campaign_settings WHERE key='stat_system'"
        ).fetchone()
        name = current["value"] if current else "Custom"
        return f"Current system: {name}"

    def load_5e(self):
        if not messagebox.askyesno(
            "Load 5e",
            "Replace skills and ability settings with Standard D&D 5e?\n\n"
            "Missing generic 5e equipment archetypes and stock spells will also "
            "be added (existing names are left alone).",
        ):
            return
        self.db.executescript("""
        INSERT OR REPLACE INTO campaign_settings (key, value) VALUES
        ('stat_system', '5e'),
        ('ability_scores', '["str","dex","con","int","wis","cha"]'),
        ('proficiency_bonus', '2');
        """)
        replace_campaign_skills(self.db, DEFAULT_5E_SKILLS)
        self.db.execute("DELETE FROM campaign_settings WHERE key='skills_list'")
        equipment = ensure_5e_equipment_archetypes(self.db)
        spells = ensure_5e_spells(self.db)
        self.db.commit()
        self.system_label.config(text=self._system_text())
        items_module = getattr(self.main_app, "items_module", None)
        if items_module is not None:
            items_module.reload()
        skills_module = getattr(self.main_app, "skills_module", None)
        if skills_module is not None:
            skills_module.reload()
        spells_module = getattr(self.main_app, "spells_module", None)
        if spells_module is not None:
            spells_module.reload()
        detail = (
            "Standard 5e rules loaded.\n"
            f"Equipment archetypes: {equipment['added']} added, "
            f"{equipment['skipped']} already present.\n"
            f"Spells: {spells['added']} added, {spells['skipped']} already present."
        )
        if equipment.get("repaired"):
            detail += (
                f"\nRepaired {equipment['repaired']} stock Waterskin(s) to vessel."
            )
        messagebox.showinfo("Success", detail)

    def apply_preset(self, _event=None):
        preset = PRESETS.get(self.provider.get())
        if not preset:
            return
        self.base_url.set(preset["base_url"])
        self.model.set(preset["model"])
        self.model_combo["values"] = ()
        self._load_model_capabilities()

    def _load_model_capabilities(self):
        cfg = load_config(self.main_app.config_path)
        key = model_profile_key({
            "provider": self.provider.get(),
            "base_url": self.base_url.get(),
            "model": self.model.get(),
        })
        selected = set(cfg["model_profiles"].get(key, []))
        for capability, variable in self.model_capabilities.items():
            variable.set(capability in selected)

    def _llm_dict(self) -> dict:
        """Connection settings for the form currently being edited."""
        return {
            "provider": self.provider.get().strip() or "spacexai",
            "base_url": self.base_url.get().strip(),
            "model": self.model.get().strip(),
            "api_key": self.api_key.get(),
            "rag_mode": next(
                mode for mode, label in RAG_MODES.items()
                if label == self.rag_mode.get()
            ),
            "enabled_ai_tools": [
                name
                for name in AI_TOOL_NAMES
                if self.enabled_ai_tools[name].get()
            ],
        }

    def _model_entry_dict(self) -> dict:
        settings = self._llm_dict()
        return {
            "id": self._selected_model_id,
            "label": self.model_label.get().strip()
            or settings.get("model")
            or "Model",
            "purpose": self.model_purpose.get().strip(),
            **settings,
        }

    def _refresh_model_entry_picker(self, cfg=None):
        cfg = cfg or load_config(self.main_app.config_path)
        choices = list_model_choices(cfg)
        self._model_choice_ids = [model_id for model_id, _label in choices]
        labels = [label for _model_id, label in choices]
        self.model_entry_combo["values"] = labels
        selected = self._selected_model_id or cfg.get("default_model_id")
        if selected in self._model_choice_ids:
            index = self._model_choice_ids.index(selected)
        elif self._model_choice_ids:
            index = 0
            selected = self._model_choice_ids[0]
        else:
            self.model_entry_var.set("")
            self.default_hint.set("")
            return
        self._selected_model_id = selected
        self.model_entry_var.set(labels[index])
        default_id = cfg.get("default_model_id")
        default_entry = get_model(cfg, default_id)
        self.default_hint.set(
            f"Default for Ask / Run as GM: {model_display_label(default_entry)}"
            + (" (this entry)" if selected == default_id else "")
        )

    def _load_entry_into_form(self, entry: dict):
        self._selected_model_id = entry.get("id")
        self.model_label.set(entry.get("label") or "")
        self.model_purpose.set(entry.get("purpose") or "")
        self.provider.set(entry.get("provider") or "spacexai")
        self.base_url.set(entry.get("base_url") or "")
        self.model.set(entry.get("model") or "")
        self.api_key.set(entry.get("api_key") or "")
        rag_mode = entry.get("rag_mode", "tools")
        self.rag_mode.set(RAG_MODES.get(rag_mode, RAG_MODES["tools"]))
        enabled = set(enabled_ai_tools(entry))
        for name, variable in self.enabled_ai_tools.items():
            variable.set(name in enabled)
        self.model_combo["values"] = ()
        self._load_model_capabilities()

    def on_model_entry_selected(self, _event=None):
        label = self.model_entry_var.get()
        choices = list(self.model_entry_combo["values"] or ())
        if label not in choices:
            return
        model_id = self._model_choice_ids[choices.index(label)]
        cfg = load_config(self.main_app.config_path)
        self._load_entry_into_form(get_model(cfg, model_id))
        self._refresh_model_entry_picker(cfg)

    def add_model_entry(self):
        cfg = load_config(self.main_app.config_path)
        existing_ids = {
            entry.get("id")
            for entry in (cfg.get("models") or [])
            if isinstance(entry, dict)
        }
        model_id = allocate_model_id(existing_ids)
        preset = PRESETS.get(self.provider.get()) or PRESETS["spacexai"]
        entry = {
            "id": model_id,
            "label": f"Model {len(existing_ids) + 1}",
            "purpose": "",
            "provider": self.provider.get().strip() or "spacexai",
            "base_url": preset["base_url"],
            "model": preset["model"],
            "api_key": "",
            "rag_mode": "tools",
            "enabled_ai_tools": list(AI_TOOL_NAMES),
        }
        upsert_model(cfg, entry, make_default=False)
        save_config(cfg, self.main_app.config_path)
        self._load_entry_into_form(get_model(cfg, model_id))
        self._refresh_model_entry_picker(cfg)
        if hasattr(self.main_app, "_refresh_gm_model_picker"):
            self.main_app._refresh_gm_model_picker(cfg)

    def remove_model_entry(self):
        cfg = load_config(self.main_app.config_path)
        if len(cfg.get("models") or []) <= 1:
            messagebox.showerror(
                "Models", "At least one model configuration is required."
            )
            return
        model_id = self._selected_model_id
        if not messagebox.askyesno(
            "Remove model",
            f"Remove “{self.model_entry_var.get()}” from the list?",
        ):
            return
        try:
            remove_model(cfg, model_id)
        except ValueError as exc:
            messagebox.showerror("Models", str(exc))
            return
        save_config(cfg, self.main_app.config_path)
        self._load_entry_into_form(get_model(cfg))
        self._refresh_model_entry_picker(cfg)
        if hasattr(self.main_app, "_refresh_gm_model_picker"):
            self.main_app._refresh_gm_model_picker(cfg)

    def set_selected_as_default(self):
        cfg = load_config(self.main_app.config_path)
        # Persist current form edits into the selected entry first.
        upsert_model(cfg, self._model_entry_dict(), make_default=False)
        try:
            set_default_model(cfg, self._selected_model_id)
        except ValueError as exc:
            messagebox.showerror("Models", str(exc))
            return
        settings = self._llm_dict()
        cfg["model_profiles"][model_profile_key(settings)] = [
            capability
            for capability, variable in self.model_capabilities.items()
            if variable.get()
        ]
        save_config(cfg, self.main_app.config_path)
        self._refresh_model_entry_picker(cfg)
        if hasattr(self.main_app, "_refresh_gm_model_picker"):
            self.main_app._refresh_gm_model_picker(cfg)
        messagebox.showinfo(
            "Models",
            f"Default model set to {model_display_label(get_model(cfg))}.",
        )

    def reload_desk_ai_controls(self):
        """Sync Settings desk AI controls from config (e.g. GM tools strip)."""
        cfg = load_config(self.main_app.config_path)
        ui_cfg = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
        self.show_gm_reply_debug.set(bool(ui_cfg.get("show_gm_reply_debug")))
        self.ai_assist_mode.set(AI_ASSIST_MODES[ai_assist_mode(cfg)])
        # Tool checkboxes belong to the selected model entry, not desk mode.
        enabled = set(enabled_ai_tools(active_llm_settings(cfg)))
        # Only sync tool boxes when viewing the default model to avoid clobbering.
        if self._selected_model_id == cfg.get("default_model_id"):
            for name, variable in self.enabled_ai_tools.items():
                variable.set(name in enabled)
        self._refresh_model_entry_picker(cfg)
        if hasattr(self.main_app, "_refresh_gm_model_picker"):
            self.main_app._refresh_gm_model_picker(cfg)

    def save_llm(self):
        cfg = load_config(self.main_app.config_path)
        entry = self._model_entry_dict()
        make_default = (
            not cfg.get("default_model_id")
            or entry["id"] == cfg.get("default_model_id")
            or len(cfg.get("models") or []) <= 1
        )
        upsert_model(cfg, entry, make_default=make_default)
        settings = self._llm_dict()
        cfg["model_profiles"][model_profile_key(settings)] = [
            capability
            for capability, variable in self.model_capabilities.items()
            if variable.get()
        ]
        save_config(cfg, self.main_app.config_path)
        self._refresh_model_entry_picker(cfg)
        if hasattr(self.main_app, "_refresh_gm_model_picker"):
            self.main_app._refresh_gm_model_picker(cfg)
        messagebox.showinfo("Saved", "Model settings saved.")

    def _seat_role_from_label(self) -> str:
        label = self.seat_password_role.get()
        return next(
            (role for role, text in SEAT_LOGIN_LABELS.items() if text == label),
            "gm",
        )

    def _refresh_seat_password_status(self):
        try:
            ensure_default_session(self.db)
            parts = []
            for role, label in SEAT_LOGIN_LABELS.items():
                state = "password set" if seat_requires_password(self.db, role) else "open"
                parts.append(f"{label}: {state}")
            self.seat_password_status.set(" · ".join(parts))
        except Exception:
            self.seat_password_status.set("")

    def save_seat_password(self):
        role = self._seat_role_from_label()
        password = self.seat_password_entry.get()
        if not password.strip():
            messagebox.showerror(
                "Seat password",
                "Enter a password to set, or use Clear for an open seat.",
            )
            return
        try:
            ensure_default_session(self.db)
            set_seat_password(self.db, role, password)
        except ValueError as exc:
            messagebox.showerror("Seat password", str(exc))
            return
        self.seat_password_entry.delete(0, tk.END)
        self._refresh_seat_password_status()
        messagebox.showinfo(
            "Seat password",
            f"Password set for {SEAT_LOGIN_LABELS[role]}.",
        )

    def clear_seat_password(self):
        role = self._seat_role_from_label()
        if not messagebox.askyesno(
            "Clear seat password",
            f"Make {SEAT_LOGIN_LABELS[role]} an open seat (no password)?",
        ):
            return
        try:
            ensure_default_session(self.db)
            set_seat_password(self.db, role, "")
        except ValueError as exc:
            messagebox.showerror("Seat password", str(exc))
            return
        self.seat_password_entry.delete(0, tk.END)
        self._refresh_seat_password_status()
        messagebox.showinfo(
            "Seat password",
            f"{SEAT_LOGIN_LABELS[role]} is open (no password).",
        )

    def save_desk_ui(self):
        from sakaki_gm.ui.desk_audience import normalize_desk_mode

        cfg = load_config(self.main_app.config_path)
        if not isinstance(cfg.get("ui"), dict):
            cfg["ui"] = {}
        cfg["ui"]["show_gm_reply_debug"] = bool(self.show_gm_reply_debug.get())
        label = self.ai_assist_mode.get()
        cfg["ui"]["ai_assist_mode"] = next(
            (key for key, text in AI_ASSIST_MODES.items() if text == label),
            "manager",
        )
        # Preserve header desk-mode toggle; Settings is GM-only.
        current_mode = cfg["ui"].get("desk_mode")
        if hasattr(self.main_app, "desk_mode_var"):
            current_mode = self.main_app.desk_mode_var.get() or current_mode
        cfg["ui"]["desk_mode"] = normalize_desk_mode(current_mode)
        save_config(cfg, self.main_app.config_path)
        self.main_app.apply_ui_preferences()

    def test_llm(self):
        settings = self._llm_dict()
        try:
            resolve_api_key(settings)
            result = test_connection(settings)
        except Exception as exc:
            messagebox.showerror("Model", str(exc))
            return
        messagebox.showinfo("Model", result)

    def test_capabilities(self):
        if self._capability_test_busy:
            return
        settings = self._llm_dict()
        if not settings["model"]:
            messagebox.showerror("Model", "Select a model before testing capabilities.")
            return
        if not settings["base_url"]:
            messagebox.showerror("Model", "Set a base URL before testing capabilities.")
            return
        self._capability_test_busy = True
        self.capability_test_button.state(["disabled"])

        def work():
            try:
                results = run_capability_tests(settings)
            except Exception as exc:
                self.main_app.root.after(
                    0,
                    lambda error=exc: self._capability_test_failed(error),
                )
                return
            self.main_app.root.after(
                0,
                lambda result=results: self._capability_test_finished(result),
            )

        threading.Thread(target=work, daemon=True).start()

    def _capability_test_failed(self, exc):
        self._capability_test_busy = False
        self.capability_test_button.state(["!disabled"])
        messagebox.showerror("Capability tests", str(exc))

    def _capability_test_finished(self, results):
        self._capability_test_busy = False
        self.capability_test_button.state(["!disabled"])
        lines = []
        for result in results:
            lines.extend([
                f"{result['capability'].replace('_', ' ').title()}: "
                f"{result['status']} ({result['elapsed_seconds']}s)",
                f"  {result['detail'][:400]}",
                "",
            ])
        report = "\n".join(lines).strip()
        messagebox.showinfo(
            "Capability test results",
            f"Model: {self.model.get().strip()}\n\n{report}\n\n"
            "Results are informational; capability checkboxes were not changed.",
        )

    def refresh_models(self):
        settings = self._llm_dict()
        try:
            models = list_models(settings)
        except Exception as exc:
            messagebox.showerror("Models", str(exc))
            return
        self.model_combo["values"] = models
        current_model = self.model.get().strip()
        if current_model in models:
            self.model_combo.current(models.index(current_model))
            messagebox.showinfo("Models", f"Loaded {len(models)} models; current model is available.")
        elif models:
            self.model_combo.current(0)
            self._load_model_capabilities()
            messagebox.showinfo(
                "Models",
                f"Loaded {len(models)} models. The previously configured model "
                "is not available; select a model from the list.",
            )
        else:
            self.model.set("")
            self._load_model_capabilities()
            messagebox.showinfo("Models", "The server returned no models.")

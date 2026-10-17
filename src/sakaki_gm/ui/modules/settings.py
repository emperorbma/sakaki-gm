# ui/modules/settings.py
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from sakaki_gm.gm import DEFAULT_INSTRUCTIONS, bootstrap_instructions, stored_system_prompt
from sakaki_gm.llm_config import (
    MODEL_CAPABILITIES,
    RAG_MODES,
    PRESETS,
    load_config,
    model_profile_key,
    resolve_api_key,
    save_config,
)
from sakaki_gm.model_capability_tests import run_capability_tests
from sakaki_gm.llm_client import list_models, test_connection
from sakaki_gm.skill_db import DEFAULT_5E_SKILLS, replace_campaign_skills


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
        self.system_label = ttk.Label(rules, text=self._system_text())
        self.system_label.pack(anchor="w", padx=10, pady=(0, 8))

        llm = ttk.LabelFrame(content, text="Language model")
        llm.pack(fill=tk.X, padx=16, pady=12)
        cfg = load_config(self.main_app.config_path)
        current = cfg["llm"]

        self.provider = tk.StringVar(value=current.get("provider") or "spacexai")
        self.base_url = tk.StringVar(value=current.get("base_url") or "")
        self.model = tk.StringVar(value=current.get("model") or "")
        self.api_key = tk.StringVar(value=current.get("api_key") or "")
        rag_mode = current.get("rag_mode", "tools")
        self.rag_mode = tk.StringVar(value=RAG_MODES.get(rag_mode, RAG_MODES["tools"]))
        self.model_capabilities = {
            capability: tk.BooleanVar(value=False)
            for capability in MODEL_CAPABILITIES
        }
        self._load_model_capabilities()

        ttk.Label(llm, text="Preset").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        preset = ttk.Combobox(
            llm, textvariable=self.provider, values=list(PRESETS), state="readonly", width=28,
        )
        preset.grid(row=0, column=1, sticky="w", padx=10, pady=6)
        preset.bind("<<ComboboxSelected>>", self.apply_preset)

        ttk.Label(llm, text="Base URL").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        base_url_entry = ttk.Entry(llm, textvariable=self.base_url, width=48)
        base_url_entry.grid(row=1, column=1, sticky="w", padx=10, pady=6)
        base_url_entry.bind(
            "<FocusOut>",
            lambda _event: self._load_model_capabilities(),
        )
        base_url_entry.bind(
            "<Return>",
            lambda _event: self._load_model_capabilities(),
        )
        ttk.Label(llm, text="Model").grid(row=2, column=0, sticky="w", padx=10, pady=6)
        model_frame = ttk.Frame(llm)
        model_frame.grid(row=2, column=1, sticky="w", padx=10, pady=6)
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
        ttk.Label(llm, text="API key").grid(row=3, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.api_key, width=48, show="*").grid(row=3, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(
            llm,
            text="Leave the key blank to use XAI_API_KEY. A local server does not need a key.",
        ).grid(row=4, column=1, sticky="w", padx=10)

        ttk.Label(llm, text="Campaign RAG").grid(row=5, column=0, sticky="w", padx=10, pady=6)
        ttk.Combobox(
            llm,
            textvariable=self.rag_mode,
            values=list(RAG_MODES.values()),
            state="readonly",
            width=28,
        ).grid(row=5, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(
            llm,
            text="Choose model-driven tool calls or app-retrieved context.",
        ).grid(row=6, column=1, sticky="w", padx=10)
        capabilities = ttk.LabelFrame(llm, text="Model capabilities")
        capabilities.grid(row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
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
        buttons.grid(row=8, column=1, sticky="w", padx=10, pady=10)
        ttk.Button(buttons, text="Save", command=self.save_llm).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Test", command=self.test_llm).pack(side=tk.LEFT, padx=4)
        self.capability_test_button = ttk.Button(
            buttons,
            text="Test capabilities",
            command=self.test_capabilities,
        )
        self.capability_test_button.pack(side=tk.LEFT, padx=4)
        self._capability_test_busy = False

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
        if not messagebox.askyesno("Load 5e", "Replace current rules with Standard D&D 5e?"):
            return
        self.db.executescript("""
        INSERT OR REPLACE INTO campaign_settings (key, value) VALUES
        ('stat_system', '5e'),
        ('ability_scores', '["str","dex","con","int","wis","cha"]'),
        ('proficiency_bonus', '2');
        """)
        replace_campaign_skills(self.db, DEFAULT_5E_SKILLS)
        self.db.execute("DELETE FROM campaign_settings WHERE key='skills_list'")
        self.db.commit()
        self.system_label.config(text=self._system_text())
        messagebox.showinfo("Success", "Standard 5e rules loaded.")

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
        return {
            "provider": self.provider.get().strip() or "spacexai",
            "base_url": self.base_url.get().strip(),
            "model": self.model.get().strip(),
            "api_key": self.api_key.get(),
            "rag_mode": next(
                mode for mode, label in RAG_MODES.items()
                if label == self.rag_mode.get()
            ),
        }

    def save_llm(self):
        cfg = load_config(self.main_app.config_path)
        settings = self._llm_dict()
        cfg["llm"] = settings
        cfg["model_profiles"][model_profile_key(settings)] = [
            capability
            for capability, variable in self.model_capabilities.items()
            if variable.get()
        ]
        save_config(cfg, self.main_app.config_path)
        messagebox.showinfo("Saved", "Model settings saved.")

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

# ui/modules/settings.py
import tkinter as tk
from tkinter import messagebox, ttk

from llm_config import PRESETS, load_config, resolve_api_key, save_config
from llm_client import test_connection


class SettingsModule:
    def __init__(self, notebook, db, main_app):
        self.db = db
        self.main_app = main_app
        self.frame = ttk.Frame(notebook)

        rules = ttk.LabelFrame(self.frame, text="Campaign rules")
        rules.pack(fill=tk.X, padx=16, pady=12)
        ttk.Button(rules, text="Load Standard D&D 5e Rules", command=self.load_5e).pack(anchor="w", padx=10, pady=8)
        self.system_label = ttk.Label(rules, text=self._system_text())
        self.system_label.pack(anchor="w", padx=10, pady=(0, 8))

        llm = ttk.LabelFrame(self.frame, text="Language model")
        llm.pack(fill=tk.X, padx=16, pady=12)
        cfg = load_config(self.main_app.config_path)
        current = cfg["llm"]

        self.provider = tk.StringVar(value=current.get("provider") or "spacexai")
        self.base_url = tk.StringVar(value=current.get("base_url") or "")
        self.model = tk.StringVar(value=current.get("model") or "")
        self.api_key = tk.StringVar(value=current.get("api_key") or "")

        ttk.Label(llm, text="Preset").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        preset = ttk.Combobox(
            llm, textvariable=self.provider, values=list(PRESETS), state="readonly", width=28,
        )
        preset.grid(row=0, column=1, sticky="w", padx=10, pady=6)
        preset.bind("<<ComboboxSelected>>", self.apply_preset)

        ttk.Label(llm, text="Base URL").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.base_url, width=48).grid(row=1, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(llm, text="Model").grid(row=2, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.model, width=48).grid(row=2, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(llm, text="API key").grid(row=3, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(llm, textvariable=self.api_key, width=48, show="*").grid(row=3, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(
            llm,
            text="Leave the key blank to use XAI_API_KEY. A local server does not need a key.",
        ).grid(row=4, column=1, sticky="w", padx=10)

        buttons = ttk.Frame(llm)
        buttons.grid(row=5, column=1, sticky="w", padx=10, pady=10)
        ttk.Button(buttons, text="Save", command=self.save_llm).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Test", command=self.test_llm).pack(side=tk.LEFT, padx=4)

    def set_db(self, db):
        self.db = db
        self.system_label.config(text=self._system_text())

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
        ('proficiency_bonus', '2'),
        ('skills_list', '["Acrobatics","Animal Handling","Arcana","Athletics","Deception","History","Insight","Intimidation","Investigation","Medicine","Nature","Perception","Performance","Persuasion","Religion","Sleight of Hand","Stealth","Survival"]');
        """)
        self.db.commit()
        self.system_label.config(text=self._system_text())
        messagebox.showinfo("Success", "Standard 5e rules loaded.")

    def apply_preset(self, _event=None):
        preset = PRESETS.get(self.provider.get())
        if not preset:
            return
        self.base_url.set(preset["base_url"])
        self.model.set(preset["model"])

    def _llm_dict(self) -> dict:
        return {
            "provider": self.provider.get().strip() or "spacexai",
            "base_url": self.base_url.get().strip(),
            "model": self.model.get().strip(),
            "api_key": self.api_key.get(),
        }

    def save_llm(self):
        cfg = load_config(self.main_app.config_path)
        cfg["llm"] = self._llm_dict()
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

# ui/main_window.py
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import pyperclip

from campaigns import (
    CAMPAIGNS_DIR,
    active_path,
    bootstrap,
    campaign_name,
    create_campaign,
    register_file,
    remove_campaign,
    rename_campaign,
    set_active,
)
from db import get_db
from gm import apply_updates, build_messages, format_clipboard, parse_reply
from llm_client import complete
from llm_config import CONFIG_PATH, load_config
from ui.log_panel import LogPanel
from ui.modules.characters import CharactersModule
from ui.modules.documents import DocumentsModule
from ui.modules.settings import SettingsModule
from ui.modules.world import WorldModule


class MainWindow:
    def __init__(self, root, config_path=None, campaigns_dir=None, legacy_db=None):
        self.root = root
        self.config_path = config_path or CONFIG_PATH
        self.campaigns_dir = campaigns_dir or CAMPAIGNS_DIR
        self._loading_campaigns = False
        self._ask_busy = False
        legacy = legacy_db if legacy_db is not None else None
        if legacy_db is None:
            from db import LEGACY_DB
            legacy = LEGACY_DB
        _cfg, path = bootstrap(self.config_path, legacy, self.campaigns_dir)
        self.db = get_db(path)
        self.root.geometry("1600x950")
        self.setup_ui()
        self.refresh_campaign_chrome()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=15, pady=(12, 4))
        ttk.Label(header, text="PyGM Core", font=("Georgia", 28, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Remove", command=self.remove_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Open", command=self.open_campaign_file).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Rename", command=self.rename_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="New", command=self.new_campaign).pack(side=tk.RIGHT, padx=4)
        self.campaign_combo = ttk.Combobox(header, state="readonly", width=32)
        self.campaign_combo.pack(side=tk.RIGHT, padx=8)
        self.campaign_combo.bind("<<ComboboxSelected>>", self.on_campaign_selected)

        place = ttk.Frame(self.root)
        place.pack(fill=tk.X, padx=15, pady=(0, 8))
        ttk.Label(place, text="Location").pack(side=tk.LEFT)
        self.location_var = tk.StringVar()
        self.location_entry = ttk.Entry(place, textvariable=self.location_var)
        self.location_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.location_entry.bind("<FocusOut>", lambda _event: self.save_location())
        self.location_entry.bind("<Return>", lambda _event: self.save_location())

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=15)
        self.characters_module = CharactersModule(notebook, self.db, self)
        notebook.add(self.characters_module.frame, text="Characters")
        self.world_module = WorldModule(notebook, self.db)
        notebook.add(self.world_module.frame, text="World")
        self.documents_module = DocumentsModule(notebook, self.db)
        notebook.add(self.documents_module.frame, text="Documents")
        self.settings_module = SettingsModule(notebook, self.db, self)
        notebook.add(self.settings_module.frame, text="Settings")
        self.log_panel = LogPanel(self.root, self.db)
        notebook.add(self.log_panel.frame, text="Log")

        bottom = ttk.Frame(self.root)
        bottom.pack(fill=tk.X, padx=15, pady=10)
        self.action_entry = tk.Entry(bottom, font=("Georgia", 12))
        self.action_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.action_entry.bind("<Return>", lambda _event: self.ask_gm())
        self.ask_button = ttk.Button(bottom, text="Ask GM", command=self.ask_gm)
        self.ask_button.pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom, text="Copy Prompt", command=self.copy_prompt).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom, text="d20", command=lambda: self.quick_roll(20)).pack(side=tk.RIGHT)

        reply_frame = ttk.LabelFrame(self.root, text="Paste LLM Reply")
        reply_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        self.reply_box = tk.Text(reply_frame, height=8, font=("Georgia", 11))
        self.reply_box.pack(fill=tk.X, expand=True, padx=10, pady=10)
        ttk.Button(reply_frame, text="Process", command=self.process_reply).pack(side=tk.RIGHT, padx=10)

    def refresh_campaign_chrome(self):
        self._loading_campaigns = True
        cfg = load_config(self.config_path)
        labels = []
        self._campaign_ids = []
        for item in cfg["campaigns"]:
            self._campaign_ids.append(item["id"])
            labels.append(campaign_name(item["path"]))
        self.campaign_combo["values"] = labels
        active = cfg.get("active_campaign")
        if active in self._campaign_ids:
            self.campaign_combo.current(self._campaign_ids.index(active))
        elif labels:
            self.campaign_combo.current(0)
        self._loading_campaigns = False
        name = self.get_meta("campaign_name", "New Campaign")
        self.root.title(f"PyGM Core — {name}")
        self.load_location()

    def load_location(self):
        row = self.db.execute("SELECT value FROM world WHERE key='location'").fetchone()
        self.location_var.set(row["value"] if row else "")

    def save_location(self):
        self.db.execute(
            "INSERT OR REPLACE INTO world (key, value) VALUES ('location', ?)",
            (self.location_var.get(),),
        )
        self.db.commit()

    def on_campaign_selected(self, _event=None):
        if self._loading_campaigns:
            return
        index = self.campaign_combo.current()
        if index < 0 or index >= len(self._campaign_ids):
            return
        cid = self._campaign_ids[index]
        cfg = load_config(self.config_path)
        if cid == cfg.get("active_campaign"):
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        path = set_active(self.config_path, cid)
        self._swap_db(path)

    def _swap_db(self, path):
        new_db = get_db(path)
        self.db.close()
        self.db = new_db
        self.characters_module.set_db(new_db)
        self.world_module.set_db(new_db)
        self.documents_module.set_db(new_db)
        self.settings_module.set_db(new_db)
        self.log_panel.set_db(new_db)
        self.refresh_campaign_chrome()

    def new_campaign(self):
        name = simpledialog.askstring("New Campaign", "Campaign name:", parent=self.root)
        if not name or not name.strip():
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        create_campaign(self.config_path, name.strip(), self.campaigns_dir)
        self._swap_db(active_path(load_config(self.config_path)))

    def rename_current(self):
        current = self.get_meta("campaign_name", "New Campaign")
        name = simpledialog.askstring("Rename Campaign", "Campaign name:", initialvalue=current, parent=self.root)
        if not name or not name.strip():
            return
        rename_campaign(self.db, name.strip())
        self.refresh_campaign_chrome()

    def open_campaign_file(self):
        chosen = filedialog.askopenfilename(
            title="Open campaign file",
            filetypes=[("SQLite", "*.db"), ("All files", "*.*")],
        )
        if not chosen:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        register_file(self.config_path, chosen)
        self._swap_db(active_path(load_config(self.config_path)))

    def remove_current(self):
        cfg = load_config(self.config_path)
        cid = cfg.get("active_campaign")
        label = self.get_meta("campaign_name", "this campaign")
        if not messagebox.askyesno("Remove campaign", f"Remove {label} from the campaign list?"):
            return
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        self.db.close()
        path = remove_campaign(self.config_path, cid, self.campaigns_dir)
        self.db = get_db(path)
        self.characters_module.set_db(self.db)
        self.world_module.set_db(self.db)
        self.documents_module.set_db(self.db)
        self.settings_module.set_db(self.db)
        self.log_panel.set_db(self.db)
        self.refresh_campaign_chrome()

    def open_character_editor(self, char_id=None):
        self.characters_module.open_editor(char_id)

    def copy_prompt(self):
        action = self.action_entry.get().strip()
        if not action:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        system, user = build_messages(self.db, action)
        pyperclip.copy(format_clipboard(system, user))
        self.log_panel.add_entry("Player", action)
        self.action_entry.delete(0, tk.END)
        messagebox.showinfo("Copied", "Prompt copied to clipboard.")

    def ask_gm(self):
        if self._ask_busy:
            return
        action = self.action_entry.get().strip()
        if not action:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        system, user = build_messages(self.db, action)
        settings = load_config(self.config_path)["llm"]
        self.log_panel.add_entry("Player", action)
        self.action_entry.delete(0, tk.END)
        self._ask_busy = True
        self.ask_button.state(["disabled"])

        def work():
            try:
                reply = complete(settings, system, user)
            except Exception as exc:
                self.root.after(0, lambda err=exc: self._ask_failed(err))
                return
            self.root.after(0, lambda text=reply: self._ask_finished(text))

        threading.Thread(target=work, daemon=True).start()

    def _ask_failed(self, exc):
        self._ask_busy = False
        self.ask_button.state(["!disabled"])
        self.log_panel.add_entry("System", f"Model request failed: {exc}")
        messagebox.showerror("Model", str(exc))

    def _ask_finished(self, reply):
        self._ask_busy = False
        self.ask_button.state(["!disabled"])
        self.reply_box.delete("1.0", tk.END)
        self.reply_box.insert("1.0", reply)
        self._apply_reply_text(reply)

    def process_reply(self):
        reply = self.reply_box.get("1.0", tk.END).strip()
        if not reply:
            return
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        self.reply_box.delete("1.0", tk.END)
        self._apply_reply_text(reply)

    def _apply_reply_text(self, reply: str):
        narration, updates = parse_reply(reply)
        self.log_panel.add_entry("GM", narration or reply)
        if updates:
            for line in apply_updates(self.db, updates):
                self.log_panel.add_entry("System", line)
        else:
            self.log_panel.add_entry("System", "No state updates in the reply")
        self.characters_module.refresh()
        self.load_location()
        self.world_module.reload()

    def quick_roll(self, sides):
        import random
        roll = random.randint(1, sides)
        self.action_entry.insert(0, f"[d{sides}: {roll}] ")
        self.log_panel.add_entry("System", f"Rolled d{sides}: {roll}")

    def get_meta(self, key, default):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

# ui/main_window.py
import tkinter as tk
from tkinter import ttk, messagebox
from ui.modules.characters import CharactersModule
from ui.modules.settings import SettingsModule
from ui.log_panel import LogPanel
import pyperclip
from datetime import datetime
from db import get_db


class MainWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("PyGM Core — Modular Edition")
        self.root.geometry("1600x950")

        self.db = get_db()
        self.campaign_name = self.get_meta("campaign_name", "New Campaign")
        self.root.title(f"PyGM Core — {self.campaign_name}")

        self.setup_ui()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        # Header
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=15, pady=15)
        ttk.Label(header, text="PyGM Core", font=("Georgia", 28, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="New Campaign", command=self.new_campaign).pack(side=tk.RIGHT, padx=5)

        # Notebook
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=15)

        # Characters Module — IMPORTANT: saved as both names for compatibility
        self.characters_module = CharactersModule(nb, self.db, self)
        self.char_module = self.characters_module  # legacy alias (some code still uses this)
        nb.add(self.characters_module.frame, text="Characters")

        self.settings_module = SettingsModule(nb, self.db, self)
        nb.add(self.settings_module.frame, text="Settings")

        self.log_panel = LogPanel(self.root, self.db)
        nb.add(self.log_panel.frame, text="Log")

        # Bottom controls
        bottom = ttk.Frame(self.root)
        bottom.pack(fill=tk.X, padx=15, pady=10)
        self.action_entry = tk.Entry(bottom, font=("Georgia", 12))
        self.action_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,10))
        self.action_entry.bind("<Return>", lambda e: self.copy_prompt())

        ttk.Button(bottom, text="Copy Prompt", command=self.copy_prompt).pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom, text="d20", command=lambda: self.quick_roll(20)).pack(side=tk.RIGHT)

        # Reply box
        reply_frame = ttk.LabelFrame(self.root, text="Paste LLM Reply")
        reply_frame.pack(fill=tk.X, padx=15, pady=(0,15))
        self.reply_box = tk.Text(reply_frame, height=8, font=("Georgia", 11))
        self.reply_box.pack(fill=tk.X, expand=True, padx=10, pady=10)
        ttk.Button(reply_frame, text="Process", command=self.process_reply).pack(side=tk.RIGHT, padx=10)

    def open_character_editor(self, char_id=None):
        """Called by importer — opens the character editor"""
        if hasattr(self, "characters_module"):
            self.characters_module.open_editor(char_id)
        elif hasattr(self, "char_module"):
            self.char_module.open_editor(char_id)

    def copy_prompt(self):
        action = self.action_entry.get().strip()
        if not action:
            return

        chars = []
        for row in self.db.execute("SELECT name, data FROM characters"):
            data = __import__('json').loads(row["data"])
            chars.append(f"{row['name']}: {data}")

        world_loc = self.db.execute("SELECT value FROM world WHERE key='location'").fetchone()
        world_loc = world_loc["value"] if world_loc else "Unknown"
        events = __import__('json').loads(self.db.execute("SELECT value FROM world WHERE key='events'").fetchone()["value"] or "[]")

        prompt = f"""You are an expert Game Master.

CURRENT STATE
Characters: {', '.join([c.split(':')[0] for c in chars])}
Location: {world_loc}
Recent events: {events[-5:]}

PLAYER ACTION
{action}

Narrate and end with JSON updates only:
{{ "updates": {{ "characters": {{...}}, "world": {{...}} }} }}
"""
        pyperclip.copy(prompt.strip())
        self.log_panel.add_entry("Player", action)
        self.action_entry.delete(0, tk.END)
        messagebox.showinfo("Copied", "Prompt copied to clipboard!")

    def process_reply(self):
        reply = self.reply_box.get(1.0, tk.END).strip()
        if not reply:
            return
        self.log_panel.add_entry("GM", reply)
        self.reply_box.delete(1.0, tk.END)

        import re, json
        match = re.search(r"\{.*\"updates\".*\}", reply, re.DOTALL)
        if match:
            try:
                updates = json.loads(match.group(0))["updates"]
                self.apply_updates(updates)
                self.log_panel.add_entry("System", "State updated")
            except:
                self.log_panel.add_entry("System", "Bad JSON in updates")
        self.characters_module.refresh()

    def apply_updates(self, updates):
        cur = self.db.cursor()
        if "characters" in updates:
            for name, data in updates["characters"].items():
                row = cur.execute("SELECT id, data FROM characters WHERE name=?", (name,)).fetchone()
                if row:
                    current = __import__('json').loads(row["data"])
                    current.update(data)
                    cur.execute("UPDATE characters SET data=? WHERE id=?", (__import__('json').dumps(current), row["id"]))
        if "world" in updates:
            for k, v in updates["world"].items():
                cur.execute("INSERT OR REPLACE INTO world (key, value) VALUES (?, ?)",
                            (k, __import__('json').dumps(v) if isinstance(v, (list,dict)) else str(v)))
        self.db.commit()

    def quick_roll(self, sides):
        import random
        roll = random.randint(1, sides)
        self.action_entry.insert(0, f"[d{sides}: {roll}] ")
        self.log_panel.add_entry("System", f"Rolled d{sides}: {roll}")

    def get_meta(self, key, default):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def new_campaign(self):
        if messagebox.askyesno("New Campaign", "This will delete all current data. Continue?"):
            import os
            db_path = "pygm_campaign.db"
            if os.path.exists(db_path):
                os.remove(db_path)
            self.root.destroy()
            import sys
            os.execl(sys.executable, sys.executable, *sys.argv)
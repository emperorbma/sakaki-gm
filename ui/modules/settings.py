# ui/modules/settings.py
import tkinter as tk
from tkinter import ttk, messagebox
import json

class SettingsModule:
    def __init__(self, notebook, db, main_app):
        self.db = db
        self.main_app = main_app
        self.frame = ttk.Frame(notebook)

        ttk.Label(self.frame, text="Campaign Settings", font=("Georgia", 16, "bold")).pack(pady=20)

        btn = ttk.Button(self.frame, text="Load Standard D&D 5e Rules", command=self.load_5e)
        btn.pack(pady=10)
        ttk.Label(self.frame, text="Sets ability scores, skills, proficiency, etc.").pack()

        current = self.db.execute("SELECT value FROM campaign_settings WHERE key='stat_system'").fetchone()
        ttk.Label(self.frame, text=f"Current System: {current['value'] if current else 'Custom'}", font=("Consolas", 12)).pack(pady=20)

    def load_5e(self):
        if messagebox.askyesno("Load 5e", "Replace current rules with Standard D&D 5e?"):
            cur = self.db.cursor()
            cur.executescript("""
            INSERT OR REPLACE INTO campaign_settings (key, value) VALUES
            ('stat_system', '5e'),
            ('ability_scores', '["str","dex","con","int","wis","cha"]'),
            ('proficiency_bonus', '2'),
            ('skills_list', '["Acrobatics","Animal Handling","Arcana","Athletics","Deception","History","Insight","Intimidation","Investigation","Medicine","Nature","Perception","Performance","Persuasion","Religion","Sleight of Hand","Stealth","Survival"]');
            """)
            self.db.commit()
            messagebox.showinfo("Success", "Standard 5e rules loaded!")
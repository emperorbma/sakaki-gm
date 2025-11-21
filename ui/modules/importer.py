# ui/modules/importer.py — SMART PARSER: separates Inventory, Attuned, Notes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re
import json
from pathlib import Path
from character_db import create_character


class ImporterModule(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        ttk.Label(self, text="Import Character from Markdown", font=("Segoe UI", 12, "bold")).pack(pady=10)
        ttk.Button(self, text="Choose Markdown File (.md)", command=self.import_file).pack(pady=5)

    def import_file(self):
        path = filedialog.askopenfilename(filetypes=[("Markdown files", "*.md")])
        if not path: return

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()

            data = self.parse_markdown_character(text)
            name = data.get("name", Path(path).stem.replace("_", " ").title())
            char_id = create_character(self.app.db, name=name, data=data)

            messagebox.showinfo("Success", f"Imported '{name}' successfully!")
            self.app.characters_module.refresh()
            self.master.after(100, lambda cid=char_id: self.app.open_character_editor(cid))
            self.master.destroy()
            return
        except Exception as e:
            messagebox.showerror("Error", f"Import failed:\n{e}")

    def parse_markdown_character(self, text: str) -> dict:
        lines = [line.rstrip() for line in text.splitlines()]
        data = {
            "name": "", "race": "", "class": "", "level": 1, "background": "", "alignment": "",
            "experience": 0, "hp_max": 10, "hp_current": 10, "ac": 10,
            "proficiency_bonus": 2, "speed": 30,
            "inventory": [], "attuned": [], "notes": "",
            "str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10,
        }

        # Ability scores + basic fields (unchanged, proven working)
        text_lower = text.lower()
        score_patterns = {
            "str": [r"strength\s+(\d+)", r"str\s+(\d+)"],
            "dex": [r"dexterity\s+(\d+)", r"dex\s+(\d+)"],
            "con": [r"constitution\s+(\d+)", r"con\s+(\d+)"],
            "int": [r"intelligence\s+(\d+)", r"int\s+(\d+)"],
            "wis": [r"wisdom\s+(\d+)", r"wis\s+(\d+)"],
            "cha": [r"charisma\s+(\d+)", r"cha\s+(\d+)"],
        }

        for key, patterns in score_patterns.items():
            for pattern in patterns:
                if m := re.search(pattern, text_lower):
                    data[key] = int(m.group(1))
                    break
            else:
                data[key] = 10  # fallback

        current_section = None
        for line in lines:
            stripped = line.strip()
            lower = line.lower()

            if not data["name"] and line.startswith("# "):
                data["name"] = line.lstrip("# ").split("–")[0].strip()

            if m := re.match(r"\*\*(.+?)\*\*\s*:\s*(.+)", stripped):
                k, v = m.group(1).lower(), m.group(2).strip()
                if "race" in k: data["race"] = v
                elif "background" in k: data["background"] = v
                elif "alignment" in k: data["alignment"] = v
                elif "class" in k or "level" in k:
                    data["class"] = v
                    if lvl := re.search(r"\b(\d+)\b", v): data["level"] = int(lvl.group(1))

            if "hp" in lower and (m := re.search(r"(\d+)", lower)): data["hp_max"] = data["hp_current"] = int(m.group(1))
            if "ac" in lower and (m := re.search(r"(\d+)", lower)): data["ac"] = int(m.group(1))

            # === SMART SECTION DETECTION ===
            if stripped.startswith(("## ", "### ")):
                title = lower
                if any(x in title for x in ["equipment", "inventory", "items", "carried", "possessions"]):
                    current_section = "inventory"
                elif "attuned" in title:
                    current_section = "attuned"
                elif any(x in title for x in ["quest", "note", "experience", "ready for"]):
                    current_section = "notes"
                else:
                    current_section = None
                continue

            # === COLLECT ITEMS ===
            if current_section and stripped and not stripped.startswith(("#", "|")) and "---" not in stripped:
                item = line.strip()
                item = re.sub(r"^[-*•>\d.\[\]xX]\s+", "", item)
                item = re.sub(r"\s*[\(\[].*?[\)\]]$", "", item)
                item = re.sub(r"^\d+\s*x?\s+", "", item, flags=re.I)
                clean = item.strip()
                if clean:
                    if current_section == "inventory":
                        data["inventory"].append(clean)
                    elif current_section == "attuned":
                        # ONLY add to attuned — NOT to main inventory
                        data["attuned"].append(clean)
                    elif current_section == "notes":
                        data["notes"] += clean + "\n"

        return data
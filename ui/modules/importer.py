# ui/modules/importer.py — markdown character sheets and campaign folders
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from character_db import create_character, update_character

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
ABILITY_RE = re.compile(
    r"(?i)\b(strength|dexterity|constitution|intelligence|wisdom|charisma|str|dex|con|int|wis|cha)\b"
    r"\D{0,4}(\d{1,2})\b"
)
ABILITY_NAME = {
    "strength": "str", "str": "str",
    "dexterity": "dex", "dex": "dex",
    "constitution": "con", "con": "con",
    "intelligence": "int", "int": "int",
    "wisdom": "wis", "wis": "wis",
    "charisma": "cha", "cha": "cha",
}
NOTE_NAME_WORDS = (
    "quest", "geograph", "pantheon", "initialization", "summary",
    "instruction", "encounter",
)


def parse_markdown_character(text: str) -> dict:
    lines = text.splitlines()
    data = {
        "name": "", "race": "", "class": "", "level": 1,
        "background": "", "alignment": "", "experience": 0,
        "hp_max": None, "hp_current": None, "ac": None, "ac_note": "",
        "proficiency_bonus": 2, "speed": 30, "initiative": 0,
        "inventory": [], "attuned": [], "notes": "",
        "str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10,
    }
    subtitle = ""
    class_from_field = False
    level_from_field = False
    section = None
    section_lines = []

    def flush():
        nonlocal section_lines
        if section and section_lines and section not in ("inventory", "attuned", "abilities"):
            block = "\n".join(section_lines).strip()
            if not block:
                section_lines = []
                return
            if section == "notes":
                data["notes"] = (data["notes"] + "\n" + block).strip() if data["notes"] else block
            else:
                previous = data.get(section, "")
                data[section] = (previous + "\n" + block).strip() if previous else block
        section_lines = []

    for line in lines:
        stripped = line.strip()
        if not data["name"] and stripped.startswith("# "):
            heading = stripped[2:].strip()
            name, subtitle = _split_heading(heading)
            data["name"] = name
            continue

        if stripped.startswith(("## ", "### ")):
            flush()
            title = stripped.lstrip("#").strip()
            section = _section_key(title)
            continue

        if not stripped or stripped.startswith("|") or set(stripped) <= {"-", " "}:
            if section in ("notes", "spells", "features", "skills", "saving_throws", "actions", "proficiencies"):
                section_lines.append(stripped)
            continue

        label, value = _bold_label(stripped)
        free_text = section in (
            "inventory", "attuned", "notes", "spells", "features", "skills",
            "saving_throws", "actions", "proficiencies",
        )
        if label and not value and not free_text:
            flush()
            section = _section_key(label)
            continue
        if label and not free_text:
            handled = _apply_label(data, label, value)
            if label.startswith("class"):
                class_from_field = True
            if label.startswith("level"):
                level_from_field = True
            if not handled:
                _store_extra(data, label, value)
            if section == "abilities" or ABILITY_RE.search(stripped):
                _apply_abilities(data, stripped)
            continue

        if section == "abilities" or (section is None and ABILITY_RE.search(stripped)):
            if _apply_abilities(data, stripped):
                continue

        if section == "inventory":
            item = _clean_item(stripped)
            if item:
                data["inventory"].append(item)
            continue
        if section == "attuned":
            item = _clean_item(stripped)
            if item:
                data["attuned"].append(item)
            continue
        if section:
            section_lines.append(stripped)

    flush()
    if subtitle and not class_from_field:
        data["class"] = subtitle
        if not level_from_field:
            match = re.search(r"(?i)\blevel\s+(\d+)\b", subtitle)
            if match:
                data["level"] = int(match.group(1))
    if data["hp_current"] is None and data["hp_max"] is None:
        data["hp_current"] = 10
        data["hp_max"] = 10
    elif data["hp_current"] is None:
        data["hp_current"] = data["hp_max"]
    elif data["hp_max"] is None:
        data["hp_max"] = data["hp_current"]
    if data["ac"] is None:
        data["ac"] = 10
    if not data["ac_note"]:
        data.pop("ac_note", None)
    return data


def looks_like_character(path: Path, text: str) -> bool:
    if path.suffix.lower() != ".md":
        return False
    name = path.name.lower()
    if any(word in name for word in NOTE_NAME_WORDS):
        return False
    if "character sheet" in name:
        return True
    lowered = text.lower()
    return any(token in lowered for token in ("**race**", "**class**", "**hp**", "ability scores", "**str**"))


def import_documents(db, folder) -> str:
    """Store reference files in this campaign's notes. Character sheets are skipped."""
    folder = Path(folder)
    stored = 0
    skipped = 0
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".md", ".txt", ".csv"}:
            continue
        text = path.read_text(encoding="utf-8")
        if looks_like_character(path, text):
            skipped += 1
            continue
        _upsert_note(db, path.stem, text)
        stored += 1
    return f"Stored {stored} documents in this campaign. Skipped {skipped} character sheets."


def import_campaign_folder(db, folder) -> str:
    folder = Path(folder)
    characters = 0
    notes = 0
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            _upsert_note(db, path.stem, path.read_text(encoding="utf-8"))
            notes += 1
            continue
        if suffix not in {".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        if looks_like_character(path, text):
            data = parse_markdown_character(text)
            _upsert_character(db, data, is_player_for_new=False)
            characters += 1
        else:
            _upsert_note(db, path.stem, text)
            notes += 1
    return f"Imported {characters} characters and {notes} notes."


def _upsert_character(db, data: dict, *, is_player_for_new: bool):
    name = data.get("name") or "Unnamed"
    row = db.execute("SELECT id, is_player FROM characters WHERE name=?", (name,)).fetchone()
    if row:
        update_character(db, row["id"], name, data)
        return row["id"]
    return create_character(db, name, data, is_player=is_player_for_new)


def _upsert_note(db, title: str, content: str) -> None:
    row = db.execute("SELECT id FROM notes WHERE title=?", (title,)).fetchone()
    if row:
        db.execute("UPDATE notes SET content=? WHERE id=?", (content, row["id"]))
    else:
        db.execute("INSERT INTO notes (title, content) VALUES (?, ?)", (title, content))
    db.commit()


def _split_heading(heading: str):
    for separator in (" – ", " — ", " - "):
        if separator in heading:
            name, subtitle = heading.split(separator, 1)
            return name.strip(), subtitle.strip()
    return heading.strip(), ""


def _section_key(title: str) -> str:
    lowered = title.lower()
    if any(word in lowered for word in ("equipment", "inventory", "carried", "possessions")):
        return "inventory"
    if "attun" in lowered:
        return "attuned"
    if "ability score" in lowered or lowered.strip() in {"abilities", "ability scores"}:
        return "abilities"
    if "spell" in lowered:
        return "spells"
    if "feature" in lowered or "trait" in lowered:
        return "features"
    if "skill" in lowered:
        return "skills"
    if "saving" in lowered:
        return "saving_throws"
    if lowered.startswith("action"):
        return "actions"
    if "proficienc" in lowered:
        return "proficiencies"
    if any(word in lowered for word in ("quest", "note", "experience", "ready for")):
        return "notes"
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return slug or "notes"


def _bold_label(stripped: str):
    match = re.match(r"\*\*(.+?)\*\*\s*:?\s*(.*)", stripped)
    if not match:
        return None, None
    return match.group(1).strip().lower(), match.group(2).strip()


def _store_extra(data: dict, label: str, value: str) -> None:
    key = re.sub(r"[^a-z0-9]+", "_", label).strip("_") or "notes"
    previous = data.get(key, "")
    if isinstance(previous, str) and previous:
        data[key] = previous + "\n" + value
    else:
        data[key] = value


def _apply_label(data: dict, label: str, value: str) -> bool:
    if "race" in label and "class" not in label:
        data["race"] = value
    elif label.startswith("class"):
        data["class"] = value
        if not re.search(r"(?i)\blevel\b", label):
            match = re.search(r"\b(\d+)\b", value)
            if match and data["level"] == 1:
                data["level"] = int(match.group(1))
    elif label.startswith("level"):
        match = re.search(r"\b(\d+)\b", value)
        if match:
            data["level"] = int(match.group(1))
        xp = re.search(r"(?i)xp\s*[:=]?\s*(\d[\d,]*)", value)
        if xp:
            data["experience"] = int(xp.group(1).replace(",", ""))
    elif "background" in label:
        data["background"] = value
    elif "alignment" in label:
        data["alignment"] = value
    elif re.search(r"\bhp\b", label):
        numbers = [int(item) for item in re.findall(r"\d+", value)]
        if "/" in value and len(numbers) >= 2:
            data["hp_current"] = numbers[0]
            data["hp_max"] = numbers[1]
        elif numbers:
            data["hp_current"] = numbers[0]
            data["hp_max"] = numbers[0]
    elif re.search(r"\bac\b", label) or "armor class" in label:
        match = re.search(r"\d+", value)
        if match:
            data["ac"] = int(match.group(0))
            note = value[match.end():].strip(" -–—")
            if note:
                data["ac_note"] = note
    elif "speed" in label:
        match = re.search(r"\d+", value)
        if match:
            data["speed"] = int(match.group(0))
    elif "initiative" in label:
        match = re.search(r"[+-]?\d+", value)
        if match:
            data["initiative"] = int(match.group(0))
    elif "proficiency" in label and "bonus" in label:
        match = re.search(r"\d+", value)
        if match:
            data["proficiency_bonus"] = int(match.group(0))
    else:
        return False
    return True


def _apply_abilities(data: dict, text: str) -> bool:
    found = False
    for match in ABILITY_RE.finditer(text):
        key = ABILITY_NAME[match.group(1).lower()]
        data[key] = int(match.group(2))
        found = True
    return found


def _clean_item(text: str) -> str:
    item = text.strip()
    item = re.sub(r"^[-*•>\u2022]\s+", "", item)
    item = re.sub(r"^\d+\.\s+", "", item)
    item = re.sub(r"^\[[ xX]\]\s+", "", item)
    return item.strip()


class ImporterModule(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        ttk.Label(self, text="Import Character from Markdown", font=("Segoe UI", 12, "bold")).pack(pady=10)
        ttk.Button(self, text="Choose Markdown File (.md)", command=self.import_file).pack(pady=5)

    def import_file(self):
        path = filedialog.askopenfilename(filetypes=[("Markdown files", "*.md")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            data = parse_markdown_character(text)
            name = data.get("name") or Path(path).stem.replace("_", " ").title()
            data["name"] = name
            char_id = _upsert_character(self.app.db, data, is_player_for_new=True)
            messagebox.showinfo("Success", f"Imported '{name}' successfully!")
            self.app.characters_module.refresh()
            self.master.after(100, lambda cid=char_id: self.app.open_character_editor(cid))
            self.master.destroy()
        except Exception as exc:
            messagebox.showerror("Error", f"Import failed:\n{exc}")

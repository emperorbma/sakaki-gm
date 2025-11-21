# character_db.py
import json
from typing import List, Dict, Any, Optional

def create_character(db, name: str, data: dict, is_player: bool = True) -> int:
    """Insert a new character and return its ID"""
    cur = db.cursor()
    cur.execute(
        "INSERT INTO characters (name, data, is_player) VALUES (?, ?, ?)",
        (name, json.dumps(data, ensure_ascii=False), int(is_player))
    )
    db.commit()
    return cur.lastrowid


def update_character(db, char_id: int, name: str, data: dict) -> None:
    """Update existing character"""
    cur = db.cursor()
    cur.execute(
        "UPDATE characters SET name = ?, data = ? WHERE id = ?",
        (name, json.dumps(data, ensure_ascii=False), char_id)
    )
    db.commit()


def get_character(db, char_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    data["id"] = row["id"]
    data["name"] = row["name"]
    data["is_player"] = bool(row["is_player"])
    return data


def get_all_characters(db) -> List[Dict[str, Any]]:
    rows = db.execute("SELECT id, name, data FROM characters ORDER BY name").fetchall()
    characters = []
    for row in rows:
        try:
            data = json.loads(row["data"])
            data["id"] = row["id"]
            data["name"] = row["name"]
            characters.append(data)
        except:
            characters.append({"id": row["id"], "name": row["name"], "error": True})
    return characters


def delete_character(db, char_id: int) -> None:
    db.execute("DELETE FROM characters WHERE id = ?", (char_id,))
    db.commit()
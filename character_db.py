# character_db.py
import json
from typing import List, Dict, Any, Optional

def create_character(db, name: str, data: dict, campaign_role: str = "") -> int:
    """Insert a new character and return its ID"""
    cur = db.cursor()
    cur.execute(
        "INSERT INTO characters (name, data, campaign_role) VALUES (?, ?, ?)",
        (name, json.dumps(data, ensure_ascii=False), campaign_role.strip())
    )
    db.commit()
    return cur.lastrowid


def update_character(
    db,
    char_id: int,
    name: str,
    data: dict,
    campaign_role: Optional[str] = None,
) -> None:
    """Update existing character"""
    cur = db.cursor()
    serialized = json.dumps(data, ensure_ascii=False)
    if campaign_role is None:
        cur.execute(
            "UPDATE characters SET name = ?, data = ? WHERE id = ?",
            (name, serialized, char_id),
        )
    else:
        cur.execute(
            "UPDATE characters SET name = ?, data = ?, campaign_role = ? WHERE id = ?",
            (name, serialized, campaign_role.strip(), char_id),
        )
    db.commit()


def get_character(db, char_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    data["id"] = row["id"]
    data["name"] = row["name"]
    data["campaign_role"] = row["campaign_role"]
    return data


def get_all_characters(db) -> List[Dict[str, Any]]:
    rows = db.execute(
        "SELECT id, name, data, campaign_role FROM characters ORDER BY name"
    ).fetchall()
    characters = []
    for row in rows:
        try:
            data = json.loads(row["data"])
            data["id"] = row["id"]
            data["name"] = row["name"]
            data["campaign_role"] = row["campaign_role"]
            characters.append(data)
        except:
            characters.append({"id": row["id"], "name": row["name"], "error": True})
    return characters


def delete_character(db, char_id: int) -> None:
    db.execute("DELETE FROM characters WHERE id = ?", (char_id,))
    db.commit()
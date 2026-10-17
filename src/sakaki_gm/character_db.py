# character_db.py
import json
from typing import List, Dict, Any, Optional

def create_character(db, name: str, data: dict, campaign_role: str = "") -> int:
    """Insert a new character and return its ID"""
    with db:
        cur = db.execute(
            "INSERT INTO characters (name, data, campaign_role) VALUES (?, ?, ?)",
            (name, json.dumps(data, ensure_ascii=False), campaign_role.strip()),
        )
        char_id = cur.lastrowid
        object_id = f"character:{char_id}"
        inventory_id = f"inventory:{char_id}"
        db.execute(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description
            ) VALUES (?, ?, 'character', 'unspecified', '')
            """,
            (object_id, name),
        )
        db.execute(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description
            ) VALUES (?, ?, 'container', 'unspecified', '')
            """,
            (inventory_id, f"{name}'s Inventory"),
        )
        db.execute(
            "UPDATE characters SET object_id=? WHERE id=?",
            (object_id, char_id),
        )
        db.execute(
            """
            INSERT INTO object_links (
                relation, from_object_id, to_object_id, properties
            ) VALUES ('contains', ?, ?, ?)
            """,
            (object_id, inventory_id, json.dumps({"role": "inventory"})),
        )
    return char_id


def update_character(
    db,
    char_id: int,
    name: str,
    data: dict,
    campaign_role: Optional[str] = None,
) -> None:
    """Update existing character"""
    serialized = json.dumps(data, ensure_ascii=False)
    with db:
        if campaign_role is None:
            db.execute(
                "UPDATE characters SET name = ?, data = ? WHERE id = ?",
                (name, serialized, char_id),
            )
        else:
            db.execute(
                """
                UPDATE characters
                SET name = ?, data = ?, campaign_role = ?
                WHERE id = ?
                """,
                (name, serialized, campaign_role.strip(), char_id),
            )
        db.execute(
            """
            UPDATE objects SET name=?
            WHERE object_id=(
                SELECT object_id FROM characters WHERE id=?
            )
            """,
            (name, char_id),
        )
        db.execute(
            """
            UPDATE objects SET name=?
            WHERE object_id=? AND object_type='container'
            """,
            (f"{name}'s Inventory", f"inventory:{char_id}"),
        )


def get_character(db, char_id: int) -> Optional[Dict[str, Any]]:
    row = db.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    data["id"] = row["id"]
    data["name"] = row["name"]
    data["campaign_role"] = row["campaign_role"]
    data["object_id"] = row["object_id"]
    return data


def get_all_characters(db) -> List[Dict[str, Any]]:
    rows = db.execute(
        "SELECT id, name, data, campaign_role, object_id "
        "FROM characters ORDER BY name"
    ).fetchall()
    characters = []
    for row in rows:
        try:
            data = json.loads(row["data"])
            data["id"] = row["id"]
            data["name"] = row["name"]
            data["campaign_role"] = row["campaign_role"]
            data["object_id"] = row["object_id"]
            characters.append(data)
        except:
            characters.append({
                "id": row["id"],
                "name": row["name"],
                "object_id": row["object_id"],
                "error": True,
            })
    return characters


def delete_character(db, char_id: int) -> None:
    with db:
        row = db.execute(
            "SELECT object_id FROM characters WHERE id=?",
            (char_id,),
        ).fetchone()
        if row is None:
            return
        db.execute("DELETE FROM objects WHERE object_id=?", (f"inventory:{char_id}",))
        db.execute("DELETE FROM characters WHERE id=?", (char_id,))
        db.execute("DELETE FROM objects WHERE object_id=?", (row["object_id"],))
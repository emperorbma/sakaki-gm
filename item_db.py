"""Campaign item objects and inventory-container relationships."""
import json
import sqlite3
import uuid


def get_items(db):
    return db.execute(
        """
        SELECT object_id AS id, name, description
        FROM objects
        WHERE object_type='item'
        ORDER BY name COLLATE NOCASE, object_id
        """
    ).fetchall()


def create_item(db, name: str, description: str = "") -> str:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    object_id = f"catalog-item:{uuid.uuid4().hex}"
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description
        ) VALUES (?, ?, 'item', 'unspecified', ?)
        """,
        (object_id, name, description.strip()),
    )
    db.commit()
    return object_id


def update_item(db, item_id: str, name: str, description: str = "") -> None:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    db.execute(
        """
        UPDATE objects SET name=?, description=?
        WHERE object_id=? AND object_type='item'
        """,
        (name, description.strip(), item_id),
    )
    db.commit()


def delete_item(db, item_id: str) -> None:
    linked = db.execute(
        """
        SELECT 1 FROM object_links
        WHERE from_object_id=? OR to_object_id=?
        LIMIT 1
        """,
        (item_id, item_id),
    ).fetchone()
    if linked:
        raise sqlite3.IntegrityError("Cannot delete an item object with graph relations.")
    db.execute(
        "DELETE FROM objects WHERE object_id=? AND object_type='item'",
        (item_id,),
    )
    db.commit()


def get_character_items(db, character_id: int):
    container_id = get_character_inventory_container(db, character_id)
    rows = db.execute(
        """
        SELECT item.object_id AS id, item.name, item.description,
               link.properties
        FROM object_links AS link
        JOIN objects AS item ON item.object_id=link.to_object_id
        WHERE link.from_object_id=? AND link.relation='contains'
          AND item.object_type='item'
        ORDER BY item.name COLLATE NOCASE, item.object_id
        """,
        (container_id,),
    ).fetchall()
    assignments = []
    for row in rows:
        properties = json.loads(row["properties"])
        assignments.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "quantity": properties["quantity"],
            "attuned": int(bool(properties.get("attuned", False))),
            "equipment_location": properties.get("equipment_location", ""),
        })
    return assignments


def set_character_items(db, character_id: int, assignments) -> None:
    container_id = get_character_inventory_container(db, character_id)
    normalized = []
    seen = set()
    for item_id, quantity, attuned, equipment_location in assignments:
        if item_id in seen:
            raise sqlite3.IntegrityError("A character cannot have duplicate item assignments.")
        seen.add(item_id)
        try:
            quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError("Item quantity must be a positive whole number.") from exc
        if quantity < 1:
            raise ValueError("Item quantity must be a positive whole number.")
        item = db.execute(
            "SELECT 1 FROM objects WHERE object_id=? AND object_type='item'",
            (item_id,),
        ).fetchone()
        if item is None:
            raise sqlite3.IntegrityError(f"Unknown catalog item object: {item_id}")
        normalized.append((
            item_id,
            json.dumps({
                "quantity": quantity,
                "attuned": bool(attuned),
                "equipment_location": equipment_location.strip(),
            }, ensure_ascii=False),
        ))

    with db:
        db.execute(
            """
            DELETE FROM object_links
            WHERE relation='contains' AND from_object_id=?
              AND to_object_id IN (
                  SELECT object_id FROM objects WHERE object_type='item'
              )
            """,
            (container_id,),
        )
        db.executemany(
            """
            INSERT INTO object_links (
                relation, from_object_id, to_object_id, properties
            ) VALUES ('contains', ?, ?, ?)
            """,
            [
                (container_id, item_id, properties)
                for item_id, properties in normalized
            ],
        )


def get_character_inventory_container(db, character_id: int) -> str:
    character = db.execute(
        "SELECT object_id FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if character is None or character["object_id"] is None:
        raise ValueError(f"Character {character_id} has no graph object.")
    containers = []
    for row in db.execute(
        """
        SELECT link.to_object_id, link.properties, object.object_type
        FROM object_links AS link
        JOIN objects AS object ON object.object_id=link.to_object_id
        WHERE link.from_object_id=? AND link.relation='contains'
        """,
        (character["object_id"],),
    ):
        if (
            row["object_type"] == "container"
            and json.loads(row["properties"]).get("role") == "inventory"
        ):
            containers.append(row["to_object_id"])
    if len(containers) != 1:
        raise ValueError(
            f"Character {character_id} must have exactly one inventory container."
        )
    return containers[0]

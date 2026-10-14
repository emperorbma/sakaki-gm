"""Campaign item catalog and character inventory persistence."""


def get_items(db):
    return db.execute(
        "SELECT id, name, description FROM items ORDER BY name COLLATE NOCASE"
    ).fetchall()


def create_item(db, name: str, description: str = "") -> int:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    cursor = db.execute(
        "INSERT INTO items (name, description) VALUES (?, ?)",
        (name, description.strip()),
    )
    db.commit()
    return cursor.lastrowid


def update_item(db, item_id: int, name: str, description: str = "") -> None:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    db.execute(
        "UPDATE items SET name=?, description=? WHERE id=?",
        (name, description.strip(), item_id),
    )
    db.commit()


def delete_item(db, item_id: int) -> None:
    db.execute("DELETE FROM items WHERE id=?", (item_id,))
    db.commit()


def get_character_items(db, character_id: int):
    return db.execute(
        """
        SELECT items.id, items.name, items.description,
               character_items.quantity, character_items.attuned
        FROM character_items
        JOIN items ON items.id = character_items.item_id
        WHERE character_items.character_id = ?
        ORDER BY items.name COLLATE NOCASE
        """,
        (character_id,),
    ).fetchall()


def set_character_items(db, character_id: int, assignments) -> None:
    with db:
        db.execute(
            "DELETE FROM character_items WHERE character_id=?",
            (character_id,),
        )
        db.executemany(
            """
            INSERT INTO character_items (character_id, item_id, quantity, attuned)
            VALUES (?, ?, ?, ?)
            """,
            [
                (character_id, item_id, quantity, int(attuned))
                for item_id, quantity, attuned in assignments
            ],
        )

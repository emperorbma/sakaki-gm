"""Campaign quests and their character assignments."""


def get_quests(db):
    return db.execute(
        "SELECT id, name, description, status FROM quests ORDER BY name COLLATE NOCASE"
    ).fetchall()


def get_quest_characters(db, quest_id: int):
    return db.execute(
        """
        SELECT characters.id, characters.name
        FROM quest_characters
        JOIN characters ON characters.id = quest_characters.character_id
        WHERE quest_characters.quest_id = ?
        ORDER BY characters.name COLLATE NOCASE
        """,
        (quest_id,),
    ).fetchall()


def save_quest(
    db,
    quest_id,
    name: str,
    description: str,
    character_ids,
    status: str = "",
) -> int:
    """Create or update a quest and replace its character assignments atomically."""
    name = name.strip()
    if not name:
        raise ValueError("Quest name cannot be empty.")
    ids = list(dict.fromkeys(int(character_id) for character_id in character_ids))
    with db:
        if quest_id is None:
            cursor = db.execute(
                "INSERT INTO quests (name, description, status) VALUES (?, ?, ?)",
                (name, description.strip(), status.strip()),
            )
            quest_id = cursor.lastrowid
        else:
            db.execute(
                "UPDATE quests SET name=?, description=?, status=? WHERE id=?",
                (name, description.strip(), status.strip(), quest_id),
            )
        db.execute("DELETE FROM quest_characters WHERE quest_id=?", (quest_id,))
        db.executemany(
            "INSERT INTO quest_characters (quest_id, character_id) VALUES (?, ?)",
            [(quest_id, character_id) for character_id in ids],
        )
    return quest_id


def delete_quest(db, quest_id: int) -> None:
    db.execute("DELETE FROM quests WHERE id=?", (quest_id,))
    db.commit()

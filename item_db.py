"""Campaign item objects and inventory-container relationships."""
import json
import sqlite3
import uuid


def get_items(db):
    rows = db.execute(
        """
        SELECT object_id AS id, name, description, properties
        FROM objects
        WHERE object_type='item'
        ORDER BY name COLLATE NOCASE, object_id
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "is_container": "container" in json.loads(row["properties"]).get(
                "tags", []
            ),
        }
        for row in rows
    ]


def create_item(
    db, name: str, description: str = "", is_container: bool = False
) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    object_id = f"catalog-item:{uuid.uuid4().hex}"
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description, properties
        ) VALUES (?, ?, 'item', 'unspecified', ?, ?)
        """,
        (
            object_id,
            name,
            description.strip(),
            json.dumps({"tags": ["container"]} if is_container else {}),
        ),
    )
    db.commit()
    return object_id


def update_item(
    db,
    item_id: str,
    name: str,
    description: str = "",
    is_container: bool = False,
) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    db.execute(
        """
        UPDATE objects SET name=?, description=?, properties=?
        WHERE object_id=? AND object_type='item'
        """,
        (
            name,
            description.strip(),
            json.dumps({"tags": ["container"]} if is_container else {}),
            item_id,
        ),
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
    tree = get_character_inventory_tree(db, character_id)
    assignments = []

    def collect(container, path):
        for child in container["children"]:
            if child["object_type"] == "container":
                if child["object_properties"].get("catalog_item_id"):
                    assignments.append({
                        "id": child["object_id"],
                        "name": child["name"],
                        "description": child["description"],
                        "is_container": True,
                        "quantity": child["properties"].get("quantity", 1),
                        "attuned": int(
                            bool(child["properties"].get("attuned", False))
                        ),
                        "equipment_location": child["properties"].get(
                            "equipment_location", ""
                        ),
                        "container_path": " > ".join(path),
                    })
                collect(child, (*path, child["name"]))
            elif child["object_type"] == "item":
                assignments.append({
                    "id": child["object_id"],
                    "name": child["name"],
                    "description": child["description"],
                    "is_container": False,
                    "quantity": child["properties"].get("quantity", 1),
                    "attuned": int(bool(child["properties"].get("attuned", False))),
                    "equipment_location": child["properties"].get(
                        "equipment_location", ""
                    ),
                    "container_path": " > ".join(path),
                })

    collect(tree, [tree["name"]])
    return assignments


def get_character_inventory_tree(db, character_id: int):
    root_id = get_character_inventory_container(db, character_id)
    objects = {
        row["object_id"]: dict(row)
        for row in db.execute("SELECT * FROM objects")
    }
    links = {}
    for row in db.execute(
        """
        SELECT from_object_id, to_object_id, properties
        FROM object_links WHERE relation='contains'
        ORDER BY id
        """
    ):
        links.setdefault(row["from_object_id"], []).append({
            "object_id": row["to_object_id"],
            "properties": json.loads(row["properties"]),
        })

    def build(object_id: str, ancestors: frozenset[str]):
        if object_id in ancestors:
            raise ValueError(f"Containment cycle found at object {object_id!r}.")
        obj = objects.get(object_id)
        if obj is None:
            raise ValueError(f"Containment references missing object {object_id!r}.")
        branch = ancestors | {object_id}
        result = {
            "object_id": object_id,
            "name": obj["name"],
            "object_type": obj["object_type"],
            "description": obj["description"],
            "object_properties": json.loads(obj["properties"]),
            "properties": {},
            "children": [],
        }
        for link in links.get(object_id, []):
            child = build(link["object_id"], branch)
            child["properties"] = link["properties"]
            result["children"].append(child)
        return result

    return build(root_id, frozenset())


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


def save_character_inventory(db, character_id: int, children) -> None:
    root_id = get_character_inventory_container(db, character_id)
    nodes = []
    links = []
    seen_objects = set()

    def collect(parent_id: str, entries):
        for entry in entries:
            object_id = entry["object_id"]
            object_type = entry["object_type"]
            if object_id in seen_objects:
                raise ValueError(f"Duplicate inventory object {object_id!r}.")
            seen_objects.add(object_id)
            if object_type == "container":
                is_new = entry.get("new_container", False)
                if is_new:
                    object_id = f"inventory-container:{object_id.removeprefix('new-container:')}"
                    object_properties = dict(entry.get("object_properties", {}))
                    nodes.append((
                        object_id,
                        entry["name"].strip(),
                        entry.get("description", ""),
                        json.dumps(object_properties, ensure_ascii=False),
                    ))
                    template_id = object_properties.get("catalog_item_id")
                    if template_id:
                        template = db.execute(
                            "SELECT 1 FROM objects "
                            "WHERE object_id=? AND object_type='item'",
                            (template_id,),
                        ).fetchone()
                        if template is None:
                            raise ValueError(
                                f"Unknown container catalog item {template_id!r}."
                            )
                        links.append(
                            ("instance_of", object_id, template_id, {})
                        )
                else:
                    existing = db.execute(
                        "SELECT 1 FROM objects WHERE object_id=? AND object_type='container'",
                        (object_id,),
                    ).fetchone()
                    if existing is None:
                        raise ValueError(f"Unknown inventory container {object_id!r}.")
                properties = dict(entry.get("properties", {}))
                if is_new:
                    properties.setdefault("role", "container")
                links.append(("contains", parent_id, object_id, properties))
                collect(object_id, entry.get("children", []))
            elif object_type == "item":
                item = db.execute(
                    "SELECT 1 FROM objects WHERE object_id=? AND object_type='item'",
                    (object_id,),
                ).fetchone()
                if item is None:
                    raise ValueError(f"Unknown catalog item {object_id!r}.")
                properties = dict(entry.get("properties", {}))
                quantity = int(properties.get("quantity", 1))
                if quantity < 1:
                    raise ValueError("Item quantity must be a positive whole number.")
                properties["quantity"] = quantity
                properties["attuned"] = bool(properties.get("attuned", False))
                properties["equipment_location"] = str(
                    properties.get("equipment_location", "")
                ).strip()
                links.append(("contains", parent_id, object_id, properties))
            else:
                raise ValueError(
                    f"Unsupported object type {object_type!r} in inventory."
                )

    collect(root_id, children)

    existing_containers = set()
    pending = [root_id]
    inspected = set()
    while pending:
        parent_id = pending.pop()
        if parent_id in inspected:
            continue
        inspected.add(parent_id)
        for row in db.execute(
            """
            SELECT object_links.to_object_id, objects.object_type
            FROM object_links
            JOIN objects ON objects.object_id=object_links.to_object_id
            WHERE object_links.relation='contains'
              AND object_links.from_object_id=?
            """,
            (parent_id,),
        ):
            child_id = row["to_object_id"]
            if row["object_type"] == "container" and child_id != root_id:
                existing_containers.add(child_id)
                pending.append(child_id)

    desired_containers = {
        node[0] for node in nodes
    } | {
        link[2] for link in links
        if db.execute(
            "SELECT 1 FROM objects WHERE object_id=? AND object_type='container'",
            (link[2],),
        ).fetchone()
    }
    with db:
        db.executemany(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description, properties
            ) VALUES (?, ?, 'container', 'unspecified', ?, ?)
            """,
            nodes,
        )
        db.execute(
            """
            WITH RECURSIVE descendants(object_id) AS (
                SELECT ?
                UNION
                SELECT object_links.to_object_id
                FROM object_links
                JOIN descendants
                  ON object_links.from_object_id=descendants.object_id
                WHERE object_links.relation='contains'
            )
            DELETE FROM object_links
            WHERE relation='contains'
              AND from_object_id IN (SELECT object_id FROM descendants)
            """,
            (root_id,),
        )
        db.executemany(
            """
            INSERT INTO object_links (
                relation, from_object_id, to_object_id, properties
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    relation,
                    parent_id,
                    child_id,
                    json.dumps(properties, ensure_ascii=False),
                )
                for relation, parent_id, child_id, properties in links
            ],
        )
        for container_id in existing_containers - desired_containers:
            if container_id.startswith("inventory-container:"):
                db.execute(
                    """
                    DELETE FROM object_links
                    WHERE relation='instance_of' AND from_object_id=?
                    """,
                    (container_id,),
                )
            references = db.execute(
                """
                SELECT 1 FROM object_links
                WHERE from_object_id=? OR to_object_id=? LIMIT 1
                """,
                (container_id, container_id),
            ).fetchone()
            if references is None:
                db.execute("DELETE FROM objects WHERE object_id=?", (container_id,))


def get_character_inventory_container(db, character_id: int) -> str:
    character = db.execute(
        "SELECT id, name, object_id FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if character is None:
        raise ValueError(f"Unknown character {character_id}.")

    with db:
        object_id = character["object_id"] or f"character:{character_id}"
        graph_object = db.execute(
            "SELECT 1 FROM objects WHERE object_id=?",
            (object_id,),
        ).fetchone()
        if graph_object is None:
            db.execute(
                """
                INSERT INTO objects (
                    object_id, name, object_type, geography_kind, description
                ) VALUES (?, ?, 'character', 'unspecified', '')
                """,
                (object_id, character["name"]),
            )
        db.execute(
            "UPDATE characters SET object_id=? WHERE id=?",
            (object_id, character_id),
        )
        containers = [
            row["to_object_id"]
            for row in db.execute(
                """
                SELECT link.to_object_id
                FROM object_links AS link
                JOIN objects AS object ON object.object_id=link.to_object_id
                WHERE link.from_object_id=? AND link.relation='contains'
                  AND object.object_type='container'
                  AND json_extract(link.properties, '$.role')='inventory'
                """,
                (object_id,),
            )
        ]
        if not containers:
            container_id = f"inventory:{character_id}"
            exists = db.execute(
                "SELECT 1 FROM objects WHERE object_id=?",
                (container_id,),
            ).fetchone()
            if exists is None:
                db.execute(
                    """
                    INSERT INTO objects (
                        object_id, name, object_type, geography_kind, description
                    ) VALUES (?, ?, 'container', 'unspecified', '')
                    """,
                    (container_id, f"{character['name']}'s Inventory"),
                )
            db.execute(
                """
                INSERT INTO object_links (
                    relation, from_object_id, to_object_id, properties
                ) VALUES ('contains', ?, ?, ?)
                """,
                (object_id, container_id, json.dumps({"role": "inventory"})),
            )
            containers = [container_id]
    if len(containers) != 1:
        raise ValueError(
            f"Character {character_id} must have exactly one inventory container."
        )
    return containers[0]

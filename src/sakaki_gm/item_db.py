"""Campaign item objects and inventory-container relationships."""
import json
import sqlite3
import uuid

ITEM_INSTANCE_TYPE = "item_instance"
ITEM_INSTANCE_PREFIX = "item-instance:"
CONTAINER_INSTANCE_PREFIX = "inventory-container:"


def is_customizable_owned_id(object_id: str, object_type: str | None = None) -> bool:
    """True for owned item/container copies the customize utility can edit."""
    object_id = str(object_id)
    if object_id.startswith(ITEM_INSTANCE_PREFIX):
        return object_type in (None, ITEM_INSTANCE_TYPE)
    if object_id.startswith(CONTAINER_INSTANCE_PREFIX):
        return object_type in (None, "container")
    return False


def _parse_properties(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _with_container_tag(properties: dict | None, is_container: bool) -> dict:
    """Return a properties dict with the container tag synced to ``is_container``."""
    props = dict(properties or {})
    tags = props.get("tags")
    if not isinstance(tags, list):
        tags = []
    else:
        tags = [tag for tag in tags if isinstance(tag, str)]
    has_container = "container" in tags
    if is_container and not has_container:
        tags = [*tags, "container"]
    elif not is_container and has_container:
        tags = [tag for tag in tags if tag != "container"]
    if tags:
        props["tags"] = tags
    else:
        props.pop("tags", None)
    return props


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def is_item_instance_id(object_id: str) -> bool:
    return str(object_id).startswith(ITEM_INSTANCE_PREFIX)


def archetype_id_for_instance(db, instance_id: str) -> str | None:
    row = db.execute(
        """
        SELECT to_object_id
        FROM object_links
        WHERE relation='instance_of' AND from_object_id=?
        ORDER BY id
        LIMIT 1
        """,
        (instance_id,),
    ).fetchone()
    return row["to_object_id"] if row else None


def resolve_item(db, object_id: str) -> dict | None:
    """Return the practical owned-copy view: archetype defaults with overrides.

    Works for item instances and inventory-container copies alike.
    """
    row = db.execute(
        "SELECT object_id, name, description, object_type, properties "
        "FROM objects WHERE object_id=?",
        (object_id,),
    ).fetchone()
    if row is None:
        return None
    overrides = _parse_properties(row["properties"])
    archetype = None
    archetype_id = None
    if is_customizable_owned_id(row["object_id"], row["object_type"]):
        archetype_id = archetype_id_for_instance(db, object_id)
        if archetype_id:
            archetype = db.execute(
                "SELECT object_id, name, description, properties "
                "FROM objects WHERE object_id=? AND object_type='item'",
                (archetype_id,),
            ).fetchone()
    base_props = _parse_properties(archetype["properties"]) if archetype else {}
    # Name/description overrides live as explicit keys; otherwise use archetype.
    if "name" in overrides:
        name = str(overrides["name"])
    elif archetype is not None:
        name = archetype["name"]
    else:
        name = row["name"]
    if "description" in overrides:
        description = str(overrides["description"])
    elif archetype is not None:
        description = archetype["description"]
    else:
        description = row["description"]
    prop_overrides = {
        key: value
        for key, value in overrides.items()
        if key not in {"name", "description"}
    }
    merged = _deep_merge(base_props, prop_overrides)
    is_container = (
        row["object_type"] == "container"
        or "container" in merged.get("tags", [])
    )
    return {
        "id": row["object_id"],
        "object_id": row["object_id"],
        "object_type": row["object_type"],
        "name": name,
        "description": description,
        "properties": merged,
        "archetype_id": archetype_id,
        "is_container": is_container,
        "is_unique": archetype_id is None
        and is_customizable_owned_id(row["object_id"], row["object_type"]),
    }


def create_item_instance(
    db,
    archetype_id: str,
    *,
    overrides: dict | None = None,
    commit: bool = False,
) -> str:
    archetype = db.execute(
        "SELECT object_id, name, description, properties "
        "FROM objects WHERE object_id=? AND object_type='item'",
        (archetype_id,),
    ).fetchone()
    if archetype is None:
        raise sqlite3.IntegrityError(f"Unknown catalog item object: {archetype_id}")
    instance_id = f"{ITEM_INSTANCE_PREFIX}{uuid.uuid4().hex}"
    props = dict(overrides or {})
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description, properties
        ) VALUES (?, ?, ?, 'unspecified', ?, ?)
        """,
        (
            instance_id,
            archetype["name"],
            ITEM_INSTANCE_TYPE,
            "",
            json.dumps(props, ensure_ascii=False),
        ),
    )
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES ('instance_of', ?, ?, '{}', '')
        """,
        (instance_id, archetype_id),
    )
    if commit:
        db.commit()
    return instance_id


def list_item_instances(db, archetype_id: str | None = None) -> list[dict]:
    """Return owned item and container copies, optionally filtered by archetype."""
    if archetype_id:
        rows = db.execute(
            """
            SELECT instance.object_id, instance.name, instance.description,
                   instance.properties, instance.object_type,
                   link.to_object_id AS archetype_id
            FROM objects AS instance
            JOIN object_links AS link
              ON link.from_object_id = instance.object_id
             AND link.relation = 'instance_of'
            WHERE link.to_object_id = ?
              AND (
                  instance.object_type = ?
                  OR (
                      instance.object_type = 'container'
                      AND instance.object_id LIKE ?
                  )
              )
            ORDER BY instance.name COLLATE NOCASE, instance.object_id
            """,
            (
                archetype_id,
                ITEM_INSTANCE_TYPE,
                f"{CONTAINER_INSTANCE_PREFIX}%",
            ),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT instance.object_id, instance.name, instance.description,
                   instance.properties, instance.object_type,
                   (
                       SELECT link.to_object_id
                       FROM object_links AS link
                       WHERE link.from_object_id = instance.object_id
                         AND link.relation = 'instance_of'
                       ORDER BY link.id
                       LIMIT 1
                   ) AS archetype_id
            FROM objects AS instance
            WHERE instance.object_type = ?
               OR (
                   instance.object_type = 'container'
                   AND instance.object_id LIKE ?
               )
            ORDER BY instance.name COLLATE NOCASE, instance.object_id
            """,
            (ITEM_INSTANCE_TYPE, f"{CONTAINER_INSTANCE_PREFIX}%"),
        ).fetchall()
    results = []
    for row in rows:
        resolved = resolve_item(db, row["object_id"]) or {}
        results.append({
            "id": row["object_id"],
            "name": resolved.get("name", row["name"]),
            "description": resolved.get("description", row["description"]),
            "properties": resolved.get("properties", {}),
            "object_type": row["object_type"],
            "archetype_id": row["archetype_id"],
            "locations": item_instance_locations(db, row["object_id"]),
            "is_unique": row["archetype_id"] is None,
            "is_container": row["object_type"] == "container"
            or bool(resolved.get("is_container")),
        })
    return results


def item_instance_locations(db, instance_id: str) -> list[str]:
    """Human-readable containment paths for an owned instance."""
    parents = db.execute(
        """
        SELECT parent.object_id, parent.name, parent.object_type
        FROM object_links AS link
        JOIN objects AS parent ON parent.object_id = link.from_object_id
        WHERE link.relation = 'contains' AND link.to_object_id = ?
        ORDER BY link.id
        """,
        (instance_id,),
    ).fetchall()
    paths = []
    for parent in parents:
        path = [parent["name"]]
        current = parent["object_id"]
        seen = {instance_id, current}
        while True:
            row = db.execute(
                """
                SELECT grand.object_id, grand.name, grand.object_type,
                       link.properties
                FROM object_links AS link
                JOIN objects AS grand ON grand.object_id = link.from_object_id
                WHERE link.relation = 'contains' AND link.to_object_id = ?
                ORDER BY link.id
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            if row is None or row["object_id"] in seen:
                break
            seen.add(row["object_id"])
            path.append(row["name"])
            current = row["object_id"]
            if row["object_type"] == "character":
                break
        paths.append(" > ".join(reversed(path)))
    return paths


def update_item_instance(
    db,
    instance_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    property_overrides: dict | None = None,
    properties: dict | None = None,
    commit: bool = True,
) -> None:
    """Persist per-copy fields on an owned item or container instance.

    While linked to an archetype, name/description are stored as overrides so
    clearing them later can fall back to defaults. Detached uniques store the
    values on the object row itself.

    Pass ``properties`` for the desired *resolved* property sets (weapon,
    armor, tags, …). Linked copies store only the override diff vs the
    archetype; uniques store the full dict. ``property_overrides`` still
    merges individual keys into the stored override blob.
    """
    row = db.execute(
        "SELECT object_id, name, description, properties, object_type "
        "FROM objects WHERE object_id=?",
        (instance_id,),
    ).fetchone()
    if row is None or not is_customizable_owned_id(row["object_id"], row["object_type"]):
        raise ValueError(f"Unknown owned copy {instance_id!r}.")
    overrides = _parse_properties(row["properties"])
    archetype_id = archetype_id_for_instance(db, instance_id)
    object_name = row["name"]
    object_description = row["description"]
    if archetype_id:
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Instance name cannot be empty.")
            overrides["name"] = name
            object_name = name
        if description is not None:
            overrides["description"] = description
            object_description = description
    else:
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Instance name cannot be empty.")
            object_name = name
            overrides.pop("name", None)
        if description is not None:
            object_description = description
            overrides.pop("description", None)
    if properties is not None:
        if not isinstance(properties, dict):
            raise ValueError("Instance properties must be a JSON object.")
        kind = {
            key: value
            for key, value in properties.items()
            if key not in {"name", "description"}
        }
        name_desc = {
            key: overrides[key]
            for key in ("name", "description")
            if key in overrides
        }
        if archetype_id:
            archetype = db.execute(
                "SELECT properties FROM objects WHERE object_id=? AND object_type='item'",
                (archetype_id,),
            ).fetchone()
            base = _parse_properties(archetype["properties"] if archetype else {})
            overrides = {**name_desc, **_override_diff(base, kind)}
        else:
            overrides = kind
    elif property_overrides is not None:
        for key, value in property_overrides.items():
            if key in {"name", "description"}:
                continue
            overrides[key] = value
    db.execute(
        """
        UPDATE objects
        SET name=?, description=?, properties=?
        WHERE object_id=?
        """,
        (
            object_name,
            object_description,
            json.dumps(overrides, ensure_ascii=False),
            instance_id,
        ),
    )
    if commit:
        db.commit()


def detach_item_instance(db, instance_id: str, *, commit: bool = True) -> dict:
    """Bake resolved values onto the owned copy and remove instance_of.

    Works for item instances and inventory-container copies. The copy becomes
    unique: further archetype edits no longer apply.
    """
    resolved = resolve_item(db, instance_id)
    if resolved is None:
        raise ValueError(f"Unknown owned copy {instance_id!r}.")
    if not is_customizable_owned_id(instance_id, resolved["object_type"]):
        raise ValueError(f"Object {instance_id!r} is not a customizable owned copy.")
    if not resolved["archetype_id"]:
        return resolved
    baked = dict(resolved["properties"])
    # Name/description live on the row for uniques; drop override keys.
    baked.pop("name", None)
    baked.pop("description", None)
    db.execute(
        """
        UPDATE objects
        SET name=?, description=?, properties=?
        WHERE object_id=?
        """,
        (
            resolved["name"],
            resolved["description"],
            json.dumps(baked, ensure_ascii=False),
            instance_id,
        ),
    )
    db.execute(
        """
        DELETE FROM object_links
        WHERE relation='instance_of' AND from_object_id=?
        """,
        (instance_id,),
    )
    if commit:
        db.commit()
    return resolve_item(db, instance_id)


def detach_instances_of_archetype(db, archetype_id: str) -> list[str]:
    """Detach every owned copy of an archetype. Returns detached instance ids."""
    instances = list_item_instances(db, archetype_id=archetype_id)
    detached = []
    with db:
        for item in instances:
            detach_item_instance(db, item["id"], commit=False)
            detached.append(item["id"])
    return detached


def _override_diff(base: dict, current: dict) -> dict:
    """Keep only keys (and nested keys) that differ from archetype defaults."""
    diff = {}
    for key, value in current.items():
        if key not in base:
            diff[key] = value
            continue
        base_value = base[key]
        if isinstance(value, dict) and isinstance(base_value, dict):
            nested = _override_diff(base_value, value)
            if nested:
                diff[key] = nested
        elif value != base_value:
            diff[key] = value
    return diff


def attach_item_instance(
    db,
    instance_id: str,
    archetype_id: str,
    *,
    commit: bool = True,
) -> dict:
    """Link an owned item/container copy to an archetype, keeping overrides.

    Replaces any existing instance_of. Values that still match the archetype
    are cleared so live defaults apply again; divergent name/description/
    property keys remain as overrides.
    """
    row = db.execute(
        "SELECT object_id, name, description, properties, object_type "
        "FROM objects WHERE object_id=?",
        (instance_id,),
    ).fetchone()
    if row is None or not is_customizable_owned_id(row["object_id"], row["object_type"]):
        raise ValueError(f"Unknown owned copy {instance_id!r}.")
    archetype = db.execute(
        "SELECT object_id, name, description, properties "
        "FROM objects WHERE object_id=? AND object_type='item'",
        (archetype_id,),
    ).fetchone()
    if archetype is None:
        raise ValueError(f"Unknown catalog item object: {archetype_id}")
    archetype_props = _parse_properties(archetype["properties"])
    archetype_is_container = "container" in archetype_props.get("tags", [])
    if row["object_type"] == "container" and not archetype_is_container:
        raise ValueError(
            f"Container copy {instance_id!r} can only attach to a container archetype."
        )
    if row["object_type"] == ITEM_INSTANCE_TYPE and archetype_is_container:
        raise ValueError(
            f"Item copy {instance_id!r} cannot attach to a container archetype."
        )

    # Resolve as if unique first (current stored state), then diff vs archetype.
    current_props = _parse_properties(row["properties"])
    current_name = str(current_props.get("name", row["name"]))
    current_description = str(
        current_props.get("description", row["description"] or "")
    )
    current_kind = {
        key: value
        for key, value in current_props.items()
        if key not in {"name", "description"}
    }
    overrides = _override_diff(archetype_props, current_kind)
    if current_name != archetype["name"]:
        overrides["name"] = current_name
    if current_description != (archetype["description"] or ""):
        overrides["description"] = current_description

    db.execute(
        "DELETE FROM object_links WHERE relation='instance_of' AND from_object_id=?",
        (instance_id,),
    )
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES ('instance_of', ?, ?, '{}', '')
        """,
        (instance_id, archetype_id),
    )
    db.execute(
        """
        UPDATE objects
        SET name=?, description=?, properties=?
        WHERE object_id=?
        """,
        (
            archetype["name"],
            "",
            json.dumps(overrides, ensure_ascii=False),
            instance_id,
        ),
    )
    if commit:
        db.commit()
    return resolve_item(db, instance_id)


def create_unique_item_instance(
    db,
    name: str,
    description: str = "",
    *,
    properties: dict | None = None,
    commit: bool = True,
) -> str:
    """Create an owned unique item with no archetype."""
    name = name.strip()
    if not name:
        raise ValueError("Instance name cannot be empty.")
    instance_id = f"{ITEM_INSTANCE_PREFIX}{uuid.uuid4().hex}"
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description, properties
        ) VALUES (?, ?, ?, 'unspecified', ?, ?)
        """,
        (
            instance_id,
            name,
            ITEM_INSTANCE_TYPE,
            description.strip(),
            json.dumps(properties or {}, ensure_ascii=False),
        ),
    )
    if commit:
        db.commit()
    return instance_id


def create_unique_container_instance(
    db,
    name: str,
    description: str = "",
    *,
    properties: dict | None = None,
    commit: bool = True,
) -> str:
    """Create an owned unique container with no archetype."""
    name = name.strip()
    if not name:
        raise ValueError("Container name cannot be empty.")
    instance_id = f"{CONTAINER_INSTANCE_PREFIX}{uuid.uuid4().hex}"
    props = dict(properties or {})
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description, properties
        ) VALUES (?, ?, 'container', 'unspecified', ?, ?)
        """,
        (
            instance_id,
            name,
            description.strip(),
            json.dumps(props, ensure_ascii=False),
        ),
    )
    if commit:
        db.commit()
    return instance_id


def create_container_instance_from_archetype(
    db,
    archetype_id: str,
    *,
    commit: bool = False,
) -> str:
    """Create an owned container copy linked to a catalog container archetype."""
    archetype = db.execute(
        "SELECT object_id, name, description, properties "
        "FROM objects WHERE object_id=? AND object_type='item'",
        (archetype_id,),
    ).fetchone()
    if archetype is None:
        raise ValueError(f"Unknown catalog item object: {archetype_id}")
    props = _parse_properties(archetype["properties"])
    if "container" not in (props.get("tags") or []):
        raise ValueError(
            f"Catalog item {archetype_id!r} is not a container archetype."
        )
    instance_id = f"{CONTAINER_INSTANCE_PREFIX}{uuid.uuid4().hex}"
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description, properties
        ) VALUES (?, ?, 'container', 'unspecified', '', ?)
        """,
        (
            instance_id,
            archetype["name"],
            json.dumps({"catalog_item_id": archetype["object_id"]}, ensure_ascii=False),
        ),
    )
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES ('instance_of', ?, ?, '{}', '')
        """,
        (instance_id, archetype["object_id"]),
    )
    if commit:
        db.commit()
    return instance_id


def place_owned_copy_in_container(
    db,
    parent_id: str,
    object_id: str,
    *,
    quantity: int = 1,
    attuned: bool = False,
    equipment_location: str = "",
    commit: bool = True,
) -> None:
    """Link an owned copy under a container via contains."""
    parent = db.execute(
        "SELECT 1 FROM objects WHERE object_id=? AND object_type='container'",
        (parent_id,),
    ).fetchone()
    if parent is None:
        raise ValueError(f"Unknown inventory container {parent_id!r}.")
    child = db.execute(
        "SELECT object_id, object_type FROM objects WHERE object_id=?",
        (object_id,),
    ).fetchone()
    if child is None or not is_customizable_owned_id(
        child["object_id"], child["object_type"]
    ):
        raise ValueError(f"Unknown owned copy {object_id!r}.")
    properties = {
        "quantity": int(quantity),
        "attuned": bool(attuned),
        "equipment_location": str(equipment_location).strip(),
    }
    if child["object_type"] == "container":
        properties.setdefault("role", "container")
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES ('contains', ?, ?, ?, '')
        """,
        (parent_id, object_id, json.dumps(properties, ensure_ascii=False)),
    )
    if commit:
        db.commit()


def migrate_catalog_contains_to_instances(db) -> int:
    """Retarget contains links from catalog items to owned item instances.

    Idempotent: links that already point at item_instance rows are left alone.
    Shared catalog rows (e.g. Spellbook on many characters) become one instance
    per contains link.
    """
    rows = db.execute(
        """
        SELECT link.id AS link_id, link.from_object_id, link.to_object_id,
               link.properties, item.name, item.description
        FROM object_links AS link
        JOIN objects AS item ON item.object_id = link.to_object_id
        WHERE link.relation = 'contains'
          AND item.object_type = 'item'
        ORDER BY link.id
        """
    ).fetchall()
    converted = 0
    for row in rows:
        instance_id = f"{ITEM_INSTANCE_PREFIX}{uuid.uuid4().hex}"
        db.execute(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description, properties
            ) VALUES (?, ?, ?, 'unspecified', '', '{}')
            """,
            (instance_id, row["name"], ITEM_INSTANCE_TYPE),
        )
        db.execute(
            """
            INSERT INTO object_links (
                relation, from_object_id, to_object_id, properties, notes
            ) VALUES ('instance_of', ?, ?, '{}', '')
            """,
            (instance_id, row["to_object_id"]),
        )
        db.execute(
            "UPDATE object_links SET to_object_id=? WHERE id=?",
            (instance_id, row["link_id"]),
        )
        converted += 1
    return converted


def get_items(db):
    rows = db.execute(
        """
        SELECT object_id AS id, name, description, properties
        FROM objects
        WHERE object_type='item'
        ORDER BY name COLLATE NOCASE, object_id
        """
    ).fetchall()
    items = []
    for row in rows:
        properties = _parse_properties(row["properties"])
        items.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "properties": properties,
            "is_container": "container" in properties.get("tags", []),
        })
    return items


def create_item(
    db,
    name: str,
    description: str = "",
    is_container: bool = False,
    properties: dict | None = None,
) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    object_id = f"catalog-item:{uuid.uuid4().hex}"
    props = _with_container_tag(properties, is_container)
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
            json.dumps(props, ensure_ascii=False),
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
    properties: dict | None = None,
) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Item name cannot be empty.")
    if properties is None:
        row = db.execute(
            "SELECT properties FROM objects WHERE object_id=? AND object_type='item'",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown catalog item object: {item_id}")
        properties = _parse_properties(row["properties"])
    props = _with_container_tag(properties, is_container)
    db.execute(
        """
        UPDATE objects SET name=?, description=?, properties=?
        WHERE object_id=? AND object_type='item'
        """,
        (
            name,
            description.strip(),
            json.dumps(props, ensure_ascii=False),
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
                    object_properties = {
                        key: value
                        for key, value in child["object_properties"].items()
                        if key != "catalog_item_id"
                    }
                    assignments.append({
                        "id": child["object_id"],
                        "name": child["name"],
                        "description": child["description"],
                        "is_container": True,
                        "archetype_id": child["object_properties"].get(
                            "catalog_item_id"
                        ),
                        "properties": object_properties,
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
            elif child["object_type"] in ("item", ITEM_INSTANCE_TYPE):
                object_properties = {
                    key: value
                    for key, value in child["object_properties"].items()
                    if key != "catalog_item_id"
                }
                assignments.append({
                    "id": child["object_id"],
                    "name": child["name"],
                    "description": child["description"],
                    "is_container": False,
                    "archetype_id": child["object_properties"].get(
                        "catalog_item_id"
                    ),
                    "properties": object_properties,
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
            "properties": _parse_properties(row["properties"]),
        })
    instance_of = {
        row["from_object_id"]: row["to_object_id"]
        for row in db.execute(
            """
            SELECT from_object_id, to_object_id
            FROM object_links WHERE relation='instance_of'
            ORDER BY id
            """
        )
    }

    def build(object_id: str, ancestors: frozenset[str]):
        if object_id in ancestors:
            raise ValueError(f"Containment cycle found at object {object_id!r}.")
        obj = objects.get(object_id)
        if obj is None:
            raise ValueError(f"Containment references missing object {object_id!r}.")
        branch = ancestors | {object_id}
        object_properties = _parse_properties(obj["properties"])
        name = obj["name"]
        description = obj["description"]
        archetype_id = instance_of.get(object_id)
        if is_customizable_owned_id(object_id, obj["object_type"]):
            resolved = resolve_item(db, object_id)
            if resolved is not None:
                name = resolved["name"]
                description = resolved["description"]
                object_properties = dict(resolved["properties"])
                if resolved["archetype_id"]:
                    object_properties["catalog_item_id"] = resolved["archetype_id"]
                    archetype_id = resolved["archetype_id"]
                else:
                    object_properties.pop("catalog_item_id", None)
        elif obj["object_type"] == "container" and archetype_id:
            object_properties.setdefault("catalog_item_id", archetype_id)
        result = {
            "object_id": object_id,
            "name": name,
            "object_type": obj["object_type"],
            "description": description,
            "object_properties": object_properties,
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
            {
                "quantity": quantity,
                "attuned": bool(attuned),
                "equipment_location": equipment_location.strip(),
            },
        ))

    with db:
        old_instances = [
            row["to_object_id"]
            for row in db.execute(
                """
                SELECT object_links.to_object_id
                FROM object_links
                JOIN objects ON objects.object_id = object_links.to_object_id
                WHERE object_links.relation='contains'
                  AND object_links.from_object_id=?
                  AND objects.object_type=?
                """,
                (container_id, ITEM_INSTANCE_TYPE),
            )
        ]
        db.execute(
            """
            DELETE FROM object_links
            WHERE relation='contains' AND from_object_id=?
              AND to_object_id IN (
                  SELECT object_id FROM objects
                  WHERE object_type IN ('item', ?)
              )
            """,
            (container_id, ITEM_INSTANCE_TYPE),
        )
        for instance_id in old_instances:
            _delete_orphan_item_instance(db, instance_id)
        for archetype_id, properties in normalized:
            instance_id = create_item_instance(db, archetype_id)
            db.execute(
                """
                INSERT INTO object_links (
                    relation, from_object_id, to_object_id, properties
                ) VALUES ('contains', ?, ?, ?)
                """,
                (
                    container_id,
                    instance_id,
                    json.dumps(properties, ensure_ascii=False),
                ),
            )


def _delete_orphan_item_instance(db, instance_id: str) -> None:
    if not is_item_instance_id(instance_id):
        return
    remaining = db.execute(
        """
        SELECT 1 FROM object_links
        WHERE to_object_id=? OR (from_object_id=? AND relation!='instance_of')
        LIMIT 1
        """,
        (instance_id, instance_id),
    ).fetchone()
    if remaining is not None:
        return
    db.execute(
        "DELETE FROM object_links WHERE relation='instance_of' AND from_object_id=?",
        (instance_id,),
    )
    db.execute(
        "DELETE FROM objects WHERE object_id=? AND object_type=?",
        (instance_id, ITEM_INSTANCE_TYPE),
    )


def save_character_inventory(db, character_id: int, children) -> None:
    root_id = get_character_inventory_container(db, character_id)
    nodes = []
    links = []
    seen_objects = set()
    kept_instances = set()

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
            elif object_type in ("item", ITEM_INSTANCE_TYPE):
                properties = dict(entry.get("properties", {}))
                quantity = int(properties.get("quantity", 1))
                if quantity < 1:
                    raise ValueError("Item quantity must be a positive whole number.")
                properties["quantity"] = quantity
                properties["attuned"] = bool(properties.get("attuned", False))
                properties["equipment_location"] = str(
                    properties.get("equipment_location", "")
                ).strip()
                object_properties = dict(entry.get("object_properties", {}))
                is_new = entry.get("new_item", False) or str(object_id).startswith(
                    "new-item:"
                )
                existing_instance = db.execute(
                    "SELECT 1 FROM objects WHERE object_id=? AND object_type=?",
                    (object_id, ITEM_INSTANCE_TYPE),
                ).fetchone()
                if existing_instance is not None:
                    kept_instances.add(object_id)
                    links.append(("contains", parent_id, object_id, properties))
                    continue
                archetype_id = object_properties.get("catalog_item_id")
                if archetype_id is None and not is_new:
                    # Legacy UI/API: contains pointed at the catalog row itself.
                    catalog = db.execute(
                        "SELECT 1 FROM objects WHERE object_id=? AND object_type='item'",
                        (object_id,),
                    ).fetchone()
                    if catalog is not None:
                        archetype_id = object_id
                if archetype_id is not None:
                    instance_id = create_item_instance(db, archetype_id)
                elif entry.get("new_unique") or is_new:
                    # Owned unique generated by customization, no archetype.
                    instance_id = create_unique_item_instance(
                        db,
                        entry.get("name") or "Unique item",
                        entry.get("description", ""),
                        properties={
                            key: value
                            for key, value in object_properties.items()
                            if key != "catalog_item_id"
                        },
                        commit=False,
                    )
                else:
                    raise ValueError(f"Unknown catalog item {object_id!r}.")
                kept_instances.add(instance_id)
                links.append(("contains", parent_id, instance_id, properties))
            else:
                raise ValueError(
                    f"Unsupported object type {object_type!r} in inventory."
                )

    existing_instances = set()
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
            elif row["object_type"] == ITEM_INSTANCE_TYPE:
                existing_instances.add(child_id)
                pending.append(child_id)

    with db:
        collect(root_id, children)
        desired_containers = {
            node[0] for node in nodes
        } | {
            child_id
            for relation, _parent_id, child_id, _properties in links
            if relation == "contains"
            and db.execute(
                "SELECT 1 FROM objects WHERE object_id=? AND object_type='container'",
                (child_id,),
            ).fetchone()
        }
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
        for instance_id in existing_instances - kept_instances:
            _delete_orphan_item_instance(db, instance_id)


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

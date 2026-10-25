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
        except Exception:
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


def _is_design_scaffold(data: dict) -> bool:
    status = data.get("stat_block_status")
    return isinstance(status, str) and status.lower().startswith("design scaffold")


def spawn_character_from_scaffold(
    db,
    scaffold_id: int,
    *,
    name: str | None = None,
) -> int:
    """Clone a playable character from a design scaffold without removing it.

    Copies sheet JSON (clearing ``stat_block_status``), campaign role, skill and
    spell loadouts, and a fresh inventory tree. The scaffold remains so it can
    spawn again (character spawner).
    """
    from sakaki_gm.item_db import (
        create_container_instance_from_archetype,
        create_item_instance,
        create_unique_container_instance,
        create_unique_item_instance,
        get_character_inventory_tree,
        place_owned_copy_in_container,
        resolve_item,
    )
    from sakaki_gm.skill_db import get_character_skills, set_character_skills
    from sakaki_gm.spell_db import get_character_spells, set_character_spells

    source = get_character(db, scaffold_id)
    if source is None:
        raise ValueError(f"Unknown character {scaffold_id}.")
    if not _is_design_scaffold(source):
        raise ValueError(
            f"{source.get('name') or scaffold_id!r} is not a design scaffold."
        )

    from sakaki_gm.name_gen import resolve_spawn_name

    data = {
        key: value
        for key, value in source.items()
        if key
        not in {
            "id",
            "object_id",
            "campaign_role",
            "error",
            "stat_block_status",
            "spawn_generate_name",
            "spawn_name_pattern",
        }
    }
    spawned_name = resolve_spawn_name(source, explicit_name=name)
    role = str(source.get("campaign_role") or "").strip()
    new_id = create_character(db, spawned_name, data, campaign_role=role)

    skill_assignments = [
        (row["id"], bool(row["proficient"]), bool(row["expertise"]))
        for row in get_character_skills(db, scaffold_id)
        if row["proficient"] or row["expertise"]
    ]
    if skill_assignments:
        set_character_skills(db, new_id, skill_assignments)

    spell_assignments = [
        (row["id"], True, int(row["prepared"] or 0), row["notes"] or "")
        for row in get_character_spells(db, scaffold_id)
    ]
    if spell_assignments:
        set_character_spells(db, new_id, spell_assignments)

    def clone_children(parent_id: str, children) -> None:
        for child in children or []:
            link_props = dict(child.get("properties") or {})
            quantity = int(link_props.get("quantity", 1) or 1)
            attuned = bool(link_props.get("attuned", False))
            location = str(link_props.get("equipment_location", "") or "")
            object_type = child.get("object_type")
            if object_type == "container":
                resolved = resolve_item(db, child["object_id"])
                archetype_id = (resolved or {}).get("archetype_id")
                if archetype_id:
                    instance_id = create_container_instance_from_archetype(
                        db, archetype_id, commit=False
                    )
                else:
                    instance_id = create_unique_container_instance(
                        db,
                        child.get("name") or "Container",
                        child.get("description") or "",
                        properties=dict(child.get("object_properties") or {}),
                        commit=False,
                    )
                place_owned_copy_in_container(
                    db,
                    parent_id,
                    instance_id,
                    quantity=quantity,
                    attuned=attuned,
                    equipment_location=location,
                    commit=False,
                )
                clone_children(instance_id, child.get("children") or [])
                continue

            resolved = resolve_item(db, child["object_id"])
            if resolved and resolved.get("archetype_id"):
                overrides = {
                    key: value
                    for key, value in (resolved.get("properties") or {}).items()
                    if key not in {"catalog_item_id"}
                }
                archetype = db.execute(
                    "SELECT name, description FROM objects WHERE object_id=?",
                    (resolved["archetype_id"],),
                ).fetchone()
                if archetype is not None:
                    if resolved["name"] != archetype["name"]:
                        overrides["name"] = resolved["name"]
                    if (resolved.get("description") or "") != (
                        archetype["description"] or ""
                    ):
                        overrides["description"] = resolved.get("description") or ""
                instance_id = create_item_instance(
                    db,
                    resolved["archetype_id"],
                    overrides=overrides or None,
                    commit=False,
                )
            else:
                instance_id = create_unique_item_instance(
                    db,
                    child.get("name") or "Item",
                    child.get("description") or "",
                    properties=dict(child.get("object_properties") or {}),
                    commit=False,
                )
            place_owned_copy_in_container(
                db,
                parent_id,
                instance_id,
                quantity=quantity,
                attuned=attuned,
                equipment_location=location,
                commit=False,
            )

    source_tree = get_character_inventory_tree(db, scaffold_id)
    with db:
        clone_children(f"inventory:{new_id}", source_tree.get("children") or [])
    return new_id

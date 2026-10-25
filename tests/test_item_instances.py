"""Item archetype / instance resolution and loadout migration."""
import json
import sqlite3

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.item_db import (
    ITEM_INSTANCE_TYPE,
    archetype_id_for_instance,
    create_item,
    create_item_instance,
    get_character_items,
    get_character_inventory_tree,
    migrate_catalog_contains_to_instances,
    resolve_item,
    save_character_inventory,
    set_character_items,
)


def test_resolve_item_applies_archetype_defaults_and_overrides(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    archetype_id = create_item(db, "Short Sword", "A simple blade.")
    db.execute(
        "UPDATE objects SET properties=? WHERE object_id=?",
        (
            json.dumps({
                "tags": ["weapon"],
                "weapon": {"damage": "1d6 piercing", "range": "melee"},
            }),
            archetype_id,
        ),
    )
    db.commit()
    instance_id = create_item_instance(
        db,
        archetype_id,
        overrides={
            "weapon": {"damage": "1d6+1 piercing"},
            "description": "Nicked edge from the shrine fight.",
        },
        commit=True,
    )
    resolved = resolve_item(db, instance_id)
    assert resolved["name"] == "Short Sword"
    assert resolved["description"] == "Nicked edge from the shrine fight."
    assert resolved["archetype_id"] == archetype_id
    assert resolved["properties"]["weapon"]["damage"] == "1d6+1 piercing"
    assert resolved["properties"]["weapon"]["range"] == "melee"
    db.close()


def test_set_character_items_creates_owned_instances(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    scepter_id = create_item(db, "Lunar Scepter", "Arcane focus.")
    set_character_items(db, character_id, [(scepter_id, 1, True, "hand")])
    assigned = get_character_items(db, character_id)
    assert len(assigned) == 1
    assert assigned[0]["id"].startswith("item-instance:")
    assert assigned[0]["archetype_id"] == scepter_id
    assert assigned[0]["name"] == "Lunar Scepter"
    assert assigned[0]["attuned"] == 1
    assert archetype_id_for_instance(db, assigned[0]["id"]) == scepter_id
    # Catalog row is no longer the contains target.
    assert db.execute(
        """
        SELECT 1 FROM object_links
        WHERE relation='contains' AND to_object_id=?
        """,
        (scepter_id,),
    ).fetchone() is None
    db.close()


def test_migrate_shared_catalog_links_become_distinct_instances(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    # Fresh bottles already run v15; seed a legacy-style contains link by
    # inserting catalog contains after resetting those instance rows is awkward.
    # Simulate pre-migration graph on a side connection path:
    a_id = create_character(db, "Talia", {"name": "Talia"})
    b_id = create_character(db, "Elara", {"name": "Elara"})
    book_id = create_item(db, "Spellbook", "Blank pages.")
    # Force legacy contains directly to the catalog row.
    for character_id in (a_id, b_id):
        root = db.execute(
            """
            SELECT to_object_id FROM object_links
            WHERE from_object_id=? AND relation='contains'
              AND json_extract(properties, '$.role')='inventory'
            """,
            (f"character:{character_id}",),
        ).fetchone()["to_object_id"]
        # Remove any auto-migrated rows first (none expected for empty inv).
        db.execute(
            """
            INSERT INTO object_links (
                relation, from_object_id, to_object_id, properties
            ) VALUES ('contains', ?, ?, ?)
            """,
            (
                root,
                book_id,
                json.dumps({
                    "quantity": 1,
                    "attuned": False,
                    "equipment_location": "",
                }),
            ),
        )
    db.commit()
    converted = migrate_catalog_contains_to_instances(db)
    db.commit()
    assert converted == 2
    instances = db.execute(
        """
        SELECT object_id FROM objects WHERE object_type=? AND name='Spellbook'
        ORDER BY object_id
        """,
        (ITEM_INSTANCE_TYPE,),
    ).fetchall()
    assert len(instances) == 2
    assert instances[0]["object_id"] != instances[1]["object_id"]
    for character_id in (a_id, b_id):
        owned = get_character_items(db, character_id)
        assert len(owned) == 1
        assert owned[0]["archetype_id"] == book_id
        assert owned[0]["name"] == "Spellbook"
    # Second migrate is a no-op.
    assert migrate_catalog_contains_to_instances(db) == 0
    db.close()


def test_save_inventory_spawns_instances_from_catalog_refs(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    potion_id = create_item(db, "Potion", "A healing draught.")
    backpack_id = create_item(db, "Backpack", "A sturdy pack.", is_container=True)
    save_character_inventory(
        db,
        character_id,
        [
            {
                "object_id": "new-container:backpack-instance",
                "object_type": "container",
                "new_container": True,
                "name": "Backpack",
                "description": "A sturdy pack.",
                "object_properties": {"catalog_item_id": backpack_id},
                "properties": {"quantity": 1, "attuned": False},
                "children": [
                    {
                        "object_id": "new-item:potion",
                        "object_type": "item",
                        "new_item": True,
                        "object_properties": {"catalog_item_id": potion_id},
                        "properties": {
                            "quantity": 2,
                            "attuned": False,
                            "equipment_location": "",
                        },
                    },
                ],
            },
        ],
    )
    inventory = get_character_inventory_tree(db, character_id)
    backpack = inventory["children"][0]
    potion = backpack["children"][0]
    assert potion["object_type"] == ITEM_INSTANCE_TYPE
    assert potion["object_id"].startswith("item-instance:")
    assert potion["object_properties"]["catalog_item_id"] == potion_id
    assert potion["name"] == "Potion"
    assert potion["properties"]["quantity"] == 2
    db.close()

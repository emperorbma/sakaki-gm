"""Customize / detach inventory-container copies and unique create."""
from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.item_db import (
    attach_item_instance,
    create_item,
    create_unique_container_instance,
    create_unique_item_instance,
    detach_item_instance,
    list_item_instances,
    place_owned_copy_in_container,
    resolve_item,
    save_character_inventory,
    update_item,
    update_item_instance,
)


def test_detach_container_instance_from_archetype(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    vault_id = create_item(
        db,
        "Warded Lunar Vault",
        "A sealed lunar container.",
        is_container=True,
    )
    save_character_inventory(
        db,
        character_id,
        [
            {
                "object_id": "new-container:vault",
                "object_type": "container",
                "new_container": True,
                "name": "Warded Lunar Vault",
                "description": "A sealed lunar container.",
                "object_properties": {"catalog_item_id": vault_id},
                "properties": {"quantity": 1, "attuned": False},
                "children": [],
            },
        ],
    )
    copies = list_item_instances(db, archetype_id=vault_id)
    assert len(copies) == 1
    assert copies[0]["is_container"] is True
    instance_id = copies[0]["id"]

    update_item_instance(
        db,
        instance_id,
        name="Serina's Lunar Vault",
        description="Her personal warded vault.",
    )
    detached = detach_item_instance(db, instance_id)
    assert detached["archetype_id"] is None
    assert detached["is_container"] is True
    assert detached["name"] == "Serina's Lunar Vault"
    assert detached["description"] == "Her personal warded vault."

    update_item(
        db,
        vault_id,
        "Warded Lunar Vault",
        "Catalog text changed.",
        is_container=True,
    )
    resolved = resolve_item(db, instance_id)
    assert resolved["description"] == "Her personal warded vault."

    reattached = attach_item_instance(db, instance_id, vault_id)
    assert reattached["archetype_id"] == vault_id
    assert reattached["name"] == "Serina's Lunar Vault"
    db.close()


def test_unique_item_and_container_create_without_archetype(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    root = f"inventory:{character_id}"
    item_id = create_unique_item_instance(
        db,
        "Moon-inked Note",
        "Found under the lectern.",
    )
    bag_id = create_unique_container_instance(
        db,
        "Travel Satchel",
        "Stitched after Veilnight.",
    )
    place_owned_copy_in_container(db, root, bag_id)
    place_owned_copy_in_container(db, bag_id, item_id)

    owned = {row["id"]: row for row in list_item_instances(db)}
    assert owned[item_id]["is_unique"] is True
    assert owned[bag_id]["is_unique"] is True
    assert owned[bag_id]["is_container"] is True
    assert any("Travel Satchel" in path for path in owned[item_id]["locations"])
    db.close()


def test_save_inventory_accepts_new_unique_item(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    save_character_inventory(
        db,
        character_id,
        [
            {
                "object_id": "new-item:unique-note",
                "object_type": "item",
                "new_item": True,
                "new_unique": True,
                "name": "Handwritten Note",
                "description": "Talia's moonpetal tea reminder.",
                "object_properties": {},
                "properties": {
                    "quantity": 1,
                    "attuned": False,
                    "equipment_location": "",
                },
            },
        ],
    )
    owned = list_item_instances(db)
    assert len(owned) == 1
    assert owned[0]["is_unique"] is True
    assert owned[0]["name"] == "Handwritten Note"
    assert owned[0]["archetype_id"] is None
    db.close()

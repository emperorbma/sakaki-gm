"""Detach / reattach owned item instances from archetypes."""
import json

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.item_db import (
    attach_item_instance,
    create_item,
    detach_item_instance,
    detach_instances_of_archetype,
    list_item_instances,
    resolve_item,
    set_character_items,
    update_item,
    update_item_instance,
)


def test_detach_bakes_resolved_values_and_clears_instance_of(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    crown_id = create_item(
        db,
        "Lunar Crown",
        "Attuned wondrous item. +2 Intelligence.",
    )
    set_character_items(db, character_id, [(crown_id, 1, True, "head")])
    instance_id = list_item_instances(db, archetype_id=crown_id)[0]["id"]

    update_item_instance(
        db,
        instance_id,
        description="Serina's crown — cracked gem, still attuned.",
    )
    detached = detach_item_instance(db, instance_id)
    assert detached["archetype_id"] is None
    assert detached["name"] == "Lunar Crown"
    assert detached["description"] == "Serina's crown — cracked gem, still attuned."
    assert db.execute(
        """
        SELECT 1 FROM object_links
        WHERE relation='instance_of' AND from_object_id=?
        """,
        (instance_id,),
    ).fetchone() is None

    # Archetype edits no longer affect the unique copy.
    update_item(db, crown_id, "Lunar Crown", "Changed catalog text.")
    resolved = resolve_item(db, instance_id)
    assert resolved["description"] == "Serina's crown — cracked gem, still attuned."
    db.close()


def test_attach_relinks_and_keeps_only_overrides(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    stick_id = create_item(db, "Lunar Stick", "A ritual focus.")
    db.execute(
        "UPDATE objects SET properties=? WHERE object_id=?",
        (json.dumps({"tags": ["focus"], "charges": 3}), stick_id),
    )
    db.commit()
    set_character_items(db, character_id, [(stick_id, 1, False, "")])
    instance_id = list_item_instances(db, archetype_id=stick_id)[0]["id"]
    detach_item_instance(db, instance_id)
    update_item_instance(
        db,
        instance_id,
        name="Starbloom Stick",
        description="Infused after the shrine.",
        property_overrides={"charges": 1},
    )

    attached = attach_item_instance(db, instance_id, stick_id)
    assert attached["archetype_id"] == stick_id
    assert attached["name"] == "Starbloom Stick"
    assert attached["description"] == "Infused after the shrine."
    assert attached["properties"]["charges"] == 1
    assert attached["properties"]["tags"] == ["focus"]

    # Matching archetype defaults are not frozen as overrides.
    props = json.loads(
        db.execute(
            "SELECT properties FROM objects WHERE object_id=?",
            (instance_id,),
        ).fetchone()["properties"]
    )
    assert props.get("name") == "Starbloom Stick"
    assert "tags" not in props
    assert props.get("charges") == 1

    update_item(
        db,
        stick_id,
        "Lunar Stick",
        "A ritual focus.",
        is_container=False,
    )
    db.execute(
        "UPDATE objects SET properties=? WHERE object_id=?",
        (json.dumps({"tags": ["focus", "starbloom"], "charges": 3}), stick_id),
    )
    db.commit()
    resolved = resolve_item(db, instance_id)
    assert resolved["properties"]["tags"] == ["focus", "starbloom"]
    assert resolved["properties"]["charges"] == 1
    db.close()


def test_detach_all_copies_of_archetype(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    a = create_character(db, "Talia", {"name": "Talia"})
    b = create_character(db, "Elara", {"name": "Elara"})
    book_id = create_item(db, "Spellbook", "Blank pages.")
    set_character_items(db, a, [(book_id, 1, False, "")])
    set_character_items(db, b, [(book_id, 1, False, "")])
    detached = detach_instances_of_archetype(db, book_id)
    assert len(detached) == 2
    assert list_item_instances(db, archetype_id=book_id) == []
    uniques = [item for item in list_item_instances(db) if item["name"] == "Spellbook"]
    assert len(uniques) == 2
    assert all(item["is_unique"] for item in uniques)
    db.close()

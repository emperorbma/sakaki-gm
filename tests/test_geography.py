import json

import pytest

from sakaki_gm.db import get_db
from sakaki_gm.object_graph_db import (
    create_object,
    create_object_link,
    find_route,
    get_encounter_entries,
    get_encounter_tables,
    get_objects,
    get_navigation_exits,
    get_geography_objects,
    parse_geography_tables,
    sync_encounter_entries,
    sync_geography,
)


GEOGRAPHY = """\
## Geography Objects
| Object ID | Name | Object Type | Geography Kind | Description |
|---|---|---|---|---|
| hub | Hub | settlement | settlement | A broad room. |
| forest | Forest | region | region | A region-sized room. |
| side-room | Side Room | chamber | point | A specific room. |

## Object Relations
| Relation | From Object ID | To Object ID | Distance Min (mi) | Distance Max (mi) | Distance Qualifier | Direction | Travel Mode | Day Minutes | Night Minutes | Notes |
|---|---|---|---:|---:|---|---|---|---:|---:|---|
| route | hub | forest | 3 | 3 | source_reported | north | trail | 60 | 75 | Exit to forest. |
| route | forest | side-room | | | | hidden | | | | Topological exit without a measured cost. |
| near | hub | side-room | 1 | 1 | source_reported | east | | | | Not an exit. |
| contains | forest | side-room | | | | | | | | Region contains this room. |

## Encounter Table Bindings
| Table ID | Table Name | Source File | Roll Die | Location Object ID | Binding Basis |
|---|---|---|---|---|---|
| forest-encounters | Forest Encounters | forest.csv | d20 | forest | Explicit table binding. |
"""

ENCOUNTERS = """Roll,Encounter,CR / XP,Notes
1,Deer herd,0 XP,Flavor
2-3,Wolf pack,CR 1/4,Pack
"""


def test_graph_uses_only_route_exits_and_keeps_rooms_unpinned(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        sync_geography(db, GEOGRAPHY)

        nodes = {obj["object_id"]: obj for obj in get_objects(db)}
        assert nodes["forest"]["geography_kind"] == "region"
        assert nodes["forest"]["coordinate_x"] is None
        assert nodes["forest"]["coordinate_y"] is None

        route = find_route(db, "forest", "hub")
        assert route["total"] == 3
        assert [obj["object_id"] for obj in route["objects"]] == ["forest", "hub"]
        assert route["links"][0]["traversed_from_object_id"] == "forest"
        assert find_route(db, "hub", "side-room") is None
        topology_route = find_route(db, "hub", "side-room", metric="hops")
        assert topology_route["total"] == 2
        assert topology_route["unit"] == "exits"
        assert [obj["object_id"] for obj in topology_route["objects"]] == [
            "hub",
            "forest",
            "side-room",
        ]
    finally:
        db.close()


def test_navigation_exits_include_routes_and_containment_but_not_near(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        sync_geography(db, GEOGRAPHY)
        exits = get_navigation_exits(db, "hub")
        assert {(item["relation"], item["target"]["object_id"]) for item in exits} == {
            ("route", "forest"),
        }
        reverse_exits = get_navigation_exits(db, "side-room")
        assert {
            (item["relation"], item["target"]["object_id"])
            for item in reverse_exits
        } == {
            ("route", "forest"),
            ("exit", "forest"),
        }
        inferred_exit = next(
            item for item in reverse_exits if item["relation"] == "exit"
        )
        assert inferred_exit["inference"] == "inverse_contains"
        assert inferred_exit["link"]["relation"] == "contains"
        assert {
            (item["relation"], item["target"]["object_id"])
            for item in get_navigation_exits(db, "forest")
        } == {
            ("route", "hub"),
            ("route", "side-room"),
            ("contains", "side-room"),
        }
        assert {
            obj["geography_kind"]
            for obj in get_geography_objects(db)
        } == {"region", "settlement"}
        assert len(get_geography_objects(db, "All geographic kinds")) == 3
    finally:
        db.close()


def test_geography_query_uses_object_classification_not_relations(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        db.execute(
            "INSERT INTO notes (title, content) VALUES (?, ?)",
            ("Geography of the Eldenweave", GEOGRAPHY),
        )
        db.commit()
        sync_geography(db, GEOGRAPHY)
        db.execute(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description
            ) VALUES ('bag', 'Bag of Holding', 'container', '', 'A persistent container.')
            """
        )
        db.execute(
            """
            INSERT INTO object_links (relation, from_object_id, to_object_id, notes)
            VALUES ('contains', 'bag', 'forest', 'Inventory containment, not geography.')
            """
        )
        db.commit()

        geography_objects = get_geography_objects(db, "All geographic kinds")
        assert "forest" in {obj["object_id"] for obj in geography_objects}
        assert "bag" not in {obj["object_id"] for obj in geography_objects}
        assert [
            (item["relation"], item["target"]["object_id"])
            for item in get_navigation_exits(db, "bag")
        ] == [("contains", "forest")]

        sync_geography(db, GEOGRAPHY)
        assert db.execute(
            "SELECT COUNT(*) FROM objects WHERE object_id='bag'"
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT COUNT(*) FROM object_links
            WHERE from_object_id='bag' AND to_object_id='forest'
            """
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_locations_and_nested_containers_can_both_contain_objects(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        sync_geography(db, GEOGRAPHY)
        create_object(db, "bag:travel", "Travel Bag", "container")
        create_object(db, "item:torch", "Torch", "item")
        create_object_link(db, "contains", "forest", "bag:travel")
        create_object_link(
            db,
            "contains",
            "forest",
            "item:torch",
            properties={"quantity": 1, "placement": "floor"},
        )
        create_object_link(
            db,
            "contains",
            "bag:travel",
            "item:torch",
            properties={"quantity": 3, "placement": "packed"},
        )

        geography = {obj["object_id"] for obj in get_geography_objects(db)}
        assert "forest" in geography
        assert "bag:travel" not in geography
        location_contents = {
            item["target"]["object_id"]
            for item in get_navigation_exits(db, "forest")
            if item["relation"] == "contains"
        }
        assert {"bag:travel", "item:torch", "side-room"} <= location_contents
        bag_contents = get_navigation_exits(db, "bag:travel")
        assert {
            (item["relation"], item["target"]["object_id"])
            for item in bag_contents
        } == {
            ("contains", "item:torch"),
            ("exit", "forest"),
        }
        assert [
            item["target"]["object_id"]
            for item in get_navigation_exits(db, "item:torch")
        ] == []
        loose_item_link = db.execute(
            """
            SELECT properties FROM object_links
            WHERE from_object_id='forest' AND to_object_id='item:torch'
            """
        ).fetchone()
        assert json.loads(loose_item_link["properties"]) == {
            "quantity": 1,
            "placement": "floor",
        }
    finally:
        db.close()


def test_encounter_tables_are_explicitly_room_bound_and_import_csv_rows(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        sync_geography(db, GEOGRAPHY)
        sync_encounter_entries(db, "forest-encounters", ENCOUNTERS)

        tables = get_encounter_tables(db, "forest")
        assert [table["table_id"] for table in tables] == ["forest-encounters"]
        assert get_encounter_tables(db, "hub") == []
        entries = get_encounter_entries(db, "forest-encounters")
        assert [(entry["roll_min"], entry["roll_max"]) for entry in entries] == [
            (1, 1),
            (2, 3),
        ]
        assert entries[1]["encounter"] == "Wolf pack"
        assert entries[1]["source_row"] == "3"
    finally:
        db.close()


def test_invalid_encounter_csv_does_not_replace_existing_rows(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        sync_geography(db, GEOGRAPHY)
        sync_encounter_entries(db, "forest-encounters", ENCOUNTERS)
        with pytest.raises(ValueError, match="Overlapping"):
            sync_encounter_entries(
                db,
                "forest-encounters",
                "Roll,Encounter,CR / XP,Notes\n1-2,First,,\n2,Second,,\n",
            )
        assert len(get_encounter_entries(db, "forest-encounters")) == 2
    finally:
        db.close()


def test_encounter_binding_must_reference_known_room():
    malformed = GEOGRAPHY.replace("| forest-encounters | Forest Encounters | forest.csv | d20 | forest |", "| forest-encounters | Forest Encounters | forest.csv | d20 | missing |")
    with pytest.raises(ValueError, match="unknown location"):
        parse_geography_tables(malformed)


def test_v10_migration_adds_geography_metadata_then_promotes_objects(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        db.execute("DROP INDEX characters_by_object")
        db.execute("DROP INDEX item_objects_by_name")
        db.execute("ALTER TABLE characters DROP COLUMN object_id")
        db.execute("ALTER TABLE object_links DROP COLUMN properties")
        db.execute("ALTER TABLE objects DROP COLUMN properties")
        db.execute(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL DEFAULT ''
            )
            """
        )
        db.execute(
            """
            CREATE TABLE character_items (
                character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                attuned BOOLEAN NOT NULL DEFAULT 0,
                equipment_location TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (character_id, item_id)
            )
            """
        )
        db.execute("DROP TABLE encounter_entries")
        db.execute("DROP TABLE encounter_tables")
        db.execute("ALTER TABLE object_links RENAME TO map_links")
        db.execute("ALTER TABLE map_links RENAME COLUMN from_object_id TO from_node_id")
        db.execute("ALTER TABLE map_links RENAME COLUMN to_object_id TO to_node_id")
        db.execute("ALTER TABLE objects RENAME TO map_nodes")
        db.execute("ALTER TABLE map_nodes RENAME COLUMN object_id TO node_id")
        db.execute("ALTER TABLE map_nodes RENAME COLUMN object_type TO node_type")
        db.execute("ALTER TABLE map_nodes RENAME COLUMN geography_kind TO spatial_kind")
        db.execute(
            "ALTER TABLE map_nodes DROP COLUMN coordinate_precision"
        )
        db.execute("DROP INDEX object_links_from_relation")
        db.execute("DROP INDEX object_links_to_relation")
        db.execute(
            "CREATE INDEX map_links_from_relation "
            "ON map_links(from_node_id, relation)"
        )
        db.execute(
            "CREATE INDEX map_links_to_relation "
            "ON map_links(to_node_id, relation)"
        )
        db.execute("PRAGMA user_version = 10")
        db.commit()

        from sakaki_gm.db import migrate

        migrate(db)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(objects)")}
        assert {"geography_kind", "coordinate_precision"} <= columns
        assert "map_nodes" not in {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert db.execute("PRAGMA user_version").fetchone()[0] == 15
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='encounter_tables'"
        ).fetchone()
    finally:
        db.close()

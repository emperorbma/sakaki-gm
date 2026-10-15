import pytest

from db import get_db
from geography_db import (
    find_route,
    get_encounter_entries,
    get_encounter_tables,
    get_map_nodes,
    get_navigation_exits,
    get_setting_candidates,
    parse_geography_tables,
    sync_encounter_entries,
    sync_geography,
)


GEOGRAPHY = """\
## Map Nodes
| ID | Name | Type | Spatial Kind | Description |
|---|---|---|---|---|
| hub | Hub | settlement | settlement | A broad room. |
| forest | Forest | region | region | A region-sized room. |
| side-room | Side Room | chamber | point | A specific room. |

## Location Links
| Relation | From ID | To ID | Distance Min (mi) | Distance Max (mi) | Distance Qualifier | Direction | Travel Mode | Day Minutes | Night Minutes | Notes |
|---|---|---|---:|---:|---|---|---|---:|---:|---|
| route | hub | forest | 3 | 3 | source_reported | north | trail | 60 | 75 | Exit to forest. |
| route | forest | side-room | | | | hidden | | | | Topological exit without a measured cost. |
| near | hub | side-room | 1 | 1 | source_reported | east | | | | Not an exit. |
| contains | forest | side-room | | | | | | | | Region contains this room. |

## Encounter Table Bindings
| Table ID | Table Name | Source File | Roll Die | Location Node ID | Binding Basis |
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

        nodes = {node["node_id"]: node for node in get_map_nodes(db)}
        assert nodes["forest"]["spatial_kind"] == "region"
        assert nodes["forest"]["coordinate_x"] is None
        assert nodes["forest"]["coordinate_y"] is None

        route = find_route(db, "forest", "hub")
        assert route["total"] == 3
        assert [node["node_id"] for node in route["nodes"]] == ["forest", "hub"]
        assert route["links"][0]["traversed_from_node_id"] == "forest"
        assert find_route(db, "hub", "side-room") is None
        topology_route = find_route(db, "hub", "side-room", metric="hops")
        assert topology_route["total"] == 2
        assert topology_route["unit"] == "exits"
        assert [node["node_id"] for node in topology_route["nodes"]] == [
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
        assert {(item["relation"], item["target"]["node_id"]) for item in exits} == {
            ("route", "forest"),
        }
        reverse_exits = get_navigation_exits(db, "side-room")
        assert [(item["relation"], item["target"]["node_id"]) for item in reverse_exits] == [
            ("route", "forest"),
        ]
        assert {
            (item["relation"], item["target"]["node_id"])
            for item in get_navigation_exits(db, "forest")
        } == {
            ("route", "hub"),
            ("route", "side-room"),
            ("contains", "side-room"),
        }
        assert {
            node["spatial_kind"]
            for node in get_setting_candidates(db)
        } == {"region", "settlement"}
        assert len(get_setting_candidates(db, "All room types")) == 3
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


def test_v10_migration_adds_room_metadata_and_encounter_tables(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        db.execute("DROP TABLE encounter_entries")
        db.execute("DROP TABLE encounter_tables")
        db.execute("ALTER TABLE map_nodes DROP COLUMN spatial_kind")
        db.execute("ALTER TABLE map_nodes DROP COLUMN coordinate_precision")
        db.execute("PRAGMA user_version = 10")
        db.commit()

        from db import migrate

        migrate(db)
        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(map_nodes)")
        }
        assert {"spatial_kind", "coordinate_precision"} <= columns
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='encounter_tables'"
        ).fetchone()
    finally:
        db.close()

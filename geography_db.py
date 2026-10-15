"""Structured campaign geography and route-graph access."""
import csv
import heapq
import io
import re

MAP_NODES_TITLE = "Geography of the Eldenweave"
NODE_COLUMNS = ("id", "name", "type", "spatial kind", "description")
LINK_COLUMNS = (
    "relation",
    "from id",
    "to id",
    "distance min (mi)",
    "distance max (mi)",
    "distance qualifier",
    "direction",
    "travel mode",
    "day minutes",
    "night minutes",
    "notes",
)
ENCOUNTER_BINDING_COLUMNS = (
    "table id",
    "table name",
    "source file",
    "roll die",
    "location node id",
    "binding basis",
)


def parse_geography_tables(
    markdown: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse room nodes, exits/descriptive links, and explicit table bindings."""
    sections = _markdown_tables(markdown)
    nodes = _rows_for_table(sections.get("map nodes"), NODE_COLUMNS, "Map Nodes")
    links = _rows_for_table(
        sections.get("location links"), LINK_COLUMNS, "Location Links"
    )
    bindings = _rows_for_table(
        sections.get("encounter table bindings"),
        ENCOUNTER_BINDING_COLUMNS,
        "Encounter Table Bindings",
    )
    node_ids = {node["node_id"] for node in nodes}
    if len(node_ids) != len(nodes):
        raise ValueError("Map Nodes contains duplicate IDs")
    table_ids = {binding["table_id"] for binding in bindings}
    if len(table_ids) != len(bindings):
        raise ValueError("Encounter Table Bindings contains duplicate table IDs")
    for link in links:
        if link["from_node_id"] not in node_ids:
            raise ValueError(
                f"Location Links references unknown From ID "
                f"{link['from_node_id']!r}"
            )
        if link["to_node_id"] not in node_ids:
            raise ValueError(
                f"Location Links references unknown To ID "
                f"{link['to_node_id']!r}"
            )
        if link["from_node_id"] == link["to_node_id"]:
            raise ValueError("Location Links cannot connect a node to itself")
    for binding in bindings:
        if binding["location_node_id"] not in node_ids:
            raise ValueError(
                f"Encounter table references unknown location "
                f"{binding['location_node_id']!r}"
            )
        if not re.fullmatch(r"d\d+", binding["roll_die"], re.IGNORECASE):
            raise ValueError(
                f"Invalid roll die for encounter table {binding['table_id']!r}"
            )
    return nodes, links, bindings


def sync_geography(db, markdown: str, source_title: str = MAP_NODES_TITLE) -> None:
    """Replace structured map rows from a geography source document atomically."""
    nodes, links, bindings = parse_geography_tables(markdown)
    source = db.execute(
        "SELECT id FROM notes WHERE title=? ORDER BY id LIMIT 1",
        (source_title,),
    ).fetchone()
    source_note_id = source["id"] if source else None

    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM map_links")
        db.executemany(
            """
            INSERT INTO map_nodes (
                node_id, name, node_type, spatial_kind, description,
                source_note_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                name=excluded.name,
                node_type=excluded.node_type,
                spatial_kind=excluded.spatial_kind,
                description=excluded.description,
                source_note_id=excluded.source_note_id,
                source_row=excluded.source_row
            """,
            [
                (
                    node["node_id"],
                    node["name"],
                    node["node_type"],
                    node["spatial_kind"],
                    node["description"],
                    source_note_id,
                    node["source_row"],
                )
                for node in nodes
            ],
        )
        binding_ids = [binding["table_id"] for binding in bindings]
        placeholders = ",".join("?" for _ in binding_ids)
        db.execute(
            f"DELETE FROM encounter_tables WHERE table_id NOT IN ({placeholders})",
            binding_ids,
        )
        db.executemany(
            """
            INSERT INTO encounter_tables (
                table_id, name, source_file, roll_die, location_node_id,
                binding_basis, source_note_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(table_id) DO UPDATE SET
                name=excluded.name,
                source_file=excluded.source_file,
                roll_die=excluded.roll_die,
                location_node_id=excluded.location_node_id,
                binding_basis=excluded.binding_basis,
                source_note_id=excluded.source_note_id,
                source_row=excluded.source_row
            """,
            [
                (
                    binding["table_id"],
                    binding["name"],
                    binding["source_file"],
                    binding["roll_die"],
                    binding["location_node_id"],
                    binding["binding_basis"],
                    source_note_id,
                    binding["source_row"],
                )
                for binding in bindings
            ],
        )
        node_ids = [node["node_id"] for node in nodes]
        node_placeholders = ",".join("?" for _ in node_ids)
        db.execute(
            f"DELETE FROM map_nodes WHERE node_id NOT IN ({node_placeholders})",
            node_ids,
        )
        db.executemany(
            """
            INSERT INTO map_links (
                relation, from_node_id, to_node_id, distance_min_miles,
                distance_max_miles, distance_qualifier, direction, travel_mode,
                day_minutes, night_minutes, notes, source_note_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    link["relation"],
                    link["from_node_id"],
                    link["to_node_id"],
                    link["distance_min_miles"],
                    link["distance_max_miles"],
                    link["distance_qualifier"],
                    link["direction"],
                    link["travel_mode"],
                    link["day_minutes"],
                    link["night_minutes"],
                    link["notes"],
                    source_note_id,
                    link["source_row"],
                )
                for link in links
            ],
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def sync_encounter_entries(db, table_id: str, csv_text: str) -> None:
    """Replace a bound encounter table's rows from its source CSV."""
    table = db.execute(
        "SELECT roll_die FROM encounter_tables WHERE table_id=?", (table_id,)
    ).fetchone()
    if table is None:
        raise KeyError(f"Unknown encounter table: {table_id}")
    die_match = re.fullmatch(r"d(\d+)", table["roll_die"], re.IGNORECASE)
    if not die_match:
        raise ValueError(f"Invalid roll die for encounter table {table_id!r}")
    die_sides = int(die_match.group(1))
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("Encounter CSV is missing its header")
    columns = {_normalize_header(name): name for name in reader.fieldnames}
    required = {"roll", "encounter", "cr / xp", "notes"}
    if not required.issubset(columns):
        raise ValueError(
            f"Encounter CSV must contain columns {sorted(required)!r}"
        )

    entries = []
    occupied_rolls: set[int] = set()
    for row_number, row in enumerate(reader, start=2):
        raw_roll = (row.get(columns["roll"]) or "").strip()
        roll_match = re.fullmatch(r"(\d+)(?:\s*[-–]\s*(\d+))?", raw_roll)
        if not roll_match:
            raise ValueError(
                f"Invalid encounter roll {raw_roll!r} on CSV row {row_number}"
            )
        roll_min = int(roll_match.group(1))
        roll_max = int(roll_match.group(2) or roll_min)
        if roll_min < 1 or roll_max > die_sides:
            raise ValueError(
                f"Encounter roll {raw_roll!r} is outside {table['roll_die']}"
            )
        roll_values = set(range(roll_min, roll_max + 1))
        if occupied_rolls & roll_values:
            raise ValueError(f"Overlapping encounter roll {raw_roll!r}")
        occupied_rolls.update(roll_values)
        encounter = (row.get(columns["encounter"]) or "").strip()
        if not encounter:
            raise ValueError(f"Encounter is required on CSV row {row_number}")
        entries.append(
            (
                table_id,
                raw_roll,
                roll_min,
                roll_max,
                encounter,
                (row.get(columns["cr / xp"]) or "").strip(),
                (row.get(columns["notes"]) or "").strip(),
                str(row_number),
            )
        )
    if not entries:
        raise ValueError("Encounter CSV has no entries")

    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM encounter_entries WHERE table_id=?", (table_id,))
        db.executemany(
            """
            INSERT INTO encounter_entries (
                table_id, roll, roll_min, roll_max, encounter, cr_xp, notes,
                source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            entries,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_encounter_tables(db, location_node_id: str) -> list[dict]:
    """Get only encounter tables explicitly bound to this room or region."""
    return [
        dict(row)
        for row in db.execute(
            """
            SELECT * FROM encounter_tables
            WHERE location_node_id=?
            ORDER BY name, table_id
            """,
            (location_node_id,),
        ).fetchall()
    ]


def get_encounter_entries(db, table_id: str) -> list[dict]:
    return [
        dict(row)
        for row in db.execute(
            """
            SELECT * FROM encounter_entries
            WHERE table_id=?
            ORDER BY roll_min, roll_max, id
            """,
            (table_id,),
        ).fetchall()
    ]


def get_map_nodes(db) -> list[dict]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT * FROM map_nodes ORDER BY name, node_id"
        ).fetchall()
    ]


def get_map_links(db, relation: str | None = None) -> list[dict]:
    if relation is None:
        rows = db.execute(
            """
            SELECT * FROM map_links
            ORDER BY relation, from_node_id, to_node_id, id
            """
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT * FROM map_links WHERE relation=?
            ORDER BY from_node_id, to_node_id, id
            """,
            (relation,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_setting_candidates(
    db, spatial_kind: str = "Regions and settlements"
) -> list[dict]:
    """Return queryable Campaign Setting root destinations."""
    nodes = get_map_nodes(db)
    if spatial_kind == "Regions and settlements":
        allowed = {"region", "settlement"}
        nodes = [node for node in nodes if node["spatial_kind"].casefold() in allowed]
    elif spatial_kind != "All room types":
        nodes = [
            node for node in nodes
            if node["spatial_kind"].casefold() == spatial_kind.casefold()
        ]
    return nodes


def get_navigation_exits(db, node_id: str) -> list[dict]:
    """Return navigable route and containment exits from a room."""
    nodes = {
        row["node_id"]: dict(row)
        for row in db.execute("SELECT * FROM map_nodes")
    }
    if node_id not in nodes:
        raise KeyError(f"Unknown location: {node_id}")

    exits = []
    for row in db.execute(
        """
        SELECT * FROM map_links
        WHERE relation='route'
          AND (from_node_id=? OR to_node_id=?)
        ORDER BY id
        """,
        (node_id, node_id),
    ):
        link = dict(row)
        target_id = (
            link["to_node_id"]
            if link["from_node_id"] == node_id
            else link["from_node_id"]
        )
        exits.append({
            "relation": "route",
            "target": nodes[target_id],
            "link": link,
        })

    for row in db.execute(
        """
        SELECT * FROM map_links
        WHERE relation='contains' AND from_node_id=?
        ORDER BY id
        """,
        (node_id,),
    ):
        link = dict(row)
        exits.append({
            "relation": "contains",
            "target": nodes[link["to_node_id"]],
            "link": link,
        })
    exits.sort(
        key=lambda item: (
            item["relation"],
            item["target"]["name"].casefold(),
            item["target"]["node_id"],
        )
    )
    return exits


def find_route(
    db,
    start_node_id: str,
    end_node_id: str,
    metric: str = "distance",
    travel_period: str = "day",
) -> dict | None:
    """Find a path through exits, optionally using movement-cost weights.

    Route links are treated as bidirectional. ``hops`` ignores geometry and
    movement costs; distance/time queries use only links with that source data.
    Returned edges preserve the source-recorded orientation and are ordered
    as traversed.
    """
    if metric not in {"distance", "time", "hops"}:
        raise ValueError("metric must be 'distance', 'time', or 'hops'")
    if travel_period not in {"day", "night"}:
        raise ValueError("travel_period must be 'day' or 'night'")
    unit = {"distance": "miles", "time": "minutes", "hops": "exits"}[metric]
    nodes = {
        row["node_id"]: dict(row)
        for row in db.execute("SELECT * FROM map_nodes")
    }
    if start_node_id not in nodes:
        raise KeyError(f"Unknown start location: {start_node_id}")
    if end_node_id not in nodes:
        raise KeyError(f"Unknown end location: {end_node_id}")
    if start_node_id == end_node_id:
        return {
            "nodes": [nodes[start_node_id]],
            "links": [],
            "total": 0,
            "unit": unit,
            "metric": metric,
            "travel_period": travel_period if metric == "time" else None,
        }

    adjacency: dict[str, list[tuple[str, dict, float]]] = {
        node_id: [] for node_id in nodes
    }
    for row in db.execute(
        "SELECT * FROM map_links WHERE relation='route' ORDER BY id"
    ):
        link = dict(row)
        if metric == "hops":
            weight = 1
        elif metric == "distance":
            weight = link["distance_min_miles"]
        else:
            weight = link[f"{travel_period}_minutes"]
        if weight is None:
            continue
        adjacency[link["from_node_id"]].append(
            (link["to_node_id"], link, float(weight))
        )
        adjacency[link["to_node_id"]].append(
            (link["from_node_id"], link, float(weight))
        )

    distances = {start_node_id: 0.0}
    previous: dict[str, tuple[str, dict]] = {}
    queue = [(0.0, start_node_id)]
    while queue:
        distance, current = heapq.heappop(queue)
        if distance != distances.get(current):
            continue
        if current == end_node_id:
            break
        for neighbor, link, weight in adjacency[current]:
            candidate = distance + weight
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = (current, link)
                heapq.heappush(queue, (candidate, neighbor))

    if end_node_id not in distances:
        return None
    path_node_ids = [end_node_id]
    path_links = []
    current = end_node_id
    while current != start_node_id:
        parent, link = previous[current]
        path_links.append(_traversed_link(link, parent, current))
        path_node_ids.append(parent)
        current = parent
    path_node_ids.reverse()
    path_links.reverse()
    return {
        "nodes": [nodes[node_id] for node_id in path_node_ids],
        "links": path_links,
        "total": _clean_number(distances[end_node_id]),
        "unit": unit,
        "metric": metric,
        "travel_period": travel_period if metric == "time" else None,
    }


def _traversed_link(link: dict, from_node_id: str, to_node_id: str) -> dict:
    traversed = dict(link)
    traversed["traversed_from_node_id"] = from_node_id
    traversed["traversed_to_node_id"] = to_node_id
    traversed["traverses_recorded_direction"] = (
        link["from_node_id"] == from_node_id
        and link["to_node_id"] == to_node_id
    )
    return traversed


def _clean_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _markdown_tables(markdown: str) -> dict[str, list[tuple[list[str], list[str]]]]:
    tables: dict[str, list[tuple[list[str], list[str]]]] = {}
    section = ""
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        heading = re.match(r"^#{2,6}\s+(.+?)\s*$", lines[index])
        if heading:
            section = heading.group(1).strip().casefold()
            index += 1
            continue
        if section not in {
            "map nodes",
            "location links",
            "encounter table bindings",
        } or not lines[index].lstrip().startswith("|"):
            index += 1
            continue
        header = _split_markdown_row(lines[index])
        index += 1
        if index >= len(lines) or not _is_separator_row(lines[index]):
            continue
        index += 1
        rows = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            row = _split_markdown_row(lines[index])
            if len(row) != len(header):
                raise ValueError(
                    f"Malformed {section} table row: expected {len(header)} cells, "
                    f"found {len(row)}"
                )
            rows.append((header, row))
            index += 1
        tables.setdefault(section, []).extend(rows)
    return tables


def _rows_for_table(
    rows: list[tuple[list[str], list[str]]] | None,
    expected_columns: tuple[str, ...],
    label: str,
) -> list[dict]:
    if not rows:
        raise ValueError(f"Geography document is missing the {label} table")
    output = []
    expected = tuple(_normalize_header(column) for column in expected_columns)
    for header, cells in rows:
        normalized = tuple(_normalize_header(column) for column in header)
        if normalized != expected:
            raise ValueError(
                f"{label} table headers must be {expected_columns!r}, "
                f"got {header!r}"
            )
        values = dict(zip(normalized, (cell.strip() for cell in cells)))
        source_row = " | ".join(cells)
        if label == "Map Nodes":
            if not values["id"] or not values["name"]:
                raise ValueError("Map node ID and name are required")
            output.append({
                "node_id": values["id"],
                "name": values["name"],
                "node_type": values["type"],
                "spatial_kind": values["spatial kind"],
                "description": values["description"],
                "source_row": source_row,
            })
        elif label == "Location Links":
            output.append({
                "relation": values["relation"].casefold(),
                "from_node_id": values["from id"],
                "to_node_id": values["to id"],
                "distance_min_miles": _optional_float(values["distance min (mi)"]),
                "distance_max_miles": _optional_float(values["distance max (mi)"]),
                "distance_qualifier": values["distance qualifier"],
                "direction": values["direction"],
                "travel_mode": values["travel mode"],
                "day_minutes": _optional_int(values["day minutes"]),
                "night_minutes": _optional_int(values["night minutes"]),
                "notes": values["notes"],
                "source_row": source_row,
            })
        else:
            if not values["table id"] or not values["table name"]:
                raise ValueError("Encounter table ID and name are required")
            output.append({
                "table_id": values["table id"],
                "name": values["table name"],
                "source_file": values["source file"],
                "roll_die": values["roll die"].casefold(),
                "location_node_id": values["location node id"],
                "binding_basis": values["binding basis"],
                "source_row": source_row,
            })
    return output


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _split_markdown_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _optional_float(value: str) -> float | None:
    if not value:
        return None
    number = float(value)
    if number < 0:
        raise ValueError("Geographic distances cannot be negative")
    return number


def _optional_int(value: str) -> int | None:
    if not value:
        return None
    number = int(value)
    if number < 0:
        raise ValueError("Travel times cannot be negative")
    return number

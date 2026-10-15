"""Campaign object graph storage and geography-specific query operations."""
import csv
import heapq
import io
import json
import re

GEOGRAPHY_SOURCE_TITLE = "Geography of the Eldenweave"
GEOGRAPHY_OBJECT_COLUMNS = (
    "object id",
    "name",
    "object type",
    "geography kind",
    "description",
)
LINK_COLUMNS = (
    "relation",
    "from object id",
    "to object id",
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
    "location object id",
    "binding basis",
)


def parse_geography_tables(
    markdown: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse geography objects, generic relations, and explicit table bindings."""
    sections = _markdown_tables(markdown)
    nodes = _rows_for_table(
        sections.get("geography objects"),
        GEOGRAPHY_OBJECT_COLUMNS,
        "Geography Objects",
    )
    links = _rows_for_table(
        sections.get("object relations"), LINK_COLUMNS, "Object Relations"
    )
    bindings = _rows_for_table(
        sections.get("encounter table bindings"),
        ENCOUNTER_BINDING_COLUMNS,
        "Encounter Table Bindings",
    )
    object_ids = {obj["object_id"] for obj in nodes}
    if len(object_ids) != len(nodes):
        raise ValueError("Geography Objects contains duplicate object IDs")
    table_ids = {binding["table_id"] for binding in bindings}
    if len(table_ids) != len(bindings):
        raise ValueError("Encounter Table Bindings contains duplicate table IDs")
    for link in links:
        if link["from_object_id"] not in object_ids:
            raise ValueError(
                f"Object Relations references unknown From ID "
                f"{link['from_object_id']!r}"
            )
        if link["to_object_id"] not in object_ids:
            raise ValueError(
                f"Object Relations references unknown To ID "
                f"{link['to_object_id']!r}"
            )
        if link["from_object_id"] == link["to_object_id"]:
            raise ValueError("Object Relations cannot connect an object to itself")
    for binding in bindings:
        if binding["location_object_id"] not in object_ids:
            raise ValueError(
                f"Encounter table references unknown location "
                f"{binding['location_object_id']!r}"
            )
        if not re.fullmatch(r"d\d+", binding["roll_die"], re.IGNORECASE):
            raise ValueError(
                f"Invalid roll die for encounter table {binding['table_id']!r}"
            )
    return nodes, links, bindings


def sync_geography(
    db, markdown: str, source_title: str = GEOGRAPHY_SOURCE_TITLE
) -> None:
    """Sync geography-sourced objects and relations into the object graph."""
    nodes, links, bindings = parse_geography_tables(markdown)
    source = db.execute(
        "SELECT id FROM notes WHERE title=? ORDER BY id LIMIT 1",
        (source_title,),
    ).fetchone()
    source_note_id = source["id"] if source else None

    db.execute("BEGIN IMMEDIATE")
    try:
        if source_note_id is not None:
            db.execute(
                "DELETE FROM object_links WHERE source_note_id=?",
                (source_note_id,),
            )
        incoming_ids = [obj["object_id"] for obj in nodes]
        if incoming_ids:
            placeholders = ",".join("?" for _ in incoming_ids)
            conflicts = db.execute(
                f"""
                SELECT object_id FROM objects
                WHERE object_id IN ({placeholders})
                  AND (source_note_id IS NOT ?)
                """,
                (*incoming_ids, source_note_id),
            ).fetchall()
            if conflicts:
                raise ValueError(
                    "Geography source object IDs conflict with objects owned "
                    f"by another source: {[row['object_id'] for row in conflicts]!r}"
                )
        if source_note_id is not None:
            binding_ids = [binding["table_id"] for binding in bindings]
            if binding_ids:
                placeholders = ",".join("?" for _ in binding_ids)
                db.execute(
                    f"""
                    DELETE FROM encounter_tables
                    WHERE source_note_id=? AND table_id NOT IN ({placeholders})
                    """,
                    (source_note_id, *binding_ids),
                )
            else:
                db.execute(
                    "DELETE FROM encounter_tables WHERE source_note_id=?",
                    (source_note_id,),
                )
            if incoming_ids:
                placeholders = ",".join("?" for _ in incoming_ids)
                db.execute(
                    f"""
                    DELETE FROM objects
                    WHERE source_note_id=?
                      AND object_id NOT IN ({placeholders})
                      AND NOT EXISTS (
                          SELECT 1 FROM object_links
                          WHERE (from_object_id=objects.object_id
                                 OR to_object_id=objects.object_id)
                            AND (source_note_id IS NULL OR source_note_id != ?)
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM encounter_tables
                          WHERE location_object_id=objects.object_id
                            AND (source_note_id IS NULL OR source_note_id != ?)
                      )
                    """,
                    (
                        source_note_id,
                        *incoming_ids,
                        source_note_id,
                        source_note_id,
                    ),
                )
            else:
                db.execute(
                    """
                    DELETE FROM objects
                    WHERE source_note_id=?
                      AND NOT EXISTS (
                          SELECT 1 FROM object_links
                          WHERE (from_object_id=objects.object_id
                                 OR to_object_id=objects.object_id)
                            AND (source_note_id IS NULL OR source_note_id != ?)
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM encounter_tables
                          WHERE location_object_id=objects.object_id
                            AND (source_note_id IS NULL OR source_note_id != ?)
                      )
                    """,
                    (source_note_id, source_note_id, source_note_id),
                )
        db.executemany(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description,
                source_note_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(object_id) DO UPDATE SET
                name=excluded.name,
                object_type=excluded.object_type,
                geography_kind=excluded.geography_kind,
                description=excluded.description,
                source_note_id=excluded.source_note_id,
                source_row=excluded.source_row
            """,
            [
                (
                    node["object_id"],
                    node["name"],
                    node["object_type"],
                    node["geography_kind"],
                    node["description"],
                    source_note_id,
                    node["source_row"],
                )
                for node in nodes
            ],
        )
        db.executemany(
            """
            INSERT INTO encounter_tables (
                table_id, name, source_file, roll_die, location_object_id,
                binding_basis, source_note_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(table_id) DO UPDATE SET
                name=excluded.name,
                source_file=excluded.source_file,
                roll_die=excluded.roll_die,
                location_object_id=excluded.location_object_id,
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
                    binding["location_object_id"],
                    binding["binding_basis"],
                    source_note_id,
                    binding["source_row"],
                )
                for binding in bindings
            ],
        )
        db.executemany(
            """
            INSERT INTO object_links (
                relation, from_object_id, to_object_id, distance_min_miles,
                distance_max_miles, distance_qualifier, direction, travel_mode,
                day_minutes, night_minutes, notes, source_note_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    link["relation"],
                    link["from_object_id"],
                    link["to_object_id"],
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


def get_encounter_tables(db, location_object_id: str) -> list[dict]:
    """Get only encounter tables explicitly bound to this room or region."""
    return [
        dict(row)
        for row in db.execute(
            """
            SELECT * FROM encounter_tables
            WHERE location_object_id=?
            ORDER BY name, table_id
            """,
            (location_object_id,),
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


def get_objects(db) -> list[dict]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT * FROM objects ORDER BY name, object_id"
        ).fetchall()
    ]


def create_object(
    db,
    object_id: str,
    name: str,
    object_type: str,
    description: str = "",
    geography_kind: str = "unspecified",
) -> str:
    object_id = object_id.strip()
    name = name.strip()
    object_type = object_type.strip()
    if not object_id or not name or not object_type:
        raise ValueError("Object ID, name, and type cannot be empty.")
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (object_id, name, object_type, geography_kind, description.strip()),
    )
    db.commit()
    return object_id


def create_object_link(
    db,
    relation: str,
    from_object_id: str,
    to_object_id: str,
    properties: dict | None = None,
    notes: str = "",
) -> int:
    if not relation.strip():
        raise ValueError("Object relation cannot be empty.")
    if from_object_id == to_object_id:
        raise ValueError("An object cannot be related to itself.")
    if properties is not None and not isinstance(properties, dict):
        raise TypeError("Object link properties must be a dictionary.")
    if relation.strip() == "contains":
        cycle = db.execute(
            """
            WITH RECURSIVE descendants(object_id) AS (
                SELECT to_object_id FROM object_links
                WHERE relation='contains' AND from_object_id=?
                UNION
                SELECT object_links.to_object_id
                FROM object_links
                JOIN descendants
                  ON object_links.from_object_id=descendants.object_id
                WHERE object_links.relation='contains'
            )
            SELECT 1 FROM descendants WHERE object_id=? LIMIT 1
            """,
            (to_object_id, from_object_id),
        ).fetchone()
        if cycle:
            raise ValueError("Containment links cannot create a cycle.")
    cursor = db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            relation.strip(),
            from_object_id,
            to_object_id,
            json.dumps(properties or {}, ensure_ascii=False),
            notes.strip(),
        ),
    )
    db.commit()
    return cursor.lastrowid


def get_object_links(db, relation: str | None = None) -> list[dict]:
    if relation is None:
        rows = db.execute(
            """
            SELECT * FROM object_links
            ORDER BY relation, from_object_id, to_object_id, id
            """
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT * FROM object_links WHERE relation=?
            ORDER BY from_object_id, to_object_id, id
            """,
            (relation,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_geography_objects(
    db, geography_kind: str = "Regions and settlements"
) -> list[dict]:
    """Query objects classified for geography, independent of their relations."""
    if geography_kind == "Regions and settlements":
        rows = db.execute(
            """
            SELECT * FROM objects
            WHERE geography_kind COLLATE NOCASE IN ('region', 'settlement')
            ORDER BY name, object_id
            """
        ).fetchall()
    elif geography_kind == "All geographic kinds":
        rows = db.execute(
            """
            SELECT * FROM objects
            WHERE geography_kind NOT IN ('', 'unspecified')
            ORDER BY name, object_id
            """
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT * FROM objects
            WHERE geography_kind=? COLLATE NOCASE
            ORDER BY name, object_id
            """,
            (geography_kind,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_navigation_exits(db, object_id: str) -> list[dict]:
    """Return navigation and containment interpretations of object links."""
    nodes = {
        row["object_id"]: dict(row)
        for row in db.execute("SELECT * FROM objects")
    }
    if object_id not in nodes:
        raise KeyError(f"Unknown object: {object_id}")

    exits = []
    for row in db.execute(
        """
        SELECT * FROM object_links
        WHERE relation='route'
          AND (from_object_id=? OR to_object_id=?)
        ORDER BY id
        """,
        (object_id, object_id),
    ):
        link = dict(row)
        target_id = (
            link["to_object_id"]
            if link["from_object_id"] == object_id
            else link["from_object_id"]
        )
        exits.append({
            "relation": "route",
            "target": nodes[target_id],
            "link": link,
        })

    for row in db.execute(
        """
        SELECT * FROM object_links
        WHERE relation='contains' AND from_object_id=?
        ORDER BY id
        """,
        (object_id,),
    ):
        link = dict(row)
        exits.append({
            "relation": "contains",
            "target": nodes[link["to_object_id"]],
            "link": link,
        })
    exits.sort(
        key=lambda item: (
            item["relation"],
            item["target"]["name"].casefold(),
            item["target"]["object_id"],
        )
    )
    return exits


def find_route(
    db,
    start_object_id: str,
    end_object_id: str,
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
    objects = {
        row["object_id"]: dict(row)
        for row in db.execute("SELECT * FROM objects")
    }
    if start_object_id not in objects:
        raise KeyError(f"Unknown start object: {start_object_id}")
    if end_object_id not in objects:
        raise KeyError(f"Unknown end object: {end_object_id}")
    if start_object_id == end_object_id:
        return {
            "objects": [objects[start_object_id]],
            "links": [],
            "total": 0,
            "unit": unit,
            "metric": metric,
            "travel_period": travel_period if metric == "time" else None,
        }

    adjacency: dict[str, list[tuple[str, dict, float]]] = {
        object_id: [] for object_id in objects
    }
    for row in db.execute(
        "SELECT * FROM object_links WHERE relation='route' ORDER BY id"
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
        adjacency[link["from_object_id"]].append(
            (link["to_object_id"], link, float(weight))
        )
        adjacency[link["to_object_id"]].append(
            (link["from_object_id"], link, float(weight))
        )

    distances = {start_object_id: 0.0}
    previous: dict[str, tuple[str, dict]] = {}
    queue = [(0.0, start_object_id)]
    while queue:
        distance, current = heapq.heappop(queue)
        if distance != distances.get(current):
            continue
        if current == end_object_id:
            break
        for neighbor, link, weight in adjacency[current]:
            candidate = distance + weight
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = (current, link)
                heapq.heappush(queue, (candidate, neighbor))

    if end_object_id not in distances:
        return None
    path_object_ids = [end_object_id]
    path_links = []
    current = end_object_id
    while current != start_object_id:
        parent, link = previous[current]
        path_links.append(_traversed_link(link, parent, current))
        path_object_ids.append(parent)
        current = parent
    path_object_ids.reverse()
    path_links.reverse()
    return {
        "objects": [objects[object_id] for object_id in path_object_ids],
        "links": path_links,
        "total": _clean_number(distances[end_object_id]),
        "unit": unit,
        "metric": metric,
        "travel_period": travel_period if metric == "time" else None,
    }


def _traversed_link(link: dict, from_object_id: str, to_object_id: str) -> dict:
    traversed = dict(link)
    traversed["traversed_from_object_id"] = from_object_id
    traversed["traversed_to_object_id"] = to_object_id
    traversed["traverses_recorded_direction"] = (
        link["from_object_id"] == from_object_id
        and link["to_object_id"] == to_object_id
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
            "geography objects",
            "object relations",
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
        if label == "Geography Objects":
            if not values["object id"] or not values["name"]:
                raise ValueError("Geography object ID and name are required")
            output.append({
                "object_id": values["object id"],
                "name": values["name"],
                "object_type": values["object type"],
                "geography_kind": values["geography kind"],
                "description": values["description"],
                "source_row": source_row,
            })
        elif label == "Object Relations":
            output.append({
                "relation": values["relation"].casefold(),
                "from_object_id": values["from object id"],
                "to_object_id": values["to object id"],
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
                "location_object_id": values["location object id"],
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

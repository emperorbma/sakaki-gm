# db.py — campaign SQLite files and schema migrations
import json
import sqlite3
from pathlib import Path

from sakaki_gm.skill_db import DEFAULT_5E_SKILLS, move_legacy_skills_to_notes

PACKAGE_DIR = Path(__file__).resolve().parent
# src/sakaki_gm -> src -> repository root (where pygm_campaign.db lives)
PROJECT_ROOT = PACKAGE_DIR.parent.parent
LEGACY_DB = PROJECT_ROOT / "pygm_campaign.db"


def get_db(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    migrate(db)
    return db


def migrate(db):
    cur = db.cursor()
    cur.execute("PRAGMA user_version")
    version = cur.fetchone()[0]

    if version < 1:
        cur.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, data TEXT NOT NULL, is_player BOOLEAN DEFAULT 0);
        CREATE TABLE world (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, speaker TEXT, message TEXT);
        INSERT INTO metadata (key, value) VALUES ('campaign_name', 'New Campaign');
        INSERT INTO world (key, value) VALUES ('location', 'A quiet tavern');
        INSERT INTO world (key, value) VALUES ('events', '[]');
        PRAGMA user_version = 1;
        """)
        db.commit()

    if version < 2:
        cur.executescript("""
        CREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT, content TEXT);
        PRAGMA user_version = 2;
        """)
        db.commit()

    if version < 3:
        cur.executescript("""
        CREATE TABLE campaign_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT OR REPLACE INTO campaign_settings (key, value) VALUES
        ('stat_system', '5e'),
        ('ability_scores', '["str","dex","con","int","wis","cha"]'),
        ('proficiency_bonus', '2');
        PRAGMA user_version = 3;
        """)
        db.commit()

    if version < 4:
        cur.executescript("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE character_items (
            character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            attuned BOOLEAN NOT NULL DEFAULT 0,
            PRIMARY KEY (character_id, item_id)
        );
        PRAGMA user_version = 4;
        """)
        db.commit()

    if version < 5:
        cur.executescript("""
        CREATE TABLE quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE quest_characters (
            quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
            character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            PRIMARY KEY (quest_id, character_id)
        );
        PRAGMA user_version = 5;
        """)
        db.commit()

    if version < 6:
        cur.executescript("""
        ALTER TABLE quests
        ADD COLUMN status TEXT NOT NULL DEFAULT '';
        PRAGMA user_version = 6;
        """)
        db.commit()

    if version < 7:
        cur.executescript("""
        ALTER TABLE character_items
        ADD COLUMN equipment_location TEXT NOT NULL DEFAULT '';
        PRAGMA user_version = 7;
        """)
        db.commit()

    if version < 8:
        cur.executescript("""
        CREATE TABLE campaign_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            ability TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE character_skills (
            character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            skill_id INTEGER NOT NULL REFERENCES campaign_skills(id) ON DELETE CASCADE,
            proficient BOOLEAN NOT NULL DEFAULT 0,
            expertise BOOLEAN NOT NULL DEFAULT 0,
            CHECK (expertise = 0 OR proficient = 1),
            PRIMARY KEY (character_id, skill_id)
        );
        """)
        ability_by_name = {name.casefold(): ability for name, ability in DEFAULT_5E_SKILLS}
        setting = db.execute(
            "SELECT value FROM campaign_settings WHERE key='skills_list'"
        ).fetchone()
        skill_names = [name for name, _ability in DEFAULT_5E_SKILLS]
        if setting:
            try:
                configured = json.loads(setting["value"])
            except (TypeError, json.JSONDecodeError):
                configured = None
            if isinstance(configured, list):
                skill_names = [
                    name.strip() for name in configured
                    if isinstance(name, str) and name.strip()
                ]
        db.executemany(
            "INSERT INTO campaign_skills (name, ability) VALUES (?, ?)",
            [
                (name, ability_by_name.get(name.casefold(), ""))
                for name in skill_names
            ],
        )
        for row in db.execute("SELECT id, data FROM characters").fetchall():
            try:
                data = json.loads(row["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or "skills" not in data:
                continue
            move_legacy_skills_to_notes(data)
            db.execute(
                "UPDATE characters SET data=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), row["id"]),
            )
        db.execute("DELETE FROM campaign_settings WHERE key='skills_list'")
        db.execute("PRAGMA user_version = 8")
        db.commit()

    if version < 9:
        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(characters)")
        }
        if "is_player" in columns:
            db.execute("ALTER TABLE characters DROP COLUMN is_player")
        if "campaign_role" not in columns:
            db.execute(
                "ALTER TABLE characters "
                "ADD COLUMN campaign_role TEXT NOT NULL DEFAULT ''"
            )
        db.execute("PRAGMA user_version = 9")
        db.commit()

    if version < 10:
        cur.executescript("""
        CREATE TABLE map_nodes (
            node_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            node_type TEXT NOT NULL DEFAULT '',
            spatial_kind TEXT NOT NULL DEFAULT 'unspecified',
            description TEXT NOT NULL DEFAULT '',
            coordinate_x REAL,
            coordinate_y REAL,
            coordinate_system TEXT NOT NULL DEFAULT '',
            coordinate_precision TEXT NOT NULL DEFAULT '',
            coordinate_basis TEXT NOT NULL DEFAULT '',
            source_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
            source_row TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE map_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relation TEXT NOT NULL,
            from_node_id TEXT NOT NULL REFERENCES map_nodes(node_id) ON DELETE CASCADE,
            to_node_id TEXT NOT NULL REFERENCES map_nodes(node_id) ON DELETE CASCADE,
            distance_min_miles REAL,
            distance_max_miles REAL,
            distance_qualifier TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT '',
            travel_mode TEXT NOT NULL DEFAULT '',
            day_minutes INTEGER,
            night_minutes INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            source_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
            source_row TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX map_links_from_relation
            ON map_links(from_node_id, relation);
        CREATE INDEX map_links_to_relation
            ON map_links(to_node_id, relation);
        PRAGMA user_version = 10;
        """)
        db.commit()

    if version < 11:
        node_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(map_nodes)")
        }
        if "spatial_kind" not in node_columns:
            db.execute(
                "ALTER TABLE map_nodes "
                "ADD COLUMN spatial_kind TEXT NOT NULL DEFAULT 'unspecified'"
            )
        if "coordinate_precision" not in node_columns:
            db.execute(
                "ALTER TABLE map_nodes "
                "ADD COLUMN coordinate_precision TEXT NOT NULL DEFAULT ''"
            )
        cur.executescript("""
        CREATE TABLE encounter_tables (
            table_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            roll_die TEXT NOT NULL DEFAULT '',
            location_node_id TEXT NOT NULL
                REFERENCES map_nodes(node_id) ON DELETE CASCADE,
            binding_basis TEXT NOT NULL DEFAULT '',
            source_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
            source_row TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX encounter_tables_by_location
            ON encounter_tables(location_node_id);
        CREATE TABLE encounter_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id TEXT NOT NULL
                REFERENCES encounter_tables(table_id) ON DELETE CASCADE,
            roll TEXT NOT NULL,
            roll_min INTEGER NOT NULL,
            roll_max INTEGER NOT NULL,
            encounter TEXT NOT NULL,
            cr_xp TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            source_row TEXT NOT NULL DEFAULT '',
            CHECK (roll_min >= 1 AND roll_max >= roll_min)
        );
        CREATE INDEX encounter_entries_by_table_roll
            ON encounter_entries(table_id, roll_min, roll_max);
        PRAGMA user_version = 11;
        """)
        db.commit()

    if version < 12:
        db.execute("ALTER TABLE map_links RENAME TO object_links")
        db.execute("ALTER TABLE object_links RENAME COLUMN from_node_id TO from_object_id")
        db.execute("ALTER TABLE object_links RENAME COLUMN to_node_id TO to_object_id")
        db.execute("ALTER TABLE map_nodes RENAME TO objects")
        db.execute("ALTER TABLE objects RENAME COLUMN node_id TO object_id")
        db.execute("ALTER TABLE objects RENAME COLUMN node_type TO object_type")
        db.execute("ALTER TABLE objects RENAME COLUMN spatial_kind TO geography_kind")
        db.execute(
            "ALTER TABLE encounter_tables "
            "RENAME COLUMN location_node_id TO location_object_id"
        )
        db.execute("DROP INDEX map_links_from_relation")
        db.execute("DROP INDEX map_links_to_relation")
        db.execute(
            "CREATE INDEX object_links_from_relation "
            "ON object_links(from_object_id, relation)"
        )
        db.execute(
            "CREATE INDEX object_links_to_relation "
            "ON object_links(to_object_id, relation)"
        )
        db.execute("DROP INDEX encounter_tables_by_location")
        db.execute(
            "CREATE INDEX encounter_tables_by_location "
            "ON encounter_tables(location_object_id)"
        )
        db.execute("PRAGMA user_version = 12")
        db.commit()

    if version < 13:
        with db:
            db.execute(
                "ALTER TABLE object_links "
                "ADD COLUMN properties TEXT NOT NULL DEFAULT '{}'"
            )
            db.execute(
                "ALTER TABLE characters ADD COLUMN object_id TEXT "
                "REFERENCES objects(object_id) ON DELETE RESTRICT"
            )

            for row in db.execute(
                "SELECT id, name FROM characters ORDER BY id"
            ).fetchall():
                object_id = f"character:{row['id']}"
                container_id = f"inventory:{row['id']}"
                db.execute(
                    """
                    INSERT INTO objects (
                        object_id, name, object_type, geography_kind, description
                    ) VALUES (?, ?, 'character', 'unspecified', '')
                    """,
                    (object_id, row["name"]),
                )
                db.execute(
                    "UPDATE characters SET object_id=? WHERE id=?",
                    (object_id, row["id"]),
                )
                db.execute(
                    """
                    INSERT INTO objects (
                        object_id, name, object_type, geography_kind, description
                    ) VALUES (?, ?, 'container', 'unspecified', '')
                    """,
                    (container_id, f"{row['name']}'s Inventory"),
                )
                db.execute(
                    """
                    INSERT INTO object_links (
                        relation, from_object_id, to_object_id, properties
                    ) VALUES ('contains', ?, ?, ?)
                    """,
                    (
                        object_id,
                        container_id,
                        json.dumps({"role": "inventory"}),
                    ),
                )

            for row in db.execute(
                "SELECT id, name, description FROM items ORDER BY id"
            ).fetchall():
                object_id = f"catalog-item:{row['id']}"
                db.execute(
                    """
                    INSERT INTO objects (
                        object_id, name, object_type, geography_kind, description
                    ) VALUES (?, ?, 'item', 'unspecified', ?)
                    """,
                    (object_id, row["name"], row["description"]),
                )

            inventory_rows = db.execute(
                """
                SELECT character_items.character_id, character_items.item_id,
                       character_items.quantity, character_items.attuned,
                       character_items.equipment_location
                FROM character_items
                ORDER BY character_items.character_id, character_items.item_id
                """
            ).fetchall()
            db.executemany(
                """
                INSERT INTO object_links (
                    relation, from_object_id, to_object_id, properties
                ) VALUES ('contains', ?, ?, ?)
                """,
                [
                    (
                        f"inventory:{row['character_id']}",
                        f"catalog-item:{row['item_id']}",
                        json.dumps({
                            "quantity": row["quantity"],
                            "attuned": bool(row["attuned"]),
                            "equipment_location": row["equipment_location"],
                        }, ensure_ascii=False),
                    )
                    for row in inventory_rows
                ],
            )
            db.execute("CREATE UNIQUE INDEX characters_by_object ON characters(object_id)")
            db.execute(
                """
                CREATE UNIQUE INDEX item_objects_by_name
                ON objects(name COLLATE NOCASE) WHERE object_type='item'
                """
            )
            db.execute("DROP TABLE character_items")
            db.execute("DROP TABLE items")
            db.execute("PRAGMA user_version = 13")

    if version < 14:
        db.execute(
            "ALTER TABLE objects ADD COLUMN properties TEXT NOT NULL DEFAULT '{}'"
        )
        db.execute("PRAGMA user_version = 14")
        db.commit()

    if version < 15:
        from sakaki_gm.item_db import migrate_catalog_contains_to_instances

        with db:
            migrate_catalog_contains_to_instances(db)
            db.execute("PRAGMA user_version = 15")

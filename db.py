# db.py — campaign SQLite files and schema migrations
import json
import sqlite3
from pathlib import Path

from skill_db import DEFAULT_5E_SKILLS, move_legacy_skills_to_notes

APP_DIR = Path(__file__).resolve().parent
LEGACY_DB = APP_DIR / "pygm_campaign.db"


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

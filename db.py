# db.py — campaign SQLite files and schema migrations
import sqlite3
from pathlib import Path

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
        ('proficiency_bonus', '2'),
        ('skills_list', '["Acrobatics","Animal Handling","Arcana","Athletics","Deception","History","Insight","Intimidation","Investigation","Medicine","Nature","Perception","Performance","Persuasion","Religion","Sleight of Hand","Stealth","Survival"]');
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

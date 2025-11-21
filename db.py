# db.py — Now with campaign-level stat system config
import sqlite3
import os
import json

DB_FILE = "pygm_campaign.db"
CURRENT_VERSION = 3  # Bumped for new table

def get_db():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
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
        -- Default to Standard 5e
        INSERT OR REPLACE INTO campaign_settings (key, value) VALUES
        ('stat_system', '5e'),
        ('ability_scores', '["str","dex","con","int","wis","cha"]'),
        ('proficiency_bonus', '2'),
        ('skills_list', '["Acrobatics","Animal Handling","Arcana","Athletics","Deception","History","Insight","Intimidation","Investigation","Medicine","Nature","Perception","Performance","Persuasion","Religion","Sleight of Hand","Stealth","Survival"]');
        PRAGMA user_version = 3;
        """)
        db.commit()
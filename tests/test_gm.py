import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from sakaki_gm.campaigns import (
    active_path,
    bootstrap,
    create_campaign_backup,
    create_campaign,
    list_campaign_backups,
    register_file,
    remove_campaign,
    restore_campaign_from_backup,
)
from sakaki_gm.character_db import create_character
from sakaki_gm.db import LEGACY_DB, get_db, migrate
from sakaki_gm.gm import (
    apply_proposals,
    apply_updates,
    build_messages,
    citation_messages,
    normalize_proposals,
    parse_reply,
)
from sakaki_gm.llm_client import complete, list_models
from sakaki_gm.llm_config import load_config, model_profile_key
from sakaki_gm.object_graph_db import create_object, create_object_link
from sakaki_gm.rag_tools import execute_campaign_tool
from sakaki_gm.item_db import (
    create_item,
    delete_item,
    get_character_inventory_tree,
    get_character_items,
    get_items,
    save_character_inventory,
    set_character_items,
)
from sakaki_gm.skill_db import (
    DEFAULT_5E_SKILLS,
    get_character_skills,
    get_skills,
    set_character_skills,
    skill_bonus,
)
from sakaki_gm.quest_db import (
    delete_quest,
    get_quest_characters,
    get_quests,
    save_quest,
)
from sakaki_gm.ui.modules.importer import (
    import_campaign_files,
    import_campaign_folder,
    import_document_files,
    import_documents,
    parse_markdown_character,
)

LIVE_DB = LEGACY_DB
FIXTURE = Path(__file__).parent / "fixtures" / "serina_headings.md"


def drop_geography_schema_for_legacy_migration(db):
    prepare_pre_object_graph_inventory_schema(db)
    db.execute("DROP TABLE encounter_entries")
    db.execute("DROP TABLE encounter_tables")
    db.execute("DROP TABLE object_links")
    db.execute("DROP TABLE objects")


def prepare_pre_object_graph_inventory_schema(db):
    character_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(characters)")
    }
    if "object_id" not in character_columns:
        return

    db.execute("DROP INDEX item_objects_by_name")
    db.execute("ALTER TABLE objects DROP COLUMN properties")
    item_rows = db.execute(
        "SELECT object_id, name, description FROM objects WHERE object_type='item'"
    ).fetchall()
    item_ids = {
        row["object_id"]: index
        for index, row in enumerate(item_rows, start=1)
    }
    db.execute(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            description TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.executemany(
        "INSERT INTO items (id, name, description) VALUES (?, ?, ?)",
        [
            (item_ids[row["object_id"]], row["name"], row["description"])
            for row in item_rows
        ],
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

    migrated_object_ids = {
        row["object_id"]
        for row in db.execute(
            """
            SELECT object_id FROM objects
            WHERE object_type IN ('character', 'container', 'item')
            """
        )
    }
    for character in db.execute(
        "SELECT id, object_id FROM characters ORDER BY id"
    ).fetchall():
        inventory_id = f"inventory:{character['id']}"
        for link in db.execute(
            """
            SELECT to_object_id, properties FROM object_links
            WHERE from_object_id=? AND relation='contains'
            """,
            (inventory_id,),
        ).fetchall():
            item_id = item_ids.get(link["to_object_id"])
            if item_id is None:
                continue
            properties = json.loads(link["properties"])
            db.execute(
                """
                INSERT INTO character_items (
                    character_id, item_id, quantity, attuned, equipment_location
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    character["id"],
                    item_id,
                    properties["quantity"],
                    int(bool(properties.get("attuned"))),
                    properties.get("equipment_location", ""),
                ),
            )

    if migrated_object_ids:
        placeholders = ",".join("?" for _ in migrated_object_ids)
        params = tuple(migrated_object_ids)
        db.execute(
            f"""
            DELETE FROM object_links
            WHERE from_object_id IN ({placeholders})
               OR to_object_id IN ({placeholders})
            """,
            (*params, *params),
        )
    db.execute("DROP INDEX characters_by_object")
    db.execute("ALTER TABLE characters DROP COLUMN object_id")
    db.execute("ALTER TABLE object_links DROP COLUMN properties")
    db.executemany(
        "DELETE FROM objects WHERE object_id=?",
        [(object_id,) for object_id in migrated_object_ids],
    )


@pytest.fixture(scope="session", autouse=True)
def live_database_stays_put():
    before = LIVE_DB.read_bytes() if LIVE_DB.is_file() else None
    digest = hashlib.sha256(before).hexdigest() if before is not None else None
    yield
    if before is None:
        return
    after = LIVE_DB.read_bytes()
    assert hashlib.sha256(after).hexdigest() == digest
    assert after == before


def test_serina_headings_round_trip_fields():
    data = parse_markdown_character(FIXTURE.read_text(encoding="utf-8"))
    assert data["name"] == "Serina"
    assert "Wizard" in data["class"]
    assert data["class"] != "2 (XP: 350 / 900)"
    assert data["level"] == 2
    assert data["experience"] == 350
    assert data["hp_current"] == 11
    assert data["hp_max"] == 11
    assert data["ac"] == 12
    assert "Shield" in data["ac_note"]
    assert data["initiative"] == 2
    assert [data[key] for key in ("str", "dex", "con", "int", "wis", "cha")] == [8, 14, 12, 15, 10, 10]
    assert data["attuned"] == [
        "Serpentine Lunar Echo Ward",
        "Lunar Scepter",
        "Lunar Crown",
    ]
    for name in data["attuned"]:
        assert name not in data["inventory"]
    assert any(item.startswith("Backpack: spellbook, 10 gp") for item in data["inventory"])
    assert "Portent" in data["features"]
    assert "Shield" in data["spells"]


def test_stat_block_and_labeled_strength():
    block = parse_markdown_character(
        "# Scaled Watcher\n"
        "**HP**: 60 (8d12 + 16)\n"
        "**AC**: 12\n"
        "**STR** 19 (+4)  **DEX** 14 (+2)  **CON** 14 (+2)\n"
        "**INT** 2 (-4)  **WIS** 12 (+1)  **CHA** 6 (-2)\n"
    )
    assert block["hp_current"] == 60
    assert block["hp_max"] == 60
    assert block["ac"] == 12
    assert [block[key] for key in ("str", "dex", "con", "int", "wis", "cha")] == [19, 14, 14, 2, 12, 6]

    labeled = parse_markdown_character("# Kael\n**Class**: Ranger 3\n- Strength: 14 (+2)\n")
    assert labeled["class"] == "Ranger 3"
    assert labeled["level"] == 3
    assert labeled["str"] == 14


def test_folder_import_preserves_manual_campaign_role(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(
        db,
        "Serina",
        {"name": "Serina", "class": "broken", "hp_current": 1},
        campaign_role="Party member",
    )
    folder = tmp_path / "binder"
    folder.mkdir()
    (folder / "Serina - Character Sheet.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "Serina - Quest Log.md").write_text("# Quest\n\nReturn to Eldenport.\n", encoding="utf-8")
    (folder / "Geography of the Eldenweave.md").write_text("# Geography\n\nStarfall's End.\n", encoding="utf-8")
    (folder / "Instructions for Grok.txt").write_text("You are GM for the world of Eldenweave.\n", encoding="utf-8")
    (folder / "outskirts.csv").write_text("Roll,Encounter\n1,Traveler\n", encoding="utf-8")

    summary = import_campaign_folder(db, folder)
    assert "characters" in summary
    row = db.execute(
        "SELECT campaign_role, data FROM characters WHERE name='Serina'"
    ).fetchone()
    assert row["campaign_role"] == "Party member"
    imported = json.loads(row["data"])
    assert "Wizard" in imported["class"]
    assert "skills" not in imported
    assert "Arcana +5" in imported["notes"]
    titles = {row["title"] for row in db.execute("SELECT title FROM notes")}
    assert "Serina - Quest Log" in titles
    assert "Geography of the Eldenweave" in titles
    assert "Instructions for Grok" in titles
    assert "outskirts" in titles
    location = db.execute("SELECT value FROM world WHERE key='location'").fetchone()["value"]
    assert location == "A quiet tavern"
    db.close()


def test_v8_migration_replaces_player_flag_with_blank_campaign_role(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    try:
        create_character(db, "Serina", {"name": "Serina"})
        db.execute("ALTER TABLE characters DROP COLUMN campaign_role")
        db.execute("ALTER TABLE characters ADD COLUMN is_player BOOLEAN DEFAULT 0")
        db.execute("UPDATE characters SET is_player = 1")
        prepare_pre_object_graph_inventory_schema(db)
        drop_geography_schema_for_legacy_migration(db)
        db.execute("PRAGMA user_version = 8")

        migrate(db)

        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(characters)")
        }
        assert "is_player" not in columns
        assert "campaign_role" in columns
        assert db.execute(
            "SELECT campaign_role FROM characters WHERE name='Serina'"
        ).fetchone()["campaign_role"] == ""
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
    finally:
        db.close()


def test_import_documents_stores_reference_files_only(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    folder = tmp_path / "binder"
    folder.mkdir()
    (folder / "Serina - Character Sheet.md").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (folder / "Geography of the Eldenweave.md").write_text(
        "# Geography\n\nStarfall's End.\n", encoding="utf-8"
    )
    (folder / "Instructions for Grok.txt").write_text(
        "You are GM for the world of Eldenweave.\n", encoding="utf-8"
    )
    (folder / "Voryn's Cultists - Generic Stat Blocks.md").write_text(
        "# Voryn's Cultists\n\nHP 33, AC 13\n", encoding="utf-8"
    )
    (folder / "outskirts.csv").write_text("Roll,Encounter\n1,Traveler\n", encoding="utf-8")

    summary = import_documents(db, folder)
    assert summary.startswith("Stored 4 documents")
    assert "Skipped 1 character" in summary
    assert db.execute("SELECT count(*) FROM characters").fetchone()[0] == 0
    titles = {row["title"] for row in db.execute("SELECT title FROM notes")}
    assert titles == {
        "Geography of the Eldenweave",
        "Instructions for Grok",
        "Voryn's Cultists - Generic Stat Blocks",
        "outskirts",
    }

    (folder / "Geography of the Eldenweave.md").write_text(
        "# Geography\n\nEldenport.\n", encoding="utf-8"
    )
    import_documents(db, folder)
    rows = db.execute(
        "SELECT content FROM notes WHERE title='Geography of the Eldenweave'"
    ).fetchall()
    assert len(rows) == 1
    assert "Eldenport" in rows[0]["content"]
    db.close()


def test_individual_file_imports_only_selected_files(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    selected = tmp_path / "Selected.md"
    ignored = tmp_path / "Ignored.md"
    selected.write_text("# Selected note\n\nKeep this.\n", encoding="utf-8")
    ignored.write_text("# Ignored note\n\nDo not import this.\n", encoding="utf-8")

    summary = import_document_files(db, [selected])
    assert summary.startswith("Stored 1 documents")
    rows = db.execute("SELECT title, content FROM notes").fetchall()
    assert [(row["title"], row["content"]) for row in rows] == [
        ("Selected", "# Selected note\n\nKeep this.\n")
    ]

    character = tmp_path / "New Character Sheet.md"
    character.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    import_campaign_files(db, [character])
    assert db.execute("SELECT name FROM characters").fetchone()["name"] == "Serina"
    assert db.execute("SELECT count(*) FROM notes").fetchone()[0] == 1
    db.close()


def test_two_bottles_leave_the_first_file_unchanged(tmp_path):
    legacy = tmp_path / "legacy.db"
    db = get_db(legacy)
    create_character(db, "Keep", {"name": "Keep", "hp_current": 11, "hp_max": 11})
    db.close()
    digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
    config = tmp_path / "config.json"
    bottles = tmp_path / "bottles"
    _cfg, path = bootstrap(config, legacy, bottles)
    assert path.resolve() == legacy.resolve()
    create_campaign(config, "Other", bottles)
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == digest
    loaded = load_config(config)
    assert len(loaded["campaigns"]) == 2
    other = active_path(loaded)
    assert other.resolve() != legacy.resolve()
    other_db = get_db(other)
    names = [row["name"] for row in other_db.execute("SELECT name FROM characters")]
    other_db.close()
    assert names == []
    kept = get_db(legacy)
    assert [row["name"] for row in kept.execute("SELECT name FROM characters")] == ["Keep"]
    kept.close()
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == digest


def test_campaign_backup_creates_timestamped_snapshot(tmp_path):
    source_path = tmp_path / "campaign.db"
    db = get_db(source_path)
    db.execute(
        "UPDATE metadata SET value='Snapshot test' WHERE key='campaign_name'"
    )
    db.execute(
        "UPDATE world SET value='Unsaved location' WHERE key='location'"
    )

    backup_path = create_campaign_backup(db, source_path)

    assert backup_path.parent == tmp_path / "backups"
    assert backup_path.name.startswith("campaign-")
    assert backup_path.name.endswith(".db")
    backup_db = sqlite3.connect(backup_path)
    try:
        assert backup_db.execute(
            "SELECT value FROM metadata WHERE key='campaign_name'"
        ).fetchone()[0] == "Snapshot test"
        assert backup_db.execute(
            "SELECT value FROM world WHERE key='location'"
        ).fetchone()[0] == "Unsaved location"
    finally:
        backup_db.close()

    db.execute("UPDATE world SET value='Changed after backup' WHERE key='location'")
    db.commit()
    verification_db = sqlite3.connect(backup_path)
    try:
        assert verification_db.execute(
            "SELECT value FROM world WHERE key='location'"
        ).fetchone()[0] == "Unsaved location"
    finally:
        verification_db.close()
    db.close()


def test_restore_campaign_from_backup_replaces_live_state_and_keeps_safety_snapshot(
    tmp_path,
):
    source_path = tmp_path / "campaign.db"
    db = get_db(source_path)
    db.execute(
        "UPDATE world SET value='Before backup' WHERE key='location'"
    )
    db.commit()
    backup_path = create_campaign_backup(db, source_path)

    db.execute(
        "UPDATE world SET value='Changed after backup' WHERE key='location'"
    )
    db.commit()
    assert db.execute(
        "SELECT value FROM world WHERE key='location'"
    ).fetchone()["value"] == "Changed after backup"

    safety_path = restore_campaign_from_backup(db, source_path, backup_path)

    assert safety_path is not None
    assert safety_path.is_file()
    assert safety_path != backup_path
    assert db.execute(
        "SELECT value FROM world WHERE key='location'"
    ).fetchone()["value"] == "Before backup"
    safety_db = sqlite3.connect(safety_path)
    try:
        assert safety_db.execute(
            "SELECT value FROM world WHERE key='location'"
        ).fetchone()[0] == "Changed after backup"
    finally:
        safety_db.close()
    listed = list_campaign_backups(source_path)
    assert backup_path in listed
    assert safety_path in listed
    db.close()


def test_restore_campaign_from_backup_rejects_missing_or_live_file(tmp_path):
    source_path = tmp_path / "campaign.db"
    db = get_db(source_path)
    with pytest.raises(FileNotFoundError):
        restore_campaign_from_backup(
            db,
            source_path,
            tmp_path / "missing.db",
            make_safety_backup=False,
        )
    with pytest.raises(ValueError):
        restore_campaign_from_backup(
            db,
            source_path,
            source_path,
            make_safety_backup=False,
        )
    db.close()


def test_bootstrap_recovers_to_existing_campaign_when_active_path_is_missing(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    legacy = tmp_path / "legacy.db"
    db = get_db(legacy)
    create_character(db, "Keep", {"name": "Keep", "hp_current": 11})
    db.close()
    config.write_text(
        json.dumps({
            "active_campaign": "missing",
            "campaigns": [
                {"id": "missing", "path": str(tmp_path / "gone.db")},
                {"id": "available", "path": str(legacy)},
            ],
        }),
        encoding="utf-8",
    )

    loaded, path = bootstrap(config, legacy, campaigns)
    assert path.resolve() == legacy.resolve()
    assert loaded["active_campaign"] == "available"
    assert not list(campaigns.glob("*.db"))
    recovered = get_db(path)
    try:
        assert recovered.execute(
            "SELECT 1 FROM characters WHERE name='Keep'"
        ).fetchone() is not None
    finally:
        recovered.close()


def test_remove_deletes_managed_files_only(tmp_path):
    legacy = tmp_path / "legacy.db"
    get_db(legacy).close()
    config = tmp_path / "config.json"
    bottles = tmp_path / "bottles"
    bootstrap(config, legacy, bottles)
    cid = create_campaign(config, "Disposable", bottles)
    managed = active_path(load_config(config))
    assert managed.is_file()
    remove_campaign(config, cid, bottles)
    assert not managed.exists()
    assert legacy.is_file()
    legacy_id = register_file(config, legacy)
    remove_campaign(config, legacy_id, bottles)
    assert legacy.is_file()


def test_remove_campaign_recovers_to_valid_database_not_stale_entry(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    legacy = tmp_path / "legacy.db"
    get_db(legacy).close()
    config.write_text(
        json.dumps({
            "active_campaign": "temporary",
            "campaigns": [
                {"id": "missing", "path": str(tmp_path / "gone.db")},
                {"id": "legacy", "path": str(legacy)},
            ],
        }),
        encoding="utf-8",
    )
    temporary_id = create_campaign(config, "Temporary", campaigns)
    temporary_path = active_path(load_config(config))

    recovered = remove_campaign(config, temporary_id, campaigns)
    assert recovered.resolve() == legacy.resolve()
    assert not temporary_path.exists()
    loaded = load_config(config)
    assert loaded["active_campaign"] == "legacy"


def test_default_config_migrates_old_name_to_sakaki_gm(monkeypatch, tmp_path):
    import sakaki_gm.llm_config as llm_config

    old_config = tmp_path / "pygm" / "config.json"
    new_config = tmp_path / "sakaki-gm" / "config.json"
    old_config.parent.mkdir()
    previous = {
        "active_campaign": "campaign-id",
        "campaigns": [{"id": "campaign-id", "path": str(tmp_path / "keep.db")}],
        "llm": {
            "provider": "openai_compatible",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key": "preserve-this-setting",
        },
    }
    old_config.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(llm_config, "CONFIG_PATH", new_config)
    monkeypatch.setattr(llm_config, "LEGACY_CONFIG_PATH", old_config)

    loaded = llm_config.load_config()

    assert loaded["active_campaign"] == "campaign-id"
    assert loaded["campaigns"] == previous["campaigns"]
    assert loaded["llm"] == {**previous["llm"], "rag_mode": "tools"}
    assert json.loads(new_config.read_text(encoding="utf-8")) == loaded
    assert json.loads(old_config.read_text(encoding="utf-8")) == previous


def test_ui_show_gm_reply_debug_defaults_off_and_loads_bool(tmp_path):
    from sakaki_gm.llm_config import load_config, save_config, show_gm_reply_debug

    config = tmp_path / "config.json"
    assert show_gm_reply_debug(load_config(config)) is False

    config.write_text(
        json.dumps({"ui": {"show_gm_reply_debug": 1}}),
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert loaded["ui"]["show_gm_reply_debug"] is True
    assert show_gm_reply_debug(loaded) is True

    loaded["ui"]["show_gm_reply_debug"] = False
    save_config(loaded, config)
    assert show_gm_reply_debug(load_config(config)) is False


def test_model_capability_profiles_are_validated_and_keyed_per_endpoint(tmp_path):
    from sakaki_gm.llm_config import load_config, model_profile_key, save_config

    config = tmp_path / "config.json"
    settings = {
        "provider": "OpenAI_Compatible",
        "base_url": "http://localhost:1234/v1/",
        "model": "local-model",
    }
    key = model_profile_key(settings)
    assert key == model_profile_key({
        **settings,
        "provider": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
    })
    assert key != model_profile_key({**settings, "model": "other-model"})

    config.write_text(
        json.dumps({
            "model_profiles": {
                key: [
                    "chat",
                    "tool_use",
                    "chat",
                    "unknown",
                    {"malformed": "entry"},
                ],
                "invalid": "not-a-list",
            }
        }),
        encoding="utf-8",
    )

    loaded = load_config(config)

    assert loaded["model_profiles"] == {key: ["chat", "tool_use"]}
    save_config(loaded, config)
    assert load_config(config)["model_profiles"] == {key: ["chat", "tool_use"]}


def test_remove_missing_campaign_keeps_active_database(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    active_db = tmp_path / "active.db"
    get_db(active_db).close()
    config.write_text(
        json.dumps({
            "active_campaign": "active",
            "campaigns": [
                {"id": "missing", "path": str(tmp_path / "gone.db")},
                {"id": "active", "path": str(active_db)},
            ],
        }),
        encoding="utf-8",
    )

    result = remove_campaign(config, "missing", campaigns)

    assert result.resolve() == active_db.resolve()
    loaded = load_config(config)
    assert loaded["active_campaign"] == "active"
    assert [item["id"] for item in loaded["campaigns"]] == ["active"]
    assert active_db.is_file()


def test_prompt_includes_sheet_and_play_tag(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(db, "Serina", {"name": "Serina", "hp_current": 11, "hp_max": 11, "class": "Wizard"})
    db.execute("INSERT INTO notes (title, content) VALUES (?, ?)", ("Instructions for Grok", "You are GM for the world of Eldenweave."))
    db.commit()
    system, user = build_messages(db, "I look around.")
    assert "Eldenweave" in system
    assert "hp_current" in system
    assert "\nWORLD\n" not in system
    db.execute(
        "INSERT INTO world (key, value) VALUES ('prompt', 'The Silver Veil Forest lies north of Eldenport.')"
    )
    db.commit()
    system, _user = build_messages(db, "I look around.")
    assert "WORLD\nThe Silver Veil Forest lies north of Eldenport." in system
    assert "[Play]" in user
    assert "Serina" in user
    assert "11" in user
    db.close()


def test_prompt_excludes_obsolete_reference_notes(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(db, "Serina", {"name": "Serina"})
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("[OBSOLETE REFERENCE] Instructions for Grok", "Old contradictory rules."),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("CANONICAL: Serina", "Current rules."),
    )
    db.commit()

    system, user = build_messages(db, "Continue.")

    assert "Old contradictory rules." not in system
    assert "Old contradictory rules." not in user
    assert "Current rules." in user
    assert "You are an expert Game Master." in system
    db.close()


def test_tool_prompt_omits_searchable_reference_material(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Moon Abbey", "The secret passage lies below the altar."),
    )
    create_item(db, "Silver Reliquary", "A relic hidden beneath the abbey.")
    db.commit()

    _system, static_user = build_messages(db, "Where is the relic?")
    _system, tool_user = build_messages(
        db,
        "Where is the relic?",
        include_reference_material=False,
    )

    assert "secret passage" in static_user
    assert "Silver Reliquary" in static_user
    assert "secret passage" not in tool_user
    assert "Silver Reliquary" not in tool_user
    assert "CURRENT SCENE" in tool_user
    assert "lookup tools" in tool_user
    db.close()


def test_ask_gm_scene_pack_is_selective(tmp_path):
    from sakaki_gm.gm import out_of_scene_lore_query
    from sakaki_gm.object_graph_db import create_object, create_object_link

    db = get_db(tmp_path / "campaign.db")
    serina_id = create_character(
        db,
        "Serina",
        {"name": "Serina", "hp_current": 11, "hp_max": 11, "ac": 12},
        campaign_role="Party member",
    )
    create_character(db, "Kael", {"name": "Kael", "hp_current": 20})
    ward_id = create_item(db, "Echo Ward", "Detect Magic once per rest.")
    set_character_items(db, serina_id, [(ward_id, 1, True, "worn")])
    create_object(db, "ritual-hall", "Ritual Hall", "location", "Academy hall.")
    create_object(db, "docks", "Tide's Rest", "location", "Harbor docks.")
    create_object_link(db, "route", "ritual-hall", "docks")
    db.execute(
        "UPDATE world SET value=? WHERE key='location'",
        ("Ritual Hall, Academy of Starlight",),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Lunar Reliquary lore", "The sealed relic waits below."),
    )
    db.commit()

    _system, scene_user = build_messages(
        db,
        "I examine the Echo Ward.",
        include_reference_material=False,
    )
    assert "CURRENT SCENE" in scene_user
    assert "Serina" in scene_user
    assert "Echo Ward" in scene_user
    assert "Ritual Hall" in scene_user
    assert "Tide's Rest" in scene_user
    assert "Kael" not in scene_user
    assert "sealed relic waits" not in scene_user
    assert "Quests:" not in scene_user

    assert out_of_scene_lore_query(db, "I examine the Echo Ward.") is None
    assert "Lunar Reliquary" in out_of_scene_lore_query(
        db, "What does lore say about the Lunar Reliquary?"
    )
    db.close()


def test_item_catalog_and_character_inventory_are_normalized(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(
        db,
        "Serina",
        {
            "name": "Serina",
            "inventory": ["Obsolete free-form item"],
            "attuned": ["Obsolete attunement"],
        },
        campaign_role="Party member",
    )
    item_id = create_item(db, "Lunar Scepter", "A silver arcane focus.")
    set_character_items(db, character_id, [(item_id, 2, True, "right hand")])

    assigned = get_character_items(db, character_id)
    assert len(assigned) == 1
    assert assigned[0]["name"] == "Lunar Scepter"
    assert assigned[0]["description"] == "A silver arcane focus."
    assert assigned[0]["quantity"] == 2
    assert assigned[0]["attuned"] == 1
    assert assigned[0]["equipment_location"] == "right hand"
    character_object_id = db.execute(
        "SELECT object_id FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()["object_id"]
    inventory_link = db.execute(
        """
        SELECT object_links.to_object_id, object_links.properties
        FROM object_links
        JOIN objects ON objects.object_id=object_links.to_object_id
        WHERE object_links.from_object_id=? AND object_links.relation='contains'
          AND objects.object_type='container'
        """,
        (character_object_id,),
    ).fetchone()
    assert json.loads(inventory_link["properties"]) == {"role": "inventory"}
    item_link = db.execute(
        """
        SELECT properties FROM object_links
        WHERE from_object_id=? AND to_object_id=? AND relation='contains'
        """,
        (inventory_link["to_object_id"], item_id),
    ).fetchone()
    assert json.loads(item_link["properties"]) == {
        "quantity": 2,
        "attuned": True,
        "equipment_location": "right hand",
    }
    assert db.execute(
        "SELECT COUNT(*) FROM objects WHERE object_type='item' AND object_id=?",
        (item_id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('items', 'character_items')"
    ).fetchone()[0] == 0
    data = json.loads(
        db.execute("SELECT data FROM characters WHERE id=?", (character_id,)).fetchone()["data"]
    )
    assert data["inventory"] == ["Obsolete free-form item"]
    assert data["attuned"] == ["Obsolete attunement"]

    system, user = build_messages(db, "Check the pack.")
    assert f'"object_id": "character:{character_id}"' in user
    assert f'"inventory_container_object_id": "inventory:{character_id}"' in user
    assert "A silver arcane focus." in user
    assert f'"object_id": "{item_id}"' in user
    assert '"quantity": 2' in user
    assert '"attuned": true' in user
    assert '"equipment_location": "right hand"' in user
    assert "Obsolete free-form item" not in user
    assert "Obsolete attunement" not in user
    assert '"campaign_role": "Party member"' in user
    with pytest.raises(sqlite3.IntegrityError):
        delete_item(db, item_id)
    db.rollback()
    db.close()


def test_container_tag_creates_nested_backpack_and_cleans_orphan(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    backpack_id = create_item(
        db,
        "Backpack",
        "A sturdy pack.",
        is_container=True,
    )
    potion_id = create_item(db, "Potion", "A healing draught.")
    assert next(item for item in get_items(db) if item["id"] == backpack_id)[
        "is_container"
    ]

    save_character_inventory(
        db,
        character_id,
        [
            {
                "object_id": "new-container:backpack-instance",
                "object_type": "container",
                "new_container": True,
                "name": "Backpack",
                "description": "A sturdy pack.",
                "object_properties": {"catalog_item_id": backpack_id},
                "properties": {"quantity": 1, "attuned": False},
                "children": [
                    {
                        "object_id": potion_id,
                        "object_type": "item",
                        "properties": {
                            "quantity": 2,
                            "attuned": False,
                            "equipment_location": "",
                        },
                    },
                ],
            },
        ],
    )
    inventory = get_character_inventory_tree(db, character_id)
    backpack = inventory["children"][0]
    assert backpack["object_type"] == "container"
    assert backpack["object_properties"]["catalog_item_id"] == backpack_id
    assert backpack["children"][0]["object_id"] == potion_id
    potion_assignment = next(
        item for item in get_character_items(db, character_id)
        if item["name"] == "Potion"
    )
    assert potion_assignment["container_path"].endswith(" > Backpack")
    assert potion_assignment["quantity"] == 2

    save_character_inventory(db, character_id, [])
    assert db.execute(
        "SELECT COUNT(*) FROM objects "
        "WHERE object_id='inventory-container:backpack-instance'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM object_links WHERE to_object_id=?",
        (potion_id,),
    ).fetchone()[0] == 0
    db.close()


def test_inventory_sibling_order_is_preserved_when_saved(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    first_item_id = create_item(db, "First item")
    second_item_id = create_item(db, "Second item")

    save_character_inventory(
        db,
        character_id,
        [
            {
                "object_id": second_item_id,
                "object_type": "item",
                "properties": {"quantity": 1},
            },
            {
                "object_id": first_item_id,
                "object_type": "item",
                "properties": {"quantity": 1},
            },
        ],
    )

    tree = get_character_inventory_tree(db, character_id)
    assert [item["object_id"] for item in tree["children"]] == [
        second_item_id,
        first_item_id,
    ]
    db.close()


def test_missing_character_inventory_container_is_created_on_demand(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    db.execute(
        "DELETE FROM object_links "
        "WHERE from_object_id=? AND relation='contains'",
        (f"character:{character_id}",),
    )
    db.execute("DELETE FROM objects WHERE object_id=?", (f"inventory:{character_id}",))
    db.commit()

    tree = get_character_inventory_tree(db, character_id)

    assert tree["object_id"] == f"inventory:{character_id}"
    assert db.execute(
        "SELECT COUNT(*) FROM object_links "
        "WHERE from_object_id=? AND to_object_id=? AND relation='contains'",
        (f"character:{character_id}", f"inventory:{character_id}"),
    ).fetchone()[0] == 1
    db.close()


def test_schema_v13_adds_object_properties(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    item_id = create_item(db, "Backpack")
    db.execute("ALTER TABLE objects DROP COLUMN properties")
    db.execute("PRAGMA user_version = 13")
    db.commit()

    migrate(db)

    backpack = next(item for item in get_items(db) if item["id"] == item_id)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 14
    assert backpack["is_container"] is False
    db.close()


def test_v12_migration_moves_catalog_inventory_to_container_links(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    character_id = create_character(db, "Serina", {"name": "Serina"})
    item_id = create_item(db, "Lunar Scepter", "A silver arcane focus.")
    set_character_items(db, character_id, [(item_id, 2, True, "right hand")])
    prepare_pre_object_graph_inventory_schema(db)
    db.execute("PRAGMA user_version = 12")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assignment = get_character_items(db, character_id)[0]
        assert assignment["name"] == "Lunar Scepter"
        assert assignment["quantity"] == 2
        assert assignment["attuned"] == 1
        assert assignment["equipment_location"] == "right hand"
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('items', 'character_items')"
        ).fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_schema_v3_campaign_migrates_to_item_catalog_without_changing_legacy_items(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    character_id = create_character(
        db,
        "Legacy",
        {
            "name": "Legacy",
            "inventory": ["Old free-form item"],
            "attuned": [],
            "skills": "Dual wielding",
        },
    )
    prepare_pre_object_graph_inventory_schema(db)
    db.execute("DROP TABLE character_skills")
    db.execute("DROP TABLE campaign_skills")
    db.execute("DROP TABLE character_items")
    db.execute("DROP TABLE items")
    db.execute("DROP TABLE quest_characters")
    db.execute("DROP TABLE quests")
    drop_geography_schema_for_legacy_migration(db)
    db.execute(
        "INSERT INTO campaign_settings (key, value) VALUES ('skills_list', ?)",
        (json.dumps([name for name, _ability in DEFAULT_5E_SKILLS]),),
    )
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assert get_items(db) == []
        assert get_quests(db) == []
        data = json.loads(
            db.execute(
                "SELECT data FROM characters WHERE id=?",
                (character_id,),
            ).fetchone()["data"]
        )
        assert data["inventory"] == ["Old free-form item"]
        assert data["attuned"] == []
        assert "skills" not in data
        assert "Dual wielding" in data["notes"]
        character_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(characters)")
        }
        assert "is_player" not in character_columns
        assert "campaign_role" in character_columns
        assert [skill["name"] for skill in get_skills(db)] == [
            name for name, _ability in DEFAULT_5E_SKILLS
        ]
    finally:
        db.close()


def test_quest_assignments_support_multiple_characters_and_multiple_quests(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    serina_id = create_character(db, "Serina", {"name": "Serina"})
    kael_id = create_character(db, "Kael", {"name": "Kael"})
    first_quest = save_quest(
        db,
        None,
        "Find the Crown",
        "Recover the Lunar Crown.",
        [serina_id, kael_id, serina_id],
        "In progress",
    )
    second_quest = save_quest(
        db,
        None,
        "Protect Eldenport",
        "Keep the city safe.",
        [serina_id],
        "Not started",
    )

    assert [quest["name"] for quest in get_quests(db)] == [
        "Find the Crown",
        "Protect Eldenport",
    ]
    first = next(quest for quest in get_quests(db) if quest["id"] == first_quest)
    assert first["status"] == "In progress"
    assert [row["name"] for row in get_quest_characters(db, first_quest)] == [
        "Kael",
        "Serina",
    ]
    assert [row["name"] for row in get_quest_characters(db, second_quest)] == [
        "Serina",
    ]
    _system, user = build_messages(db, "What are our objectives?")
    assert "Find the Crown" in user
    assert "Recover the Lunar Crown." in user
    assert '"status": "In progress"' in user
    assert '"characters": ["Kael", "Serina"]' in user

    save_quest(
        db,
        first_quest,
        "Find the Crown",
        "Updated objective.",
        [kael_id],
        "Completed",
    )
    first = next(quest for quest in get_quests(db) if quest["id"] == first_quest)
    assert first["status"] == "Completed"
    assert [row["name"] for row in get_quest_characters(db, first_quest)] == ["Kael"]
    delete_quest(db, first_quest)
    assert [quest["id"] for quest in get_quests(db)] == [second_quest]
    db.close()


def test_schema_v4_campaign_migrates_to_quests(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    prepare_pre_object_graph_inventory_schema(db)
    db.execute("DROP TABLE character_skills")
    db.execute("DROP TABLE campaign_skills")
    db.execute("DROP TABLE quest_characters")
    db.execute("DROP TABLE quests")
    db.execute("ALTER TABLE character_items DROP COLUMN equipment_location")
    drop_geography_schema_for_legacy_migration(db)
    db.execute("PRAGMA user_version = 4")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assert get_quests(db) == []
    finally:
        db.close()


def test_schema_v5_campaign_adds_empty_quest_status(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    prepare_pre_object_graph_inventory_schema(db)
    quest_id = save_quest(db, None, "Legacy quest", "Existing objective.", [])
    db.execute("DROP TABLE character_skills")
    db.execute("DROP TABLE campaign_skills")
    db.execute("ALTER TABLE quests DROP COLUMN status")
    db.execute("ALTER TABLE character_items DROP COLUMN equipment_location")
    drop_geography_schema_for_legacy_migration(db)
    db.execute("PRAGMA user_version = 5")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        quest = get_quests(db)[0]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assert quest["id"] == quest_id
        assert quest["name"] == "Legacy quest"
        assert quest["description"] == "Existing objective."
        assert quest["status"] == ""
    finally:
        db.close()


def test_schema_v6_campaign_adds_equipment_location(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    character_id = create_character(db, "Serina", {"name": "Serina"})
    item_id = create_item(db, "Lunar Scepter")
    set_character_items(db, character_id, [(item_id, 1, True, "right hand")])
    prepare_pre_object_graph_inventory_schema(db)
    db.execute("DROP TABLE character_skills")
    db.execute("DROP TABLE campaign_skills")
    db.execute("ALTER TABLE character_items DROP COLUMN equipment_location")
    drop_geography_schema_for_legacy_migration(db)
    db.execute("PRAGMA user_version = 6")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        assignment = get_character_items(db, character_id)[0]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assert assignment["equipment_location"] == ""
        assert assignment["attuned"] == 1
    finally:
        db.close()


def test_character_skill_assignments_calculate_and_appear_in_gm_prompt(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(
        db,
        "Serina",
        {
            "name": "Serina",
            "dex": 14,
            "proficiency_bonus": 2,
        },
    )
    stealth = next(skill for skill in get_skills(db) if skill["name"] == "Stealth")
    set_character_skills(db, character_id, [(stealth["id"], False, True)])

    assigned = next(
        skill for skill in get_character_skills(db, character_id)
        if skill["id"] == stealth["id"]
    )
    assert assigned["proficient"] == 1
    assert assigned["expertise"] == 1
    assert skill_bonus(14, 2, assigned["proficient"], assigned["expertise"]) == 6

    _system, user = build_messages(db, "Sneak past the guard.")
    assert '"name": "Stealth"' in user
    assert '"ability": "dex"' in user
    assert '"expertise": true' in user
    assert '"bonus": 6' in user
    assert '"skills": []' not in user
    db.close()


def test_saved_system_prompt_replaces_the_instruction_note(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Instructions for Grok", "You are GM for the world of Eldenweave."),
    )
    db.commit()
    system, _user = build_messages(db, "I wait.")
    assert system.startswith("You are GM for the world of Eldenweave.")
    db.execute(
        "INSERT INTO campaign_settings (key, value) VALUES ('system_prompt', 'Speak only in questions.')"
    )
    db.commit()
    system, _user = build_messages(db, "I wait.")
    assert system.startswith("Speak only in questions.")
    assert "Eldenweave" not in system
    db.close()


def test_apply_updates_accepts_hp_alias_and_events(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(db, "Serina", {"name": "Serina", "hp_current": 11, "hp_max": 11, "class": "Wizard"})
    parsed = parse_reply(
        'The crown hums.\n{"narration": "The crown hums.", "updates": '
        '{"characters": {"Serina": {"hp": 8, "max_hp": 11}}, '
        '"world": {"location": "Starfall\'s End", "events": "attuned the crown"}}}'
    )
    assert parsed.narration == "The crown hums."
    updates = parsed.updates
    messages = apply_updates(db, updates)
    assert "State updated" in messages
    data = json.loads(db.execute("SELECT data FROM characters WHERE name='Serina'").fetchone()["data"])
    assert data["hp_current"] == 8
    assert data["hp_max"] == 11
    assert "hp" not in data
    assert data["class"] == "Wizard"
    assert db.execute("SELECT value FROM world WHERE key='location'").fetchone()["value"] == "Starfall's End"
    events = json.loads(db.execute("SELECT value FROM world WHERE key='events'").fetchone()["value"])
    assert events == ["attuned the crown"]
    apply_updates(db, {"world": {"events": ["one", "two"]}})
    events = json.loads(db.execute("SELECT value FROM world WHERE key='events'").fetchone()["value"])
    assert events == ["one", "two"]
    messages = apply_updates(db, {"characters": {"Nobody": {"hp_current": 1}}})
    assert any(line.startswith("Unknown character") for line in messages)
    db.close()


def test_parse_reply_extracts_citations_and_spend_use_proposals():
    parsed = parse_reply(
        json.dumps({
            "narration": "The ward opens Detect Magic.",
            "updates": {"world": {"events": "examined the reagents"}},
            "cited_record_ids": ["object:catalog-item:4", "object:catalog-item:4", ""],
            "proposals": [
                {
                    "op": "spend_use",
                    "character": "Serina",
                    "item_object_id": "catalog-item:4",
                    "use": "detect_magic",
                    "summary": "Ward Detect Magic spent until long rest",
                    "record_id": "object:catalog-item:4",
                },
                {"op": "rewrite_notes", "summary": "unsupported"},
                {"op": "spend_use", "character": "Serina"},
                {
                    "op": "inventory_add",
                    "character": "Serina",
                    "item_object_id": "catalog-item:1",
                    "quantity": 2,
                },
                {
                    "op": "note_append",
                    "note_id": 11,
                    "text": "Detected reagents were mundane.",
                },
                {
                    "op": "link_create",
                    "relation": "located_at",
                    "from_object_id": "character:1",
                    "to_object_id": "ritual-hall",
                },
            ],
        })
    )
    assert parsed.cited_record_ids == ["object:catalog-item:4"]
    assert [proposal["op"] for proposal in parsed.proposals] == [
        "spend_use",
        "inventory_add",
        "note_append",
        "link_create",
    ]
    assert parsed.proposals[0]["use"] == "detect_magic"
    assert normalize_proposals([{"op": "nope"}]) == []


def test_citation_messages_report_known_and_unknown_ids():
    from sakaki_gm.scratch_context import ScratchContext

    scratch = ScratchContext(context_id="turn")
    scratch.stage_object({
        "object_id": "catalog-item:4",
        "name": "Serpentine Lunar Echo Ward",
        "object_type": "item",
        "geography_kind": None,
        "description": "Once per long rest, cast Detect Magic.",
    })
    assert citation_messages(None, ["object:catalog-item:4"])[0].startswith(
        "Cited record IDs were present"
    )
    lines = citation_messages(
        scratch,
        ["object:catalog-item:4", "document:missing"],
    )
    assert any(line.startswith("Cited 1 scratch record") for line in lines)
    assert any("document:missing" in line for line in lines)


def test_apply_proposals_records_spend_use_on_character(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(db, "Serina", {"name": "Serina", "hp_current": 11})
    messages = apply_proposals(db, [{
        "op": "spend_use",
        "character": "Serina",
        "item_object_id": "catalog-item:4",
        "use": "detect_magic",
        "summary": "Ward Detect Magic spent until long rest",
    }])
    assert any(line.startswith("Recorded spent use") for line in messages)
    assert "Proposals applied" in messages
    data = json.loads(
        db.execute("SELECT data FROM characters WHERE name='Serina'").fetchone()["data"]
    )
    assert data["spent_uses"][0]["use"] == "detect_magic"
    assert data["spent_uses"][0]["item_object_id"] == "catalog-item:4"
    assert "spent_at" in data["spent_uses"][0]
    db.close()


def test_apply_proposals_inventory_note_and_link_backends(tmp_path):
    from sakaki_gm.object_graph_db import create_object

    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    ward_id = create_item(db, "Echo Ward", "Once per rest Detect Magic.")
    stick_id = create_item(db, "Lunar Stick", "A ritual focus.")
    set_character_items(db, character_id, [(ward_id, 1, True, "worn")])
    create_object(db, "ritual-hall", "Ritual Hall", "location", "Academy hall.")
    note_id = db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Session", "Preparation continues."),
    ).lastrowid
    db.commit()

    messages = apply_proposals(db, [
        {
            "op": "inventory_update",
            "character": "Serina",
            "item_object_id": ward_id,
            "equipment_location": "left hand",
            "attuned": True,
        },
        {
            "op": "inventory_add",
            "character": "Serina",
            "item_object_id": stick_id,
            "quantity": 1,
            "equipment_location": "beside circle",
        },
        {
            "op": "note_append",
            "note_id": note_id,
            "text": "Detect Magic spent on reagents.",
        },
        {
            "op": "link_create",
            "relation": "located_at",
            "from_object_id": f"character:{character_id}",
            "to_object_id": "ritual-hall",
        },
    ])
    assert any("Updated" in line for line in messages)
    assert any("Added" in line for line in messages)
    assert any("Appended" in line for line in messages)
    assert any("Created located_at" in line for line in messages)

    items = {item["id"]: item for item in get_character_items(db, character_id)}
    assert items[ward_id]["equipment_location"] == "left hand"
    assert stick_id in items
    content = db.execute(
        "SELECT content FROM notes WHERE id=?", (note_id,)
    ).fetchone()["content"]
    assert "Detect Magic spent on reagents." in content
    assert db.execute(
        """
        SELECT 1 FROM object_links
        WHERE relation='located_at' AND from_object_id=? AND to_object_id=?
        """,
        (f"character:{character_id}", "ritual-hall"),
    ).fetchone()

    remove_messages = apply_proposals(db, [{
        "op": "inventory_remove",
        "character": "Serina",
        "item_object_id": stick_id,
    }])
    assert any("Removed" in line for line in remove_messages)
    assert stick_id not in {
        item["id"] for item in get_character_items(db, character_id)
    }
    db.close()


def test_apply_proposals_skips_stale_scratch_revision(tmp_path):
    from sakaki_gm.scratch_context import ScratchContext, snapshot_revision

    db = get_db(tmp_path / "campaign.db")
    note_id = db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Lore", "Original text."),
    ).lastrowid
    db.commit()
    scratch = ScratchContext(context_id="rev")
    scratch.stage(
        "document",
        str(note_id),
        {"id": note_id, "title": "Lore", "excerpt": "Original text."},
        revision=snapshot_revision({
            "id": note_id,
            "title": "Lore",
            "content": "Original text.",
        }),
    )
    db.execute(
        "UPDATE notes SET content=? WHERE id=?",
        ("Changed while Ask GM ran.", note_id),
    )
    db.commit()

    messages = apply_proposals(
        db,
        [{
            "op": "note_append",
            "note_id": note_id,
            "text": "Should not apply.",
            "record_id": f"document:{note_id}",
        }],
        scratch=scratch,
    )
    assert any("changed since the scratch snapshot" in line for line in messages)
    assert "Should not apply." not in db.execute(
        "SELECT content FROM notes WHERE id=?", (note_id,)
    ).fetchone()["content"]
    db.close()


def test_scratch_wall_clock_budget_raises():
    from sakaki_gm.scratch_context import ScratchBudgetExceeded, ScratchContext

    scratch = ScratchContext(max_seconds=0.01)
    scratch.created_at -= 1.0
    with pytest.raises(ScratchBudgetExceeded, match="wall-clock"):
        scratch.check_deadline()


def test_complete_retries_without_response_format():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("unsupported response_format")

        class Message:
            content = '{"narration": "ok", "updates": {}}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()

    text = complete(
        {"provider": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1", "model": "local", "api_key": ""},
        "system",
        "user",
        create=create,
    )
    assert "narration" in text
    assert len(calls) == 2
    assert "response_format" not in calls[1]


def test_campaign_lookup_tools_search_documents_objects_and_relationships(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Reliquary history", "The lunar reliquary was sealed beneath the abbey."),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("[OBSOLETE REFERENCE] old reliquary", "Discarded text."),
    )
    create_object(db, "abbey", "Moon Abbey", "location", "An old lunar shrine.")
    create_object(db, "reliquary", "Lunar Reliquary", "item", "A sealed relic.")
    create_object_link(
        db,
        "kept beneath",
        "abbey",
        "reliquary",
        properties={"sealed": True},
    )

    documents = execute_campaign_tool(
        db, "search_documents", {"query": "lunar reliquary"}
    )
    objects = execute_campaign_tool(
        db, "search_objects", {"query": "lunar reliquary"}
    )
    inspected = execute_campaign_tool(
        db, "inspect_object", {"object_id": "reliquary"}
    )

    assert [doc["title"] for doc in documents["documents"]] == [
        "Reliquary history"
    ]
    assert objects["objects"][0]["object_id"] == "reliquary"
    assert "abbey" in {
        obj["object_id"] for obj in objects["objects"]
    }
    assert objects["objects"][0]["properties"] == {}
    assert inspected["object"]["name"] == "Lunar Reliquary"
    assert inspected["relationships"][0]["from_object_id"] == "abbey"
    assert inspected["relationships"][0]["properties"] == {"sealed": True}
    assert inspected["location_inference"]["absence_of_link_proves_absence"] is False
    db.close()


def test_inspect_location_distinguishes_recorded_presence_from_unknown(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    character_id = create_character(db, "Serina", {"name": "Serina"})
    create_object(
        db, "ritual-hall", "Ritual Hall", "ritual hall", geography_kind="room"
    )
    create_object(db, "empty-room", "Empty Room", "room", geography_kind="room")
    create_object_link(
        db,
        "located_at",
        f"character:{character_id}",
        "ritual-hall",
        properties={"position": "at the circle"},
        notes="Present in the latest scene.",
    )

    recorded = execute_campaign_tool(
        db, "inspect_location", {"location_object_id": "ritual-hall"}
    )
    unknown = execute_campaign_tool(
        db, "inspect_location", {"location_object_id": "empty-room"}
    )
    character_search = execute_campaign_tool(
        db, "search_objects", {"query": "Serina"}
    )
    location_search = execute_campaign_tool(
        db, "search_objects", {"query": "Ritual Hall"}
    )

    assert recorded["presence_status"] == "recorded"
    assert recorded["known_presences"] == [{
        "object_id": f"character:{character_id}",
        "name": "Serina",
        "properties": {"position": "at the circle"},
        "notes": "Present in the latest scene.",
    }]
    assert unknown["presence_status"] == "unknown"
    assert unknown["known_presences"] == []
    assert unknown["unlinked_presence"] == "unknown"
    assert unknown["absence_of_link_proves_absence"] is False
    assert unknown["encounter_likelihood"] == "gm_adjudication"
    serina = next(
        item for item in character_search["objects"]
        if item["object_id"] == f"character:{character_id}"
    )
    assert serina["recorded_locations"][0]["object_id"] == "ritual-hall"
    ritual_hall = next(
        item for item in location_search["objects"]
        if item["object_id"] == "ritual-hall"
    )
    assert ritual_hall["location_context"]["known_presences"][0]["name"] == "Serina"
    assert (
        character_search["location_inference"]["unlinked_presence"]
        == "unknown"
    )
    db.close()


def test_complete_uses_campaign_lookup_tools_during_turn(tmp_path):
    db_path = tmp_path / "campaign.db"
    db = get_db(db_path)
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Abbey", "The abbey stands beneath the silver moon."),
    )
    db.commit()
    db.close()
    calls = []

    class ResponseMessage:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class Response:
        def __init__(self, message):
            self.choices = [type("Choice", (), {"message": message})()]

    def create(**kwargs):
        calls.append(kwargs)
        assert "tools" in kwargs
        assert "response_format" not in kwargs
        if len(calls) == 1:
            messages = kwargs["messages"]
            assert any(
                message["role"] == "system"
                and "original user request in force" in message["content"]
                and "return an empty updates object" in message["content"]
                and "missing links mean unknown" in message["content"]
                and "Encounter likelihood is GM adjudication" in message["content"]
                for message in messages
            )
            assert messages[-1] == {
                "role": "user",
                "content": "Do not change campaign state.",
            }
            call = type("ToolCall", (), {
                "id": "lookup-1",
                "function": type("Function", (), {
                    "name": "search_documents",
                    "arguments": '{"query":"silver moon"}',
                })(),
            })()
            return Response(ResponseMessage(tool_calls=[call]))
        tool_message = next(
            message for message in kwargs["messages"]
            if message["role"] == "tool"
        )
        assert "silver moon" in tool_message["content"]
        assert any(
            message["role"] == "user"
            and message["content"] == "Do not change campaign state."
            for message in kwargs["messages"]
        )
        return Response(ResponseMessage(
            '{"narration":"The abbey shines beneath the moon.","updates":{}}'
        ))

    settings = {
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "local",
        "api_key": "",
        "rag_mode": "tools",
    }
    reply = complete(
        settings,
        "system",
        "Do not change campaign state.",
        create=create,
        tool_db_path=db_path,
        model_profiles={
            model_profile_key(settings): ["tool_use", "structured_json"],
        },
    )

    assert "abbey shines" in reply
    assert len(calls) == 2
    assert {tool["function"]["name"] for tool in calls[0]["tools"]} == {
        "search_documents",
        "search_objects",
        "inspect_object",
        "inspect_location",
    }
    tool_payload = json.loads(next(
        message["content"] for message in calls[1]["messages"]
        if message["role"] == "tool"
    ))
    assert "context_id" in tool_payload
    assert tool_payload["documents"][0]["record_id"].startswith("document:")


def test_complete_uses_app_retrieved_context_without_model_tools(tmp_path):
    db_path = tmp_path / "campaign.db"
    db = get_db(db_path)
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "Canonical rules",
            "General campaign state and history precede the rule.\n"
            "Full Lunara set (Tome, Reliquary, Scepter, Crown): +1 to spell save DCs "
            "while all four are carried. The Reliquary must be active and the Crown attuned.",
        ),
    )
    create_object(
        db,
        "tome",
        "Tome of Eldritch Whispers",
        "item",
        "As a Divination or Enchantment focus, grants +1 spell save DC, stacking with set.",
    )
    create_object(db, "crown", "Lunar Crown", "item", "With the complete set and Lunarglow Skirt, +1 initiative under moonlight.")
    db.commit()
    db.close()
    calls = []

    class ResponseMessage:
        content = '{"narration":"The complete set grants +1 DC when its conditions are met.","updates":{}}'

    class Response:
        choices = [type("Choice", (), {"message": ResponseMessage()})()]

    def create(**kwargs):
        calls.append(kwargs)
        assert "tools" not in kwargs
        prompt = kwargs["messages"][1]["content"]
        assert "Full Lunara set" in prompt
        assert "all four are carried" in prompt
        assert "Lunar Crown" in prompt
        assert "Lunarglow Skirt" in prompt
        assert "Tome of Eldritch Whispers" in prompt
        assert '"unlinked_presence": "unknown"' in prompt
        assert '"encounter_likelihood": "gm_adjudication"' in prompt
        assert "original user request" in prompt
        return Response()

    reply = complete(
        {
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "local",
            "api_key": "",
            "rag_mode": "context",
        },
        "system",
        "CURRENT STATE\nCharacters: []\nPLAYER ACTION\n"
        "[Play] List the Tome of Eldritch Whispers and the Lunara set conditions "
        "and Lunar Crown benefit. "
        "Do not change campaign state.",
        create=create,
        tool_db_path=db_path,
    )

    assert "complete set grants" in reply
    assert len(calls) == 1


def test_list_models_reads_openai_compatible_model_ids_and_authenticates():
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"data":[{"id":"model-b"},{"id":"model-a"},{"id":"model-b"}]}'

    def opener(request, timeout):
        requests.append((request, timeout))
        return Response()

    settings = {
        "provider": "spacexai",
        "base_url": "https://models.example/v1",
        "model": "",
        "api_key": "test-key",
    }
    assert list_models(settings, opener=opener) == ["model-a", "model-b"]
    request, timeout = requests[0]
    assert request.full_url == "https://models.example/v1/models"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert timeout == 20

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from campaigns import (
    active_path,
    bootstrap,
    create_campaign,
    register_file,
    remove_campaign,
)
from character_db import create_character
from db import LEGACY_DB, get_db
from gm import apply_updates, build_messages, parse_reply
from llm_client import complete
from llm_config import load_config
from item_db import (
    create_item,
    delete_item,
    get_character_items,
    get_items,
    set_character_items,
)
from quest_db import (
    delete_quest,
    get_quest_characters,
    get_quests,
    save_quest,
)
from ui.modules.importer import (
    import_campaign_files,
    import_campaign_folder,
    import_document_files,
    import_documents,
    parse_markdown_character,
)

LIVE_DB = LEGACY_DB
FIXTURE = Path(__file__).parent / "fixtures" / "serina_headings.md"


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


def test_folder_import_keeps_player_flag_and_location(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(db, "Serina", {"name": "Serina", "class": "broken", "hp_current": 1}, is_player=True)
    folder = tmp_path / "binder"
    folder.mkdir()
    (folder / "Serina - Character Sheet.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "Serina - Quest Log.md").write_text("# Quest\n\nReturn to Eldenport.\n", encoding="utf-8")
    (folder / "Geography of the Eldenweave.md").write_text("# Geography\n\nStarfall's End.\n", encoding="utf-8")
    (folder / "Instructions for Grok.txt").write_text("You are GM for the world of Eldenweave.\n", encoding="utf-8")
    (folder / "outskirts.csv").write_text("Roll,Encounter\n1,Traveler\n", encoding="utf-8")

    summary = import_campaign_folder(db, folder)
    assert "characters" in summary
    row = db.execute("SELECT is_player, data FROM characters WHERE name='Serina'").fetchone()
    assert row["is_player"] == 1
    assert "Wizard" in json.loads(row["data"])["class"]
    titles = {row["title"] for row in db.execute("SELECT title FROM notes")}
    assert "Serina - Quest Log" in titles
    assert "Geography of the Eldenweave" in titles
    assert "Instructions for Grok" in titles
    assert "outskirts" in titles
    location = db.execute("SELECT value FROM world WHERE key='location'").fetchone()["value"]
    assert location == "A quiet tavern"
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
    import llm_config

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
    assert loaded["llm"] == previous["llm"]
    assert json.loads(new_config.read_text(encoding="utf-8")) == loaded
    assert json.loads(old_config.read_text(encoding="utf-8")) == previous


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


def test_item_catalog_and_character_inventory_are_normalized(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    original_inventory = ["Traveler's pack", "10 gp"]
    character_id = create_character(
        db,
        "Serina",
        {
            "name": "Serina",
            "inventory": original_inventory,
            "attuned": ["Lunar Scepter"],
        },
    )
    item_id = create_item(db, "Lunar Scepter", "A silver arcane focus.")
    set_character_items(db, character_id, [(item_id, 2, True)])

    assigned = get_character_items(db, character_id)
    assert len(assigned) == 1
    assert assigned[0]["name"] == "Lunar Scepter"
    assert assigned[0]["description"] == "A silver arcane focus."
    assert assigned[0]["quantity"] == 2
    assert assigned[0]["attuned"] == 1
    data = json.loads(
        db.execute("SELECT data FROM characters WHERE id=?", (character_id,)).fetchone()["data"]
    )
    assert data["inventory"] == original_inventory
    assert data["attuned"] == ["Lunar Scepter"]

    system, user = build_messages(db, "Check the pack.")
    assert "A silver arcane focus." in user
    assert '"quantity": 2' in user
    assert '"attuned": true' in user
    with pytest.raises(sqlite3.IntegrityError):
        delete_item(db, item_id)
    db.rollback()
    db.close()


def test_schema_v3_campaign_migrates_to_item_catalog_without_changing_legacy_items(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    character_id = create_character(
        db,
        "Legacy",
        {"name": "Legacy", "inventory": ["Old free-form item"], "attuned": []},
    )
    db.execute("DROP TABLE character_items")
    db.execute("DROP TABLE items")
    db.execute("DROP TABLE quest_characters")
    db.execute("DROP TABLE quests")
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
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
    db.execute("DROP TABLE quest_characters")
    db.execute("DROP TABLE quests")
    db.execute("PRAGMA user_version = 4")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert get_quests(db) == []
    finally:
        db.close()


def test_schema_v5_campaign_adds_empty_quest_status(tmp_path):
    path = tmp_path / "campaign.db"
    db = get_db(path)
    quest_id = save_quest(db, None, "Legacy quest", "Existing objective.", [])
    db.execute("ALTER TABLE quests DROP COLUMN status")
    db.execute("PRAGMA user_version = 5")
    db.commit()
    db.close()

    db = get_db(path)
    try:
        quest = get_quests(db)[0]
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert quest["id"] == quest_id
        assert quest["name"] == "Legacy quest"
        assert quest["description"] == "Existing objective."
        assert quest["status"] == ""
    finally:
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
    narration, updates = parse_reply(
        'The crown hums.\n{"narration": "The crown hums.", "updates": '
        '{"characters": {"Serina": {"hp": 8, "max_hp": 11}}, '
        '"world": {"location": "Starfall\'s End", "events": "attuned the crown"}}}'
    )
    assert narration == "The crown hums."
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

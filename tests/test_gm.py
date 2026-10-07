import hashlib
import json
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
from ui.modules.importer import import_campaign_folder, parse_markdown_character

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


def test_prompt_includes_sheet_and_play_tag(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(db, "Serina", {"name": "Serina", "hp_current": 11, "hp_max": 11, "class": "Wizard"})
    db.execute("INSERT INTO notes (title, content) VALUES (?, ?)", ("Instructions for Grok", "You are GM for the world of Eldenweave."))
    db.commit()
    system, user = build_messages(db, "I look around.")
    assert "Eldenweave" in system
    assert "hp_current" in system
    assert "[Play]" in user
    assert "Serina" in user
    assert "11" in user
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

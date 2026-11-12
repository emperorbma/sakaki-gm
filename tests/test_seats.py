from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db, migrate
from sakaki_gm.seat_db import (
    clear_turn_wait,
    ensure_default_session,
    filter_characters_for_seat,
    get_active_seat,
    get_knowledge,
    get_seat_by_role,
    get_session_state,
    request_player_roll,
    set_active_seat_by_role,
    set_knowledge,
)
from sakaki_gm.ui.modules.characters import CharactersModule


def test_schema_v19_creates_seats_and_defaults(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 19
    state = ensure_default_session(db)
    assert get_seat_by_role(db, "gm")["seat_key"] == "gm"
    assert get_seat_by_role(db, "player")["seat_key"] == "player"
    assert state["turn_phase"] == "idle"
    assert get_active_seat(db)["role"] == "gm"
    db.close()


def test_active_seat_swap_and_turn_wait(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_default_session(db)
    set_active_seat_by_role(db, "player")
    assert get_active_seat(db)["role"] == "player"
    player = get_seat_by_role(db, "player")
    request_player_roll(
        db,
        hotseat_seat_id=player["id"],
        wait={"kind": "initiative", "character": "Serina"},
    )
    state = get_session_state(db)
    assert state["turn_phase"] == "awaiting_roll"
    assert state["hotseat_seat_id"] == player["id"]
    assert state["turn_wait"]["kind"] == "initiative"
    clear_turn_wait(db)
    assert get_session_state(db)["turn_phase"] == "idle"
    assert get_session_state(db)["turn_wait"] == {}
    db.close()


def test_knowledge_filters_player_roster(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    serina = create_character(
        db,
        "Serina",
        {"name": "Serina", "class": "Wizard"},
        campaign_role="Protagonist",
    )
    create_character(
        db,
        "Voryn Spy",
        {"name": "Voryn Spy", "class": "Rogue"},
        campaign_role="Enemy",
    )
    elara = create_character(
        db,
        "Elara Starveil",
        {"name": "Elara Starveil", "class": "Wizard"},
        campaign_role="Ally",
    )
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    assert player["character_id"] == serina
    assert get_knowledge(db, player["id"], "character", serina) == "present"

    module = CharactersModule.__new__(CharactersModule)
    module.db = db
    module.list_mode = CharactersModule.LIST_CHARACTERS
    module._desk_mode = "gm"
    gm_names = [c["name"] for c in module._visible_characters()]
    assert "Serina" in gm_names
    assert "Voryn Spy" in gm_names
    assert "Elara Starveil" in gm_names

    module._desk_mode = "player"
    set_active_seat_by_role(db, "player")
    player_names = [c["name"] for c in module._visible_characters()]
    assert player_names == ["Serina"]

    set_knowledge(db, player["id"], "character", elara, "known")
    player_names = [c["name"] for c in module._visible_characters()]
    assert set(player_names) == {"Serina", "Elara Starveil"}

    gm = get_seat_by_role(db, "gm")
    assert len(filter_characters_for_seat(db, module._visible_characters(), gm)) >= 2
    db.close()


def test_session_tables_survive_version_rewind(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_default_session(db)
    db.execute("PRAGMA user_version = 18")
    db.commit()
    migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 19
    assert get_seat_by_role(db, "gm") is not None
    db.close()

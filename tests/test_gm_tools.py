from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.seat_db import (
    ensure_default_session,
    get_seat_by_role,
    get_session_state,
    request_player_roll,
    set_turn_phase,
)
from sakaki_gm.ui.gm_tools import build_roll_wait_payload, format_turn_wait_status


def test_build_roll_wait_payload():
    payload = build_roll_wait_payload(
        character_name="Serina",
        kind="Initiative",
        skill="",
        dc="15",
        note="Roll for initiative",
    )
    assert payload["character"] == "Serina"
    assert payload["kind"] == "initiative"
    assert payload["dc"] == 15
    assert payload["note"] == "Roll for initiative"
    assert payload["requested_by"] == "gm_seat"


def test_request_roll_sets_player_hotseat(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(
        db,
        "Serina",
        {"name": "Serina", "class": "Wizard"},
        campaign_role="Protagonist",
    )
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    payload = build_roll_wait_payload(
        character_name="Serina",
        kind="initiative",
    )
    state = request_player_roll(
        db, hotseat_seat_id=player["id"], wait=payload
    )
    assert state["turn_phase"] == "awaiting_roll"
    assert state["hotseat_seat_id"] == player["id"]
    assert "initiative" in format_turn_wait_status(get_session_state(db))
    set_turn_phase(db, "model_busy")
    assert get_session_state(db)["turn_phase"] == "model_busy"
    assert format_turn_wait_status(get_session_state(db)) == "phase=model_busy"
    db.close()

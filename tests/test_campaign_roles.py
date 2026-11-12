from sakaki_gm.campaign_roles import (
    campaign_role_tokens,
    is_allyish_campaign_role,
    is_playerish_campaign_role,
)
from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db, migrate
from sakaki_gm.gm import _select_acting_character
from sakaki_gm.seat_db import (
    claim_player_character,
    ensure_default_session,
    get_seat_by_key,
    get_seat_by_role,
    list_controller_seats,
    list_player_claimable_characters,
    reassign_controller_seat,
    set_gm_control_character,
)


def test_pc_token_does_not_match_npc():
    assert campaign_role_tokens("NPC") == {"npc"}
    assert not is_playerish_campaign_role("NPC")
    assert is_playerish_campaign_role("Protagonist")
    assert is_playerish_campaign_role("Player PC")
    assert is_allyish_campaign_role("Ally")
    assert not is_allyish_campaign_role("NPC")


def test_acting_character_skips_npc_substring_and_prefers_protagonist(tmp_path):
    db_path = tmp_path / "roles.db"
    db = get_db(db_path)
    migrate(db)
    create_character(db, "Doremy Sweet", {"notes": "dream"}, campaign_role="NPC")
    create_character(db, "Aelun", {"notes": "ally"}, campaign_role="Ally")
    create_character(db, "Serina", {"notes": "pc"}, campaign_role="Protagonist")
    db.commit()

    acting = _select_acting_character(db, "Review the state of the story.")
    assert acting is not None
    assert acting["name"] == "Serina"

    named = _select_acting_character(db, "Doremy Sweet looks around.")
    assert named["name"] == "Doremy Sweet"


def test_playerish_creation_binds_controller_npc_does_not(tmp_path):
    db = get_db(tmp_path / "create.db")
    migrate(db)
    ensure_default_session(db)

    doremy = create_character(
        db, "Doremy Sweet", {"notes": "npc"}, campaign_role="NPC"
    )
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )

    player = get_seat_by_role(db, "player")
    assert player is not None
    assert player["character_id"] == serina
    assert player["display_name"] == "Serina's controller"
    assert all(
        seat.get("character_id") != doremy
        for seat in list_controller_seats(db)
    )


def test_gm_reassign_transfers_controller_ownership(tmp_path):
    db = get_db(tmp_path / "reassign.db")
    migrate(db)
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )
    talia = create_character(
        db, "Talia Moonsong", {"notes": "pc"}, campaign_role="Player"
    )
    player = get_seat_by_role(db, "player")
    assert player is not None
    assert player["character_id"] == serina

    # Second PC got its own controller seat.
    talia_seat = get_seat_by_key(db, f"controller:{talia}")
    assert talia_seat is not None
    assert talia_seat["character_id"] == talia

    rebound = reassign_controller_seat(db, player["id"], talia)
    assert rebound["character_id"] == talia
    assert rebound["display_name"] == "Talia Moonsong's controller"
    talia_seat = get_seat_by_key(db, f"controller:{talia}")
    assert talia_seat is not None
    assert talia_seat["character_id"] is None

    try:
        reassign_controller_seat(db, player["id"], serina)  # serina still playerish
    except Exception:
        raise
    # NPC cannot receive ownership.
    doremy = create_character(
        db, "Doremy Sweet", {"notes": "npc"}, campaign_role="NPC"
    )
    try:
        reassign_controller_seat(db, player["id"], doremy)
        assert False, "expected NPC reassign to fail"
    except PermissionError:
        pass


def test_player_claim_and_gm_absentee_lease(tmp_path):
    db = get_db(tmp_path / "lease.db")
    migrate(db)
    create_character(db, "Doremy Sweet", {"notes": "npc"}, campaign_role="NPC")
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )

    claimable = list_player_claimable_characters(db)
    assert {item["name"] for item in claimable} == {"Serina"}

    gm = set_gm_control_character(db, serina)
    assert gm["character_id"] == serina
    acting_gm = _select_acting_character(
        db, "Review the state of the story.", seat_id=gm["id"]
    )
    assert acting_gm["name"] == "Serina"

    player = claim_player_character(db, serina, "")
    assert player["character_id"] == serina
    gm = get_seat_by_role(db, "gm")
    assert gm is not None
    assert gm["character_id"] is None

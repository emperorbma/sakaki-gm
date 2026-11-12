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
    get_seat_by_role,
    list_player_claimable_characters,
    set_gm_control_character,
    set_seat_character,
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


def test_acting_character_follows_seat_binding(tmp_path):
    db = get_db(tmp_path / "seats.db")
    migrate(db)
    create_character(
        db, "Doremy Sweet", {"notes": "dream"}, campaign_role="NPC"
    )
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    assert player is not None
    set_seat_character(db, player["id"], serina)

    acting = _select_acting_character(
        db, "Review the state of the story.", seat_id=player["id"]
    )
    assert acting["name"] == "Serina"


def test_player_claim_and_gm_absentee_lease(tmp_path):
    db = get_db(tmp_path / "lease.db")
    migrate(db)
    create_character(db, "Doremy Sweet", {"notes": "npc"}, campaign_role="NPC")
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )
    ensure_default_session(db)

    claimable = list_player_claimable_characters(db)
    assert {item["name"] for item in claimable} == {"Serina"}

    # GM alone may lease the absentee PC.
    gm = set_gm_control_character(db, serina)
    assert gm["character_id"] == serina
    acting_gm = _select_acting_character(
        db, "Review the state of the story.", seat_id=gm["id"]
    )
    assert acting_gm["name"] == "Serina"

    # Player login reclaim ends the GM lease.
    player = claim_player_character(db, serina, "")
    assert player["character_id"] == serina
    gm = get_seat_by_role(db, "gm")
    assert gm is not None
    assert gm["character_id"] is None

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db, migrate
from sakaki_gm.seat_db import (
    bottle_occupied_character_ids,
    claim_desk_occupancy,
    claim_player_character,
    release_desk_occupancy,
)


def test_bottle_occupancy_blocks_second_holder(tmp_path):
    db = get_db(tmp_path / "occ.db")
    migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 23
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )

    claim_player_character(db, serina, "", holder_id="desk-a")
    assert serina in bottle_occupied_character_ids(db, except_holder_id="desk-b")
    assert serina not in bottle_occupied_character_ids(
        db, except_holder_id="desk-a"
    )

    try:
        claim_player_character(db, serina, "", holder_id="desk-b")
        assert False, "expected exclusive login failure"
    except PermissionError as exc:
        assert "already seated" in str(exc).lower()

    release_desk_occupancy(db, "desk-a")
    claim_player_character(db, serina, "", holder_id="desk-b")
    assert serina in bottle_occupied_character_ids(db, except_holder_id="desk-a")


def test_claim_desk_occupancy_rejects_taken_character(tmp_path):
    db = get_db(tmp_path / "occ2.db")
    migrate(db)
    serina = create_character(
        db, "Serina", {"notes": "pc"}, campaign_role="Protagonist"
    )
    claim_desk_occupancy(
        db, holder_id="one", desk_role="player", character_id=serina
    )
    try:
        claim_desk_occupancy(
            db, holder_id="two", desk_role="player", character_id=serina
        )
        assert False, "expected failure"
    except PermissionError:
        pass

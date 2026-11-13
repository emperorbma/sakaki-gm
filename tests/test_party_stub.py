from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.party_db import (
    active_party_for_character,
    approve_party_proposal,
    ensure_default_party,
    list_parties,
    list_party_proposals,
    merge_parties,
    party_member_ids,
    reject_party_proposal,
    request_party_merge,
    request_party_split,
    split_party,
)
from sakaki_gm.seat_db import (
    ensure_default_session,
    get_knowledge,
    get_seat_by_role,
    set_gm_control_character,
    sync_seat_knowledge_from_world,
)


def _pc(db, name, role="Protagonist"):
    return create_character(
        db, name, {"name": name, "class": "Fighter"}, campaign_role=role
    )


def test_default_party_and_split_merge(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 22
    a = _pc(db, "Serina")
    b = _pc(db, "Elara")
    c = _pc(db, "Koren")
    party = ensure_default_party(db)
    assert party["status"] == "active"
    assert set(party["member_ids"]) >= {a, b, c}

    split = split_party(db, party["id"], [b, c], new_party_name="Scout wing")
    assert set(split["remaining"]["member_ids"]) == {a}
    assert set(split["split"]["member_ids"]) == {b, c}
    assert active_party_for_character(db, b)["id"] == split["split"]["id"]
    assert len(list_parties(db, status="active")) == 2

    merged = merge_parties(
        db,
        [split["remaining"]["id"], split["split"]["id"]],
        into_party_id=split["remaining"]["id"],
        new_name="Reunited",
    )
    assert merged["name"] == "Reunited"
    assert set(merged["member_ids"]) == {a, b, c}
    assert len(list_parties(db, status="active")) == 1
    db.close()


def test_player_request_needs_gm_approval(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    a = _pc(db, "Serina")
    b = _pc(db, "Elara")
    party = ensure_default_party(db)
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")

    proposal = request_party_split(
        db,
        from_party_id=party["id"],
        character_ids=[b],
        new_party_name="Elara alone",
        requested_by_seat_id=player["id"],
        note="I scout ahead",
    )
    assert proposal["status"] == "pending"
    assert list_party_proposals(db, status="pending")[0]["id"] == proposal["id"]
    # Membership unchanged until approval.
    assert set(ensure_default_party(db)["member_ids"]) >= {a, b}

    approved = approve_party_proposal(db, proposal["id"])
    assert approved["proposal"]["status"] == "approved"
    assert active_party_for_character(db, b)["name"] == "Elara alone"

    # Merge request + reject leaves split intact.
    parties = list_parties(db, status="active")
    merge_req = request_party_merge(
        db,
        party_ids=[party["id"] for party in parties],
        into_party_id=parties[0]["id"],
        requested_by_seat_id=player["id"],
    )
    rejected = reject_party_proposal(db, merge_req["id"])
    assert rejected["status"] == "rejected"
    assert len(list_parties(db, status="active")) == 2
    db.close()


def test_party_membership_feeds_seat_knowledge(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    serina = _pc(db, "Serina")
    elara = _pc(db, "Elara")
    ensure_default_party(db)
    ensure_default_session(db)
    player = get_seat_by_role(db, "player")
    db.execute(
        "UPDATE seats SET character_id=? WHERE id=?",
        (serina, player["id"]),
    )
    db.commit()
    sync_seat_knowledge_from_world(db, player["id"])
    assert get_knowledge(db, player["id"], "character", elara) == "present"

    split_party(db, ensure_default_party(db)["id"], [elara], new_party_name="Away")
    # Knowledge upgrades only; split does not strip prior awareness.
    assert get_knowledge(db, player["id"], "character", elara) == "present"
    db.close()


def test_gm_override_is_dual_membership_not_a_split(tmp_path):
    """GM control keeps home-party membership and adds the GM implicit party."""
    from sakaki_gm.party_db import (
        CONTROL_GM_OVERRIDE,
        CONTROL_SEATED,
        active_parties_for_character,
        gm_control_party_for_character,
        resolve_member_control,
    )

    db = get_db(tmp_path / "campaign.db")
    serina = _pc(db, "Serina")
    elara = _pc(db, "Elara")
    party = ensure_default_party(db)
    ensure_default_session(db)

    assert resolve_member_control(db, serina)["mode"] == CONTROL_SEATED
    # Elara is playerish with a controller seat after creation.
    assert resolve_member_control(db, elara)["mode"] == CONTROL_SEATED

    set_gm_control_character(db, elara)
    parties = active_parties_for_character(db, elara)
    assert len(parties) == 2
    assert elara in party_member_ids(db, party["id"])
    gm_party = gm_control_party_for_character(db, elara)
    assert gm_party is not None
    assert gm_party["notes"].startswith("gm_control_party")
    assert active_party_for_character(db, elara)["id"] == party["id"]
    assert resolve_member_control(db, elara)["mode"] == CONTROL_GM_OVERRIDE

    set_gm_control_character(db, None)
    assert active_parties_for_character(db, elara) == [
        active_party_for_character(db, elara)
    ]
    assert elara in party_member_ids(db, party["id"])
    assert gm_control_party_for_character(db, elara) is None
    db.close()


def test_unseated_npc_member_is_committee_until_gm_override(tmp_path):
    from sakaki_gm.character_db import create_character
    from sakaki_gm.party_db import (
        CONTROL_COMMITTEE,
        CONTROL_GM_OVERRIDE,
        add_party_member,
        resolve_member_control,
    )

    db = get_db(tmp_path / "campaign.db")
    _pc(db, "Serina")
    snake = create_character(
        db,
        "Giant Constrictor Snake",
        {"name": "Giant Constrictor Snake"},
        campaign_role="NPC",
    )
    party = ensure_default_party(db)
    ensure_default_session(db)
    add_party_member(db, party["id"], snake)
    assert resolve_member_control(db, snake)["mode"] == CONTROL_COMMITTEE

    set_gm_control_character(db, snake)
    assert snake in party_member_ids(db, party["id"])
    assert resolve_member_control(db, snake)["mode"] == CONTROL_GM_OVERRIDE
    set_gm_control_character(db, None)
    assert resolve_member_control(db, snake)["mode"] == CONTROL_COMMITTEE
    db.close()


def test_party_members_may_lack_seats_and_gm_seat_attaches(tmp_path):
    """Party → Members; Seat attaches optionally (Members ↔ Seat)."""
    from sakaki_gm.character_db import create_character
    from sakaki_gm.party_db import add_party_member, get_party_with_members
    from sakaki_gm.seat_db import get_seat_by_role, seats_for_party

    db = get_db(tmp_path / "campaign.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 26
    serina = _pc(db, "Serina")
    snake = create_character(
        db,
        "Giant Constrictor Snake",
        {"name": "Giant Constrictor Snake"},
        campaign_role="NPC",
    )
    party = ensure_default_party(db)
    ensure_default_session(db)
    add_party_member(db, party["id"], snake)
    full = get_party_with_members(db, party["id"])
    assert set(full["member_ids"]) >= {serina, snake}
    # Snake is a member with no controller seat.
    player = get_seat_by_role(db, "player")
    gm = get_seat_by_role(db, "gm")
    assert player["party_id"] == party["id"]
    assert gm["party_id"] == party["id"]
    assert {s["id"] for s in seats_for_party(db, party["id"])} >= {
        player["id"],
        gm["id"],
    }

    set_gm_control_character(db, snake)
    gm = get_seat_by_role(db, "gm")
    from sakaki_gm.party_db import gm_control_party_for_character

    ctrl = gm_control_party_for_character(db, snake)
    assert ctrl is not None
    assert gm["party_id"] == ctrl["id"]
    assert gm["character_id"] == snake
    # Dual membership: still in the player party; GM seat is on the override party.
    assert snake in full["member_ids"] or snake in party_member_ids(db, party["id"])
    assert player["id"] in {
        s["id"] for s in seats_for_party(db, party["id"])
    }
    assert gm["id"] not in {
        s["id"] for s in seats_for_party(db, party["id"])
    }

    set_gm_control_character(db, None)
    gm = get_seat_by_role(db, "gm")
    assert gm["party_id"] == party["id"]
    assert gm["character_id"] is None
    assert snake in party_member_ids(db, party["id"])
    db.close()

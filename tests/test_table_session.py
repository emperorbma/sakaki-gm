from pathlib import Path

from sakaki_gm.campaigns import create_campaign
from sakaki_gm.net.client import TableClient, TableClientError
from sakaki_gm.net.server import TableServer, TableServerState
from sakaki_gm.session import TableSession


def test_host_blocks_campaign_change_while_client_connected(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    cid = create_campaign(config, "Alpha", campaigns)
    path = Path(campaigns / f"{cid}.db")

    state = TableServerState(
        campaign_id=cid,
        campaign_name="Alpha",
        campaign_path=str(path),
        secret="table-key",
    )
    server = TableServer(state, host="127.0.0.1", port=0)
    host, port = server.start()
    assert state.can_change_campaign() is True

    client = TableClient(f"http://127.0.0.1:{port}", secret="table-key")
    joined = client.join("Scout")
    assert joined["session_id"]
    assert state.other_peer_count() == 1
    assert state.can_change_campaign() is False

    try:
        state.set_campaign(
            campaign_id="other",
            campaign_name="Beta",
            campaign_path=str(path),
            session_id=state.host_session_id,
        )
        assert False, "expected PermissionError"
    except PermissionError as exc:
        assert "other players" in str(exc).lower() or "session" in str(exc).lower()

    client.leave()
    assert state.can_change_campaign() is True
    info = state.set_campaign(
        campaign_id="beta",
        campaign_name="Beta",
        campaign_path=str(path),
        session_id=state.host_session_id,
    )
    assert info["name"] == "Beta"
    server.stop()


def test_peer_chat_round_trip(tmp_path):
    state = TableServerState(
        campaign_id="c1",
        campaign_name="Chat",
        campaign_path=str(tmp_path / "x.db"),
        secret="",
    )
    server = TableServer(state, host="127.0.0.1", port=0)
    _host, port = server.start()
    client = TableClient(f"http://127.0.0.1:{port}")
    client.join("Player")
    posted = client.post_chat("Player", "hello table")
    assert posted["text"] == "hello table"
    messages = client.chat_since(0)
    assert any(item["text"] == "hello table" for item in messages)
    server.stop()


def test_table_session_host_endpoint_and_solo_allows_switch(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    cid = create_campaign(config, "Solo", campaigns)
    path = campaigns / f"{cid}.db"
    solo = TableSession.solo(
        campaign_id=cid, campaign_name="Solo", campaign_path=path
    )
    solo.start()
    assert solo.can_change_campaign() is True
    solo.stop()

    host = TableSession(
        mode="host",
        campaign_id=cid,
        campaign_name="Solo",
        campaign_path=path,
        bind_host="127.0.0.1",
        port=0,
        secret="s",
    )
    host.start()
    assert host.endpoint().startswith("http://127.0.0.1:")
    assert host.can_change_campaign() is True
    client = TableClient(host.endpoint(), secret="s")
    client.join("P2")
    assert host.can_change_campaign() is False
    assert host.campaign_change_block_reason()
    client.leave()
    assert host.can_change_campaign() is True
    host.stop()


def test_character_occupancy_is_exclusive(tmp_path):
    state = TableServerState(
        campaign_id="c1",
        campaign_name="Occ",
        campaign_path=str(tmp_path / "x.db"),
        secret="",
    )
    server = TableServer(state, host="127.0.0.1", port=0)
    _host, port = server.start()
    client = TableClient(f"http://127.0.0.1:{port}")
    joined = client.join("P2")
    session_id = joined["session_id"]

    state.claim_occupancy(
        session_id=state.host_session_id,
        character_id=25,
        desk_role="player",
        display_name="Host",
    )
    try:
        client.claim_occupancy(character_id=25, desk_role="player")
        assert False, "expected exclusive occupancy failure"
    except TableClientError as exc:
        assert "already seated" in str(exc).lower()

    state.release_occupancy(state.host_session_id)
    client.claim_occupancy(character_id=25, desk_role="player")
    assert 25 in state.occupied_character_ids(
        except_session_id=state.host_session_id
    )
    assert 25 not in state.occupied_character_ids(
        except_session_id=session_id
    )
    client.leave()
    assert 25 not in state.occupied_character_ids()
    server.stop()


def test_bad_secret_rejected(tmp_path):
    state = TableServerState(
        campaign_id="c1",
        campaign_name="Secured",
        campaign_path=str(tmp_path / "x.db"),
        secret="correct",
    )
    server = TableServer(state, host="127.0.0.1", port=0)
    _host, port = server.start()
    client = TableClient(f"http://127.0.0.1:{port}", secret="wrong")
    try:
        client.join("Nope")
        assert False, "expected auth failure"
    except TableClientError:
        pass
    server.stop()

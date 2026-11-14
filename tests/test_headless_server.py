from pathlib import Path

from sakaki_gm.campaigns import create_campaign
from sakaki_gm.headless_server import resolve_host_campaign
from sakaki_gm.net.client import TableClient
from sakaki_gm.net.server import TableServer, TableServerState
from sakaki_gm.session import TableSession


def test_resolve_host_campaign_none_means_unbound(tmp_path):
    assert resolve_host_campaign(campaign="") is None
    assert resolve_host_campaign(campaign=None) is None


def test_resolve_host_campaign_active_path_and_name(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    cid = create_campaign(config, "Headless Demo", campaigns)
    resolved = resolve_host_campaign(
        campaign=cid,
        config_path=config,
        campaigns_dir=campaigns,
    )
    assert resolved is not None
    assert resolved[0] == cid
    assert resolved[1] == "Headless Demo"
    assert Path(resolved[2]).is_file()

    by_path = resolve_host_campaign(
        campaign=str(resolved[2]),
        config_path=config,
        campaigns_dir=campaigns,
    )
    assert by_path[0] == cid
    by_name = resolve_host_campaign(
        campaign="Headless Demo",
        config_path=config,
        campaigns_dir=campaigns,
    )
    assert by_name[0] == cid


def test_gm_login_binds_unbound_table_campaign(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    cid = create_campaign(config, "From Login", campaigns)
    path = campaigns / f"{cid}.db"

    state = TableServerState(secret="")
    assert state.is_bound() is False
    server = TableServer(state, host="127.0.0.1", port=0)
    _host, port = server.start()
    try:
        client = TableClient(f"http://127.0.0.1:{port}")
        joined = client.join(
            "Table GM",
            role="gm",
            campaign={
                "id": cid,
                "name": "From Login",
                "path": str(path.resolve()),
            },
        )
        assert joined["role"] == "gm"
        assert joined["campaign"]["bound"] is True
        assert joined["campaign"]["name"] == "From Login"
        assert state.is_bound() is True

        player = TableClient(f"http://127.0.0.1:{port}")
        follow = player.join("Player Two", role="player")
        assert follow["campaign"]["name"] == "From Login"
    finally:
        server.stop()


def test_headless_host_session_accepts_join_without_ui(tmp_path):
    """Same TableSession host path --server uses; no Tk involved."""
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    cid = create_campaign(config, "Serve Me", campaigns)
    _cid, name, path = resolve_host_campaign(
        campaign=cid, config_path=config, campaigns_dir=campaigns
    )
    session = TableSession(
        mode="host",
        campaign_id=cid,
        campaign_name=name,
        campaign_path=path,
        bind_host="127.0.0.1",
        port=0,
    )
    session.start()
    try:
        assert session.endpoint().startswith("http://127.0.0.1:")
        client = TableClient(session.endpoint())
        joined = client.join("Player One")
        assert joined.get("session_id")
        assert (joined.get("campaign") or {}).get("name") == "Serve Me"
    finally:
        session.stop()

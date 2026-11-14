from pathlib import Path

from sakaki_gm.campaigns import create_campaign
from sakaki_gm.headless_server import resolve_host_campaign
from sakaki_gm.net.client import TableClient
from sakaki_gm.session import TableSession


def test_resolve_host_campaign_active_path_and_name(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    cid = create_campaign(config, "Headless Demo", campaigns)
    resolved = resolve_host_campaign(
        config_path=config, campaigns_dir=campaigns
    )
    assert resolved[0] == cid
    assert resolved[1] == "Headless Demo"
    assert Path(resolved[2]).is_file()

    by_path = resolve_host_campaign(
        campaign=str(resolved[2]),
        config_path=config,
        campaigns_dir=campaigns,
    )
    assert by_path[0] == cid
    by_id = resolve_host_campaign(
        campaign=cid,
        config_path=config,
        campaigns_dir=campaigns,
    )
    assert by_id[2] == resolved[2]
    by_name = resolve_host_campaign(
        campaign="Headless Demo",
        config_path=config,
        campaigns_dir=campaigns,
    )
    assert by_name[0] == cid


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

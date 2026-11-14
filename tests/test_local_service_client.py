from pathlib import Path

from sakaki_gm.campaigns import create_campaign
from sakaki_gm.net.client import TableClient
from sakaki_gm.net.protocol import DEFAULT_PORT
from sakaki_gm.net.service import (
    headless_server_command,
    spawn_headless_server,
    terminate_service,
    wait_for_table,
)
from sakaki_gm.session import TableSession


def _free_port() -> int:
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def test_headless_server_command_shape():
    cmd = headless_server_command(port=9001, bind="127.0.0.1", secret="s")
    assert "--server" in cmd
    assert cmd[cmd.index("--port") + 1] == "9001"
    assert cmd[cmd.index("--bind") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--secret") + 1] == "s"
    assert "--campaign" not in cmd  # unbound by default


def test_spawn_unbound_then_gm_client_binds(tmp_path):
    config = tmp_path / "config.json"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    cid = create_campaign(config, "Spawned", campaigns)
    path = campaigns / f"{cid}.db"
    port = _free_port()

    session = TableSession.start_local_service_client(
        campaign_id=cid,
        campaign_name="Spawned",
        campaign_path=path,
        port=port,
        display_name="GM",
    )
    try:
        assert session.mode == "client"
        assert session._service_process is not None
        assert session._service_process.poll() is None
        info = TableClient(session.remote_url).campaign()
        # After GM login the table should be bound.
        assert info.get("bound") is True
        assert info.get("name") == "Spawned"
        # Headless host process is not a desk; GM join alone → desks=1.
        assert info.get("peer_count") == 1
        assert session.peer_count() == 1
        player = TableClient(session.remote_url)
        player.join("Serina", role="player")
        assert session.peer_count() == 2
        assert TableClient(session.remote_url).campaign()["peer_count"] == 2
    finally:
        session.stop()
        assert (
            session._service_process is None
            or session._service_process.poll() is not None
        )


def test_wait_for_table_timeout():
    import pytest

    with pytest.raises(TimeoutError):
        wait_for_table("http://127.0.0.1:1", timeout=0.3, interval=0.05)

from sakaki_gm.launch import (
    LAUNCH_JOIN,
    LAUNCH_SERVER,
    is_headless_server,
    lobby_default_mode,
    parse_launch_args,
)


def test_default_launch_opens_start_server_lobby():
    args = parse_launch_args([])
    assert args.server is False
    assert args.join is False
    assert is_headless_server(args) is False
    assert lobby_default_mode(args) == LAUNCH_SERVER


def test_legacy_solo_flag_maps_to_start_server_lobby():
    args = parse_launch_args(["--solo"])
    assert lobby_default_mode(args) == LAUNCH_SERVER


def test_server_flag_is_headless_not_lobby_default():
    args = parse_launch_args(["--server"])
    assert args.server is True
    assert is_headless_server(args) is True
    assert lobby_default_mode(args) == LAUNCH_SERVER


def test_server_accepts_host_options():
    args = parse_launch_args(
        ["--server", "--campaign", "demo", "--port", "9001", "--bind", "127.0.0.1", "--secret", "s"]
    )
    assert args.campaign == "demo"
    assert args.port == 9001
    assert args.bind == "127.0.0.1"
    assert args.secret == "s"


def test_join_flag_selects_client_lobby():
    args = parse_launch_args(["--join"])
    assert args.join is True
    assert is_headless_server(args) is False
    assert lobby_default_mode(args) == LAUNCH_JOIN


def test_server_and_join_are_exclusive():
    import pytest

    with pytest.raises(SystemExit):
        parse_launch_args(["--server", "--join"])

from sakaki_gm.launch import (
    LAUNCH_JOIN,
    LAUNCH_SERVER,
    LAUNCH_SOLO,
    lobby_default_mode,
    parse_launch_args,
)


def test_default_launch_is_single_table_solo():
    args = parse_launch_args([])
    assert args.server is False
    assert args.join is False
    assert lobby_default_mode(args) == LAUNCH_SOLO


def test_server_flag_selects_host_lobby():
    args = parse_launch_args(["--server"])
    assert args.server is True
    assert lobby_default_mode(args) == LAUNCH_SERVER


def test_join_flag_selects_client_lobby():
    args = parse_launch_args(["--join"])
    assert args.join is True
    assert lobby_default_mode(args) == LAUNCH_JOIN


def test_server_and_join_are_exclusive():
    import pytest

    with pytest.raises(SystemExit):
        parse_launch_args(["--server", "--join"])

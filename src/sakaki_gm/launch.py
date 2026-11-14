"""Process launch modes for Sakaki GM.

Default desk launch is a single-table session (local bottle, multiuser seats,
no network). ``--server`` runs a headless campaign table host (no Tk UI);
dedicated start-*-server wrappers pass that flag.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from sakaki_gm.net.protocol import DEFAULT_PORT


LAUNCH_SOLO = "solo"
LAUNCH_SERVER = "host"
LAUNCH_JOIN = "client"

LAUNCH_MODES = (LAUNCH_SOLO, LAUNCH_SERVER, LAUNCH_JOIN)


def parse_launch_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sakaki-gm",
        description=(
            "Sakaki GM — local Game Master desk. Default is single-table play "
            "(multiuser seats, no network). Pass --server for a headless "
            "campaign table host."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--server",
        action="store_true",
        help="Run a headless campaign table server (no lobby or desk UI).",
    )
    mode.add_argument(
        "--join",
        action="store_true",
        help="Open the join-table lobby (login to a remote host).",
    )
    mode.add_argument(
        "--solo",
        action="store_true",
        help="Single-table play with no network (default).",
    )
    parser.add_argument(
        "--campaign",
        default="",
        help=(
            "With --server: bottle path, campaign id, or registered name "
            "(default: active campaign)."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"With --server: listen port (default {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--bind",
        default="0.0.0.0",
        help="With --server: bind address (default 0.0.0.0).",
    )
    parser.add_argument(
        "--secret",
        default="",
        help="With --server: optional table secret.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def lobby_default_mode(args: argparse.Namespace | None = None) -> str:
    """Lobby radio mode for the visual desk path (not used by --server)."""
    if args is None:
        return LAUNCH_SOLO
    if getattr(args, "server", False):
        # Headless path; callers should not open the lobby.
        return LAUNCH_SERVER
    if getattr(args, "join", False):
        return LAUNCH_JOIN
    return LAUNCH_SOLO


def is_headless_server(args: argparse.Namespace | None) -> bool:
    return bool(args is not None and getattr(args, "server", False))

"""Process launch modes for Sakaki GM.

Default desk launch is a single-table session (local bottle, multiuser seats,
no network). ``--server`` starts the campaign table as a host so others can
join; dedicated start-*-server wrappers pass that flag.
"""

from __future__ import annotations

import argparse
from typing import Sequence


LAUNCH_SOLO = "solo"
LAUNCH_SERVER = "host"
LAUNCH_JOIN = "client"

LAUNCH_MODES = (LAUNCH_SOLO, LAUNCH_SERVER, LAUNCH_JOIN)


def parse_launch_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sakaki-gm",
        description=(
            "Sakaki GM — local Game Master desk. Default is single-table play "
            "(multiuser seats, no network). Pass --server to host a table."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--server",
        action="store_true",
        help="Host a campaign table server (visual host lobby).",
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
    return parser.parse_args(list(argv) if argv is not None else None)


def lobby_default_mode(args: argparse.Namespace | None = None) -> str:
    """Lobby radio / session mode implied by launch flags."""
    if args is None:
        return LAUNCH_SOLO
    if getattr(args, "server", False):
        return LAUNCH_SERVER
    if getattr(args, "join", False):
        return LAUNCH_JOIN
    return LAUNCH_SOLO

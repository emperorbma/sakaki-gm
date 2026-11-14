"""Process launch modes for Sakaki GM.

Desk lobby offers two paths: login to an existing table, or start a server
(spawn headless host + join as client). ``--server`` runs headless with no Tk.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from sakaki_gm.net.protocol import DEFAULT_PORT


# Lobby / session wire modes (desk UI).
LAUNCH_SERVER = "host"
LAUNCH_JOIN = "client"
# Deprecated alias — same as start-server in the lobby.
LAUNCH_SOLO = "solo"

LAUNCH_MODES = (LAUNCH_SERVER, LAUNCH_JOIN)


def parse_launch_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sakaki-gm",
        description=(
            "Sakaki GM — local Game Master desk. Default opens the lobby to "
            "start a table server or login to one. Pass --server for a "
            "headless campaign table host."
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
        help="Open the lobby on Login to existing server.",
    )
    mode.add_argument(
        "--solo",
        action="store_true",
        help=argparse.SUPPRESS,  # legacy; same as default start-server lobby
    )
    parser.add_argument(
        "--campaign",
        default="",
        help=(
            "With --server: optional bottle path, campaign id, or name to pin "
            "at startup. Default is unbound — a GM login supplies the campaign."
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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="With --server: DEBUG logs (HTTP access lines).",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def lobby_default_mode(args: argparse.Namespace | None = None) -> str:
    """Lobby radio mode for the visual desk path (not used by --server)."""
    if args is None:
        return LAUNCH_SERVER
    if getattr(args, "server", False):
        # Headless path; callers should not open the lobby.
        return LAUNCH_SERVER
    if getattr(args, "join", False):
        return LAUNCH_JOIN
    # --solo and bare launch both open Start server.
    return LAUNCH_SERVER


def is_headless_server(args: argparse.Namespace | None) -> bool:
    return bool(args is not None and getattr(args, "server", False))

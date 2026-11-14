"""Headless campaign table server (no Tk lobby or desk).

Started by ``python -m sakaki_gm --server`` / the start-*-server wrappers.
Serves the active (or ``--campaign``) bottle until interrupted.
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
from typing import Any

from sakaki_gm.campaigns import (
    CAMPAIGNS_DIR,
    active_path,
    bootstrap,
    campaign_name,
)
from sakaki_gm.llm_config import CONFIG_PATH, load_config
from sakaki_gm.net.protocol import DEFAULT_PORT
from sakaki_gm.session import TableSession


def resolve_host_campaign(
    *,
    campaign: str | None = None,
    config_path=None,
    campaigns_dir=None,
) -> tuple[str, str, Path]:
    """Return ``(campaign_id, campaign_name, path)`` for hosting."""
    config_path = config_path or CONFIG_PATH
    campaigns_dir = campaigns_dir or CAMPAIGNS_DIR
    cfg, _active = bootstrap(config_path, campaigns_dir=campaigns_dir)
    cfg = load_config(config_path)
    needle = (campaign or "").strip()
    if needle:
        path = Path(needle).expanduser()
        if path.is_file():
            resolved = str(path.resolve())
            for item in cfg["campaigns"]:
                if str(Path(item["path"]).resolve()) == resolved:
                    return (
                        item["id"],
                        campaign_name(path),
                        Path(item["path"]),
                    )
            # Unregistered bottle — host by path with a synthetic id.
            return path.stem, campaign_name(path), path
        for item in cfg["campaigns"]:
            if item["id"] == needle:
                path = Path(item["path"])
                if not path.is_file():
                    raise FileNotFoundError(f"Campaign bottle missing: {path}")
                return item["id"], campaign_name(path), path
            if campaign_name(item["path"]).casefold() == needle.casefold():
                path = Path(item["path"])
                if path.is_file():
                    return item["id"], campaign_name(path), path
        raise FileNotFoundError(
            f"Unknown campaign {needle!r}. Pass a bottle path, campaign id, "
            "or registered name."
        )
    path = active_path(cfg)
    if path is None or not path.is_file():
        raise FileNotFoundError("No active campaign bottle to host.")
    cid = str(cfg.get("active_campaign") or "")
    return cid, campaign_name(path), path


def run_headless_server(args: Any) -> int:
    """Start the table HTTP server and block until SIGINT/SIGTERM."""
    cid, name, path = resolve_host_campaign(
        campaign=getattr(args, "campaign", None),
    )
    raw_port = getattr(args, "port", None)
    port = DEFAULT_PORT if raw_port is None else int(raw_port)
    bind = str(getattr(args, "bind", None) or "0.0.0.0").strip() or "0.0.0.0"
    secret = str(getattr(args, "secret", None) or "")
    session = TableSession(
        mode="host",
        campaign_id=cid,
        campaign_name=name,
        campaign_path=path,
        secret=secret,
        bind_host=bind,
        port=port,
    )
    session.start()
    stop = threading.Event()

    def _stop(_signum=None, _frame=None):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    print(
        f"Sakaki GM table server: {name}\n"
        f"  bottle: {path}\n"
        f"  listen: {bind}:{session.port}\n"
        f"  join:   {session.endpoint()}\n"
        f"Press Ctrl+C to stop.",
        flush=True,
    )
    try:
        while not stop.wait(timeout=0.5):
            pass
    finally:
        session.stop()
        print("Sakaki GM table server stopped.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry used by tests; production goes through ``sakaki_gm.__main__``."""
    from sakaki_gm.launch import parse_launch_args

    args = parse_launch_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "server", False):
        raise SystemExit("headless_server.main requires --server")
    return run_headless_server(args)

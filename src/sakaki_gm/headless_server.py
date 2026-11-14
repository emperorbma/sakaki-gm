"""Headless campaign table server (no Tk lobby or desk).

Started by ``python -m sakaki_gm --server`` / the start-*-server wrappers.

By default the table starts **unbound**; a GM login supplies the campaign
(``join`` with ``role=gm`` and campaign metadata, or ``POST /campaign``).
``--campaign`` still pins a bottle at process start when you want that.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger("sakaki_gm.table")


def configure_server_logging(*, verbose: bool = False) -> None:
    """Send table-server logs to stderr."""
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger("sakaki_gm")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("sakaki_gm.table").setLevel(level)


def resolve_host_campaign(
    *,
    campaign: str | None = None,
    config_path=None,
    campaigns_dir=None,
) -> tuple[str, str, Path] | None:
    """Return ``(campaign_id, campaign_name, path)`` or ``None`` if unbound.

    Empty ``campaign`` means wait for a GM login to bind the bottle.
    """
    needle = (campaign or "").strip()
    if not needle:
        return None
    config_path = config_path or CONFIG_PATH
    campaigns_dir = campaigns_dir or CAMPAIGNS_DIR
    cfg, _active = bootstrap(config_path, campaigns_dir=campaigns_dir)
    cfg = load_config(config_path)
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
        "or registered name — or omit --campaign and let a GM login bind it."
    )


def run_headless_server(args: Any) -> int:
    """Start the table HTTP server and block until SIGINT/SIGTERM."""
    configure_server_logging(verbose=bool(getattr(args, "verbose", False)))
    resolved = resolve_host_campaign(
        campaign=getattr(args, "campaign", None),
    )
    raw_port = getattr(args, "port", None)
    port = DEFAULT_PORT if raw_port is None else int(raw_port)
    bind = str(getattr(args, "bind", None) or "0.0.0.0").strip() or "0.0.0.0"
    secret = str(getattr(args, "secret", None) or "")
    if resolved is None:
        cid, name, path = "", "(unbound)", ""
        logger.info(
            "starting unbound table — GM login will supply the campaign"
        )
    else:
        cid, name, path = resolved
        path = str(path)
        logger.info("starting with pinned campaign %s (%s)", name, path)
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

    def _on_event(event: dict[str, Any]) -> None:
        kind = event.get("type") or "event"
        reason = event.get("reason") or ""
        if kind == "chat":
            msg = event.get("message") or {}
            logger.info(
                "chat %s: %s",
                msg.get("speaker") or "?",
                (msg.get("text") or "")[:120],
            )
        else:
            logger.info(
                "table event: %s%s",
                kind,
                f" ({reason})" if reason else "",
            )

    if session._state is not None:
        session._state.on_event = _on_event

    stop = threading.Event()

    def _stop(_signum=None, _frame=None):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    bound = bool(resolved)
    print(
        f"Sakaki GM table server\n"
        f"  campaign: {name}"
        f"{'' if bound else ' (waiting for GM login)'}\n"
        f"  bottle:   {path or '—'}\n"
        f"  listen:   {bind}:{session.port}\n"
        f"  join:     {session.endpoint()}\n"
        f"Press Ctrl+C to stop.",
        flush=True,
    )
    logger.info(
        "listening on %s:%s join=%s secret=%s",
        bind,
        session.port,
        session.endpoint(),
        "yes" if secret else "no",
    )
    try:
        while not stop.wait(timeout=0.5):
            pass
    finally:
        session.stop()
        logger.info("table server stopped")
        print("Sakaki GM table server stopped.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry used by tests; production goes through ``sakaki_gm.__main__``."""
    from sakaki_gm.launch import parse_launch_args

    args = parse_launch_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "server", False):
        raise SystemExit("headless_server.main requires --server")
    return run_headless_server(args)

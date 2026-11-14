"""python -m sakaki_gm"""
from __future__ import annotations

import sys
from typing import Any, Sequence
from urllib.parse import urlparse

from sakaki_gm.launch import (
    LAUNCH_JOIN,
    is_headless_server,
    lobby_default_mode,
    parse_launch_args,
)


def _join_prefills_from_session(session) -> dict[str, Any]:
    """Build lobby Login fields from a disconnected client session."""
    endpoint = ""
    secret = ""
    name = "Player"
    try:
        endpoint = session.endpoint() or ""
        secret = str(getattr(session, "secret", "") or "")
        name = str(getattr(session, "display_name", "") or "Player")
    except Exception:
        pass
    host = "127.0.0.1"
    port = None
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.hostname:
            host = parsed.hostname
        if parsed.port:
            port = parsed.port
    return {
        "default_mode": LAUNCH_JOIN,
        "join_host": host,
        "join_port": port,
        "join_secret": secret,
        "join_name": name,
    }


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_launch_args(argv if argv is not None else sys.argv[1:])
    if is_headless_server(args):
        # No Tk — dedicated server wrappers / --server stay headless.
        from sakaki_gm.headless_server import run_headless_server

        raise SystemExit(run_headless_server(args))

    import tkinter as tk

    from sakaki_gm.ui.lobby import run_lobby
    from sakaki_gm.ui.main_window import MainWindow

    default_mode = lobby_default_mode(args)
    reconnect: dict[str, Any] | None = None

    root = tk.Tk()
    root.title("Sakaki GM")

    while True:
        root.withdraw()
        for child in root.winfo_children():
            try:
                child.destroy()
            except tk.TclError:
                pass

        lobby_kwargs: dict[str, Any] = {"default_mode": default_mode}
        if reconnect:
            lobby_kwargs.update(reconnect)
            reconnect = None

        # Lobby is a free Toplevel (not transient on a withdrawn parent) so it
        # actually maps; wait_window then blocks until Continue / Quit.
        lobby = run_lobby(root, **lobby_kwargs)
        if lobby is None:
            root.destroy()
            return

        outcome: dict[str, Any] = {"action": "quit"}
        state: dict[str, Any] = {"window": None}

        def return_to_lobby() -> None:
            win = state["window"]
            if win is not None and win.session is not None:
                outcome["reconnect"] = _join_prefills_from_session(win.session)
            outcome["action"] = "lobby"
            try:
                if win is not None:
                    win.release_desk_presence()
            except Exception:
                pass
            try:
                if win is not None and win.session is not None:
                    win.session.stop()
            except Exception:
                pass
            root.quit()

        def on_close() -> None:
            outcome["action"] = "quit"
            win = state["window"]
            try:
                if win is not None:
                    win.release_desk_presence()
            except Exception:
                pass
            try:
                if win is not None and win.session is not None:
                    win.session.stop()
            except Exception:
                pass
            root.quit()

        # Stay withdrawn through first seat login so the desk cannot spoil.
        window = MainWindow(
            root,
            session=lobby.session,
            on_return_to_lobby=return_to_lobby,
        )
        state["window"] = window
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()

        if outcome.get("action") != "lobby":
            root.destroy()
            return
        reconnect = outcome.get("reconnect")
        default_mode = LAUNCH_JOIN


if __name__ == "__main__":
    main()

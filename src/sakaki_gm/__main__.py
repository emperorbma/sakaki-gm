"""python -m sakaki_gm"""
from __future__ import annotations

import sys
from typing import Sequence

from sakaki_gm.launch import (
    is_headless_server,
    lobby_default_mode,
    parse_launch_args,
)


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

    root = tk.Tk()
    root.title("Sakaki GM")
    root.withdraw()
    # Lobby is a free Toplevel (not transient on a withdrawn parent) so it
    # actually maps; wait_window then blocks until Continue / Quit.
    lobby = run_lobby(root, default_mode=default_mode)
    if lobby is None:
        root.destroy()
        return
    # Stay withdrawn through first seat login so the desk cannot spoil the
    # table before a seat is chosen. MainWindow reveals the root after sit.
    window = MainWindow(root, session=lobby.session)

    def on_close():
        try:
            window.release_desk_presence()
        except Exception:
            pass
        try:
            if window.session is not None:
                window.session.stop()
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

"""python -m sakaki_gm"""
import tkinter as tk

from sakaki_gm.ui.lobby import run_lobby
from sakaki_gm.ui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    lobby = run_lobby(root)
    if lobby is None:
        root.destroy()
        return
    root.deiconify()
    window = MainWindow(root, session=lobby.session)

    def on_close():
        try:
            if window.session is not None:
                window.session.stop()
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

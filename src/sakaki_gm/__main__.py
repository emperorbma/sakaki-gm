"""python -m sakaki_gm"""
import tkinter as tk

from sakaki_gm.ui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()

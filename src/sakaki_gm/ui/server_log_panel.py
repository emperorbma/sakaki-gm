"""Host-process tab: live table-server log.

Visible on the desk whose process is running the table server — not gated by
GM vs player seat. Client desks (joined to someone else's host) do not get it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sakaki_gm.net.server_log import ServerLogBridge


class ServerLogPanel:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self._bridge: Optional[ServerLogBridge] = None
        hint = ttk.Label(
            self.frame,
            text="Table server log — this desk's process is the host.",
            font=("Georgia", 10),
        )
        hint.pack(anchor="w", padx=10, pady=(10, 0))
        self.log = tk.Text(
            self.frame,
            font=("Menlo", 10),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            wrap=tk.WORD,
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log.configure(state="disabled")

    def bind_bridge(self, bridge: ServerLogBridge | None) -> None:
        if self._bridge is not None:
            self._bridge.unsubscribe(self._on_line)
        self._bridge = bridge
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        if bridge is None:
            self.log.configure(state="disabled")
            return
        for line in bridge.snapshot():
            self.log.insert(tk.END, line + "\n")
        self.log.configure(state="disabled")
        self.log.see(tk.END)
        bridge.subscribe(self._on_line)

    def _on_line(self, line: str) -> None:
        # Logging may emit off the Tk thread; schedule UI work.
        try:
            self.frame.after(0, lambda text=line: self._append(text))
        except tk.TclError:
            pass

    def _append(self, line: str) -> None:
        try:
            if not self.frame.winfo_exists():
                return
        except tk.TclError:
            return
        self.log.configure(state="normal")
        self.log.insert(tk.END, line + "\n")
        self.log.configure(state="disabled")
        self.log.see(tk.END)

    def close(self) -> None:
        if self._bridge is not None:
            self._bridge.unsubscribe(self._on_line)
            self._bridge = None

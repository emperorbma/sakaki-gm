"""Startup lobby: login to an existing table, or start a server.

Two desk paths only:
- **Login** — join an already-running table service as a client.
- **Start server** — spawn headless ``sakaki-gm --server``, pipe output to the
  Server log tab, join as a client. Host = who runs that process (≠ GM seat).
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Optional
from urllib.parse import urlparse

from sakaki_gm.campaigns import (
    CAMPAIGNS_DIR,
    active_path,
    bootstrap,
    campaign_name,
    create_campaign,
    set_active,
)
from sakaki_gm.launch import LAUNCH_JOIN, LAUNCH_SERVER
from sakaki_gm.llm_config import CONFIG_PATH, load_config
from sakaki_gm.net.protocol import DEFAULT_PORT
from sakaki_gm.session import TableSession


class LobbyResult:
    def __init__(self, session: TableSession):
        self.session = session


class LobbyDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        *,
        config_path=None,
        campaigns_dir=None,
        default_mode: str = LAUNCH_SERVER,
    ):
        super().__init__(parent)
        self.config_path = config_path or CONFIG_PATH
        self.campaigns_dir = campaigns_dir or CAMPAIGNS_DIR
        self.result: Optional[LobbyResult] = None
        mode = str(default_mode or LAUNCH_SERVER).strip().lower()
        # Legacy "solo" was the same as start-server; fold it in.
        if mode == "solo":
            mode = LAUNCH_SERVER
        if mode not in {LAUNCH_SERVER, LAUNCH_JOIN}:
            mode = LAUNCH_SERVER
        self.title("Sakaki GM — Lobby")
        self.geometry("560x580")
        self.minsize(560, 580)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._quit_app)
        # Do not call transient() while the parent is withdrawn — on X11 that
        # often leaves this dialog unmapped and wait_window looks like a freeze.

        # Ensure registry has at least one bottle for host pickers.
        bootstrap(self.config_path, campaigns_dir=self.campaigns_dir)
        cfg = load_config(self.config_path)

        # Footer first so Start/Quit stay pinned even if content grows.
        buttons = ttk.Frame(self, padding=(16, 0, 16, 16))
        buttons.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(buttons, text="Quit", command=self._quit_app).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(buttons, text="Start", command=self._continue).pack(
            side=tk.RIGHT
        )

        body = ttk.Frame(self, padding=16)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text="Table lobby",
            font=("Georgia", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            text=(
                "Login joins an already-running table. "
                "Start server spawns the table host and opens this desk as a "
                "client (server output on the Server log tab). "
                "Host = who runs the server — not the GM seat."
            ),
            wraplength=470,
        ).pack(anchor="w", pady=(6, 12))

        self.mode = tk.StringVar(value=mode)
        ttk.Radiobutton(
            body,
            text="Login to existing server",
            variable=self.mode,
            value=LAUNCH_JOIN,
            command=self._refresh_mode,
        ).pack(anchor="w")
        ttk.Radiobutton(
            body,
            text="Start server",
            variable=self.mode,
            value=LAUNCH_SERVER,
            command=self._refresh_mode,
        ).pack(anchor="w", pady=(0, 8))

        self.panels = ttk.Frame(body)
        self.panels.pack(fill=tk.BOTH, expand=True)

        self.host_frame = ttk.LabelFrame(self.panels, text="Campaign + service")
        ttk.Label(self.host_frame, text="Campaign").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )
        self._campaign_ids = [item["id"] for item in cfg["campaigns"]]
        labels = [campaign_name(item["path"]) for item in cfg["campaigns"]]
        self.campaign_var = tk.StringVar()
        self.campaign_combo = ttk.Combobox(
            self.host_frame,
            textvariable=self.campaign_var,
            values=labels,
            state="readonly",
            width=36,
        )
        self.campaign_combo.grid(row=0, column=1, sticky="w", padx=8, pady=6)
        active = cfg.get("active_campaign")
        if active in self._campaign_ids:
            self.campaign_combo.current(self._campaign_ids.index(active))
        elif labels:
            self.campaign_combo.current(0)
        ttk.Button(
            self.host_frame, text="New…", command=self._new_campaign
        ).grid(row=0, column=2, padx=6)
        self.host_port_label = ttk.Label(self.host_frame, text="Port")
        self.host_port_label.grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.host_port_entry = ttk.Entry(
            self.host_frame, textvariable=self.port_var, width=10
        )
        self.host_port_entry.grid(row=1, column=1, sticky="w", padx=8, pady=6)
        self.host_secret_label = ttk.Label(
            self.host_frame, text="Table secret (optional)"
        )
        self.host_secret_label.grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.host_secret = tk.StringVar()
        self.host_secret_entry = ttk.Entry(
            self.host_frame, textvariable=self.host_secret, show="*", width=28
        )
        self.host_secret_entry.grid(row=2, column=1, sticky="w", padx=8, pady=6)

        self.join_frame = ttk.LabelFrame(self.panels, text="Join")
        ttk.Label(self.join_frame, text="Host URL or IP").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )
        self.join_host = tk.StringVar(value="127.0.0.1")
        ttk.Entry(self.join_frame, textvariable=self.join_host, width=28).grid(
            row=0, column=1, sticky="w", padx=8, pady=6
        )
        ttk.Label(self.join_frame, text="Port").grid(
            row=1, column=0, sticky="w", padx=8, pady=6
        )
        self.join_port = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(self.join_frame, textvariable=self.join_port, width=10).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )
        ttk.Label(self.join_frame, text="Table secret (optional)").grid(
            row=2, column=0, sticky="w", padx=8, pady=6
        )
        self.join_secret = tk.StringVar()
        ttk.Entry(
            self.join_frame, textvariable=self.join_secret, show="*", width=28
        ).grid(row=2, column=1, sticky="w", padx=8, pady=6)
        ttk.Label(self.join_frame, text="Display name").grid(
            row=3, column=0, sticky="w", padx=8, pady=6
        )
        self.join_name = tk.StringVar(value="Player")
        ttk.Entry(self.join_frame, textvariable=self.join_name, width=28).grid(
            row=3, column=1, sticky="w", padx=8, pady=6
        )

        self._refresh_mode()
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            # Some WMs refuse grab until the window is fully mapped.
            self.after(50, self._try_grab)

    def _try_grab(self):
        try:
            if self.winfo_exists():
                self.grab_set()
        except tk.TclError:
            pass

    def _refresh_mode(self):
        mode = self.mode.get()
        self.host_frame.pack_forget()
        self.join_frame.pack_forget()
        if mode == LAUNCH_JOIN:
            self.join_frame.pack(fill=tk.X, pady=4)
            return
        self.host_frame.pack(fill=tk.X, pady=4)
        self.campaign_combo.configure(state="readonly")

    def _new_campaign(self):
        name = simpledialog.askstring(
            "New campaign", "Campaign name:", parent=self
        )
        if not name or not name.strip():
            return
        cid = create_campaign(
            self.config_path, name.strip(), self.campaigns_dir
        )
        cfg = load_config(self.config_path)
        self._campaign_ids = [item["id"] for item in cfg["campaigns"]]
        labels = [campaign_name(item["path"]) for item in cfg["campaigns"]]
        self.campaign_combo["values"] = labels
        if cid in self._campaign_ids:
            self.campaign_combo.current(self._campaign_ids.index(cid))

    def _selected_campaign(self):
        cfg = load_config(self.config_path)
        index = self.campaign_combo.current()
        if index < 0 or index >= len(self._campaign_ids):
            path = active_path(cfg)
            cid = cfg.get("active_campaign")
        else:
            cid = self._campaign_ids[index]
            item = next(item for item in cfg["campaigns"] if item["id"] == cid)
            path = Path(item["path"])
        if path is None or not Path(path).is_file():
            raise ValueError("Choose a valid campaign bottle.")
        set_active(self.config_path, cid)
        return cid, campaign_name(path), Path(path)

    def _continue(self):
        mode = self.mode.get()
        try:
            if mode == LAUNCH_JOIN:
                host = self.join_host.get().strip() or "127.0.0.1"
                port = int(self.join_port.get().strip() or DEFAULT_PORT)
                if "://" in host:
                    remote = host
                    parsed = urlparse(host)
                    if parsed.port:
                        port = parsed.port
                else:
                    remote = f"http://{host}:{port}"
                # Joiner still needs a local bottle for the desk stub; use active.
                bootstrap(self.config_path, campaigns_dir=self.campaigns_dir)
                cfg = load_config(self.config_path)
                path = active_path(cfg)
                session = TableSession(
                    mode=LAUNCH_JOIN,
                    campaign_id=str(cfg.get("active_campaign") or ""),
                    campaign_name=campaign_name(path) if path else "Joined table",
                    campaign_path=path or Path("."),
                    secret=self.join_secret.get(),
                    remote_url=remote,
                    display_name=self.join_name.get().strip() or "Player",
                    port=port,
                    join_role="player",
                )
                session.start()
            else:
                cid, name, path = self._selected_campaign()
                port = int(self.port_var.get().strip() or DEFAULT_PORT)
                session = TableSession.start_local_service_client(
                    campaign_id=cid,
                    campaign_name=name,
                    campaign_path=path,
                    secret=self.host_secret.get(),
                    port=port,
                    display_name="GM",
                )
        except Exception as exc:
            messagebox.showerror("Lobby", str(exc), parent=self)
            return
        self.result = LobbyResult(session)
        self.destroy()

    def _quit_app(self):
        self.result = None
        self.destroy()


def run_lobby(
    parent,
    *,
    config_path=None,
    campaigns_dir=None,
    default_mode: str = LAUNCH_SERVER,
) -> Optional[LobbyResult]:
    dialog = LobbyDialog(
        parent,
        config_path=config_path,
        campaigns_dir=campaigns_dir,
        default_mode=default_mode,
    )
    parent.wait_window(dialog)
    return dialog.result

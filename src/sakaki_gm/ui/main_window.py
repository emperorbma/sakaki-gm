# ui/main_window.py
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pyperclip

from sakaki_gm.campaigns import (
    CAMPAIGNS_DIR,
    active_path,
    bootstrap,
    campaign_backup_dir,
    create_campaign_backup,
    campaign_name,
    create_campaign,
    register_file,
    remove_campaign,
    rename_campaign,
    restore_campaign_from_backup,
    set_active,
)  # campaign_name used for session campaign switches
from sakaki_gm.db import get_db
from sakaki_gm.gm import (
    apply_proposals,
    apply_updates,
    build_messages,
    citation_messages,
    format_clipboard,
    parse_reply,
    proposal_summary_lines,
)
from sakaki_gm.llm_client import complete
from sakaki_gm.llm_config import (
    AI_ASSIST_MODES,
    CONFIG_PATH,
    ai_assist_mode,
    desk_mode,
    effective_ai_assist_mode,
    list_model_choices,
    load_config,
    model_settings_dict,
    resolve_ask_model,
    save_config,
    set_default_model,
    show_gm_reply_debug,
)
from sakaki_gm.scratch_context import ScratchContext
from sakaki_gm.ui.desk_audience import (
    normalize_desk_mode,
    shared_is_stripped,
    surface_visible,
)
from sakaki_gm.ui.seat_login import (
    SeatLoginResult,
    apply_seat_choice,
    run_seat_login,
    seat_login_label,
)
from sakaki_gm.ui.gm_tools import (
    format_turn_wait_status,
    run_clear_wait,
    run_gm_control_character,
    run_party_manager,
    run_reassign_controller_seat,
    run_request_roll,
    run_set_character_knowledge,
    run_set_turn_phase,
    run_sync_awareness,
)
from sakaki_gm.ui.log_panel import LogPanel
from sakaki_gm.ui.modules.characters import CharactersModule
from sakaki_gm.ui.modules.documents import DocumentsModule
from sakaki_gm.ui.modules.geography import GeographyModule
from sakaki_gm.ui.modules.items import ItemsModule
from sakaki_gm.ui.modules.actors import ActorsModule
from sakaki_gm.ui.modules.quests import QuestsModule
from sakaki_gm.ui.modules.settings import SettingsModule
from sakaki_gm.ui.modules.skills import SkillsModule
from sakaki_gm.ui.modules.spells import SpellsModule
from sakaki_gm.ui.modules.world import WorldModule


class MainWindow:
    def __init__(
        self,
        root,
        config_path=None,
        campaigns_dir=None,
        legacy_db=None,
        session=None,
    ):
        self.root = root
        self.config_path = config_path or CONFIG_PATH
        self.campaigns_dir = campaigns_dir or CAMPAIGNS_DIR
        self.session = session
        self._loading_campaigns = False
        self._ask_busy = False
        legacy = legacy_db if legacy_db is not None else None
        if legacy_db is None:
            from sakaki_gm.db import LEGACY_DB
            legacy = LEGACY_DB
        if session is not None and session.campaign_path:
            path = Path(session.campaign_path)
            if path.is_file():
                cfg = load_config(self.config_path)
                if session.campaign_id:
                    try:
                        set_active(self.config_path, session.campaign_id)
                        cfg = load_config(self.config_path)
                    except Exception:
                        pass
                self._active_campaign_id = cfg.get("active_campaign")
                self.db_path = path.resolve()
                self.db = get_db(path)
            else:
                cfg, path = bootstrap(self.config_path, legacy, self.campaigns_dir)
                self._active_campaign_id = cfg["active_campaign"]
                self.db_path = Path(path).resolve()
                self.db = get_db(path)
        else:
            cfg, path = bootstrap(self.config_path, legacy, self.campaigns_dir)
            self._active_campaign_id = cfg["active_campaign"]
            self.db_path = Path(path).resolve()
            self.db = get_db(path)
        self.root.geometry("1600x950")
        self._seat_session_active = False
        import uuid

        self.desk_holder_id = uuid.uuid4().hex
        self.setup_ui()
        self.refresh_campaign_chrome()
        if self.session is not None:
            self.session.subscribe(self._on_session_event)
            endpoint = self.session.endpoint()
            if endpoint:
                self.log_panel.add_entry(
                    "System",
                    f"Table session ({self.session.mode}): {endpoint}"
                    + (
                        f" — {self.session.campaign_name}"
                        if self.session.campaign_name
                        else ""
                    ),
                )
        # Seat login gates the desk: GM vs player is a login, not a mid-flow toggle.
        # Keep the board hidden on first sit so campaign chrome cannot spoil.
        try:
            self.root.withdraw()
        except tk.TclError:
            pass
        self.root.update_idletasks()
        self.prompt_seat_login()
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            pass

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=15, pady=(12, 4))
        ttk.Label(header, text="Sakaki GM", font=("Georgia", 28, "bold")).pack(
            side=tk.LEFT
        )
        mode_wrap = ttk.Frame(header)
        mode_wrap.pack(side=tk.LEFT, padx=(16, 0))
        self.desk_mode_var = tk.StringVar(
            value=desk_mode(load_config(self.config_path))
        )
        self.seat_login_var = tk.StringVar(
            value=f"Signed in as {seat_login_label(self.desk_mode_var.get())}"
        )
        self.seat_login_label = ttk.Label(
            mode_wrap,
            textvariable=self.seat_login_var,
            font=("Georgia", 11),
        )
        self.seat_login_label.pack(side=tk.LEFT)
        self.switch_seat_button = ttk.Button(
            mode_wrap,
            text="Switch seat…",
            command=self.switch_seat_login,
        )
        self.switch_seat_button.pack(side=tk.LEFT, padx=(10, 0))
        self.desk_mode_hint = ttk.Label(mode_wrap, text="")
        self.desk_mode_hint.pack(side=tk.LEFT, padx=(8, 0))

        self.campaign_admin = ttk.Frame(header)
        self.campaign_admin.pack(side=tk.RIGHT)
        for label, command in (
            ("Remove", self.remove_current),
            ("Restore", self.restore_current),
            ("Backup", self.backup_current),
            ("Open", self.open_campaign_file),
            ("Rename", self.rename_current),
            ("New", self.new_campaign),
        ):
            ttk.Button(self.campaign_admin, text=label, command=command).pack(
                side=tk.RIGHT, padx=4
            )
        self.campaign_combo = ttk.Combobox(header, state="readonly", width=32)
        self.campaign_combo.pack(side=tk.RIGHT, padx=8)
        self.campaign_combo.bind("<<ComboboxSelected>>", self.on_campaign_selected)

        place = ttk.Frame(self.root)
        place.pack(fill=tk.X, padx=15, pady=(0, 8))
        self.location_label = ttk.Label(place, text="Location")
        self.location_label.pack(side=tk.LEFT)
        self.location_var = tk.StringVar()
        self.location_entry = ttk.Entry(place, textvariable=self.location_var)
        self.location_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.location_entry.bind("<FocusOut>", lambda _event: self.save_location())
        self.location_entry.bind("<Return>", lambda _event: self.save_location())

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=15)
        self.characters_module = CharactersModule(self.notebook, self.db, self)
        self.world_module = WorldModule(self.notebook, self.db)
        self.documents_module = DocumentsModule(self.notebook, self.db)
        self.geography_module = GeographyModule(self.notebook, self.db)
        self.items_module = ItemsModule(self.notebook, self.db)
        self.quests_module = QuestsModule(self.notebook, self.db)
        self.actors_module = ActorsModule(self.notebook, self.db)
        self.skills_module = SkillsModule(self.notebook, self.db)
        self.spells_module = SpellsModule(self.notebook, self.db)
        self.settings_module = SettingsModule(self.notebook, self.db, self)
        self.log_panel = LogPanel(self.root, self.db)
        self._desk_tabs = [
            ("characters", self.characters_module.frame, "Characters"),
            ("world", self.world_module.frame, "World"),
            ("documents", self.documents_module.frame, "Documents"),
            ("geography", self.geography_module.frame, "Geography"),
            ("items", self.items_module.frame, "Items"),
            ("quests", self.quests_module.frame, "Quests"),
            ("actors", self.actors_module.frame, "Actors"),
            ("skills", self.skills_module.frame, "Skills"),
            ("spells", self.spells_module.frame, "Spells"),
            ("settings", self.settings_module.frame, "Settings"),
            ("log", self.log_panel.frame, "Log"),
        ]
        for _key, frame, title in self._desk_tabs:
            self.notebook.add(frame, text=title)

        self.gm_tools = ttk.LabelFrame(self.root, text="GM tools")
        self.gm_tools.pack(fill=tk.X, padx=15, pady=(8, 0))
        ttk.Button(
            self.gm_tools,
            text="Request roll",
            command=self.gm_request_roll,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Clear wait",
            command=self.gm_clear_wait,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Set phase",
            command=self.gm_set_phase,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Sync awareness",
            command=self.gm_sync_awareness,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Set knowledge",
            command=self.gm_set_knowledge,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Parties",
            command=self.gm_parties,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Ask focus…",
            command=self.gm_control_character,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(
            self.gm_tools,
            text="Reassign seat…",
            command=self.gm_reassign_seat,
        ).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Label(self.gm_tools, text="Model").pack(side=tk.LEFT, padx=(12, 4))
        self.gm_model_var = tk.StringVar(value="")
        self._gm_model_ids = []
        self.gm_model_combo = ttk.Combobox(
            self.gm_tools,
            textvariable=self.gm_model_var,
            state="readonly",
            width=28,
        )
        self.gm_model_combo.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        self.gm_model_combo.bind(
            "<<ComboboxSelected>>", self.on_gm_model_selected
        )
        ttk.Label(self.gm_tools, text="AI role").pack(side=tk.LEFT, padx=(8, 4))
        self.ai_assist_var = tk.StringVar(
            value=AI_ASSIST_MODES[ai_assist_mode(load_config(self.config_path))]
        )
        self.ai_assist_combo = ttk.Combobox(
            self.gm_tools,
            textvariable=self.ai_assist_var,
            values=list(AI_ASSIST_MODES.values()),
            state="readonly",
            width=24,
        )
        self.ai_assist_combo.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        self.ai_assist_combo.bind(
            "<<ComboboxSelected>>", self.on_ai_assist_mode_selected
        )
        ttk.Label(
            self.gm_tools,
            text="GM selects model; players use config default / manager routing.",
        ).pack(side=tk.LEFT, padx=(6, 6))

        self.bottom_bar = ttk.Frame(self.root)
        self.bottom_bar.pack(fill=tk.X, padx=15, pady=(10, 4))
        bottom = self.bottom_bar
        self.action_entry = tk.Entry(bottom, font=("Georgia", 12))
        self.action_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.action_entry.bind("<Return>", lambda _event: self.ask_gm())
        self.ask_button = ttk.Button(bottom, text="Ask GM", command=self.ask_gm)
        self.ask_button.pack(side=tk.RIGHT, padx=5)
        self.copy_prompt_button = ttk.Button(
            bottom, text="Copy Prompt", command=self.copy_prompt
        )
        self.copy_prompt_button.pack(side=tk.RIGHT, padx=5)
        self.d20_button = ttk.Button(
            bottom, text="d20", command=lambda: self.quick_roll(20)
        )
        self.d20_button.pack(side=tk.RIGHT)

        self.chat_bar = ttk.Frame(self.root)
        self.chat_bar.pack(fill=tk.X, padx=15, pady=(0, 4))
        ttk.Label(self.chat_bar, text="Table chat").pack(side=tk.LEFT)
        self.chat_entry = tk.Entry(self.chat_bar, font=("Georgia", 11))
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.chat_entry.bind("<Return>", lambda _event: self.send_table_chat())
        ttk.Button(
            self.chat_bar, text="Send", command=self.send_table_chat
        ).pack(side=tk.RIGHT)
        self._seen_chat_ids: set[int] = set()

        status_row = ttk.Frame(self.root)
        status_row.pack(fill=tk.X, padx=15, pady=(0, 8))
        self.ask_status_var = tk.StringVar(value="")
        self.ask_status = ttk.Label(
            status_row,
            textvariable=self.ask_status_var,
            foreground="#4a5568",
        )
        self.ask_status.pack(side=tk.LEFT)
        self.session_status_var = tk.StringVar(value="")
        self.session_status = ttk.Label(
            status_row,
            textvariable=self.session_status_var,
            foreground="#2f855a",
        )
        self.session_status.pack(side=tk.LEFT, padx=(12, 0))
        self.turn_status_var = tk.StringVar(value="")
        self.turn_status = ttk.Label(
            status_row,
            textvariable=self.turn_status_var,
            foreground="#2c5282",
        )
        self.turn_status.pack(side=tk.RIGHT)

        self.reply_frame = ttk.LabelFrame(
            self.root,
            text="AI reply intercept (peek / rewrite before state changes)",
        )
        ttk.Label(
            self.reply_frame,
            text=(
                "When this panel is open, Ask / Run as GM parks the raw JSON "
                "here instead of applying it. Edit freely, then Apply, or Discard."
            ),
            wraplength=720,
        ).pack(anchor="w", padx=10, pady=(8, 0))
        self.reply_box = tk.Text(self.reply_frame, height=8, font=("Georgia", 11))
        self.reply_box.pack(fill=tk.X, expand=True, padx=10, pady=10)
        reply_buttons = ttk.Frame(self.reply_frame)
        reply_buttons.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(
            reply_buttons, text="Discard", command=self.discard_pending_reply
        ).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(
            reply_buttons, text="Apply", command=self.process_reply
        ).pack(side=tk.RIGHT)
        self.reply_pending_hint = ttk.Label(reply_buttons, text="")
        self.reply_pending_hint.pack(side=tk.LEFT)
        self._reply_debug_visible = False
        self._pending_reply_scratch = None
        self._pending_ask_assist = "manager"
        self.apply_ui_preferences()

    def refresh_campaign_chrome(self):
        self._loading_campaigns = True
        cfg = load_config(self.config_path)
        labels = []
        self._campaign_ids = []
        for item in cfg["campaigns"]:
            self._campaign_ids.append(item["id"])
            labels.append(campaign_name(item["path"]))
        self.campaign_combo["values"] = labels
        active = cfg.get("active_campaign")
        if active in self._campaign_ids:
            self.campaign_combo.current(self._campaign_ids.index(active))
        elif labels:
            self.campaign_combo.current(0)
        self._loading_campaigns = False
        name = self.get_meta("campaign_name", "New Campaign")
        self.root.title(f"Sakaki GM — {name}")
        self.load_location()

    def load_location(self):
        row = self.db.execute("SELECT value FROM world WHERE key='location'").fetchone()
        self.location_var.set(row["value"] if row else "")

    def save_location(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        self.db.execute(
            "INSERT OR REPLACE INTO world (key, value) VALUES ('location', ?)",
            (self.location_var.get(),),
        )
        self.db.commit()

    def _ensure_can_change_campaign(self) -> bool:
        """GM may change campaigns only when no other table sessions are live."""
        if self.session is None:
            return True
        reason = self.session.campaign_change_block_reason()
        if reason:
            messagebox.showerror("Campaign", reason)
            self.refresh_campaign_chrome()
            return False
        if not self.session.can_change_campaign():
            messagebox.showerror(
                "Campaign",
                "Cannot change campaign while other players have a session.",
            )
            self.refresh_campaign_chrome()
            return False
        return True

    def send_table_chat(self):
        text = self.chat_entry.get().strip()
        if not text:
            return
        if self.session is None:
            self.log_panel.add_entry("Chat", text)
            self.chat_entry.delete(0, tk.END)
            return
        seat = normalize_desk_mode(self.desk_mode_var.get())
        speaker = "GM" if seat == "gm" else (
            self.session.display_name or "Player"
        )
        try:
            entry = self.session.post_chat(speaker, text)
        except Exception as exc:
            messagebox.showerror("Table chat", str(exc))
            return
        self.chat_entry.delete(0, tk.END)
        # Host gets the event via subscribe; clients may also echo locally.
        chat_id = int(entry.get("id") or 0)
        if chat_id and chat_id not in self._seen_chat_ids:
            self._seen_chat_ids.add(chat_id)
            self.log_panel.add_entry("Chat", f"{entry.get('speaker')}: {entry.get('text')}")

    def _on_session_event(self, event: dict):
        """Network thread → UI thread desk refresh / chat."""
        self.root.after(0, lambda e=event: self._handle_session_event(e))

    def _handle_session_event(self, event: dict):
        kind = event.get("type")
        if kind == "chat":
            message = event.get("message") or {}
            chat_id = int(message.get("id") or 0)
            if chat_id and chat_id in self._seen_chat_ids:
                return
            if chat_id:
                self._seen_chat_ids.add(chat_id)
            speaker = message.get("speaker") or "Peer"
            text = message.get("text") or ""
            self.log_panel.add_entry("Chat", f"{speaker}: {text}")
            return
        if kind == "refresh":
            reason = event.get("reason") or ""
            if self.session is not None:
                peers = self.session.peer_count()
                alone = self.session.can_change_campaign()
                self.session_status_var.set(
                    f"table peers={peers}"
                    + ("; campaign switch ok" if alone else "; campaign locked")
                )
            if reason in {"campaign_changed", "peer_joined", "peer_left", "state_changed"}:
                self.characters_module.refresh()
                self.load_location()
                self.world_module.reload()
                self.log_panel.reload()
                self._refresh_turn_status()

    def on_campaign_selected(self, _event=None):
        if self._loading_campaigns:
            return
        index = self.campaign_combo.current()
        if index < 0 or index >= len(self._campaign_ids):
            return
        cid = self._campaign_ids[index]
        cfg = load_config(self.config_path)
        if cid == self._active_campaign_id:
            return
        if not self._ensure_can_change_campaign():
            return
        selected = next(
            (item for item in cfg["campaigns"] if item["id"] == cid),
            None,
        )
        if selected is None or not Path(selected["path"]).is_file():
            messagebox.showerror(
                "Campaign",
                "The selected campaign file is missing. Use Remove to drop this entry from the list.",
            )
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        path = set_active(self.config_path, cid)
        self._swap_db(path)
        if self.session is not None:
            try:
                self.session.set_campaign(
                    campaign_id=cid,
                    campaign_name=campaign_name(path),
                    campaign_path=path,
                )
            except PermissionError as exc:
                messagebox.showerror("Campaign", str(exc))

    def _swap_db(self, path):
        new_db = get_db(path)
        self.db.close()
        self.db = new_db
        self.db_path = Path(path).resolve()
        resolved_path = path.resolve()
        cfg = load_config(self.config_path)
        self._active_campaign_id = next(
            (
                item["id"] for item in cfg["campaigns"]
                if Path(item["path"]).resolve() == resolved_path
            ),
            None,
        )
        self.characters_module.set_db(new_db)
        self.world_module.set_db(new_db)
        self.documents_module.set_db(new_db)
        self.geography_module.set_db(new_db)
        self.items_module.set_db(new_db)
        self.quests_module.set_db(new_db)
        self.actors_module.set_db(new_db)
        self.skills_module.set_db(new_db)
        self.spells_module.set_db(new_db)
        self.settings_module.set_db(new_db)
        self.log_panel.set_db(new_db)
        self.refresh_campaign_chrome()

    def new_campaign(self):
        if not self._ensure_can_change_campaign():
            return
        name = simpledialog.askstring("New Campaign", "Campaign name:", parent=self.root)
        if not name or not name.strip():
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        cid = create_campaign(self.config_path, name.strip(), self.campaigns_dir)
        path = active_path(load_config(self.config_path))
        self._swap_db(path)
        if self.session is not None:
            try:
                self.session.set_campaign(
                    campaign_id=cid,
                    campaign_name=name.strip(),
                    campaign_path=path,
                )
            except PermissionError as exc:
                messagebox.showerror("Campaign", str(exc))

    def rename_current(self):
        current = self.get_meta("campaign_name", "New Campaign")
        name = simpledialog.askstring("Rename Campaign", "Campaign name:", initialvalue=current, parent=self.root)
        if not name or not name.strip():
            return
        rename_campaign(self.db, name.strip())
        self.refresh_campaign_chrome()

    def backup_current(self):
        try:
            self.save_location()
            self.world_module.save_prompt(quiet=True)
            self.documents_module.save_current(quiet=True)
            self.settings_module.save_system_prompt(quiet=True)
            backup_path = create_campaign_backup(self.db, self.db_path)
        except Exception as exc:
            messagebox.showerror("Campaign backup", str(exc))
            return
        messagebox.showinfo(
            "Campaign backup",
            f"Campaign backup created at:\n{backup_path}",
        )

    def restore_current(self):
        if self._ask_busy:
            messagebox.showinfo(
                "Campaign restore",
                "Wait for the current Ask GM request to finish before restoring.",
            )
            return
        initial_dir = campaign_backup_dir(self.db_path)
        initial_dir.mkdir(parents=True, exist_ok=True)
        chosen = filedialog.askopenfilename(
            title="Restore campaign from backup",
            initialdir=initial_dir,
            filetypes=[("SQLite", "*.db"), ("All files", "*.*")],
        )
        if not chosen:
            return
        backup_path = Path(chosen).resolve()
        if backup_path == self.db_path.resolve():
            messagebox.showerror(
                "Campaign restore",
                "Choose a backup file, not the live campaign database.",
            )
            return
        if not messagebox.askyesno(
            "Campaign restore",
            "Replace the current campaign with this backup?\n\n"
            f"{backup_path}\n\n"
            "A safety snapshot of the current campaign will be created first.",
        ):
            return
        try:
            self.save_location()
            self.world_module.save_prompt(quiet=True)
            self.documents_module.save_current(quiet=True)
            self.settings_module.save_system_prompt(quiet=True)
            safety_path = restore_campaign_from_backup(
                self.db,
                self.db_path,
                backup_path,
            )
        except Exception as exc:
            messagebox.showerror("Campaign restore", str(exc))
            return
        self._reload_open_campaign()
        detail = f"Restored from:\n{backup_path}"
        if safety_path is not None:
            detail += f"\n\nSafety snapshot saved at:\n{safety_path}"
        messagebox.showinfo("Campaign restore", detail)

    def _reload_open_campaign(self):
        """Refresh modules after in-place restore of the open database."""
        self.characters_module.set_db(self.db)
        self.world_module.set_db(self.db)
        self.documents_module.set_db(self.db)
        self.geography_module.set_db(self.db)
        self.items_module.set_db(self.db)
        self.quests_module.set_db(self.db)
        self.actors_module.set_db(self.db)
        self.skills_module.set_db(self.db)
        self.spells_module.set_db(self.db)
        self.settings_module.set_db(self.db)
        self.log_panel.set_db(self.db)
        self.refresh_campaign_chrome()

    def open_campaign_file(self):
        if not self._ensure_can_change_campaign():
            return
        chosen = filedialog.askopenfilename(
            title="Open campaign file",
            filetypes=[("SQLite", "*.db"), ("All files", "*.*")],
        )
        if not chosen:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        cid = register_file(self.config_path, chosen)
        path = active_path(load_config(self.config_path))
        self._swap_db(path)
        if self.session is not None:
            try:
                self.session.set_campaign(
                    campaign_id=cid,
                    campaign_name=campaign_name(path),
                    campaign_path=path,
                )
            except PermissionError as exc:
                messagebox.showerror("Campaign", str(exc))

    def remove_current(self):
        if not self._ensure_can_change_campaign():
            return
        index = self.campaign_combo.current()
        selected_id = (
            self._campaign_ids[index]
            if 0 <= index < len(self._campaign_ids)
            else None
        )
        cfg = load_config(self.config_path)
        selected = next(
            (item for item in cfg["campaigns"] if item["id"] == selected_id),
            None,
        )
        missing_selected = (
            selected is not None and not Path(selected["path"]).is_file()
        )
        cid = selected_id if missing_selected else self._active_campaign_id
        if cid is None:
            messagebox.showerror("Remove campaign", "The open campaign is no longer in the campaign list.")
            return
        label = "(missing campaign)" if missing_selected else self.get_meta("campaign_name", "this campaign")
        if not messagebox.askyesno(
            "Remove campaign",
            f"Remove {label} from the campaign list?",
        ):
            return
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        self.db.close()
        path = remove_campaign(self.config_path, cid, self.campaigns_dir)
        self._swap_db(path)

    def open_character_editor(self, char_id=None):
        self.characters_module.open_editor(char_id)

    def _desk_seat(self):
        """Seat for this desk's login — not the shared campaign active_seat.

        Multi-window tables share one bottle: a player desk can flip
        ``session_state.active_seat_id`` while the GM desk still holds a
        temporary lease. Ask / scene packs must follow this desk's role.
        """
        from sakaki_gm.seat_db import get_seat_by_role

        try:
            return get_seat_by_role(
                self.db, normalize_desk_mode(self.desk_mode_var.get())
            )
        except Exception:
            return None

    def _ask_speaker(self) -> str:
        """Human ask line: Controller on GM desk, Player on a controller desk."""
        from sakaki_gm.ui.log_panel import SPEAKER_CONTROLLER, SPEAKER_PLAYER

        if normalize_desk_mode(self.desk_mode_var.get()) == "gm":
            return SPEAKER_CONTROLLER
        return SPEAKER_PLAYER

    def copy_prompt(self):
        action = self.action_entry.get().strip()
        if not action:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        desk_seat = self._desk_seat()
        system, user = build_messages(
            self.db,
            action,
            seat_id=desk_seat["id"] if desk_seat else None,
        )
        pyperclip.copy(format_clipboard(system, user))
        self.log_panel.add_entry(self._ask_speaker(), action)
        self.action_entry.delete(0, tk.END)
        messagebox.showinfo("Copied", "Prompt copied to clipboard.")

    def prompt_seat_login(self, *, allow_cancel: bool = False, title: str = "Seat login"):
        """Modal seat login — required at startup; optional when switching."""
        cfg = load_config(self.config_path)
        current = desk_mode(cfg)
        occupied = set()
        if self.session is not None:
            try:
                occupied |= self.session.occupied_character_ids()
            except Exception:
                pass
        while True:
            choice = run_seat_login(
                self.root,
                self.db,
                initial=current,
                title=title,
                allow_cancel=allow_cancel and self._seat_session_active,
                holder_id=self.desk_holder_id,
                occupied_character_ids=occupied,
            )
            if choice is None:
                if allow_cancel and self._seat_session_active:
                    return False
                # Startup: cancel is not allowed — keep prompting.
                continue
            try:
                self._commit_seat_login(choice)
            except PermissionError as exc:
                messagebox.showerror("Seat login", str(exc))
                continue
            return True

    def switch_seat_login(self):
        """Re-open seat login so a solo host can swap GM ↔ player."""
        self.prompt_seat_login(allow_cancel=True, title="Switch seat")

    def release_desk_presence(self):
        """Drop bottle + table occupancy for this desk process."""
        from sakaki_gm.seat_db import release_desk_occupancy

        try:
            release_desk_occupancy(self.db, self.desk_holder_id)
        except Exception:
            pass
        if self.session is not None:
            try:
                self.session.release_presence()
            except Exception:
                pass

    def _commit_seat_login(self, choice: SeatLoginResult | str):
        from sakaki_gm.seat_db import release_desk_occupancy

        if isinstance(choice, SeatLoginResult):
            mode = normalize_desk_mode(choice.role)
            character_id = choice.character_id
        else:
            mode = normalize_desk_mode(choice)
            character_id = None
        # Table-wide exclusive claim (host/client). Solo relies on bottle occupancy
        # already recorded during claim_player_character.
        if mode == "gm":
            release_desk_occupancy(self.db, self.desk_holder_id, commit=True)
        if self.session is not None:
            try:
                self.session.claim_presence(
                    desk_role=mode,
                    character_id=character_id if mode == "player" else None,
                    display_name=self.session.display_name,
                )
            except PermissionError:
                if mode == "player":
                    release_desk_occupancy(self.db, self.desk_holder_id, commit=True)
                raise
        cfg = load_config(self.config_path)
        apply_seat_choice(cfg, mode)
        save_config(cfg, self.config_path)
        self.desk_mode_var.set(mode)
        self._seat_session_active = True
        self._sync_active_seat(mode)
        self.apply_ui_preferences()
        detail = seat_login_label(mode)
        if mode == "player" and character_id is not None:
            row = self.db.execute(
                "SELECT name FROM characters WHERE id=?", (character_id,)
            ).fetchone()
            if row is not None:
                detail = f"{detail} controlling {row['name']}"
        self.log_panel.add_entry("System", f"Seated as {detail}")

    def on_desk_mode_selected(self, _event=None):
        # Kept for any residual callers; seat changes go through login.
        mode = normalize_desk_mode(self.desk_mode_var.get())
        self._commit_seat_login(SeatLoginResult(role=mode))

    def _sync_active_seat(self, mode: str):
        """Desk toggle = active seat swap on the implicit two-seat table."""
        import sqlite3

        from sakaki_gm.seat_db import set_active_seat_by_role

        try:
            set_active_seat_by_role(self.db, mode)
        except (sqlite3.Error, ValueError):
            # Pre-v19 bottles mid-migrate still get ensure on next open.
            pass

    def _refresh_gm_model_picker(self, cfg=None):
        """GM-seat only: show configured models; selection sets the Ask default."""
        if not hasattr(self, "gm_model_combo"):
            return
        cfg = cfg or load_config(self.config_path)
        choices = list_model_choices(cfg)
        self._gm_model_ids = [model_id for model_id, _label in choices]
        labels = [label for _model_id, label in choices]
        self.gm_model_combo["values"] = labels
        default_id = cfg.get("default_model_id")
        if default_id in self._gm_model_ids:
            index = self._gm_model_ids.index(default_id)
            self.gm_model_var.set(labels[index])
        elif labels:
            self.gm_model_var.set(labels[0])
        else:
            self.gm_model_var.set("")

    def apply_ui_preferences(self):
        """Apply active-seat lens and debug-only controls from saved UI settings."""
        cfg = load_config(self.config_path)
        mode = desk_mode(cfg)
        self.desk_mode_var.set(mode)
        if hasattr(self, "seat_login_var"):
            self.seat_login_var.set(
                f"Signed in as {seat_login_label(mode)}"
            )
        assist = ai_assist_mode(cfg)
        if hasattr(self, "ai_assist_var"):
            self.ai_assist_var.set(AI_ASSIST_MODES[assist])
        self._refresh_gm_model_picker(cfg)
        self._sync_active_seat(mode)
        self.apply_desk_audience(mode)
        debug_ok = show_gm_reply_debug(cfg) and surface_visible("gm_reply_debug", mode)
        if debug_ok and not self._reply_debug_visible:
            self.reply_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
            self._reply_debug_visible = True
        elif not debug_ok and self._reply_debug_visible:
            if self.reply_box.get("1.0", tk.END).strip():
                self.log_panel.add_entry(
                    "System",
                    "Intercept panel closed; pending reply discarded.",
                )
            self.reply_box.delete("1.0", tk.END)
            self._pending_reply_scratch = None
            self.reply_pending_hint.configure(text="")
            self.reply_frame.pack_forget()
            self._reply_debug_visible = False

    def apply_desk_audience(self, mode: str | None = None):
        """Show GM-only surfaces for GM seat; strip shared modules for player seat."""
        from sakaki_gm.seat_db import get_session_state

        mode = normalize_desk_mode(mode if mode is not None else self.desk_mode_var.get())
        phase = "idle"
        try:
            phase = get_session_state(self.db).get("turn_phase") or "idle"
        except Exception:
            phase = "idle"
        if mode == "gm":
            hint = f"(GM seat; phase={phase})"
            self.ask_button.configure(text="Run as GM")
            if not self.gm_tools.winfo_ismapped():
                self.gm_tools.pack(
                    fill=tk.X, padx=15, pady=(8, 0), before=self.bottom_bar
                )
        else:
            hint = f"(player seat; phase={phase}; knowledge-filtered)"
            self.ask_button.configure(text="Ask GM")
            if self.gm_tools.winfo_ismapped():
                self.gm_tools.pack_forget()
        self.desk_mode_hint.configure(text=hint)
        self._refresh_turn_status()

        selected = None
        try:
            selected = self.notebook.select()
        except tk.TclError:
            selected = None
        for key, frame, title in self._desk_tabs:
            try:
                present = str(frame) in self.notebook.tabs()
            except tk.TclError:
                present = False
            visible = surface_visible(key, mode)
            if visible and not present:
                self.notebook.add(frame, text=title)
            elif not visible and present:
                self.notebook.forget(frame)
        if selected:
            try:
                if selected in self.notebook.tabs():
                    self.notebook.select(selected)
                elif self.notebook.tabs():
                    self.notebook.select(self.notebook.tabs()[0])
            except tk.TclError:
                pass

        if surface_visible("campaign_admin", mode):
            if not self.campaign_admin.winfo_ismapped():
                self.campaign_admin.pack(side=tk.RIGHT, before=self.campaign_combo)
        elif self.campaign_admin.winfo_ismapped():
            self.campaign_admin.pack_forget()

        if surface_visible("copy_prompt", mode):
            if not self.copy_prompt_button.winfo_ismapped():
                self.copy_prompt_button.pack(
                    side=tk.RIGHT, padx=5, before=self.ask_button
                )
        elif self.copy_prompt_button.winfo_ismapped():
            self.copy_prompt_button.pack_forget()

        self.location_entry.configure(
            state="normal" if mode == "gm" else "disabled"
        )

        self.characters_module.set_desk_audience(mode)
        self.world_module.set_desk_audience(mode)
        self.quests_module.set_desk_audience(mode)
        self.geography_module.set_desk_audience(mode)
        self.log_panel.set_desk_audience(mode)

    def _ask_button_label(self) -> str:
        if normalize_desk_mode(self.desk_mode_var.get()) == "gm":
            return "Run as GM"
        return "Ask GM"

    def _set_ask_busy(self, busy: bool, *, model: str = "") -> None:
        self._ask_busy = busy
        if busy:
            label = "Asking GM…"
            if model:
                label = f"Asking GM ({model})…"
            self.ask_status_var.set(label)
            self.ask_button.configure(text="Asking…")
            self.ask_button.state(["disabled"])
            self.copy_prompt_button.state(["disabled"])
            self.action_entry.configure(state="disabled")
            self.root.config(cursor="watch")
            try:
                from sakaki_gm.seat_db import set_turn_phase

                set_turn_phase(self.db, "model_busy")
                self._refresh_turn_status()
            except Exception:
                pass
        else:
            self.ask_status_var.set("")
            self.ask_button.configure(text=self._ask_button_label())
            self.ask_button.state(["!disabled"])
            self.copy_prompt_button.state(["!disabled"])
            self.action_entry.configure(state="normal")
            self.root.config(cursor="")
            try:
                from sakaki_gm.seat_db import get_session_state, set_turn_phase

                if get_session_state(self.db).get("turn_phase") == "model_busy":
                    set_turn_phase(self.db, "idle")
                self._refresh_turn_status()
            except Exception:
                pass

    def on_ai_assist_mode_selected(self, _event=None):
        label = self.ai_assist_var.get()
        mode = next(
            (key for key, text in AI_ASSIST_MODES.items() if text == label),
            "manager",
        )
        cfg = load_config(self.config_path)
        if not isinstance(cfg.get("ui"), dict):
            cfg["ui"] = {}
        cfg["ui"]["ai_assist_mode"] = mode
        save_config(cfg, self.config_path)
        if hasattr(self, "settings_module"):
            self.settings_module.reload_desk_ai_controls()

    def on_gm_model_selected(self, _event=None):
        """GM seat only: choosing a model sets the config default for Ask."""
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        label = self.gm_model_var.get()
        labels = list(self.gm_model_combo["values"] or ())
        if label not in labels:
            return
        model_id = self._gm_model_ids[labels.index(label)]
        cfg = load_config(self.config_path)
        try:
            set_default_model(cfg, model_id)
        except ValueError:
            return
        save_config(cfg, self.config_path)
        if hasattr(self, "settings_module"):
            self.settings_module.reload_desk_ai_controls()

    def ask_gm(self):
        if self._ask_busy:
            return
        action = self.action_entry.get().strip()
        if not action:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        desk_seat = self._desk_seat()
        system, user = build_messages(
            self.db,
            action,
            include_reference_material=False,
            seat_id=desk_seat["id"] if desk_seat else None,
        )
        cfg = load_config(self.config_path)
        seat = normalize_desk_mode(self.desk_mode_var.get())
        # Players never select a model; resolve_ask_model ignores overrides.
        entry = resolve_ask_model(cfg, desk_mode=seat)
        settings = model_settings_dict(entry)
        assist = effective_ai_assist_mode(cfg, model_entry=entry)
        if assist == "muse":
            user = (
                user
                + "\n\nASSIST MODE: muse. Return vivid narration and advice only. "
                "Do not change campaign state: updates must be {} and proposals "
                "must be []. Do not claim you applied writes."
            )
        tool_db_path = self.db_path
        model_name = str(settings.get("model") or "").strip()
        turn_scratch = ScratchContext()
        self._pending_ask_assist = assist
        self.log_panel.add_entry(self._ask_speaker(), action)
        self.action_entry.delete(0, tk.END)
        self._set_ask_busy(True, model=model_name)
        role_note = " as muse" if assist == "muse" else ""
        purpose = str(entry.get("purpose") or "").strip()
        purpose_note = (
            f", purpose={purpose}" if assist == "muse" and purpose else ""
        )
        self.log_panel.add_entry(
            "System",
            f"Asking model{role_note}"
            f"{' (' + model_name + ')' if model_name else ''}"
            f"{purpose_note}…",
        )

        def work():
            try:
                reply = complete(
                    settings,
                    system,
                    user,
                    tool_db_path=tool_db_path,
                    scratch=turn_scratch,
                    assist_mode=assist,
                )
            except Exception as exc:
                self.root.after(0, lambda err=exc: self._ask_failed(err))
                return
            self.root.after(
                0,
                lambda text=reply, scratch=turn_scratch: self._ask_finished(
                    text, scratch
                ),
            )

        threading.Thread(target=work, daemon=True).start()

    def _ask_failed(self, exc):
        self._set_ask_busy(False)
        self.log_panel.add_entry("System", f"Model request failed: {exc}")
        messagebox.showerror("Model", str(exc))

    def _ask_finished(self, reply, scratch=None):
        self._set_ask_busy(False)
        if self._reply_debug_visible:
            self._pending_reply_scratch = scratch
            self.reply_box.delete("1.0", tk.END)
            self.reply_box.insert("1.0", reply)
            self.reply_pending_hint.configure(
                text="Pending — rewrite if needed, then Apply or Discard."
            )
            self.log_panel.add_entry(
                "System",
                "AI reply parked in intercept panel (not applied yet).",
            )
            return
        self._apply_reply_text(reply, scratch=scratch)

    def discard_pending_reply(self):
        if not self._reply_debug_visible:
            return
        self.reply_box.delete("1.0", tk.END)
        self._pending_reply_scratch = None
        self.reply_pending_hint.configure(text="")
        self.log_panel.add_entry("System", "Pending AI reply discarded")

    def process_reply(self):
        if not self._reply_debug_visible:
            return
        reply = self.reply_box.get("1.0", tk.END).strip()
        if not reply:
            return
        scratch = self._pending_reply_scratch
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        self.reply_box.delete("1.0", tk.END)
        self._pending_reply_scratch = None
        self.reply_pending_hint.configure(text="")
        self._apply_reply_text(reply, scratch=scratch)

    def _apply_reply_text(self, reply: str, *, scratch=None):
        parsed = parse_reply(reply)
        assist = getattr(self, "_pending_ask_assist", None) or effective_ai_assist_mode(
            load_config(self.config_path)
        )
        allow_writes = assist == "manager"
        from sakaki_gm.ui.log_panel import SPEAKER_MODEL

        narration = parsed.narration or reply
        self.log_panel.add_entry(SPEAKER_MODEL, narration)
        for line in citation_messages(scratch, parsed.cited_record_ids):
            self.log_panel.add_entry("System", line)
        # Actors are AI minds, not GM Ask focus — still feed portrayed play.
        self._feed_actors_from_gm_ask(narration)
        if not allow_writes:
            if parsed.updates or parsed.proposals:
                self.log_panel.add_entry(
                    "System",
                    "Muse mode: ignored updates/proposals (narration only).",
                )
            else:
                self.log_panel.add_entry("System", "Muse mode: narration only")
            return
        if parsed.updates:
            for line in apply_updates(self.db, parsed.updates):
                self.log_panel.add_entry("System", line)
        else:
            self.log_panel.add_entry("System", "No state updates in the reply")
        if parsed.proposals:
            self._review_proposals(parsed.proposals, scratch=scratch)
        self.characters_module.refresh()
        self.load_location()
        self.world_module.reload()

    def _feed_actors_from_gm_ask(self, narration: str) -> None:
        """Append observed GM play onto Actors linked to the Ask focus."""
        desk = self._desk_seat()
        if desk is None or desk.get("role") != "gm":
            return
        if desk.get("character_id") is None:
            return
        try:
            from sakaki_gm.gm import note_gm_ask_for_actors

            updated = note_gm_ask_for_actors(
                self.db, narration, seat_id=desk.get("id")
            )
        except Exception:
            return
        if updated:
            self.log_panel.add_entry(
                "System",
                f"Actors noted GM play ({len(updated)}).",
            )

    def _review_proposals(self, proposals, *, scratch=None):
        proposal_record_ids = [
            proposal["record_id"]
            for proposal in proposals
            if isinstance(proposal.get("record_id"), str)
        ]
        for line in citation_messages(scratch, proposal_record_ids):
            if line.startswith("Unknown"):
                self.log_panel.add_entry("System", line)
        summaries = proposal_summary_lines(proposals)
        for summary in summaries:
            self.log_panel.add_entry("System", f"Proposal pending: {summary}")
        body = "\n".join(f"• {line}" for line in summaries)
        if not messagebox.askyesno(
            "GM proposals",
            "The GM proposed writes that need approval before they change "
            "the campaign:\n\n"
            f"{body}\n\n"
            "Apply these proposals now?",
        ):
            self.log_panel.add_entry("System", "Proposals declined")
            return
        for line in apply_proposals(self.db, proposals, scratch=scratch):
            self.log_panel.add_entry("System", line)

    def _refresh_turn_status(self):
        from sakaki_gm.seat_db import ensure_default_session, get_session_state

        try:
            ensure_default_session(self.db)
            state = get_session_state(self.db)
            self.turn_status_var.set(format_turn_wait_status(state))
        except Exception:
            self.turn_status_var.set("")

    def gm_request_roll(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        state = run_request_roll(self.root, self.db)
        if state is None:
            return
        wait = state.get("turn_wait") or {}
        self.log_panel.add_entry(
            "System",
            "GM requested roll: "
            f"{wait.get('character', '?')} · {wait.get('kind', 'check')}"
            + (f" ({wait['skill']})" if wait.get("skill") else "")
            + (f" DC {wait['dc']}" if wait.get("dc") is not None else ""),
        )
        self._refresh_turn_status()
        self.apply_desk_audience("gm")

    def gm_clear_wait(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        run_clear_wait(self.root, self.db)
        self.log_panel.add_entry("System", "GM cleared pending wait")
        self._refresh_turn_status()
        self.apply_desk_audience("gm")

    def gm_sync_awareness(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        upgraded = run_sync_awareness(self.root, self.db)
        self.log_panel.add_entry(
            "System", f"GM synced player awareness ({len(upgraded)} updates)"
        )
        self.characters_module.refresh()
        self.geography_module.set_desk_audience(
            normalize_desk_mode(self.desk_mode_var.get())
        )

    def gm_set_knowledge(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        if run_set_character_knowledge(self.root, self.db):
            self.log_panel.add_entry("System", "GM set character knowledge")
            self.characters_module.refresh()

    def gm_parties(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        summary = run_party_manager(self.root, self.db)
        if summary:
            self.log_panel.add_entry("System", f"GM parties: {summary}")
            self.characters_module.refresh()

    def gm_control_character(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        summary = run_gm_control_character(self.root, self.db)
        if summary:
            # Keep shared active_seat on GM so solo paths match the lease lens.
            self._sync_active_seat("gm")
            # Temporary-party marker is written by set_gm_control_character.
            self.log_panel.reload()
            self.characters_module.refresh()

    def gm_reassign_seat(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        summary = run_reassign_controller_seat(self.root, self.db)
        if summary:
            self.log_panel.add_entry("System", summary)
            self.characters_module.refresh()

    def gm_set_phase(self):
        if normalize_desk_mode(self.desk_mode_var.get()) != "gm":
            return
        state = run_set_turn_phase(self.root, self.db)
        if state is None:
            return
        self.log_panel.add_entry(
            "System",
            f"GM set session phase to {state.get('turn_phase') or 'idle'}",
        )
        self._refresh_turn_status()
        self.apply_desk_audience("gm")

    def quick_roll(self, sides):
        import random
        roll = random.randint(1, sides)
        self.action_entry.insert(0, f"[d{sides}: {roll}] ")
        self.log_panel.add_entry("System", f"Rolled d{sides}: {roll}")

    def get_meta(self, key, default):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

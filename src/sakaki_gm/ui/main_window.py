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
)
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
from sakaki_gm.llm_config import CONFIG_PATH, load_config, show_gm_reply_debug
from sakaki_gm.scratch_context import ScratchContext
from sakaki_gm.ui.log_panel import LogPanel
from sakaki_gm.ui.modules.characters import CharactersModule
from sakaki_gm.ui.modules.documents import DocumentsModule
from sakaki_gm.ui.modules.geography import GeographyModule
from sakaki_gm.ui.modules.items import ItemsModule
from sakaki_gm.ui.modules.quests import QuestsModule
from sakaki_gm.ui.modules.settings import SettingsModule
from sakaki_gm.ui.modules.world import WorldModule


class MainWindow:
    def __init__(self, root, config_path=None, campaigns_dir=None, legacy_db=None):
        self.root = root
        self.config_path = config_path or CONFIG_PATH
        self.campaigns_dir = campaigns_dir or CAMPAIGNS_DIR
        self._loading_campaigns = False
        self._ask_busy = False
        legacy = legacy_db if legacy_db is not None else None
        if legacy_db is None:
            from sakaki_gm.db import LEGACY_DB
            legacy = LEGACY_DB
        cfg, path = bootstrap(self.config_path, legacy, self.campaigns_dir)
        self._active_campaign_id = cfg["active_campaign"]
        self.db_path = Path(path).resolve()
        self.db = get_db(path)
        self.root.geometry("1600x950")
        self.setup_ui()
        self.refresh_campaign_chrome()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=15, pady=(12, 4))
        ttk.Label(header, text="Sakaki GM", font=("Georgia", 28, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Remove", command=self.remove_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Restore", command=self.restore_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Backup", command=self.backup_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Open", command=self.open_campaign_file).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="Rename", command=self.rename_current).pack(side=tk.RIGHT, padx=4)
        ttk.Button(header, text="New", command=self.new_campaign).pack(side=tk.RIGHT, padx=4)
        self.campaign_combo = ttk.Combobox(header, state="readonly", width=32)
        self.campaign_combo.pack(side=tk.RIGHT, padx=8)
        self.campaign_combo.bind("<<ComboboxSelected>>", self.on_campaign_selected)

        place = ttk.Frame(self.root)
        place.pack(fill=tk.X, padx=15, pady=(0, 8))
        ttk.Label(place, text="Location").pack(side=tk.LEFT)
        self.location_var = tk.StringVar()
        self.location_entry = ttk.Entry(place, textvariable=self.location_var)
        self.location_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.location_entry.bind("<FocusOut>", lambda _event: self.save_location())
        self.location_entry.bind("<Return>", lambda _event: self.save_location())

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=15)
        self.characters_module = CharactersModule(notebook, self.db, self)
        notebook.add(self.characters_module.frame, text="Characters")
        self.world_module = WorldModule(notebook, self.db)
        notebook.add(self.world_module.frame, text="World")
        self.documents_module = DocumentsModule(notebook, self.db)
        notebook.add(self.documents_module.frame, text="Documents")
        self.geography_module = GeographyModule(notebook, self.db)
        notebook.add(self.geography_module.frame, text="Geography")
        self.items_module = ItemsModule(notebook, self.db)
        notebook.add(self.items_module.frame, text="Archetypes")
        self.quests_module = QuestsModule(notebook, self.db)
        notebook.add(self.quests_module.frame, text="Quests")
        self.settings_module = SettingsModule(notebook, self.db, self)
        notebook.add(self.settings_module.frame, text="Settings")
        self.log_panel = LogPanel(self.root, self.db)
        notebook.add(self.log_panel.frame, text="Log")

        bottom = ttk.Frame(self.root)
        bottom.pack(fill=tk.X, padx=15, pady=(10, 4))
        self.action_entry = tk.Entry(bottom, font=("Georgia", 12))
        self.action_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.action_entry.bind("<Return>", lambda _event: self.ask_gm())
        self.ask_button = ttk.Button(bottom, text="Ask GM", command=self.ask_gm)
        self.ask_button.pack(side=tk.RIGHT, padx=5)
        self.copy_prompt_button = ttk.Button(
            bottom, text="Copy Prompt", command=self.copy_prompt
        )
        self.copy_prompt_button.pack(side=tk.RIGHT, padx=5)
        ttk.Button(bottom, text="d20", command=lambda: self.quick_roll(20)).pack(
            side=tk.RIGHT
        )

        status_row = ttk.Frame(self.root)
        status_row.pack(fill=tk.X, padx=15, pady=(0, 8))
        self.ask_status_var = tk.StringVar(value="")
        self.ask_status = ttk.Label(
            status_row,
            textvariable=self.ask_status_var,
            foreground="#4a5568",
        )
        self.ask_status.pack(side=tk.LEFT)

        self.reply_frame = ttk.LabelFrame(
            self.root,
            text="GM reply debug (raw model output)",
        )
        self.reply_box = tk.Text(self.reply_frame, height=8, font=("Georgia", 11))
        self.reply_box.pack(fill=tk.X, expand=True, padx=10, pady=10)
        ttk.Button(
            self.reply_frame, text="Process", command=self.process_reply
        ).pack(side=tk.RIGHT, padx=10, pady=(0, 10))
        self._reply_debug_visible = False
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
        self.db.execute(
            "INSERT OR REPLACE INTO world (key, value) VALUES ('location', ?)",
            (self.location_var.get(),),
        )
        self.db.commit()

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
        self.settings_module.set_db(new_db)
        self.log_panel.set_db(new_db)
        self.refresh_campaign_chrome()

    def new_campaign(self):
        name = simpledialog.askstring("New Campaign", "Campaign name:", parent=self.root)
        if not name or not name.strip():
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        create_campaign(self.config_path, name.strip(), self.campaigns_dir)
        self._swap_db(active_path(load_config(self.config_path)))

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
        self.settings_module.set_db(self.db)
        self.log_panel.set_db(self.db)
        self.refresh_campaign_chrome()

    def open_campaign_file(self):
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
        register_file(self.config_path, chosen)
        self._swap_db(active_path(load_config(self.config_path)))

    def remove_current(self):
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

    def copy_prompt(self):
        action = self.action_entry.get().strip()
        if not action:
            return
        self.save_location()
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        system, user = build_messages(self.db, action)
        pyperclip.copy(format_clipboard(system, user))
        self.log_panel.add_entry("Player", action)
        self.action_entry.delete(0, tk.END)
        messagebox.showinfo("Copied", "Prompt copied to clipboard.")

    def apply_ui_preferences(self):
        """Show or hide debug-only controls from saved UI settings."""
        visible = show_gm_reply_debug(load_config(self.config_path))
        if visible and not self._reply_debug_visible:
            self.reply_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
            self._reply_debug_visible = True
        elif not visible and self._reply_debug_visible:
            self.reply_frame.pack_forget()
            self._reply_debug_visible = False

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
        else:
            self.ask_status_var.set("")
            self.ask_button.configure(text="Ask GM")
            self.ask_button.state(["!disabled"])
            self.copy_prompt_button.state(["!disabled"])
            self.action_entry.configure(state="normal")
            self.root.config(cursor="")

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
        system, user = build_messages(
            self.db,
            action,
            include_reference_material=False,
        )
        settings = load_config(self.config_path)["llm"]
        tool_db_path = self.db_path
        model_name = str(settings.get("model") or "").strip()
        turn_scratch = ScratchContext()
        self.log_panel.add_entry("Player", action)
        self.action_entry.delete(0, tk.END)
        self._set_ask_busy(True, model=model_name)
        self.log_panel.add_entry(
            "System",
            f"Asking GM{' (' + model_name + ')' if model_name else ''}…",
        )

        def work():
            try:
                reply = complete(
                    settings,
                    system,
                    user,
                    tool_db_path=tool_db_path,
                    scratch=turn_scratch,
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
            self.reply_box.delete("1.0", tk.END)
            self.reply_box.insert("1.0", reply)
        self._apply_reply_text(reply, scratch=scratch)

    def process_reply(self):
        if not self._reply_debug_visible:
            return
        reply = self.reply_box.get("1.0", tk.END).strip()
        if not reply:
            return
        self.world_module.save_prompt(quiet=True)
        self.documents_module.save_current(quiet=True)
        self.settings_module.save_system_prompt(quiet=True)
        self.reply_box.delete("1.0", tk.END)
        self._apply_reply_text(reply, scratch=None)

    def _apply_reply_text(self, reply: str, *, scratch=None):
        parsed = parse_reply(reply)
        self.log_panel.add_entry("GM", parsed.narration or reply)
        for line in citation_messages(scratch, parsed.cited_record_ids):
            self.log_panel.add_entry("System", line)
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

    def quick_roll(self, sides):
        import random
        roll = random.randint(1, sides)
        self.action_entry.insert(0, f"[d{sides}: {roll}] ")
        self.log_panel.add_entry("System", f"Rolled d{sides}: {roll}")

    def get_meta(self, key, default):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

"""Table session façade: host/join role, chat, refresh subscriptions.

- **solo** — single-table desk; table authority stays in this process (no
  separate service).
- **host** — in-process HTTP table (legacy / embedded).
- **client** — desk joined to a table service (spawned locally or remote).

Campaign changes follow server policy (unbound until a GM login binds one).
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from sakaki_gm.net.client import TableClient, TableClientError
from sakaki_gm.net.protocol import DEFAULT_PORT
from sakaki_gm.net.server import TableServer, TableServerState
from sakaki_gm.net.service import spawn_headless_server, terminate_service


class TableSession:
    def __init__(
        self,
        *,
        mode: str,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str | Path,
        secret: str = "",
        bind_host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        remote_url: str | None = None,
        display_name: str = "Player",
        join_role: str = "player",
        join_campaign: dict[str, Any] | None = None,
        service_process: subprocess.Popen | None = None,
    ):
        self.mode = mode  # "host" | "client" | "solo"
        self.campaign_id = campaign_id
        self.campaign_name = campaign_name
        raw_path = str(campaign_path or "").strip()
        self.campaign_path = (
            str(Path(raw_path).resolve()) if raw_path else ""
        )
        self.secret = str(secret or "")
        self.display_name = display_name
        self.bind_host = bind_host
        self.port = int(port)
        self.remote_url = remote_url
        self.join_role = str(join_role or "player").strip().lower() or "player"
        self.join_campaign = join_campaign
        self.session_id: str | None = None
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.RLock()
        self._server: TableServer | None = None
        self._state: TableServerState | None = None
        self._client: TableClient | None = None
        self._service_process = service_process
        self._last_chat_id = 0
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None

    @classmethod
    def solo(
        cls,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str | Path,
    ) -> "TableSession":
        """Single-table play: authority stays in this process (no service)."""
        return cls(
            mode="solo",
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            campaign_path=campaign_path,
        )

    @classmethod
    def start_local_service_client(
        cls,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str | Path,
        secret: str = "",
        port: int = DEFAULT_PORT,
        display_name: str = "GM",
    ) -> "TableSession":
        """Spawn headless ``--server`` (unbound) and join as a client.

        The desk is a normal network client. Binding the campaign selected in
        the lobby uses a GM join for that bind step only — Login is a separate
        client path and is not GM-only.
        """
        path = Path(campaign_path)
        proc = spawn_headless_server(
            port=port,
            bind="127.0.0.1",
            secret=secret,
            campaign="",  # unbound until this GM login
        )
        remote = f"http://127.0.0.1:{int(port)}"
        session = cls(
            mode="client",
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            campaign_path=path,
            secret=secret,
            port=port,
            remote_url=remote,
            display_name=display_name,
            join_role="gm",
            join_campaign={
                "id": campaign_id,
                "name": campaign_name,
                "path": str(path.resolve()),
            },
            service_process=proc,
        )
        try:
            session.start()
        except Exception:
            terminate_service(proc)
            raise
        return session

    def start(self) -> None:
        if self.mode == "host":
            self._state = TableServerState(
                campaign_id=self.campaign_id,
                campaign_name=self.campaign_name,
                campaign_path=self.campaign_path,
                secret=self.secret,
            )
            self.session_id = self._state.host_session_id
            self._state.on_event = self._dispatch
            self._server = TableServer(
                self._state, host=self.bind_host, port=self.port
            )
            _host, self.port = self._server.start()
        elif self.mode == "client":
            if not self.remote_url:
                raise ValueError("Client session needs remote_url.")
            self._client = TableClient(self.remote_url, secret=self.secret)
            joined = self._client.join(
                self.display_name,
                role=self.join_role,
                campaign=self.join_campaign,
            )
            self.session_id = str(joined.get("session_id") or "")
            campaign = joined.get("campaign") or {}
            if campaign.get("bound"):
                self.campaign_id = str(campaign.get("id") or self.campaign_id)
                self.campaign_name = str(
                    campaign.get("name") or self.campaign_name
                )
                if campaign.get("path"):
                    self.campaign_path = str(campaign["path"])
            self._start_poll()
        # solo: local-only authority in this process (no HTTP service)

    def stop(self) -> None:
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=1.5)
            self._poll_thread = None
        try:
            self.release_presence()
        except Exception:
            pass
        if self._client is not None:
            try:
                self._client.leave()
            except TableClientError:
                pass
            self._client = None
        if self._server is not None:
            if self.session_id and self._state is not None:
                try:
                    self._state.release_occupancy(self.session_id)
                except Exception:
                    pass
            self._server.stop()
            self._server = None
        self._state = None
        if self._service_process is not None:
            terminate_service(self._service_process)
            self._service_process = None

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def _dispatch(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                pass

    def endpoint(self) -> str:
        if self.mode == "host":
            # Prefer localhost for display; bind may be 0.0.0.0.
            return f"http://127.0.0.1:{self.port}"
        if self.mode == "client":
            return self.remote_url or ""
        return ""

    def peer_count(self) -> int:
        """Desk peers at the table (excludes the synthetic headless host)."""
        if self._state is not None:
            return self._state.desk_peer_count()
        if self._client is not None:
            try:
                return int(self._client.campaign().get("peer_count") or 0)
            except TableClientError:
                return 0
        return 0

    def can_change_campaign(self) -> bool:
        """GM may set campaign when alone (or table still unbound)."""
        if self.mode == "solo":
            return True
        if self.mode == "host":
            if self._state is None:
                return False
            return self._state.can_change_campaign(self.session_id)
        if self.mode == "client":
            # Client GM can bind/switch when the table allows this session.
            if self._state is not None:
                return self._state.can_change_campaign(self.session_id)
            return False
        return False

    def campaign_change_block_reason(self) -> str | None:
        if self.mode == "client":
            if self._client is not None:
                # Remote table: follow server policy; local reason is generic.
                return (
                    "Joined tables follow the table campaign; a GM may bind or "
                    "switch only when the server allows it (usually when alone)."
                )
            return "Joined tables use the host's campaign; only the host GM can switch."
        if self.mode == "host" and self._state is not None:
            if not self._state.can_change_campaign(self.session_id):
                others = self._state.other_peer_count()
                return (
                    f"Cannot change campaign while {others} other "
                    f"player{'s' if others != 1 else ''} still have a session."
                )
        return None

    def set_campaign(
        self,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str | Path,
    ) -> None:
        raw = str(campaign_path or "").strip()
        path = str(Path(raw).resolve()) if raw else ""
        if self.mode == "solo":
            self.campaign_id = campaign_id
            self.campaign_name = campaign_name
            self.campaign_path = path
            self._dispatch(
                {
                    "type": "refresh",
                    "reason": "campaign_changed",
                }
            )
            return
        if self.mode == "client" and self._client is not None:
            info = self._client.set_campaign(
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                campaign_path=path,
            )
            self.campaign_id = str(info.get("id") or campaign_id)
            self.campaign_name = str(info.get("name") or campaign_name)
            self.campaign_path = str(info.get("path") or path)
            return
        if self.mode != "host" or self._state is None:
            raise PermissionError(self.campaign_change_block_reason() or "Not host.")
        info = self._state.set_campaign(
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            campaign_path=path,
            session_id=self.session_id,
        )
        self.campaign_id = info["id"]
        self.campaign_name = info["name"]
        self.campaign_path = info["path"]

    def post_chat(self, speaker: str, text: str) -> dict[str, Any]:
        if self._state is not None:
            return self._state.post_chat(
                speaker=speaker, text=text, session_id=self.session_id
            )
        if self._client is not None:
            return self._client.post_chat(speaker, text)
        # Solo: local echo only.
        entry = {"id": 0, "speaker": speaker, "text": text, "session_id": None}
        self._dispatch({"type": "chat", "message": entry})
        return entry

    def notify_state_changed(self, reason: str = "state_changed") -> None:
        if self._state is not None:
            self._state.bump_revision(reason)
        elif self._client is not None:
            try:
                self._client.notify(reason)
            except TableClientError:
                pass
        else:
            self._dispatch({"type": "refresh", "reason": reason})

    def claim_presence(
        self,
        *,
        desk_role: str,
        character_id: int | None = None,
        seat_id: int | None = None,
        display_name: str = "",
    ) -> None:
        """Claim exclusive live presence for a PC on the table (no-op when solo)."""
        if self.mode == "solo":
            return
        if self._state is not None and self.session_id:
            self._state.claim_occupancy(
                session_id=self.session_id,
                character_id=character_id if desk_role == "player" else None,
                desk_role=desk_role,
                seat_id=seat_id,
                display_name=display_name or self.display_name,
            )
            return
        if self._client is not None:
            self._client.claim_occupancy(
                character_id=character_id if desk_role == "player" else None,
                desk_role=desk_role,
                seat_id=seat_id,
                display_name=display_name or self.display_name,
            )

    def release_presence(self) -> None:
        if self.mode == "solo":
            return
        if self._state is not None and self.session_id:
            self._state.release_occupancy(self.session_id)
            return
        if self._client is not None:
            try:
                self._client.release_occupancy()
            except TableClientError:
                pass

    def occupied_character_ids(self) -> set[int]:
        """Character ids currently seated at another desk on this table."""
        if self._state is not None:
            return self._state.occupied_character_ids(
                except_session_id=self.session_id
            )
        if self._client is not None:
            try:
                rows = self._client.occupancy()
            except TableClientError:
                return set()
            occupied = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("session_id") == self.session_id:
                    continue
                if row.get("character_id") is not None:
                    occupied.add(int(row["character_id"]))
            return occupied
        return set()

    def _start_poll(self) -> None:
        self._poll_stop.clear()

        def loop():
            while not self._poll_stop.wait(1.5):
                if self._client is None:
                    continue
                try:
                    messages = self._client.chat_since(self._last_chat_id)
                    for message in messages:
                        self._last_chat_id = max(
                            self._last_chat_id, int(message.get("id") or 0)
                        )
                        self._dispatch({"type": "chat", "message": message})
                    campaign = self._client.campaign()
                    # Surface campaign switches / peer churn as desk refresh.
                    self._dispatch(
                        {
                            "type": "refresh",
                            "reason": "poll",
                            "campaign": campaign,
                            "revision": campaign.get("revision"),
                        }
                    )
                except TableClientError:
                    continue

        self._poll_thread = threading.Thread(target=loop, daemon=True)
        self._poll_thread.start()

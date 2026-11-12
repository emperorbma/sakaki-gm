"""Table session façade: host/join role, chat, refresh subscriptions.

Campaign changes are allowed for the host GM only when no other peer still
holds a session at the table.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from sakaki_gm.net.client import TableClient, TableClientError
from sakaki_gm.net.protocol import DEFAULT_PORT
from sakaki_gm.net.server import TableServer, TableServerState


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
    ):
        self.mode = mode  # "host" | "client" | "solo"
        self.campaign_id = campaign_id
        self.campaign_name = campaign_name
        self.campaign_path = str(Path(campaign_path).resolve())
        self.secret = str(secret or "")
        self.display_name = display_name
        self.bind_host = bind_host
        self.port = int(port)
        self.remote_url = remote_url
        self.session_id: str | None = None
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.RLock()
        self._server: TableServer | None = None
        self._state: TableServerState | None = None
        self._client: TableClient | None = None
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
        return cls(
            mode="solo",
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            campaign_path=campaign_path,
        )

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
            joined = self._client.join(self.display_name)
            self.session_id = str(joined.get("session_id") or "")
            campaign = joined.get("campaign") or {}
            self.campaign_id = str(campaign.get("id") or self.campaign_id)
            self.campaign_name = str(campaign.get("name") or self.campaign_name)
            self._start_poll()
        # solo: local-only, no network

    def stop(self) -> None:
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=1.5)
            self._poll_thread = None
        if self._client is not None:
            try:
                self._client.leave()
            except TableClientError:
                pass
            self._client = None
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._state = None

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
        if self._state is not None:
            return self._state.peer_count()
        if self._client is not None:
            try:
                return int(self._client.campaign().get("peer_count") or 1)
            except TableClientError:
                return 1
        return 1

    def can_change_campaign(self) -> bool:
        """Host GM only, and only when no other sessions are connected."""
        if self.mode == "solo":
            return True
        if self.mode != "host":
            return False
        if self._state is None:
            return False
        return self._state.can_change_campaign()

    def campaign_change_block_reason(self) -> str | None:
        if self.mode == "client":
            return "Joined tables use the host's campaign; only the host GM can switch."
        if self.mode == "host" and self._state is not None:
            if not self._state.can_change_campaign():
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
        path = str(Path(campaign_path).resolve())
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

"""Campaign table HTTP server (host stub).

Holds peer sessions, optional shared secret, in-memory chat, and campaign
metadata. Changing the hosted campaign is refused while other peers are
connected — only the host GM may switch when alone at the table.
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from sakaki_gm.net.protocol import DEFAULT_PORT, err, ok


class TableServerState:
    def __init__(
        self,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str,
        secret: str = "",
        host_session_id: str | None = None,
    ):
        self.lock = threading.RLock()
        self.campaign_id = campaign_id
        self.campaign_name = campaign_name
        self.campaign_path = campaign_path
        self.secret = str(secret or "")
        self.host_session_id = host_session_id or uuid.uuid4().hex
        self.sessions: dict[str, dict[str, Any]] = {
            self.host_session_id: {
                "id": self.host_session_id,
                "role": "host",
                "display_name": "Host",
            }
        }
        self.chat: list[dict[str, Any]] = []
        self._chat_seq = 0
        self.revision = 1
        self.on_event: Optional[Callable[[dict[str, Any]], None]] = None

    def peer_count(self) -> int:
        with self.lock:
            return len(self.sessions)

    def other_peer_count(self) -> int:
        """Peers besides the host session."""
        with self.lock:
            return max(0, len(self.sessions) - 1)

    def can_change_campaign(self) -> bool:
        """GM may change campaign only when no one else has a session."""
        return self.other_peer_count() == 0

    def authorize(self, provided_secret: str | None) -> bool:
        if not self.secret:
            return True
        return str(provided_secret or "") == self.secret

    def join(
        self, *, display_name: str = "Player", secret: str | None = None
    ) -> dict[str, Any]:
        if not self.authorize(secret):
            raise PermissionError("Incorrect table secret.")
        session_id = uuid.uuid4().hex
        with self.lock:
            self.sessions[session_id] = {
                "id": session_id,
                "role": "client",
                "display_name": str(display_name or "Player").strip() or "Player",
            }
            self.revision += 1
        self._emit({"type": "refresh", "reason": "peer_joined", "revision": self.revision})
        return {
            "session_id": session_id,
            "campaign": self.campaign_info(),
            "revision": self.revision,
        }

    def leave(self, session_id: str) -> None:
        with self.lock:
            if session_id == self.host_session_id:
                return
            self.sessions.pop(session_id, None)
            self.revision += 1
        self._emit({"type": "refresh", "reason": "peer_left", "revision": self.revision})

    def campaign_info(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.campaign_id,
                "name": self.campaign_name,
                "path": self.campaign_path,
                "peer_count": len(self.sessions),
                "can_change_campaign": self.can_change_campaign(),
                "revision": self.revision,
            }

    def set_campaign(
        self,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if session_id and session_id != self.host_session_id:
                raise PermissionError("Only the host GM can change the campaign.")
            if not self.can_change_campaign():
                raise PermissionError(
                    "Cannot change campaign while other players have a session. "
                    "Wait until you are alone at the table."
                )
            self.campaign_id = campaign_id
            self.campaign_name = campaign_name
            self.campaign_path = campaign_path
            self.revision += 1
            info = self.campaign_info()
        self._emit({"type": "refresh", "reason": "campaign_changed", "revision": self.revision})
        return info

    def post_chat(
        self,
        *,
        speaker: str,
        text: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("Empty chat message.")
        with self.lock:
            self._chat_seq += 1
            entry = {
                "id": self._chat_seq,
                "speaker": str(speaker or "Peer").strip() or "Peer",
                "text": text,
                "session_id": session_id,
            }
            self.chat.append(entry)
            self.chat = self.chat[-200:]
            self.revision += 1
        self._emit({"type": "chat", "message": entry, "revision": self.revision})
        return entry

    def chat_since(self, since_id: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            return [item for item in self.chat if int(item["id"]) > int(since_id or 0)]

    def bump_revision(self, reason: str = "state_changed") -> int:
        with self.lock:
            self.revision += 1
            revision = self.revision
        self._emit({"type": "refresh", "reason": reason, "revision": revision})
        return revision

    def _emit(self, event: dict[str, Any]) -> None:
        callback = self.on_event
        if callback is not None:
            try:
                callback(event)
            except Exception:
                pass


def make_handler(state: TableServerState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A003
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _secret(self, data: dict[str, Any] | None = None) -> str | None:
            header = self.headers.get("X-Sakaki-Secret")
            if header is not None:
                return header
            if data and "secret" in data:
                return str(data.get("secret") or "")
            return None

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if path == "/health":
                self._send(200, ok(service="sakaki-table"))
                return
            if path == "/campaign":
                if not state.authorize(self._secret()):
                    self._send(401, err("Incorrect table secret."))
                    return
                self._send(200, ok(campaign=state.campaign_info()))
                return
            if path == "/chat":
                if not state.authorize(self._secret()):
                    self._send(401, err("Incorrect table secret."))
                    return
                since = 0
                if "since" in query:
                    try:
                        since = int(query["since"][0])
                    except (TypeError, ValueError, IndexError):
                        since = 0
                self._send(200, ok(messages=state.chat_since(since)))
                return
            if path == "/sessions":
                if not state.authorize(self._secret()):
                    self._send(401, err("Incorrect table secret."))
                    return
                with state.lock:
                    peers = list(state.sessions.values())
                self._send(200, ok(sessions=peers, campaign=state.campaign_info()))
                return
            self._send(404, err("Not found."))

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            data = self._read_json()
            try:
                if path == "/join":
                    result = state.join(
                        display_name=str(data.get("display_name") or "Player"),
                        secret=self._secret(data),
                    )
                    self._send(200, ok(**result))
                    return
                if path == "/leave":
                    state.leave(str(data.get("session_id") or ""))
                    self._send(200, ok())
                    return
                if path == "/chat":
                    if not state.authorize(self._secret(data)):
                        self._send(401, err("Incorrect table secret."))
                        return
                    entry = state.post_chat(
                        speaker=str(data.get("speaker") or "Peer"),
                        text=str(data.get("text") or ""),
                        session_id=str(data.get("session_id") or "") or None,
                    )
                    self._send(200, ok(message=entry))
                    return
                if path == "/campaign":
                    if not state.authorize(self._secret(data)):
                        self._send(401, err("Incorrect table secret."))
                        return
                    info = state.set_campaign(
                        campaign_id=str(data.get("id") or ""),
                        campaign_name=str(data.get("name") or ""),
                        campaign_path=str(data.get("path") or ""),
                        session_id=str(data.get("session_id") or "") or None,
                    )
                    self._send(200, ok(campaign=info))
                    return
                if path == "/notify":
                    if not state.authorize(self._secret(data)):
                        self._send(401, err("Incorrect table secret."))
                        return
                    revision = state.bump_revision(
                        str(data.get("reason") or "state_changed")
                    )
                    self._send(200, ok(revision=revision))
                    return
            except PermissionError as exc:
                self._send(403, err(str(exc)))
                return
            except ValueError as exc:
                self._send(400, err(str(exc)))
                return
            self._send(404, err("Not found."))

    return Handler


class TableServer:
    def __init__(
        self,
        state: TableServerState,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
    ):
        self.state = state
        self.host = host
        self.port = int(port)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> tuple[str, int]:
        handler = make_handler(self.state)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        # If port 0 was requested, pick an ephemeral port.
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.host, self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

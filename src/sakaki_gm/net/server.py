"""Campaign table HTTP server (host stub).

Holds peer sessions, optional shared secret, in-memory chat, and campaign
metadata. A headless process may start **unbound**; a GM login supplies the
campaign. Changing a bound campaign is refused while other client peers are
still connected.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from sakaki_gm.net.protocol import DEFAULT_PORT, err, ok

logger = logging.getLogger("sakaki_gm.table")

SEAT_ROLES = ("gm", "player")


class TableServerState:
    def __init__(
        self,
        *,
        campaign_id: str = "",
        campaign_name: str = "",
        campaign_path: str = "",
        secret: str = "",
        host_session_id: str | None = None,
    ):
        self.lock = threading.RLock()
        self.campaign_id = str(campaign_id or "")
        self.campaign_name = str(campaign_name or "") or (
            "(unbound)" if not campaign_path else ""
        )
        self.campaign_path = str(campaign_path or "")
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
        # character_id -> {session_id, seat_id?, desk_role, display_name}
        self.occupancy: dict[int, dict[str, Any]] = {}
        self.revision = 1
        self.on_event: Optional[Callable[[dict[str, Any]], None]] = None

    def is_bound(self) -> bool:
        return bool(str(self.campaign_path or "").strip())

    def peer_count(self) -> int:
        """All sessions including the synthetic table-host process entry."""
        with self.lock:
            return len(self.sessions)

    def desk_peer_count(self) -> int:
        """Joined desks only — excludes the synthetic host process session.

        The headless ``--server`` process always holds a non-seat ``host``
        session so campaign-lock math can ignore it. GM and Player joins are
        the desks humans sit at; those are what the UI should count.
        """
        with self.lock:
            return sum(
                1 for sid in self.sessions if sid != self.host_session_id
            )

    def other_peer_count(self) -> int:
        """Peers besides the host session (same as desk_peer_count)."""
        return self.desk_peer_count()

    def can_change_campaign(self, session_id: str | None = None) -> bool:
        """Whether ``session_id`` (or the process host) may set the campaign.

        Unbound tables are always changeable by an authorized GM. Bound tables
        require no *other* client sessions (requester may already be seated).
        """
        with self.lock:
            if not self.is_bound():
                return True
            blocking = 0
            for sid in self.sessions:
                if sid == self.host_session_id:
                    continue
                if session_id and sid == session_id:
                    continue
                blocking += 1
            return blocking == 0

    def authorize(self, provided_secret: str | None) -> bool:
        if not self.secret:
            return True
        return str(provided_secret or "") == self.secret

    def join(
        self,
        *,
        display_name: str = "Player",
        secret: str | None = None,
        role: str = "player",
        campaign: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.authorize(secret):
            logger.warning("join rejected: bad table secret (%s)", display_name)
            raise PermissionError("Incorrect table secret.")
        seat_role = str(role or "player").strip().lower()
        if seat_role not in SEAT_ROLES:
            seat_role = "player"
        name = str(display_name or "Player").strip() or "Player"
        session_id = uuid.uuid4().hex
        bound_now = False
        with self.lock:
            self.sessions[session_id] = {
                "id": session_id,
                "role": seat_role,
                "display_name": name,
            }
            if (
                seat_role == "gm"
                and isinstance(campaign, dict)
                and (
                    not self.is_bound()
                    or self.can_change_campaign(session_id)
                )
            ):
                path = str(campaign.get("path") or "").strip()
                if path:
                    self.campaign_id = str(campaign.get("id") or "")
                    self.campaign_name = (
                        str(campaign.get("name") or "").strip() or path
                    )
                    self.campaign_path = path
                    bound_now = True
                    logger.info(
                        "campaign bound by GM login %s: %s (%s)",
                        name,
                        self.campaign_name,
                        self.campaign_path,
                    )
            self.revision += 1
            revision = self.revision
            info = self.campaign_info()
        logger.info(
            "peer joined: %s role=%s session=%s bound=%s peers=%s",
            name,
            seat_role,
            session_id[:8],
            self.is_bound(),
            self.other_peer_count(),
        )
        self._emit(
            {
                "type": "refresh",
                "reason": "campaign_changed" if bound_now else "peer_joined",
                "revision": revision,
            }
        )
        return {
            "session_id": session_id,
            "role": seat_role,
            "campaign": info,
            "revision": revision,
        }

    def leave(self, session_id: str) -> None:
        with self.lock:
            if session_id == self.host_session_id:
                # Host stays; still drop any character occupancy for this session.
                self._release_occupancy_locked(session_id)
                return
            left = self.sessions.pop(session_id, None)
            self._release_occupancy_locked(session_id)
            self.revision += 1
            revision = self.revision
        if left:
            logger.info(
                "peer left: %s role=%s session=%s peers=%s",
                left.get("display_name") or "?",
                left.get("role") or "?",
                session_id[:8],
                self.other_peer_count(),
            )
        self._emit({"type": "refresh", "reason": "peer_left", "revision": revision})

    def claim_occupancy(
        self,
        *,
        session_id: str,
        character_id: int | None,
        desk_role: str = "player",
        seat_id: int | None = None,
        display_name: str = "",
    ) -> dict[str, Any]:
        """Exclusive live claim on a PC for this table session."""
        with self.lock:
            if session_id not in self.sessions:
                raise PermissionError("Unknown table session.")
            self._release_occupancy_locked(session_id)
            if character_id is None or desk_role != "player":
                self.revision += 1
                info = self._occupancy_info_locked()
            else:
                character_id = int(character_id)
                held = self.occupancy.get(character_id)
                if held is not None and held.get("session_id") != session_id:
                    raise PermissionError(
                        "That character is already seated at another desk."
                    )
                self.occupancy[character_id] = {
                    "session_id": session_id,
                    "character_id": character_id,
                    "seat_id": seat_id,
                    "desk_role": "player",
                    "display_name": str(display_name or "").strip(),
                }
                self.revision += 1
                info = self._occupancy_info_locked()
        self._emit(
            {
                "type": "refresh",
                "reason": "occupancy_changed",
                "revision": self.revision,
                "occupancy": info,
            }
        )
        return {"occupancy": info, "revision": self.revision}

    def release_occupancy(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            self._release_occupancy_locked(session_id)
            self.revision += 1
            info = self._occupancy_info_locked()
        self._emit(
            {
                "type": "refresh",
                "reason": "occupancy_changed",
                "revision": self.revision,
                "occupancy": info,
            }
        )
        return {"occupancy": info, "revision": self.revision}

    def occupied_character_ids(self, *, except_session_id: str | None = None) -> set[int]:
        with self.lock:
            occupied = set()
            for character_id, held in self.occupancy.items():
                if except_session_id and held.get("session_id") == except_session_id:
                    continue
                occupied.add(int(character_id))
            return occupied

    def _release_occupancy_locked(self, session_id: str) -> None:
        drop = [
            character_id
            for character_id, held in self.occupancy.items()
            if held.get("session_id") == session_id
        ]
        for character_id in drop:
            self.occupancy.pop(character_id, None)

    def _occupancy_info_locked(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.occupancy.values()]

    def campaign_info(self) -> dict[str, Any]:
        with self.lock:
            desks = sum(
                1 for sid in self.sessions if sid != self.host_session_id
            )
            return {
                "id": self.campaign_id,
                "name": self.campaign_name or ("(unbound)" if not self.is_bound() else ""),
                "path": self.campaign_path,
                "bound": self.is_bound(),
                "peer_count": desks,
                "can_change_campaign": self.can_change_campaign(),
                "revision": self.revision,
                "occupancy": self._occupancy_info_locked(),
            }

    def _session_may_set_campaign(self, session_id: str | None) -> bool:
        if session_id is None or session_id == self.host_session_id:
            return True
        row = self.sessions.get(session_id)
        if row is None:
            return False
        return str(row.get("role") or "") in {"gm", "host"}

    def set_campaign(
        self,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if not self._session_may_set_campaign(session_id):
                raise PermissionError(
                    "Only a GM seat (or the table host) can set the campaign."
                )
            if not self.can_change_campaign(session_id):
                raise PermissionError(
                    "Cannot change campaign while other players have a session. "
                    "Wait until you are alone at the table."
                )
            self.campaign_id = str(campaign_id or "")
            self.campaign_name = str(campaign_name or "").strip() or str(
                campaign_path or ""
            )
            self.campaign_path = str(campaign_path or "")
            self.revision += 1
            desks = sum(
                1 for sid in self.sessions if sid != self.host_session_id
            )
            info = {
                "id": self.campaign_id,
                "name": self.campaign_name or ("(unbound)" if not self.is_bound() else ""),
                "path": self.campaign_path,
                "bound": self.is_bound(),
                "peer_count": desks,
                "can_change_campaign": self.can_change_campaign(session_id),
                "revision": self.revision,
                "occupancy": self._occupancy_info_locked(),
            }
            revision = self.revision
        logger.info(
            "campaign set: %s (%s) bound=%s by session=%s",
            info["name"],
            info["path"] or "—",
            info["bound"],
            (session_id or self.host_session_id)[:8],
        )
        self._emit(
            {"type": "refresh", "reason": "campaign_changed", "revision": revision}
        )
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
            # Access lines at DEBUG; join/campaign/occupancy use the table logger.
            logger.debug("http %s - %s", self.address_string(), format % args)

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
            if path == "/occupancy":
                if not state.authorize(self._secret()):
                    self._send(401, err("Incorrect table secret."))
                    return
                with state.lock:
                    info = state._occupancy_info_locked()
                self._send(200, ok(occupancy=info, campaign=state.campaign_info()))
                return
            self._send(404, err("Not found."))

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            data = self._read_json()
            try:
                if path == "/join":
                    campaign = data.get("campaign")
                    if campaign is not None and not isinstance(campaign, dict):
                        campaign = None
                    result = state.join(
                        display_name=str(data.get("display_name") or "Player"),
                        secret=self._secret(data),
                        role=str(data.get("role") or "player"),
                        campaign=campaign,
                    )
                    self._send(200, ok(**result))
                    return
                if path == "/leave":
                    state.leave(str(data.get("session_id") or ""))
                    self._send(200, ok())
                    return
                if path == "/chat":
                    if not state.authorize(self._secret(data)):
                        logger.warning("chat rejected: bad table secret")
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
                        logger.warning("campaign set rejected: bad table secret")
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
                if path == "/occupancy/claim":
                    if not state.authorize(self._secret(data)):
                        self._send(401, err("Incorrect table secret."))
                        return
                    character_raw = data.get("character_id")
                    character_id = (
                        int(character_raw)
                        if character_raw is not None and str(character_raw).strip() != ""
                        else None
                    )
                    seat_raw = data.get("seat_id")
                    seat_id = (
                        int(seat_raw)
                        if seat_raw is not None and str(seat_raw).strip() != ""
                        else None
                    )
                    result = state.claim_occupancy(
                        session_id=str(data.get("session_id") or ""),
                        character_id=character_id,
                        desk_role=str(data.get("desk_role") or "player"),
                        seat_id=seat_id,
                        display_name=str(data.get("display_name") or ""),
                    )
                    logger.info(
                        "occupancy claim: character=%s role=%s session=%s",
                        character_id,
                        data.get("desk_role") or "player",
                        str(data.get("session_id") or "")[:8],
                    )
                    self._send(200, ok(**result))
                    return
                if path == "/occupancy/release":
                    if not state.authorize(self._secret(data)):
                        self._send(401, err("Incorrect table secret."))
                        return
                    result = state.release_occupancy(
                        str(data.get("session_id") or "")
                    )
                    logger.info(
                        "occupancy release: session=%s",
                        str(data.get("session_id") or "")[:8],
                    )
                    self._send(200, ok(**result))
                    return
            except PermissionError as exc:
                logger.warning("forbidden: %s", exc)
                self._send(403, err(str(exc)))
                return
            except ValueError as exc:
                logger.warning("bad request: %s", exc)
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

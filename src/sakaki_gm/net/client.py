"""HTTP client for joining a hosted campaign table."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode


class TableClientError(RuntimeError):
    pass


class TableClient:
    def __init__(self, base_url: str, *, secret: str = ""):
        self.base_url = base_url.rstrip("/")
        self.secret = str(secret or "")
        self.session_id: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = None
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["X-Sakaki-Secret"] = self.secret
        if payload is not None:
            body = dict(payload)
            if self.secret and "secret" not in body:
                body["secret"] = self.secret
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                message = parsed.get("error") or detail
            except json.JSONDecodeError:
                message = detail or str(exc)
            raise TableClientError(message) from exc
        except urllib.error.URLError as exc:
            raise TableClientError(str(exc.reason or exc)) from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise TableClientError("Invalid JSON from table server.") from exc
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            raise TableClientError(
                str((parsed or {}).get("error") or "Table request failed.")
            )
        return parsed

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def campaign(self) -> dict[str, Any]:
        return self._request("GET", "/campaign")["campaign"]

    def join(
        self,
        display_name: str = "Player",
        *,
        role: str = "player",
        campaign: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "display_name": display_name,
            "role": role,
        }
        if campaign is not None:
            payload["campaign"] = campaign
        result = self._request("POST", "/join", payload=payload)
        self.session_id = str(result.get("session_id") or "")
        return result

    def set_campaign(
        self,
        *,
        campaign_id: str,
        campaign_name: str,
        campaign_path: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/campaign",
            payload={
                "id": campaign_id,
                "name": campaign_name,
                "path": campaign_path,
                "session_id": self.session_id,
            },
        )["campaign"]

    def leave(self) -> None:
        if not self.session_id:
            return
        try:
            self._request(
                "POST", "/leave", payload={"session_id": self.session_id}
            )
        finally:
            self.session_id = None

    def post_chat(self, speaker: str, text: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/chat",
            payload={
                "speaker": speaker,
                "text": text,
                "session_id": self.session_id,
            },
        )["message"]

    def chat_since(self, since_id: int = 0) -> list[dict[str, Any]]:
        return self._request(
            "GET", "/chat", query={"since": int(since_id)}
        ).get("messages", [])

    def notify(self, reason: str = "state_changed") -> int:
        return int(
            self._request(
                "POST",
                "/notify",
                payload={"reason": reason, "session_id": self.session_id},
            ).get("revision")
            or 0
        )

    def claim_occupancy(
        self,
        *,
        character_id: int | None,
        desk_role: str = "player",
        seat_id: int | None = None,
        display_name: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/occupancy/claim",
            payload={
                "session_id": self.session_id,
                "character_id": character_id,
                "desk_role": desk_role,
                "seat_id": seat_id,
                "display_name": display_name,
            },
        )

    def release_occupancy(self) -> dict[str, Any]:
        return self._request(
            "POST",
            "/occupancy/release",
            payload={"session_id": self.session_id},
        )

    def occupancy(self) -> list[dict[str, Any]]:
        return list(
            self._request("GET", "/occupancy").get("occupancy") or []
        )

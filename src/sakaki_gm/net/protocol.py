"""JSON envelopes for the campaign table server stub."""

from __future__ import annotations

from typing import Any

DEFAULT_PORT = 8765


def ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def err(message: str, **payload: Any) -> dict[str, Any]:
    return {"ok": False, "error": str(message), **payload}

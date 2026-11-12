"""Desk audience: active-seat lens (GM vs player) visibility.

Surfaces are either ``gm_only`` (hidden for the player seat) or ``shared``
(visible in both; modules may strip controls when the active seat is player).

``desk_mode`` remains the config key; it is the active seat *role* on a solo
two-seat table (GM + player).
"""

from __future__ import annotations

DESK_MODES = ("gm", "player")
# Alias: desk mode values are seat roles on the implicit two-seat table.
SEAT_ROLES = DESK_MODES

AUDIENCE_GM_ONLY = "gm_only"
AUDIENCE_SHARED = "shared"

# Notebook / chrome keys used by MainWindow.apply_desk_audience.
DESK_SURFACES = {
    # Shared play surfaces (may strip GM tooling inside the module).
    "characters": AUDIENCE_SHARED,
    "world": AUDIENCE_SHARED,
    "quests": AUDIENCE_SHARED,
    "log": AUDIENCE_SHARED,
    "ask_gm": AUDIENCE_SHARED,
    "location": AUDIENCE_SHARED,
    # GM-only design / catalog / cheat surfaces.
    "documents": AUDIENCE_GM_ONLY,
    "geography": AUDIENCE_GM_ONLY,
    "items": AUDIENCE_GM_ONLY,
    "actors": AUDIENCE_GM_ONLY,
    "skills": AUDIENCE_GM_ONLY,
    "spells": AUDIENCE_GM_ONLY,
    "settings": AUDIENCE_GM_ONLY,
    "copy_prompt": AUDIENCE_GM_ONLY,
    "campaign_admin": AUDIENCE_GM_ONLY,
    "gm_reply_debug": AUDIENCE_GM_ONLY,
}


def normalize_desk_mode(value) -> str:
    mode = str(value or "gm").strip().lower()
    if mode not in DESK_MODES:
        return "gm"
    return mode


def desk_mode_from_config(cfg: dict | None) -> str:
    if not isinstance(cfg, dict):
        return "gm"
    ui = cfg.get("ui")
    if not isinstance(ui, dict):
        return "gm"
    return normalize_desk_mode(ui.get("desk_mode"))


def surface_visible(surface: str, desk_mode: str) -> bool:
    audience = DESK_SURFACES.get(surface, AUDIENCE_GM_ONLY)
    mode = normalize_desk_mode(desk_mode)
    if audience == AUDIENCE_SHARED:
        return True
    return mode == "gm"


def shared_is_stripped(desk_mode: str) -> bool:
    """True when shared modules should hide GM-only controls."""
    return normalize_desk_mode(desk_mode) == "player"


def active_seat_role(desk_mode: str) -> str:
    """Map desk toggle value to seat role (gm / player)."""
    return normalize_desk_mode(desk_mode)

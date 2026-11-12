"""Campaign-role string helpers.

Roles are free text (Protagonist, NPC, Ally, …). Matching must use whole
tokens so ``pc`` does not hit inside ``NPC``.
"""

from __future__ import annotations

import re

_ROLE_TOKEN_RE = re.compile(r"[a-z0-9]+")

PLAYERISH_ROLE_TOKENS = frozenset({"party", "protagonist", "player", "pc"})
ALLYISH_ROLE_TOKENS = frozenset({"ally", "party", "companion"})


def campaign_role_tokens(role: str | None) -> set[str]:
    return set(_ROLE_TOKEN_RE.findall(str(role or "").casefold()))


def role_has_any(role: str | None, tokens: set[str] | frozenset[str]) -> bool:
    return bool(campaign_role_tokens(role) & set(tokens))


def is_playerish_campaign_role(role: str | None) -> bool:
    return role_has_any(role, PLAYERISH_ROLE_TOKENS)


def is_allyish_campaign_role(role: str | None) -> bool:
    return role_has_any(role, ALLYISH_ROLE_TOKENS)

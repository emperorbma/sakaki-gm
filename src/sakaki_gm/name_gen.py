"""Lightweight given-name generation for scaffold spawners."""

from __future__ import annotations

import random

# Compact fantasy-leaning syllables (fine for Voryn cultists and similar).
_STARTS = (
    "Ae", "Al", "Ar", "Bel", "Bren", "Cael", "Cor", "Dar", "Dor", "El",
    "Fen", "Gar", "Hal", "Iri", "Kael", "Kor", "Lor", "Mal", "Mor", "Nar",
    "Or", "Pyr", "Quel", "Rav", "Sar", "Sel", "Tal", "Thal", "Tor", "Ul",
    "Val", "Vor", "Wil", "Xor", "Yel", "Zan",
)
_MIDDLES = (
    "a", "ae", "an", "ar", "e", "el", "en", "i", "ia", "il",
    "in", "o", "or", "u", "ul", "y",
)
_ENDS = (
    "a", "an", "ar", "as", "en", "er", "eth", "ia", "iel", "in",
    "is", "or", "os", "ra", "riel", "rin", "th", "us", "yn", "ys",
)


def generate_given_name(*, rng: random.Random | None = None) -> str:
    """Return a short procedural given name."""
    rng = rng or random
    parts = [_STARTS[rng.randrange(len(_STARTS))]]
    if rng.random() < 0.65:
        parts.append(_MIDDLES[rng.randrange(len(_MIDDLES))])
    parts.append(_ENDS[rng.randrange(len(_ENDS))])
    name = "".join(parts)
    # Avoid accidental awkward doubles from syllable joins.
    while "aa" in name.casefold() or "ii" in name.casefold():
        parts = [_STARTS[rng.randrange(len(_STARTS))]]
        if rng.random() < 0.65:
            parts.append(_MIDDLES[rng.randrange(len(_MIDDLES))])
        parts.append(_ENDS[rng.randrange(len(_ENDS))])
        name = "".join(parts)
    return name


def resolve_spawn_name(
    scaffold: dict,
    *,
    explicit_name: str | None = None,
    rng: random.Random | None = None,
) -> str:
    """Choose the spawned character name from scaffold spawn options.

    If ``explicit_name`` is provided it wins. Otherwise, when
    ``spawn_generate_name`` is true, build from ``spawn_name_pattern``
    (tokens: ``{given}``, ``{scaffold}``, ``{role}``, ``{class}``).
    """
    if explicit_name and str(explicit_name).strip():
        return str(explicit_name).strip()

    scaffold_name = str(scaffold.get("name") or "Spawned").strip() or "Spawned"
    generate = bool(scaffold.get("spawn_generate_name"))
    if not generate:
        return f"{scaffold_name} (spawned)"

    pattern = str(scaffold.get("spawn_name_pattern") or "{given}").strip() or "{given}"
    given = generate_given_name(rng=rng)
    role = str(scaffold.get("campaign_role") or "").strip()
    klass = str(scaffold.get("class") or "").strip()
    return (
        pattern.replace("{given}", given)
        .replace("{scaffold}", scaffold_name)
        .replace("{role}", role)
        .replace("{class}", klass)
        .strip()
        or given
    )

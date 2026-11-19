"""Between-scene Dreamtime: one additive pass that fills missing actor brains.

The pass reads canonical notes (obsolete references are ignored), character
sheets, quests, and the log. It creates minds that are not there yet and fills
empty drive, goals, traits, affect, methods, and mortality. A second run fills
only what is still empty.

Authored rows are left alone, including blank fields the GM has not written.
The pass does not change hit points, inventory, location, the log, or quest
status. Heavier theory-of-mind updates are not this pass.

Cohorts come from evidence, not from a campaign name:

- A playerish character gets a derived shadow: the mind the table expects,
  which the player can break.
- Characters named where the notes or log talk about students become students.
  Heads, mentors, and teachers named in that same passage are not students.
- A sheet whose class or role calls them a guardian becomes a guardian mind.
  Design scaffolds stay roles, not people.
- A cult named in the notes or in those scaffolds becomes one organization.
  The scaffolds bind to it as roles.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from sakaki_gm.actor_db import (
    add_brain_goal,
    bind_role_to_actor,
    create_actor,
    get_actor_by_name,
    get_actor_for_character,
    get_actor_for_role,
    get_actor_traits,
    get_affect_edges,
    get_brain_goals,
    set_actor_traits,
    set_affect_edge,
    sync_goals_from_quests,
    update_actor,
)
from sakaki_gm.campaign_roles import is_playerish_campaign_role
from sakaki_gm.character_db import get_all_characters
from sakaki_gm.gm import OBSOLETE_REFERENCE_PREFIX

_CANONICAL_PREFIX = "CANONICAL"
_AUTHORITY_RE = re.compile(
    r"\b(mentor|teacher|headmaster|headmistress)\b|\bhead of\b",
    re.IGNORECASE,
)
_GUARDIAN_RE = re.compile(r"\bguardians?\b", re.IGNORECASE)
_STUDENT_RE = re.compile(r"\bstudents?\b", re.IGNORECASE)
_CULT_NAME_RE = re.compile(
    r"\b([A-Z][\w'’\-]*(?:['’]s)?)\s+Cult(?:ist|ists)?\b"
)
_CULT_OF_RE = re.compile(r"\bCult of ([A-Z][\w'’\-]+)\b")
_SCHOOL_RE = re.compile(r"School of ([A-Za-z]+)")
_OBJECTIVE_RE = re.compile(
    r"(?:\*\*)?Next Objective(?:\*\*)?\s*:\s*(.+)",
    re.IGNORECASE,
)
_MAIN_QUEST_RE = re.compile(
    r"(?:\*\*)?Main Quest(?:\*\*)?\s*:\s*(.+)",
    re.IGNORECASE,
)
_WANTS_RE = re.compile(r"\bWants\s+(.+?)(?:\.|$)", re.IGNORECASE)
_RELATION_RE = re.compile(
    r"Relationship with\s+([^:\n]+?)\s*:\s*([^\n]+)",
    re.IGNORECASE,
)
_STATUS_AFFECT_RE = re.compile(
    r"\b(Appeased by|Friendly to)\s+([A-Z][\w'’\-]+)"
)
_DISPUTED_RE = re.compile(
    r"do not transfer|identity/status conflict|identity conflict|"
    r"until that identity is confirmed",
    re.IGNORECASE,
)
_STOPWORDS = frozenset({
    "a",
    "an",
    "the",
    "and",
    "but",
    "yet",
    "quietly",
    "slightly",
    "fiercely",
    "openly",
    "very",
    "quite",
    "little",
})
_TRAIT_WORDS = frozenset({
    "ambitious",
    "boastful",
    "bookish",
    "calm",
    "cautious",
    "charming",
    "competitive",
    "curious",
    "devout",
    "dutiful",
    "eager",
    "eccentric",
    "generous",
    "gentle",
    "kind",
    "loyal",
    "methodical",
    "perfectionist",
    "proud",
    "protective",
    "reserved",
    "scholarly",
    "secretive",
    "supportive",
    "wary",
    "zealous",
})
_ALIGNMENTS = frozenset({
    "lawful good",
    "neutral good",
    "chaotic good",
    "lawful neutral",
    "neutral",
    "chaotic neutral",
    "lawful evil",
    "neutral evil",
    "chaotic evil",
})
_CREATURE_WORDS = frozenset({
    "beast",
    "serpent",
    "snake",
    "undead",
    "wolf",
    "wraith",
})
_MAX_TRAITS = 8
_MAX_ACTOR_NOTE = 500


@dataclass
class DreamtimeResult:
    """Names touched by one pass. Authored names were seen and not written."""

    created: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    skipped_authored: list[str] = field(default_factory=list)


@dataclass
class _Cult:
    name: str
    stem: str
    section: str
    scaffolds: list[dict[str, Any]] = field(default_factory=list)


def format_dreamtime_summary(result: DreamtimeResult) -> str:
    """Short desk summary. The last line is the promise about scene state."""
    if not result.created and not result.filled:
        if result.skipped_authored:
            lines = [
                "No empty brains to fill.",
                "Left authored brains alone: "
                + ", ".join(result.skipped_authored)
                + ".",
            ]
        else:
            lines = ["No missing actor brains to fill."]
    else:
        lines = []
        if result.created:
            lines.append("Created: " + ", ".join(result.created) + ".")
        if result.filled:
            lines.append("Filled gaps: " + ", ".join(result.filled) + ".")
        if result.skipped_authored:
            lines.append(
                "Left authored brains alone: "
                + ", ".join(result.skipped_authored)
                + "."
            )
    lines.append(
        "Hit points, inventory, location, and quest status were not changed."
    )
    return "\n".join(lines)


def run_dreamtime_pass(db, *, commit: bool = True) -> DreamtimeResult:
    """Fill missing actor brains once. See the module docstring for the rules."""
    result = DreamtimeResult()
    try:
        characters = [
            row for row in get_all_characters(db) if not row.get("error")
        ]
        notes = _load_notes(db)
        narrative = _narrative_lines(db)
        quests = _load_quests(db)
        cohorts = _cohorts(characters, notes, narrative)
        created_ids: set[int] = set()
        for char in _ordered(characters, cohorts):
            _apply_person(
                db,
                char,
                cohorts[char["id"]],
                characters,
                notes,
                quests,
                narrative,
                result,
                created_ids,
            )
        for cult in _find_cults(characters, notes):
            _apply_cult(
                db, cult, characters, quests, result, created_ids
            )
        if commit:
            db.commit()
    except (sqlite3.Error, ValueError):
        db.rollback()
        raise
    return result


def _apply_person(
    db,
    char: dict[str, Any],
    cohort: set[str],
    characters: list[dict[str, Any]],
    notes: list[tuple[str, str]],
    quests: list[dict[str, Any]],
    narrative: list[str],
    result: DreamtimeResult,
    created_ids: set[int],
) -> None:
    existing = get_actor_for_character(db, char["id"]) or get_actor_by_name(
        db, char["name"]
    )
    if existing is not None and existing["provenance"] == "authored":
        _skip(result, existing["name"])
        return
    if (
        existing is not None
        and existing["character_id"] not in (None, char["id"])
    ):
        return
    proposal = _person_proposal(
        char, cohort, characters, notes, quests, narrative
    )
    if existing is None:
        actor_id = create_actor(
            db,
            char["name"],
            kind=proposal["kind"],
            character_id=char["id"],
            drive=proposal["drive"],
            methods=proposal["methods"],
            mortality=proposal["mortality"],
            provenance="derived_from_history",
            confidence=proposal["confidence"],
            notes=proposal["notes"],
            traits=proposal["traits"],
            commit=False,
        )
        result.created.append(char["name"])
        created_ids.add(actor_id)
        changed = True
    else:
        actor_id = existing["id"]
        changed = _fill_text(db, existing, proposal)
        changed = _fill_traits(db, actor_id, proposal["traits"]) or changed
    changed = (
        _fill_person_goals(db, actor_id, char["id"], proposal, quests) or changed
    )
    changed = (
        _fill_affect(db, actor_id, proposal["affect"], characters) or changed
    )
    if changed and actor_id not in created_ids:
        result.filled.append(char["name"])


def _apply_cult(
    db,
    cult: _Cult,
    characters: list[dict[str, Any]],
    quests: list[dict[str, Any]],
    result: DreamtimeResult,
    created_ids: set[int],
) -> None:
    existing = get_actor_by_name(db, cult.name)
    if existing is not None and existing["provenance"] == "authored":
        _skip(result, existing["name"])
        return
    if existing is not None and existing["character_id"] is not None:
        return
    proposal = _cult_proposal(cult, characters, quests)
    if existing is None:
        actor_id = create_actor(
            db,
            cult.name,
            kind="org",
            drive=proposal["drive"],
            methods=proposal["methods"],
            provenance="derived_from_history",
            confidence=0.4,
            notes=proposal["notes"],
            traits=proposal["traits"],
            commit=False,
        )
        result.created.append(cult.name)
        created_ids.add(actor_id)
        changed = True
    else:
        actor_id = existing["id"]
        changed = _fill_text(db, existing, proposal)
        changed = _fill_traits(db, actor_id, proposal["traits"]) or changed
    changed = _fill_goal_rows(db, actor_id, proposal["goals"]) or changed
    changed = _bind_roles(db, actor_id, proposal["roles"]) or changed
    changed = (
        _fill_affect(db, actor_id, proposal["affect"], characters) or changed
    )
    if changed and actor_id not in created_ids:
        result.filled.append(cult.name)


def _fill_text(db, existing: dict[str, Any], proposal: dict[str, Any]) -> bool:
    fields: dict[str, Any] = {}
    if not (existing.get("drive") or "").strip() and proposal.get("drive"):
        fields["drive"] = proposal["drive"]
    if not (existing.get("methods") or "").strip() and proposal.get("methods"):
        fields["methods"] = proposal["methods"]
    if not (existing.get("notes") or "").strip() and proposal.get("notes"):
        fields["notes"] = proposal["notes"]
    if existing.get("mortality") in (None, "") and proposal.get("mortality"):
        fields["mortality"] = proposal["mortality"]
    if existing.get("character_id") is None and proposal.get("character_id"):
        fields["character_id"] = proposal["character_id"]
    if not fields:
        return False
    update_actor(db, existing["id"], commit=False, **fields)
    return True


def _fill_traits(db, actor_id: int, traits: list[str]) -> bool:
    before = get_actor_traits(db, actor_id)
    merged = list(before)
    seen = set(before)
    for tag in traits:
        if tag not in seen and len(merged) < _MAX_TRAITS:
            merged.append(tag)
            seen.add(tag)
    if len(merged) == len(before):
        return False
    set_actor_traits(db, actor_id, merged, commit=False)
    return True


def _fill_person_goals(
    db,
    actor_id: int,
    character_id: int,
    proposal: dict[str, Any],
    quests: list[dict[str, Any]],
) -> bool:
    added = sync_goals_from_quests(db, actor_id, character_id, commit=False)
    changed = bool(added)
    linked = {
        goal["quest_id"]
        for goal in get_brain_goals(db, actor_id)
        if goal["quest_id"] is not None
    }
    present = {_norm(goal["summary"]) for goal in get_brain_goals(db, actor_id)}
    for quest in _completed_quest_mentions(
        proposal.get("mention_text") or "", quests
    ):
        if quest["id"] in linked or _norm(quest["name"]) in present:
            continue
        add_brain_goal(
            db,
            actor_id,
            quest["name"],
            status="done",
            priority=0,
            quest_id=quest["id"],
            commit=False,
        )
        present.add(_norm(quest["name"]))
        changed = True
    return _fill_goal_rows(db, actor_id, proposal["goals"]) or changed


def _fill_goal_rows(db, actor_id: int, goals: list[dict[str, Any]]) -> bool:
    present = [_norm(goal["summary"]) for goal in get_brain_goals(db, actor_id)]
    changed = False
    for goal in goals:
        summary = str(goal.get("summary") or "").strip()
        if not summary:
            continue
        key = _norm(summary)
        if key in present:
            continue
        add_brain_goal(
            db,
            actor_id,
            summary,
            status=str(goal.get("status") or "active"),
            priority=int(goal.get("priority") or 0),
            quest_id=goal.get("quest_id"),
            commit=False,
        )
        present.append(key)
        changed = True
    return changed


def _fill_affect(
    db,
    actor_id: int,
    affect: list[dict[str, Any]],
    characters: list[dict[str, Any]],
) -> bool:
    existing = get_affect_edges(db, actor_id)
    changed = False
    for edge in affect:
        target = _resolve_target(db, str(edge["target"]), characters)
        if target is None:
            continue
        kind, target_id = target
        if _edge_exists(existing, edge["axis"], kind, target_id):
            continue
        kwargs = (
            {"to_actor_id": target_id}
            if kind == "actor"
            else {"to_character_id": target_id}
        )
        set_affect_edge(
            db,
            actor_id,
            str(edge["axis"]),
            int(edge["level"]),
            commit=False,
            **kwargs,
        )
        existing.append(
            {
                "axis": edge["axis"],
                "to_actor_id": target_id if kind == "actor" else None,
                "to_character_id": target_id if kind == "character" else None,
            }
        )
        changed = True
    return changed


def _bind_roles(db, actor_id: int, roles: list[str]) -> bool:
    changed = False
    for role in roles:
        role = role.strip()
        if not role or get_actor_for_role(db, role) is not None:
            continue
        bind_role_to_actor(db, role, actor_id, commit=False)
        changed = True
    return changed


def _person_proposal(
    char: dict[str, Any],
    cohort: set[str],
    characters: list[dict[str, Any]],
    notes: list[tuple[str, str]],
    quests: list[dict[str, Any]],
    narrative: list[str],
) -> dict[str, Any]:
    # Sheet and notes titled for this person. Other people's quests must not
    # leak mortality, affect, or a labeled objective into this mind.
    own = _own_text(char, notes)
    quest_blob = "\n".join(
        f"{quest['name']}\n{quest['description']}" for quest in quests
    )
    objective = _next_objective(char, notes)
    goals = _sheet_goals(char, objective)
    if "student" in cohort:
        goals = _merge_goal_lists(
            goals, _assignment_goals(narrative, char, characters)
        )
    if "guardian" in cohort:
        goals = _merge_goal_lists(goals, _guardian_goals(char))
    if "shadow" in cohort:
        drive = _shadow_drive(char, notes)
        methods = _shadow_methods(own + "\n" + quest_blob)
        confidence = 0.6
    elif "student" in cohort:
        drive = _student_drive(char)
        methods = _school_or(own, "study")
        confidence = 0.55
    else:
        drive = _guardian_drive(char)
        methods = _guardian_methods(char, own)
        confidence = 0.5
    kind = "other" if "guardian" in cohort and _creature(char) else "person"
    affect = [] if _disputed(str(char.get("notes") or "")) else _affect_from_text(own)
    return {
        "kind": kind,
        "drive": drive,
        "methods": methods,
        "mortality": _mortality(own),
        "notes": _clip(_person_note(char, cohort), _MAX_ACTOR_NOTE),
        "traits": _person_traits(char, cohort, own),
        "goals": goals,
        "affect": affect,
        "confidence": confidence,
        "character_id": char["id"],
        "mention_text": own,
    }


def _cult_proposal(
    cult: _Cult,
    characters: list[dict[str, Any]],
    quests: list[dict[str, Any]],
) -> dict[str, Any]:
    section = cult.section
    traits = ["cult"]
    if re.search(r"rival faction", section, re.IGNORECASE):
        traits.append("rival")
    symbol = _symbol(section)
    if symbol and len(symbol.split()) <= 3:
        traits.append(symbol.casefold())
    goals: list[dict[str, Any]] = []
    if re.search(r"rival faction", section, re.IGNORECASE):
        goals.append(
            {
                "summary": "Act as the rival faction.",
                "status": "active",
                "priority": 1,
            }
        )
    for quest in quests:
        blob = _norm(f"{quest['name']} {quest['description']}")
        if _norm(cult.name) in blob or (
            _norm(cult.stem) in blob and "cult" in blob
        ):
            goals.append(
                {
                    "summary": quest["name"],
                    "status": _quest_goal_status(str(quest.get("status") or "")),
                    "priority": 1,
                    "quest_id": quest["id"],
                }
            )
    rival = bool(re.search(r"rival faction", section, re.IGNORECASE))
    enemy = any(
        str(row.get("campaign_role") or "").casefold() == "enemy"
        for row in cult.scaffolds
    )
    affect = []
    if rival or enemy:
        affect = [
            {"target": char["name"], "axis": "trust", "level": -1}
            for char in characters
            if is_playerish_campaign_role(char.get("campaign_role"))
            and not _scaffold(char)
        ]
    return {
        "drive": _cult_drive(cult),
        "methods": _cult_methods(cult),
        "notes": (
            "Organization mind for the cult. Role scaffolds stay roles on "
            "this mind, not separate people. Filled from the notes and "
            "those scaffolds."
        ),
        "traits": traits,
        "goals": goals,
        "affect": affect,
        "roles": [str(row["name"]) for row in cult.scaffolds],
        "character_id": None,
    }


def _cohorts(
    characters: list[dict[str, Any]],
    notes: list[tuple[str, str]],
    narrative: list[str],
) -> dict[int, set[str]]:
    cohorts: dict[int, set[str]] = {}
    for char in characters:
        # Spawners are roles for an organization, not people. A named person
        # whose stat block is still a design scaffold can still have a mind.
        if _spawner(char):
            continue
        if is_playerish_campaign_role(char.get("campaign_role")):
            cohorts.setdefault(char["id"], set()).add("shadow")
        if _guardian_sheet(char):
            cohorts.setdefault(char["id"], set()).add("guardian")
    for char in _students(characters, notes, narrative):
        cohorts.setdefault(char["id"], set()).add("student")
    return cohorts


def _students(
    characters: list[dict[str, Any]],
    notes: list[tuple[str, str]],
    narrative: list[str],
) -> list[dict[str, Any]]:
    texts = _student_texts(notes, narrative)
    if not texts:
        return []
    found: dict[int, dict[str, Any]] = {}
    for text in texts:
        for char in _mentioned(text, characters):
            if _spawner(char):
                continue
            if is_playerish_campaign_role(char.get("campaign_role")):
                continue
            if _authority(char):
                continue
            found[char["id"]] = char
    return list(found.values())


def _student_texts(
    notes: list[tuple[str, str]],
    narrative: list[str],
) -> list[str]:
    texts: list[str] = []
    for index, line in enumerate(narrative):
        if _STUDENT_RE.search(line):
            texts.append("\n".join(narrative[index:index + 3]))
    for title, content in notes:
        paragraphs = re.split(r"\n\s*\n", content)
        for index, paragraph in enumerate(paragraphs):
            if not _STUDENT_RE.search(paragraph):
                continue
            follow = paragraphs[index + 1] if index + 1 < len(paragraphs) else ""
            texts.append(paragraph + "\n" + follow)
        if _STUDENT_RE.search(title) and paragraphs:
            texts.append(paragraphs[0])
    return texts


def _ordered(
    characters: list[dict[str, Any]],
    cohorts: dict[int, set[str]],
) -> list[dict[str, Any]]:
    rank = {"shadow": 0, "student": 1, "guardian": 2}

    def sort_key(char: dict[str, Any]) -> tuple[int, str]:
        roles = cohorts.get(char["id"], set())
        best = min((rank[role] for role in roles), default=9)
        return best, str(char["name"]).casefold()

    return sorted(
        (char for char in characters if char["id"] in cohorts),
        key=sort_key,
    )


def _find_cults(
    characters: list[dict[str, Any]],
    notes: list[tuple[str, str]],
) -> list[_Cult]:
    found: dict[str, _Cult] = {}

    def add(raw_stem: str, section: str) -> None:
        stem = raw_stem.replace("’", "'").strip()
        stem = re.sub(r"\s+Cult$", "", stem, flags=re.IGNORECASE).strip()
        if not stem:
            return
        if stem.casefold().endswith("cult"):
            name = stem
        else:
            name = f"{stem} Cult"
        key = _norm(name)
        cult = found.get(key)
        if cult is None:
            cult = _Cult(name=name, stem=stem.split("'")[0], section=section)
            found[key] = cult
        elif section and len(section) > len(cult.section):
            cult.section = section

    for title, content in notes:
        blob = f"{title}\n{content}"
        for match in _CULT_NAME_RE.finditer(blob):
            add(match.group(1), _pantheon_section(notes, match.group(1)) or blob)
        for match in _CULT_OF_RE.finditer(blob):
            add(match.group(1), _pantheon_section(notes, match.group(1)) or blob)
    for char in characters:
        blob = f"{char['name']}\n{char.get('notes') or ''}"
        for match in _CULT_NAME_RE.finditer(blob):
            add(match.group(1), _pantheon_section(notes, match.group(1)))
        for match in _CULT_OF_RE.finditer(blob):
            add(match.group(1), _pantheon_section(notes, match.group(1)))
    for cult in found.values():
        stem = _norm(cult.stem)
        for char in characters:
            if not _scaffold(char):
                continue
            if stem and stem in _norm(char["name"]):
                cult.scaffolds.append(char)
        if not cult.section:
            cult.section = _pantheon_section(notes, cult.stem)
    return [found[key] for key in sorted(found)]


def _pantheon_section(notes: list[tuple[str, str]], raw_name: str) -> str:
    stem = raw_name.replace("’", "'").split("'")[0].strip()
    if not stem:
        return ""
    pattern = re.compile(rf"\b{re.escape(stem)}\b", re.IGNORECASE)
    best = ""
    for title, content in notes:
        if "pantheon" not in title.casefold() and not pattern.search(content):
            continue
        parts = re.split(r"\n(?=\*\*|[A-Z][\w'’\-]+ –)", content)
        for part in parts:
            if pattern.search(part) and len(part) > len(best):
                # Prefer the short deity entry over the whole note.
                if "pantheon" in title.casefold() or len(part) < len(content):
                    best = part
        if not best and pattern.search(content) and "pantheon" in title.casefold():
            best = content
    return best.strip()


def _shadow_drive(char: dict[str, Any], notes: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    objective = _next_objective(char, notes)
    if objective:
        parts.append(objective.rstrip("."))
    driven = re.search(
        r"\bdriven to\s+([^.]*)",
        _personality_text(char),
        re.IGNORECASE,
    )
    if driven:
        clause = _clean(driven.group(1)).strip().rstrip(".")
        if clause:
            parts.append("Driven to " + clause)
    if not parts:
        ideal = _labeled_line(str(char.get("notes") or ""), "Ideal")
        if ideal:
            parts.append(ideal.strip("\"'").rstrip("."))
    if not parts:
        return ""
    text = ". ".join(parts)
    if not text.endswith("."):
        text += "."
    return _clip(text, 320)


def _student_drive(char: dict[str, Any]) -> str:
    goal = _clean(str(char.get("current_goal") or ""))
    if goal:
        return goal if goal.endswith(".") else goal + "."
    duties = _role_duties(str(char.get("notes") or ""))
    if duties:
        duty = duties[0].rstrip(".")
        return duty + "."
    return "Study, and do the work they were asked to do."


def _guardian_drive(char: dict[str, Any]) -> str:
    notes = str(char.get("notes") or "")
    role = _clean(str(char.get("role_in_campaign") or ""))
    if role and not _disputed(notes):
        sentence = re.split(r"(?<=[.!])\s+", role)[0].strip()
        if sentence:
            if re.search(r"[.!?][\"'”’]*$", sentence):
                return sentence
            return sentence + "."
    label = _shrine_label(str(char.get("class") or ""))
    if label == "dream":
        drive = "Guard dreams and see intruders out."
    elif label:
        drive = f"Hold the {label} shrine."
    else:
        drive = "Hold the post they guard."
    if not _disputed(notes) and re.search(r"\bappeased\b", notes, re.IGNORECASE):
        if re.search(r"remain passive", notes, re.IGNORECASE):
            drive = drive.rstrip(".") + ". Appeased; remain passive."
        else:
            drive = drive.rstrip(".") + ". Appeased."
    return drive


def _guardian_goals(char: dict[str, Any]) -> list[dict[str, Any]]:
    notes = str(char.get("notes") or "")
    features = str(char.get("features") or "")
    if _disputed(notes):
        return [
            {
                "summary": (
                    "Confirm this guardian's identity before treating "
                    "passage as settled."
                ),
                "status": "active",
                "priority": 1,
            }
        ]
    goals: list[dict[str, Any]] = []
    if re.search(r"remain passive", notes, re.IGNORECASE):
        goals.append(
            {"summary": "Remain passive.", "status": "active", "priority": 1}
        )
    elif re.search(r"safe passage", notes, re.IGNORECASE):
        goals.append(
            {
                "summary": "Allow safe passage.",
                "status": "active",
                "priority": 1,
            }
        )
    if re.search(r"\bdream\b", str(char.get("class") or ""), re.IGNORECASE):
        if re.search(r"escort", features, re.IGNORECASE):
            goals.append(
                {
                    "summary": "Escort intruders out of foreign dreams.",
                    "status": "active",
                    "priority": 1,
                }
            )
    return goals


def _sheet_goals(char: dict[str, Any], objective: str) -> list[dict[str, Any]]:
    notes = str(char.get("notes") or "")
    goals: list[dict[str, Any]] = []
    if objective:
        goals.append(
            {"summary": objective.rstrip("."), "status": "active", "priority": 2}
        )
    main = _main_quest(notes)
    if main is not None:
        summary, status = main
        goals.append({"summary": summary, "status": status, "priority": 0})
    for match in _WANTS_RE.finditer(notes):
        summary = _clean(match.group(0)).rstrip(".")
        if summary:
            goals.append(
                {"summary": summary, "status": "active", "priority": 1}
            )
    for duty in _role_duties(notes):
        goals.append({"summary": duty.rstrip("."), "status": "active", "priority": 1})
    return goals


def _assignment_goals(
    narrative: list[str],
    char: dict[str, Any],
    characters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tasks the log gives this person in a student passage.

    Name lists stay intact ("Talia and Lyssia in the inner circle") because
    the clause splitter knows every cast name, then this person is kept only
    when the clause names them.
    """
    goals: list[dict[str, Any]] = []
    names = _cast_names(characters)
    own = str(char["name"])
    first = own.split()[0]
    for index, line in enumerate(narrative):
        if not _STUDENT_RE.search(line):
            continue
        window = "\n".join(narrative[index:index + 3])
        for who, predicate in _name_clauses(window, names):
            hay = _norm(who)
            if not re.search(
                rf"(?<!\w)(?:{re.escape(_norm(own))}|{re.escape(_norm(first))})(?!\w)",
                hay,
            ):
                continue
            summary = _goal_from_predicate(predicate)
            if not summary:
                continue
            goals.append(
                {"summary": summary, "status": "active", "priority": 2}
            )
            if len(goals) >= 2:
                return goals
    return goals


def _cast_names(characters: list[dict[str, Any]]) -> list[str]:
    names = []
    for char in characters:
        if _scaffold(char):
            continue
        names.append(str(char["name"]))
        first = str(char["name"]).split()[0]
        if len(first) >= 3:
            names.append(first)
    return names


def _name_clauses(text: str, names: list[str]) -> list[tuple[str, str]]:
    cleaned = [name for name in names if name and len(name) >= 3]
    if not cleaned:
        return []
    unique = sorted(set(cleaned), key=len, reverse=True)
    name_pat = "|".join(re.escape(name) for name in unique)
    results: list[tuple[str, str]] = []
    for sentence in re.split(r"[\n.]", text):
        pieces = re.split(r",\s*", sentence)
        for piece in pieces:
            match = re.search(
                rf"(?:^|:\s*)((?:{name_pat})(?:\s+and\s+(?:{name_pat}))*)"
                rf"\s+(?P<pred>.+)$",
                piece.strip(),
                re.IGNORECASE,
            )
            if not match:
                continue
            predicate = match.group("pred").strip()
            if not _acceptable_predicate(predicate):
                continue
            results.append((match.group(1), predicate))
    return results


def _acceptable_predicate(predicate: str) -> bool:
    return bool(
        re.match(r"(?i)(?:in|at|on) the\b", predicate)
        or re.match(r"(?i)(?:should|will|to)\s+", predicate)
        or re.match(r"(?i)[a-z]+ing\b", predicate)
    )


def _goal_from_predicate(predicate: str) -> str:
    predicate = re.split(
        r"[—–;]|\s+(?:she|he|they)\b",
        predicate,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    predicate = re.sub(
        r"^(?:should|will|to)\s+",
        "",
        predicate.strip(),
        flags=re.IGNORECASE,
    )
    predicate = predicate.strip(" .")
    if len(predicate) < 8:
        return ""
    return predicate[0].upper() + predicate[1:]


def _person_traits(
    char: dict[str, Any],
    cohort: set[str],
    evidence: str,
) -> list[str]:
    traits: list[str] = []

    def add(tag: str) -> None:
        tag = re.sub(r"\s+", " ", tag).strip().casefold()
        if not tag or tag in traits or len(tag) > 40 or len(traits) >= _MAX_TRAITS:
            return
        traits.append(tag)

    if "student" in cohort:
        add("student")
    if "guardian" in cohort:
        add("guardian")
    if not _disputed(evidence) and re.search(
        r"\bappeased\b", str(char.get("notes") or ""), re.IGNORECASE
    ):
        add("appeased")
    race = str(char.get("race") or "")
    if re.search(r"\byoukai\b", race, re.IGNORECASE) or re.search(
        r"\byoukai\b", evidence, re.IGNORECASE
    ):
        add("youkai")
    for tag in _personality_traits(_personality_text(char)):
        add(tag)
    alignment = str(char.get("alignment") or "").strip().casefold()
    if alignment in _ALIGNMENTS:
        add(alignment)
    school = _SCHOOL_RE.search(evidence)
    if school:
        add(school.group(1))
    return traits


def _personality_traits(text: str) -> list[str]:
    text = _clean(text)
    if not text:
        return []
    parts = [part.strip(" .") for part in re.split(r",|\band\b|\bbut\b|\byet\b", text)]
    parts = [part for part in parts if part]
    if len(parts) >= 2 and all(len(part.split()) <= 4 for part in parts):
        traits = []
        listed = True
        for part in parts:
            words = [
                word
                for word in re.findall(r"[A-Za-z']+", part)
                if word.casefold() not in _STOPWORDS
            ]
            if not words:
                continue
            if any(word.casefold() not in _TRAIT_WORDS for word in words):
                listed = False
                break
            traits.append(" ".join(word.casefold() for word in words))
        if listed and traits:
            return traits
    found = []
    for match in re.finditer(r"[A-Za-z']+", text):
        word = match.group(0).casefold().strip("'")
        if word in _TRAIT_WORDS and word not in found:
            found.append(word)
    return found


def _affect_from_text(text: str) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for match in _RELATION_RE.finditer(text):
        target = match.group(1).strip().strip("*")
        scored = _score_relationship(match.group(2))
        for axis, level in scored.items():
            edges.append({"target": target, "axis": axis, "level": level})
    for match in _STATUS_AFFECT_RE.finditer(text):
        kind, target = match.group(1), match.group(2)
        if kind.casefold().startswith("appeased"):
            edges.append({"target": target, "axis": "trust", "level": 1})
        else:
            edges.append({"target": target, "axis": "trust", "level": 2})
            edges.append({"target": target, "axis": "loyalty", "level": 1})
    return edges


def _score_relationship(phrase: str) -> dict[str, int]:
    text = phrase.casefold()
    if "close friend" in text:
        return {"love": 2, "loyalty": 2, "trust": 2}
    if "without warmth" in text or re.search(r"\bdistant\b", text):
        return {"trust": 1, "loyalty": 1}
    if "competi" in text or re.search(r"\brival", text):
        return {"trust": 1}
    if "warm" in text and "support" in text:
        return {"loyalty": 2, "trust": 2}
    if "friendly" in text:
        return {"trust": 2, "loyalty": 1}
    if "respect" in text:
        return {"trust": 1, "loyalty": 1}
    return {}


def _shadow_methods(evidence: str) -> str:
    text = evidence.casefold()
    bits = []
    if "ritual" in text:
        bits.append("ritual")
    if any(word in text for word in ("study", "research", "sage", "lore")):
        bits.append("study")
    if "combat avoided" in text or ("avoid" in text and "combat" in text):
        bits.append("avoid needless combat")
    return ", ".join(bits)


def _guardian_methods(char: dict[str, Any], evidence: str) -> str:
    if _disputed(str(char.get("notes") or "")):
        return "hold the shrine"
    if re.search(r"\bdream\b", str(char.get("class") or ""), re.IGNORECASE):
        return "enter dreams and escort intruders out"
    if re.search(r"phrase|sprig|offering", evidence, re.IGNORECASE):
        return "judge the phrase and the offering"
    return "hold the shrine"


def _cult_drive(cult: _Cult) -> str:
    section = cult.section
    rank = re.search(
        r"((?:Greater|Intermediate|Lesser)\s+(?:God|Goddess)\s+of\s+[^(\n]+)",
        section,
        re.IGNORECASE,
    )
    if rank:
        title = re.sub(r"\s+", " ", rank.group(1)).strip(" .")
        drive = f"Serve {cult.stem} ({title.casefold()})"
    else:
        drive = f"Serve {cult.stem}"
    extras = []
    if re.search(r"rival faction", section, re.IGNORECASE):
        extras.append("rival faction")
    symbol = _symbol(section)
    if symbol:
        extras.append(symbol.casefold())
    if extras:
        drive += "; " + "; ".join(extras)
    return drive + "."


def _cult_methods(cult: _Cult) -> str:
    labels = []
    prefix = re.compile(
        rf"^{re.escape(cult.stem)}(?:['’]s)?\s+",
        re.IGNORECASE,
    )
    for row in cult.scaffolds:
        rest = prefix.sub("", str(row["name"])).strip()
        rest = re.sub(r"^Cult\s+", "", rest, flags=re.IGNORECASE).strip()
        label = rest.casefold() or str(row["name"]).casefold()
        if label not in labels:
            labels.append(label)
    return ", ".join(labels)


def _symbol(section: str) -> str:
    match = re.search(r"Symbols:\s*([^\n]+)", section, re.IGNORECASE)
    if not match:
        return ""
    return _clean(match.group(1)).strip(" .")


def _mortality(text: str) -> Optional[str]:
    hay = text.casefold()
    permanent = any(
        phrase in hay
        for phrase in (
            "cannot be killed",
            "unkillable",
            "youkai permanence",
            "not a mortal hp",
            "death is not on the table",
        )
    )
    if not permanent:
        return None
    if "banish" in hay or "drive off" in hay:
        return "banish_only"
    return "unkillable"


def _person_note(char: dict[str, Any], cohort: set[str]) -> str:
    bits = []
    if "shadow" in cohort:
        bits.append(
            "Derived shadow: what the table expects this person to do. "
            "The player can break it."
        )
    if "student" in cohort:
        bits.append("Named among the students in the log or notes.")
    if "guardian" in cohort:
        bits.append("Guardian role taken from the sheet.")
    if _scaffold(char):
        bits.append(
            "The sheet is still a design scaffold; this mind does not "
            "invent its missing mechanics."
        )
    bits.append("Filled from the sheet, canonical notes, quests, and the log.")
    return " ".join(bits)


def _own_text(char: dict[str, Any], notes: list[tuple[str, str]]) -> str:
    chunks = [
        str(char.get("notes") or ""),
        str(char.get("personality") or ""),
        str(char.get("features") or ""),
        str(char.get("role") or ""),
        str(char.get("role_in_campaign") or ""),
        str(char.get("background") or ""),
        str(char.get("class") or ""),
    ]
    for title, content in notes:
        if _title_names(title, char):
            chunks.append(content)
    return "\n".join(chunks)


def _next_objective(char: dict[str, Any], notes: list[tuple[str, str]]) -> str:
    canonical = []
    other = []
    for title, content in notes:
        if not _title_names(title, char):
            continue
        found = _objective_line(content)
        if not found:
            continue
        if title.upper().startswith(_CANONICAL_PREFIX):
            canonical.append(found)
        else:
            other.append(found)
    if canonical:
        return canonical[0]
    current = _clean(str(char.get("current_goal") or ""))
    if current:
        return current
    sheet = _objective_line(str(char.get("notes") or ""))
    if sheet:
        return sheet
    return other[0] if other else ""


def _objective_line(text: str) -> str:
    match = _OBJECTIVE_RE.search(text)
    if not match:
        return ""
    return _clean(match.group(1)).strip()


def _main_quest(notes: str) -> Optional[tuple[str, str]]:
    match = _MAIN_QUEST_RE.search(notes)
    if not match:
        return None
    raw = _clean(match.group(1))
    done = bool(re.search(r"\b(complete|completed|done)\b", raw, re.IGNORECASE))
    summary = re.split(r"\s+[–—-]\s+", raw)[0]
    summary = re.sub(
        r"\b(COMPLETE|COMPLETED|DONE)\b",
        "",
        summary,
        flags=re.IGNORECASE,
    ).strip(" .")
    if not summary:
        return None
    return summary, "done" if done else "active"


def _completed_quest_mentions(
    text: str,
    quests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hay = _norm(text)
    found = []
    for quest in quests:
        name = _norm(str(quest["name"]))
        if not name or name not in hay:
            continue
        index = hay.find(name)
        window = hay[index:index + len(name) + 48]
        if re.search(r"\b(complete|completed|done|finished)\b", window):
            found.append(quest)
    return found


def _role_duties(notes: str) -> list[str]:
    duties = []
    collecting = False
    for line in notes.splitlines():
        stripped = line.strip()
        if re.match(r"\*\*Role\b", stripped, re.IGNORECASE):
            collecting = True
            continue
        if not collecting:
            continue
        if not stripped or stripped.startswith("**"):
            break
        match = re.match(r"^[-*]\s+(.+)", stripped)
        if not match:
            continue
        duty = re.sub(r"\s*\([^)]*\)", "", match.group(1))
        duty = _clean(duty).strip()
        if not duty or re.match(
            r"(?i)(?:rarely|always|never|is|was)\b", duty
        ):
            continue
        duties.append(duty)
        if len(duties) >= 3:
            break
    return duties


def _personality_text(char: dict[str, Any]) -> str:
    parts = [str(char.get("personality") or "")]
    block = _labeled_block(str(char.get("notes") or ""), "Personality")
    if block:
        parts.append(block)
    return " ".join(part for part in parts if part.strip())


def _labeled_block(notes: str, label: str) -> str:
    lines = notes.splitlines()
    found: list[str] = []
    grabbing = False
    pattern = re.compile(rf"(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:", re.IGNORECASE)
    for line in lines:
        if pattern.search(line.strip()):
            found.append(line.split(":", 1)[1])
            grabbing = True
            continue
        if not grabbing:
            continue
        if not line.strip() or line.strip().startswith("**"):
            break
        found.append(line)
    return _clean(" ".join(found))


def _labeled_line(notes: str, label: str) -> str:
    match = re.search(
        rf"(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*(.+)",
        notes,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else ""


def _school_or(evidence: str, default: str) -> str:
    match = _SCHOOL_RE.search(evidence)
    if match:
        return match.group(1).casefold()
    return default


def _shrine_label(class_name: str) -> str:
    label = _GUARDIAN_RE.sub("", class_name).strip(" -–—")
    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        return ""
    return label.casefold()


def _guardian_sheet(char: dict[str, Any]) -> bool:
    blob = " ".join(
        str(char.get(key) or "")
        for key in ("class", "role", "role_in_campaign")
    )
    return bool(_GUARDIAN_RE.search(blob))


def _authority(char: dict[str, Any]) -> bool:
    blob = " ".join(
        str(char.get(key) or "")
        for key in ("role", "role_in_campaign", "class")
    )
    return bool(_AUTHORITY_RE.search(blob))


def _creature(char: dict[str, Any]) -> bool:
    race = str(char.get("race") or "").casefold()
    return any(word in race for word in _CREATURE_WORDS)


def _scaffold(char: dict[str, Any]) -> bool:
    status = char.get("stat_block_status")
    return isinstance(status, str) and status.casefold().startswith(
        "design scaffold"
    )


def _spawner(char: dict[str, Any]) -> bool:
    """A role template that generates people, not one named person."""
    if char.get("spawn_generate_name"):
        return True
    return bool(str(char.get("spawn_name_pattern") or "").strip())


def _disputed(text: str) -> bool:
    return bool(_DISPUTED_RE.search(text or ""))


def _mentioned(
    text: str,
    characters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hay = _norm(text)
    found: list[dict[str, Any]] = []
    claimed: set[int] = set()
    ordered = sorted(characters, key=lambda row: len(str(row["name"])), reverse=True)
    for char in ordered:
        full = _norm(str(char["name"]))
        if full and re.search(rf"(?<!\w){re.escape(full)}(?!\w)", hay):
            found.append(char)
            claimed.add(char["id"])
    firsts: dict[str, list[dict[str, Any]]] = {}
    for char in characters:
        first = _norm(str(char["name"]).split()[0])
        firsts.setdefault(first, []).append(char)
    for first, group in firsts.items():
        if len(group) != 1 or len(first) < 3:
            continue
        char = group[0]
        if char["id"] in claimed:
            continue
        if re.search(rf"(?<!\w){re.escape(first)}(?!\w)", hay):
            found.append(char)
            claimed.add(char["id"])
    return found


def _title_names(title: str, char: dict[str, Any]) -> bool:
    return bool(_mentioned(title, [char]))


def _resolve_target(
    db,
    name: str,
    characters: list[dict[str, Any]],
) -> Optional[tuple[str, int]]:
    matches = _mentioned(name, characters)
    if len(matches) != 1:
        # A relationship label may be only a first name. _mentioned on the
        # whole cast still resolves a unique first name.
        matches = _mentioned(name, characters)
    if len(matches) != 1:
        return None
    char = matches[0]
    actor = get_actor_for_character(db, char["id"]) or get_actor_by_name(
        db, char["name"]
    )
    if actor is not None:
        return "actor", int(actor["id"])
    return "character", int(char["id"])


def _edge_exists(edges: list[dict[str, Any]], axis: str, kind: str, target_id: int) -> bool:
    column = "to_actor_id" if kind == "actor" else "to_character_id"
    for edge in edges:
        if edge.get("axis") != axis:
            continue
        if edge.get(column) == target_id:
            return True
    return False


def _quest_goal_status(status: str) -> str:
    value = status.strip().casefold()
    if value in {"done", "complete", "completed", "finished"}:
        return "done"
    if value in {"failed", "fail", "abandoned"}:
        return "failed"
    if value in {"paused", "hold", "waiting"}:
        return "paused"
    return "active"


def _merge_goal_lists(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(left)
    keys = [_norm(str(goal.get("summary") or "")) for goal in merged]
    for goal in right:
        key = _norm(str(goal.get("summary") or ""))
        if not key or key in keys:
            continue
        merged.append(goal)
        keys.append(key)
    return merged


def _load_notes(db) -> list[tuple[str, str]]:
    rows = db.execute("SELECT title, content FROM notes").fetchall()
    notes = []
    for row in rows:
        title = row["title"] or ""
        if title.startswith(OBSOLETE_REFERENCE_PREFIX):
            continue
        notes.append((title, row["content"] or ""))
    return notes


def _narrative_lines(db) -> list[str]:
    rows = db.execute(
        "SELECT speaker, message FROM log ORDER BY id"
    ).fetchall()
    lines = []
    for row in rows:
        if str(row["speaker"] or "").casefold() not in {"player", "gm"}:
            continue
        message = str(row["message"] or "").strip()
        if message:
            lines.append(message)
    return lines


def _load_quests(db) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id, name, description, status FROM quests"
    ).fetchall()
    return [dict(row) for row in rows]


def _skip(result: DreamtimeResult, name: str) -> None:
    if name not in result.skipped_authored:
        result.skipped_authored.append(name)


def _clean(text: str) -> str:
    text = text.replace("**", "").replace("*", "")
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _norm(text: str) -> str:
    return _clean(text).casefold()


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"

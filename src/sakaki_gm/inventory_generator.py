"""Generate sensible starter inventory kits without irrelevant duplicates.

Adds missing catalog copies for a character based on class. Skips an entry when
the character already has that archetype, the same name, or a "family" stand-in
(e.g. Lunarglow Skirt covers Traveler's Clothes; Lunar Scepter covers Arcane Focus).
"""

from __future__ import annotations

import json
import re
import uuid

from sakaki_gm.item_db import (
    CONTAINER_INSTANCE_PREFIX,
    create_item_instance,
    get_character_inventory_container,
    get_character_inventory_tree,
    place_owned_copy_in_container,
)
from sakaki_gm.rules_5e_equipment import ensure_5e_equipment_archetypes

# Specs: (catalog_name, quantity, family, parent_family_or_None)
# parent_family "pack" places the item inside an owned pack/backpack when present.
_KIT_WIZARD = (
    ("Spellbook", 1, "spellbook", None),
    ("Arcane Focus", 1, "focus", None),
    ("Traveler's Clothes", 1, "clothes", None),
    ("Backpack", 1, "pack", None),
    ("Ink (1 ounce bottle)", 1, "ink", "pack"),
    ("Ink Pen", 1, "ink_pen", "pack"),
    ("Parchment", 10, "parchment", "pack"),
    ("Rations", 3, "rations", "pack"),
    ("Waterskin", 1, "waterskin", "pack"),
    ("Gold Coin", 10, "gold", "pack"),
    ("Dagger", 1, "simple_melee", None),
)

_KIT_RANGER = (
    ("Leather Armor", 1, "armor", None),
    ("Shortsword", 1, "simple_melee", None),
    ("Longbow", 1, "ranged", None),
    ("Traveler's Clothes", 1, "clothes", None),
    ("Backpack", 1, "pack", None),
    ("Quiver", 1, "quiver", None),
    ("Arrow", 20, "ammo", "quiver"),
    ("Rations", 5, "rations", "pack"),
    ("Waterskin", 1, "waterskin", "pack"),
    ("Gold Coin", 10, "gold", "pack"),
)

_KIT_BASIC = (
    ("Traveler's Clothes", 1, "clothes", None),
    ("Backpack", 1, "pack", None),
    ("Rations", 3, "rations", "pack"),
    ("Waterskin", 1, "waterskin", "pack"),
    ("Gold Coin", 5, "gold", "pack"),
    ("Dagger", 1, "simple_melee", None),
)

KITS = {
    "wizard": _KIT_WIZARD,
    "ranger": _KIT_RANGER,
    "basic": _KIT_BASIC,
    "none": (),
}

# Classes / roles that should not get mundane adventuring kits by default.
_SKIP_CLASS_RE = re.compile(
    r"(serpent|guardian|wraith|deity|avatar|spirit|dream\s*guardian|"
    r"star\s*guardian|construct|elemental)",
    re.IGNORECASE,
)

_FAMILY_NAME_HINTS = {
    "spellbook": ("spellbook", "spell book"),
    "focus": (
        "arcane focus",
        "component pouch",
        "scepter",
        "wand",
        "orb",
        "rod",
        "staff",
        "focus",
    ),
    "pack": ("backpack", "pack", "satchel"),
    "clothes": (
        "traveler's clothes",
        "travellers clothes",
        "clothes",
        "skirt",
        "robe",
        "gown",
        "outfit",
        "dress",
        "tunic",
    ),
    "armor": ("armor", "mail", "breastplate", "shield"),
    "simple_melee": (
        "dagger",
        "shortsword",
        "short sword",
        "club",
        "handaxe",
        "mace",
        "scimitar",
        "sword",
        "stick",
        "scepter",
        "sceptre",
        "staff",
        "quarterstaff",
        "rod",
    ),
    "ranged": ("longbow", "shortbow", "crossbow", "bow"),
    "quiver": ("quiver",),
    "ammo": ("arrow", "bolt"),
    "gold": ("gold coin", "gold", "gp"),
    "ink": ("ink",),
    "ink_pen": ("ink pen", "quill"),
    "parchment": ("parchment", "paper"),
    "rations": ("rations", "ration"),
    "waterskin": ("waterskin", "water skin"),
}


def choose_kit_for_character(data: dict | None, *, campaign_role: str = "") -> str:
    """Pick a kit id from character JSON + role."""
    data = data if isinstance(data, dict) else {}
    class_name = str(
        data.get("class")
        or data.get("character_class")
        or data.get("class_name")
        or ""
    ).strip()
    role = (campaign_role or "").strip().casefold()
    if role in {"cameo"} or _SKIP_CLASS_RE.search(class_name):
        return "none"
    lowered = class_name.casefold()
    if "wizard" in lowered or "mage" in lowered or "sorcerer" in lowered:
        return "wizard"
    if "ranger" in lowered or "hunter" in lowered:
        return "ranger"
    if not class_name:
        return "none"
    return "basic"


def _catalog_by_name(db) -> dict[str, dict]:
    rows = db.execute(
        """
        SELECT object_id, name, description, properties
        FROM objects WHERE object_type='item'
        """
    ).fetchall()
    by_name = {}
    for row in rows:
        key = str(row["name"]).strip().casefold()
        if key and key not in by_name:
            by_name[key] = row
    return by_name


def _iter_owned_nodes(tree: dict):
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.get("children") or []))


def _owned_index(tree: dict) -> dict:
    """Build lookup structures for skip decisions."""
    names = set()
    archetype_ids = set()
    families_present = set()
    nodes_by_family = {family: [] for family in _FAMILY_NAME_HINTS}
    for node in _iter_owned_nodes(tree):
        if node.get("object_type") == "container" and node.get("object_id", "").startswith(
            "inventory:"
        ):
            # Root inventory container — not an owned gear copy.
            continue
        name = str(node.get("name") or "").strip()
        if not name:
            continue
        names.add(name.casefold())
        props = node.get("object_properties") or {}
        arch = props.get("catalog_item_id") or node.get("archetype_id")
        if arch:
            archetype_ids.add(arch)
        lowered = name.casefold()
        for family, hints in _FAMILY_NAME_HINTS.items():
            if any(hint in lowered for hint in hints):
                families_present.add(family)
                nodes_by_family[family].append(node)
                break
    return {
        "names": names,
        "archetype_ids": archetype_ids,
        "families": families_present,
        "nodes_by_family": nodes_by_family,
    }


def _family_covered(index: dict, family: str, catalog_name: str, archetype_id: str) -> bool:
    if archetype_id in index["archetype_ids"]:
        return True
    if catalog_name.strip().casefold() in index["names"]:
        return True
    if family in index["families"]:
        return True
    return False


def _spawn_container_from_archetype(db, archetype) -> str:
    instance_id = f"{CONTAINER_INSTANCE_PREFIX}{uuid.uuid4().hex}"
    db.execute(
        """
        INSERT INTO objects (
            object_id, name, object_type, geography_kind, description, properties
        ) VALUES (?, ?, 'container', 'unspecified', '', ?)
        """,
        (
            instance_id,
            archetype["name"],
            json.dumps({"catalog_item_id": archetype["object_id"]}, ensure_ascii=False),
        ),
    )
    db.execute(
        """
        INSERT INTO object_links (
            relation, from_object_id, to_object_id, properties, notes
        ) VALUES ('instance_of', ?, ?, '{}', '')
        """,
        (instance_id, archetype["object_id"]),
    )
    return instance_id


def _parent_for_family(index: dict, parent_family: str | None, root_id: str) -> str | None:
    if not parent_family:
        return root_id
    nodes = index["nodes_by_family"].get(parent_family) or []
    for node in nodes:
        if node.get("object_type") == "container" or (
            node.get("object_id") or ""
        ).startswith(CONTAINER_INSTANCE_PREFIX):
            return node["object_id"]
    # Fall back to root if pack/quiver was skipped or not a container yet.
    return root_id


def generate_character_inventory(
    db,
    character_id: int,
    *,
    kit: str | None = None,
    commit: bool = True,
    ensure_catalog: bool = True,
) -> dict:
    """Add missing kit gear for one character. Returns a summary dict.

    Never removes items. Skips entries when an equivalent (same archetype, same
    name, or family stand-in) is already owned — so a skirt blocks Traveler's
    Clothes, a lunar scepter blocks Arcane Focus, etc.
    """
    row = db.execute(
        "SELECT id, name, data, campaign_role FROM characters WHERE id=?",
        (character_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown character {character_id}.")
    try:
        data = json.loads(row["data"])
    except (TypeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    if ensure_catalog:
        ensure_5e_equipment_archetypes(db, commit=False)

    kit_id = kit or choose_kit_for_character(
        data, campaign_role=row["campaign_role"] or ""
    )
    if kit_id not in KITS:
        raise ValueError(f"Unknown inventory kit {kit_id!r}.")
    specs = KITS[kit_id]
    summary = {
        "character_id": character_id,
        "character": row["name"],
        "kit": kit_id,
        "added": [],
        "skipped": [],
        "missing_catalog": [],
    }
    if not specs:
        if commit:
            db.commit()
        return summary

    catalog = _catalog_by_name(db)
    root_id = get_character_inventory_container(db, character_id)
    tree = get_character_inventory_tree(db, character_id)
    index = _owned_index(tree)

    # First pass: create top-level containers/items so pack contents can nest.
    ordered = sorted(specs, key=lambda spec: 0 if spec[3] is None else 1)

    for catalog_name, quantity, family, parent_family in ordered:
        archetype = catalog.get(catalog_name.casefold())
        if archetype is None:
            summary["missing_catalog"].append(catalog_name)
            continue
        archetype_id = archetype["object_id"]
        if _family_covered(index, family, catalog_name, archetype_id):
            summary["skipped"].append(
                {"name": catalog_name, "family": family, "reason": "already covered"}
            )
            continue

        parent_id = _parent_for_family(index, parent_family, root_id)
        if parent_id is None:
            summary["skipped"].append(
                {
                    "name": catalog_name,
                    "family": family,
                    "reason": f"missing parent ({parent_family})",
                }
            )
            continue

        props = json.loads(archetype["properties"] or "{}")
        is_container = "container" in (props.get("tags") or [])
        if is_container:
            instance_id = _spawn_container_from_archetype(db, archetype)
            place_owned_copy_in_container(
                db,
                parent_id,
                instance_id,
                quantity=1,
                commit=False,
            )
        else:
            instance_id = create_item_instance(db, archetype_id, commit=False)
            place_owned_copy_in_container(
                db,
                parent_id,
                instance_id,
                quantity=int(quantity),
                commit=False,
            )

        summary["added"].append(
            {
                "name": catalog_name,
                "family": family,
                "object_id": instance_id,
                "quantity": int(quantity),
                "parent": parent_id,
            }
        )
        # Refresh index so later kit rows see this addition.
        tree = get_character_inventory_tree(db, character_id)
        index = _owned_index(tree)

    if commit:
        db.commit()
    return summary


def generate_inventories_for_campaign(
    db,
    *,
    character_ids: list[int] | None = None,
    commit: bool = True,
) -> dict:
    """Run kit generation for many characters (default: all)."""
    ensure_5e_equipment_archetypes(db, commit=False)
    if character_ids is None:
        character_ids = [
            int(row["id"])
            for row in db.execute("SELECT id FROM characters ORDER BY name")
        ]
    results = []
    for character_id in character_ids:
        results.append(
            generate_character_inventory(
                db,
                character_id,
                commit=False,
                ensure_catalog=False,
            )
        )
    if commit:
        db.commit()
    return {
        "characters": len(results),
        "added": sum(len(r["added"]) for r in results),
        "skipped": sum(len(r["skipped"]) for r in results),
        "results": results,
    }

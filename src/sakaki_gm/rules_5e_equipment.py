"""Standard D&D 5e equipment archetypes for campaign bottles.

Names and mechanical summaries follow common 5e SRD-style gear. Installing
adds missing archetypes only — existing rows (including Eldenweave uniques'
former catalog names) are left alone.
"""

from __future__ import annotations

import json
import uuid

# Each entry: name, description, properties (tags + typed sets).
DEFAULT_5E_EQUIPMENT: tuple[dict, ...] = (
    # --- Weapons (simple melee) ---
    {
        "name": "Club",
        "description": "Simple melee weapon. 1d4 bludgeoning. Light.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d4 bludgeoning",
                "properties": ["light"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Dagger",
        "description": "Simple melee weapon. 1d4 piercing. Finesse, light, thrown (20/60).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d4 piercing",
                "properties": ["finesse", "light", "thrown"],
                "range": "20/60",
            },
        },
    },
    {
        "name": "Greatclub",
        "description": "Simple melee weapon. 1d8 bludgeoning. Two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d8 bludgeoning",
                "properties": ["two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Handaxe",
        "description": "Simple melee weapon. 1d6 slashing. Light, thrown (20/60).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d6 slashing",
                "properties": ["light", "thrown"],
                "range": "20/60",
            },
        },
    },
    {
        "name": "Javelin",
        "description": "Simple melee weapon. 1d6 piercing. Thrown (30/120).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d6 piercing",
                "properties": ["thrown"],
                "range": "30/120",
            },
        },
    },
    {
        "name": "Light Hammer",
        "description": "Simple melee weapon. 1d4 bludgeoning. Light, thrown (20/60).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d4 bludgeoning",
                "properties": ["light", "thrown"],
                "range": "20/60",
            },
        },
    },
    {
        "name": "Mace",
        "description": "Simple melee weapon. 1d6 bludgeoning.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d6 bludgeoning",
                "properties": [],
                "range": "melee",
            },
        },
    },
    {
        "name": "Quarterstaff",
        "description": "Simple melee weapon. 1d6 bludgeoning (versatile 1d8).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d6 bludgeoning",
                "properties": ["versatile (1d8)"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Sickle",
        "description": "Simple melee weapon. 1d4 slashing. Light.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d4 slashing",
                "properties": ["light"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Spear",
        "description": "Simple melee weapon. 1d6 piercing (versatile 1d8). Thrown (20/60).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple melee",
                "damage": "1d6 piercing",
                "properties": ["thrown", "versatile (1d8)"],
                "range": "20/60",
            },
        },
    },
    # --- Weapons (simple ranged) ---
    {
        "name": "Light Crossbow",
        "description": "Simple ranged weapon. 1d8 piercing. Ammunition (80/320), loading, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple ranged",
                "damage": "1d8 piercing",
                "properties": ["ammunition", "loading", "two-handed"],
                "range": "80/320",
            },
        },
    },
    {
        "name": "Dart",
        "description": "Simple ranged weapon. 1d4 piercing. Finesse, thrown (20/60).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple ranged",
                "damage": "1d4 piercing",
                "properties": ["finesse", "thrown"],
                "range": "20/60",
            },
        },
    },
    {
        "name": "Shortbow",
        "description": "Simple ranged weapon. 1d6 piercing. Ammunition (80/320), two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple ranged",
                "damage": "1d6 piercing",
                "properties": ["ammunition", "two-handed"],
                "range": "80/320",
            },
        },
    },
    {
        "name": "Sling",
        "description": "Simple ranged weapon. 1d4 bludgeoning. Ammunition (30/120).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "simple ranged",
                "damage": "1d4 bludgeoning",
                "properties": ["ammunition"],
                "range": "30/120",
            },
        },
    },
    # --- Weapons (martial melee) ---
    {
        "name": "Battleaxe",
        "description": "Martial melee weapon. 1d8 slashing (versatile 1d10).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 slashing",
                "properties": ["versatile (1d10)"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Flail",
        "description": "Martial melee weapon. 1d8 bludgeoning.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 bludgeoning",
                "properties": [],
                "range": "melee",
            },
        },
    },
    {
        "name": "Glaive",
        "description": "Martial melee weapon. 1d10 slashing. Heavy, reach, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d10 slashing",
                "properties": ["heavy", "reach", "two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Greataxe",
        "description": "Martial melee weapon. 1d12 slashing. Heavy, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d12 slashing",
                "properties": ["heavy", "two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Greatsword",
        "description": "Martial melee weapon. 2d6 slashing. Heavy, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "2d6 slashing",
                "properties": ["heavy", "two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Halberd",
        "description": "Martial melee weapon. 1d10 slashing. Heavy, reach, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d10 slashing",
                "properties": ["heavy", "reach", "two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Lance",
        "description": "Martial melee weapon. 1d12 piercing. Reach, special (disadvantage within 5 ft unless mounted).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d12 piercing",
                "properties": ["reach", "special"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Longsword",
        "description": "Martial melee weapon. 1d8 slashing (versatile 1d10).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 slashing",
                "properties": ["versatile (1d10)"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Maul",
        "description": "Martial melee weapon. 2d6 bludgeoning. Heavy, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "2d6 bludgeoning",
                "properties": ["heavy", "two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Morningstar",
        "description": "Martial melee weapon. 1d8 piercing.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 piercing",
                "properties": [],
                "range": "melee",
            },
        },
    },
    {
        "name": "Pike",
        "description": "Martial melee weapon. 1d10 piercing. Heavy, reach, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d10 piercing",
                "properties": ["heavy", "reach", "two-handed"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Rapier",
        "description": "Martial melee weapon. 1d8 piercing. Finesse.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 piercing",
                "properties": ["finesse"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Scimitar",
        "description": "Martial melee weapon. 1d6 slashing. Finesse, light.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d6 slashing",
                "properties": ["finesse", "light"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Shortsword",
        "description": "Martial melee weapon. 1d6 piercing. Finesse, light.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d6 piercing",
                "properties": ["finesse", "light"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Trident",
        "description": "Martial melee weapon. 1d6 piercing (versatile 1d8). Thrown (20/60).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d6 piercing",
                "properties": ["thrown", "versatile (1d8)"],
                "range": "20/60",
            },
        },
    },
    {
        "name": "War Pick",
        "description": "Martial melee weapon. 1d8 piercing.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 piercing",
                "properties": [],
                "range": "melee",
            },
        },
    },
    {
        "name": "Warhammer",
        "description": "Martial melee weapon. 1d8 bludgeoning (versatile 1d10).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d8 bludgeoning",
                "properties": ["versatile (1d10)"],
                "range": "melee",
            },
        },
    },
    {
        "name": "Whip",
        "description": "Martial melee weapon. 1d4 slashing. Finesse, reach.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial melee",
                "damage": "1d4 slashing",
                "properties": ["finesse", "reach"],
                "range": "melee",
            },
        },
    },
    # --- Weapons (martial ranged) ---
    {
        "name": "Blowgun",
        "description": "Martial ranged weapon. 1 piercing. Ammunition (25/100), loading.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial ranged",
                "damage": "1 piercing",
                "properties": ["ammunition", "loading"],
                "range": "25/100",
            },
        },
    },
    {
        "name": "Hand Crossbow",
        "description": "Martial ranged weapon. 1d6 piercing. Ammunition (30/120), light, loading.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial ranged",
                "damage": "1d6 piercing",
                "properties": ["ammunition", "light", "loading"],
                "range": "30/120",
            },
        },
    },
    {
        "name": "Heavy Crossbow",
        "description": "Martial ranged weapon. 1d10 piercing. Ammunition (100/400), heavy, loading, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial ranged",
                "damage": "1d10 piercing",
                "properties": ["ammunition", "heavy", "loading", "two-handed"],
                "range": "100/400",
            },
        },
    },
    {
        "name": "Longbow",
        "description": "Martial ranged weapon. 1d8 piercing. Ammunition (150/600), heavy, two-handed.",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial ranged",
                "damage": "1d8 piercing",
                "properties": ["ammunition", "heavy", "two-handed"],
                "range": "150/600",
            },
        },
    },
    {
        "name": "Net",
        "description": "Martial ranged weapon. Special. Thrown (5/15).",
        "properties": {
            "tags": ["weapon"],
            "weapon": {
                "category": "martial ranged",
                "damage": "special",
                "properties": ["special", "thrown"],
                "range": "5/15",
            },
        },
    },
    # --- Armor ---
    {
        "name": "Padded Armor",
        "description": "Light armor. AC 11 + Dex. Stealth disadvantage.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "light",
                "ac": "11 + Dex",
                "stealth_disadvantage": True,
                "strength": None,
            },
        },
    },
    {
        "name": "Leather Armor",
        "description": "Light armor. AC 11 + Dex.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "light",
                "ac": "11 + Dex",
                "stealth_disadvantage": False,
                "strength": None,
            },
        },
    },
    {
        "name": "Studded Leather Armor",
        "description": "Light armor. AC 12 + Dex.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "light",
                "ac": "12 + Dex",
                "stealth_disadvantage": False,
                "strength": None,
            },
        },
    },
    {
        "name": "Hide Armor",
        "description": "Medium armor. AC 12 + Dex (max 2).",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "medium",
                "ac": "12 + Dex (max 2)",
                "stealth_disadvantage": False,
                "strength": None,
            },
        },
    },
    {
        "name": "Chain Shirt",
        "description": "Medium armor. AC 13 + Dex (max 2).",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "medium",
                "ac": "13 + Dex (max 2)",
                "stealth_disadvantage": False,
                "strength": None,
            },
        },
    },
    {
        "name": "Scale Mail",
        "description": "Medium armor. AC 14 + Dex (max 2). Stealth disadvantage.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "medium",
                "ac": "14 + Dex (max 2)",
                "stealth_disadvantage": True,
                "strength": None,
            },
        },
    },
    {
        "name": "Breastplate",
        "description": "Medium armor. AC 14 + Dex (max 2).",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "medium",
                "ac": "14 + Dex (max 2)",
                "stealth_disadvantage": False,
                "strength": None,
            },
        },
    },
    {
        "name": "Half Plate",
        "description": "Medium armor. AC 15 + Dex (max 2). Stealth disadvantage.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "medium",
                "ac": "15 + Dex (max 2)",
                "stealth_disadvantage": True,
                "strength": None,
            },
        },
    },
    {
        "name": "Ring Mail",
        "description": "Heavy armor. AC 14. Stealth disadvantage.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "heavy",
                "ac": "14",
                "stealth_disadvantage": True,
                "strength": None,
            },
        },
    },
    {
        "name": "Chain Mail",
        "description": "Heavy armor. AC 16. Stealth disadvantage. Strength 13.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "heavy",
                "ac": "16",
                "stealth_disadvantage": True,
                "strength": 13,
            },
        },
    },
    {
        "name": "Splint Armor",
        "description": "Heavy armor. AC 17. Stealth disadvantage. Strength 15.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "heavy",
                "ac": "17",
                "stealth_disadvantage": True,
                "strength": 15,
            },
        },
    },
    {
        "name": "Plate Armor",
        "description": "Heavy armor. AC 18. Stealth disadvantage. Strength 15.",
        "properties": {
            "tags": ["armor"],
            "armor": {
                "category": "heavy",
                "ac": "18",
                "stealth_disadvantage": True,
                "strength": 15,
            },
        },
    },
    {
        "name": "Shield",
        "description": "Shield. +2 AC while wielded.",
        "properties": {
            "tags": ["armor", "shield"],
            "armor": {
                "category": "shield",
                "ac": "+2",
                "stealth_disadvantage": False,
                "strength": None,
            },
        },
    },
    # --- Adventuring gear / ammo / food / containers ---
    {
        "name": "Arrow",
        "description": "Ammunition for shortbows and longbows.",
        "properties": {"tags": ["ammunition"]},
    },
    {
        "name": "Crossbow Bolt",
        "description": "Ammunition for crossbows.",
        "properties": {"tags": ["ammunition"]},
    },
    {
        "name": "Sling Bullet",
        "description": "Ammunition for slings.",
        "properties": {"tags": ["ammunition"]},
    },
    {
        "name": "Blowgun Needle",
        "description": "Ammunition for blowguns.",
        "properties": {"tags": ["ammunition"]},
    },
    {
        "name": "Backpack",
        "description": "A leather pack with straps. Holds up to 1 cubic foot / 30 lb of gear.",
        "properties": {
            "tags": ["container"],
            "container": {"capacity": "1 cu ft / 30 lb", "role": "pack"},
        },
    },
    {
        "name": "Pouch",
        "description": "A cloth or leather pouch. Holds 1/5 cubic foot / 6 lb.",
        "properties": {
            "tags": ["container"],
            "container": {"capacity": "1/5 cu ft / 6 lb", "role": "pouch"},
        },
    },
    {
        "name": "Quiver",
        "description": "Holds up to 20 arrows.",
        "properties": {
            "tags": ["container"],
            "container": {"capacity": "20 arrows", "role": "quiver"},
        },
    },
    {
        "name": "Chest",
        "description": "A wooden chest with a lid. Holds 12 cubic feet / 300 lb.",
        "properties": {
            "tags": ["container"],
            "container": {"capacity": "12 cu ft / 300 lb", "role": "chest"},
        },
    },
    {
        "name": "Waterskin",
        "description": "Holds 4 pints of liquid.",
        "properties": {
            "tags": ["container"],
            "container": {"capacity": "4 pints", "role": "waterskin"},
        },
    },
    {
        "name": "Rations",
        "description": "One day's trail rations (dried meat, hardtack, dried fruit).",
        "properties": {
            "tags": ["food"],
            "food": {"portions": 1, "effect": "sustains one person for one day"},
        },
    },
    {
        "name": "Torch",
        "description": "Burns for 1 hour. Bright light 20 ft, dim light +20 ft.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Rope, Hempen (50 feet)",
        "description": "Has 2 hit points and can be burst with a DC 17 Strength check.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Rope, Silk (50 feet)",
        "description": "Has 2 hit points and can be burst with a DC 17 Strength check.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Crowbar",
        "description": "Advantage on Strength checks where leverage applies.",
        "properties": {"tags": ["gear", "tool"]},
    },
    {
        "name": "Hammer",
        "description": "A common hammer.",
        "properties": {"tags": ["gear", "tool"]},
    },
    {
        "name": "Pitons (10)",
        "description": "Iron spikes for climbing or securing rope.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Lantern, Hooded",
        "description": "Burns oil for 6 hours. Bright light 30 ft, dim +30 ft; hood reduces to dim 5 ft.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Oil (flask)",
        "description": "As an action, splash or coat; burns for 2 rounds when lit (5-ft radius).",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Tinderbox",
        "description": "Flint, fire steel, and tinder. Lights a torch as an action; other fires take 1 minute.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Bedroll",
        "description": "A sleeping roll for camping.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Blanket",
        "description": "A warm blanket.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Mess Kit",
        "description": "Tin cup, plate, utensils for field meals.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Grappling Hook",
        "description": "Used with rope for climbing.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Healer's Kit",
        "description": "Ten uses. Stabilize a dying creature as an action without a Medicine check.",
        "properties": {"tags": ["gear", "tool"]},
    },
    {
        "name": "Herbalism Kit",
        "description": "Tools for identifying and using herbs, and crafting antitoxin and potions of healing.",
        "properties": {"tags": ["tool"]},
    },
    {
        "name": "Thieves' Tools",
        "description": "Picks, files, and a small mirror for picking locks and disarming traps.",
        "properties": {"tags": ["tool"]},
    },
    {
        "name": "Holy Symbol",
        "description": "A divine focus for casting cleric or paladin spells.",
        "properties": {"tags": ["focus"]},
    },
    {
        "name": "Arcane Focus",
        "description": "A crystal, orb, rod, staff, or wand used as a spellcasting focus.",
        "properties": {"tags": ["focus"]},
    },
    {
        "name": "Component Pouch",
        "description": "A watertight pouch of spell components (excludes costly ones).",
        "properties": {
            "tags": ["container", "focus"],
            "container": {"capacity": "spell components", "role": "pouch"},
        },
    },
    {
        "name": "Spellbook",
        "description": "A blank book for a wizard's prepared spells (100 pages).",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Ink (1 ounce bottle)",
        "description": "Black ink for writing.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Ink Pen",
        "description": "A writing pen.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Parchment",
        "description": "A sheet of parchment.",
        "properties": {"tags": ["gear"]},
    },
    {
        "name": "Gold Coin",
        "description": "A gold piece.",
        "properties": {"tags": ["currency"]},
    },
    {
        "name": "Silver Coin",
        "description": "A silver piece.",
        "properties": {"tags": ["currency"]},
    },
    {
        "name": "Copper Coin",
        "description": "A copper piece.",
        "properties": {"tags": ["currency"]},
    },
    {
        "name": "Traveler's Clothes",
        "description": "A costume suitable for traveling.",
        "properties": {"tags": ["gear", "clothing"]},
    },
)


def ensure_5e_equipment_archetypes(db, *, commit: bool = False) -> dict:
    """Insert missing 5e equipment archetypes. Does not overwrite existing names.

    Returns ``{"added": int, "skipped": int, "ids": [object_id, ...]}``.
    Pass ``commit=True`` when the caller will not commit (e.g. Archetypes UI).
    """
    existing = {
        row["name"].casefold(): row["object_id"]
        for row in db.execute(
            "SELECT object_id, name FROM objects WHERE object_type='item'"
        )
    }
    added_ids = []
    skipped = 0
    for entry in DEFAULT_5E_EQUIPMENT:
        name = entry["name"].strip()
        key = name.casefold()
        if key in existing:
            skipped += 1
            continue
        object_id = f"catalog-item:{uuid.uuid4().hex}"
        db.execute(
            """
            INSERT INTO objects (
                object_id, name, object_type, geography_kind, description, properties
            ) VALUES (?, ?, 'item', 'unspecified', ?, ?)
            """,
            (
                object_id,
                name,
                entry.get("description", "").strip(),
                json.dumps(entry.get("properties") or {}, ensure_ascii=False),
            ),
        )
        existing[key] = object_id
        added_ids.append(object_id)
    if commit and added_ids:
        db.commit()
    return {"added": len(added_ids), "skipped": skipped, "ids": added_ids}

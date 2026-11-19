"""Dreamtime fills missing brains from a fixture bottle, not a live campaign."""

import json

from sakaki_gm.actor_db import (
    create_actor,
    get_actor_by_name,
    get_actor_for_role,
    get_brain,
    set_affect_edge,
    update_actor,
)
from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.dreamtime import format_dreamtime_summary, run_dreamtime_pass
from sakaki_gm.quest_db import save_quest

_ACTOR_TABLES = frozenset({
    "actors",
    "brain_goals",
    "actor_traits",
    "affect_edges",
    "actor_role_bindings",
})


def _freeze(db):
    tables = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    snap = {}
    for table in tables:
        if table in _ACTOR_TABLES:
            continue
        rows = db.execute(f"SELECT * FROM {table}").fetchall()
        snap[table] = [tuple(row) for row in rows]
    return snap


def _sheet(**fields):
    data = {
        "name": fields["name"],
        "class": fields.get("class", ""),
        "race": fields.get("race", "Human"),
        "hp_current": fields.get("hp_current", 10),
        "hp_max": fields.get("hp_max", 10),
        "notes": fields.get("notes", ""),
        "alignment": fields.get("alignment", ""),
        "background": fields.get("background", ""),
        "personality": fields.get("personality", ""),
        "features": fields.get("features", ""),
    }
    for key in (
        "role",
        "role_in_campaign",
        "current_goal",
        "stat_block_status",
        "spawn_generate_name",
        "spawn_name_pattern",
    ):
        if key in fields:
            data[key] = fields[key]
    return data


def _catchup(db):
    serina = create_character(
        db,
        "Serina",
        _sheet(
            name="Serina",
            **{
                "class": "Wizard",
                "hp_current": 11,
                "hp_max": 11,
                "alignment": "Neutral Good",
                "background": "Sage",
                "notes": (
                    "**Main Quest**: Retrieve Lunara's relics – COMPLETE\n"
                    "**Next Objective**: Return to Eldenport, rest, prepare for "
                    "*Lunara's Celestial Call* on Veilnight\n"
                    'Side clue: Starfall\'s End complete.\n'
                    "Personality: Serina is curious and methodical, driven to "
                    "uncover the secrets of the world through magic. She's kind "
                    "but cautious, wary of strangers yet eager to help those in need.\n"
                    "Combat avoided through clever play\n"
                    "Background: Sage (trained in arcane lore)\n"
                ),
            },
        ),
        campaign_role="Protagonist",
    )
    talia = create_character(
        db,
        "Talia Moonsong",
        _sheet(
            name="Talia Moonsong",
            **{
                "class": "Wizard",
                "personality": "Gentle, bookish, supportive, quietly devout",
                "notes": (
                    "School of Divination\n\n"
                    "**Role at Academy**\n"
                    "- Primary researcher of shrine sigils and star charts\n"
                    "- Helps maintain the ritual chamber\n\n"
                    "**Relationship with Serina**: Close friend; they often study together.\n"
                ),
            },
        ),
        campaign_role="Ally",
    )
    lyssia = create_character(
        db,
        "Lyssia Dawnridge",
        _sheet(
            name="Lyssia Dawnridge",
            **{
                "class": "Wizard",
                "personality": "Reserved, dutiful, perfectionist",
                "notes": (
                    "School of Abjuration\n\n"
                    "**Role at Academy**\n"
                    "- Maintains and strengthens all protective wards\n\n"
                    "**Relationship with Serina**: Polite but distant; respects "
                    "Serina's accomplishments without warmth.\n"
                ),
            },
        ),
        campaign_role="NPC",
    )
    koren = create_character(
        db,
        "Koren Brightwind",
        _sheet(
            name="Koren Brightwind",
            **{
                "class": "Wizard",
                "personality": "Charming, ambitious, a little boastful",
                "notes": (
                    "School of Enchantment\n\n"
                    "**Role at Academy**\n"
                    "- Runs errands in the Moonmarket\n"
                    "- Wants his own relic quest someday\n\n"
                    "**Relationship with Serina**: Friendly but competitive.\n"
                ),
            },
        ),
        campaign_role="NPC",
    )
    create_character(
        db,
        "Elara Starveil",
        _sheet(
            name="Elara Starveil",
            **{
                "class": "Wizard",
                "role": "Head of the Academy of Starlight, Serina's mentor",
                "current_goal": "Guide the ritual.",
                "personality": "Calm, scholarly, quietly proud",
            },
        ),
        campaign_role="Ally",
    )
    create_character(
        db,
        "Shadow Serpent",
        _sheet(
            name="Shadow Serpent",
            **{
                "class": "Third Star Guardian",
                "race": "Serpent",
                "notes": (
                    "Third Star - Guardian of Subterranean Cave\n"
                    "Current Status: Appeased by Serina's moonpetal sprig and "
                    "phrase; will remain passive indefinitely.\n"
                ),
            },
        ),
        campaign_role="NPC",
    )
    create_character(
        db,
        "Starlight Wraith",
        _sheet(
            name="Starlight Wraith",
            **{
                "class": "Fourth Star Guardian",
                "race": "Wraith",
                "notes": (
                    "Current Status: Appeased by Serina's phrase and sprig; "
                    "allowed safe passage.\n"
                ),
            },
        ),
        campaign_role="NPC",
    )
    create_character(
        db,
        "Giant Constrictor Snake",
        _sheet(
            name="Giant Constrictor Snake",
            **{
                "class": "First Star Guardian",
                "race": "Snake",
                "notes": (
                    "Current campaign role: First Star Guardian. "
                    "Identity/status conflict: an older sheet describes a "
                    "different shrine guardian. Do not transfer its "
                    "safe-passage status until that identity is confirmed.\n"
                ),
            },
        ),
        campaign_role="NPC",
    )
    create_character(
        db,
        "Doremy Sweet",
        _sheet(
            name="Doremy Sweet",
            **{
                "class": "Dream Guardian",
                "race": "Youkai",
                "role_in_campaign": (
                    'Appeared once to usher Serina back from a dream, '
                    'hinting at "not your story yet."'
                ),
                "notes": (
                    "Youkai permanence, not a mortal HP death state; "
                    "non-combat dream guardian.\n"
                ),
                "features": (
                    "Youkai: cannot be killed (defeat/banish/drive off only; "
                    "death is not on the table). Gently escorts intruders "
                    "out of foreign dreamscapes.\n"
                ),
                "stat_block_status": (
                    "Design scaffold; youkai dream guardian; mechanics unassigned."
                ),
            },
        ),
        campaign_role="NPC",
    )
    scaffold = "Design scaffold; mechanics unassigned."
    create_character(
        db,
        "Voryn's Cult Minion",
        _sheet(
            name="Voryn's Cult Minion",
            stat_block_status=scaffold,
            spawn_generate_name=True,
            spawn_name_pattern="{given}",
            notes="Ordinary follower. Not an individual person.",
        ),
        campaign_role="Enemy",
    )
    create_character(
        db,
        "Voryn's Cult Fanatic Leader",
        _sheet(
            name="Voryn's Cult Fanatic Leader",
            stat_block_status=scaffold,
            notes="Ritual leader scaffold.",
        ),
        campaign_role="Enemy",
    )
    create_character(
        db,
        "Voryn's Shrine Guardian",
        _sheet(
            name="Voryn's Shrine Guardian",
            **{
                "class": "Shrine Guardian",
                "stat_block_status": scaffold,
                "spawn_generate_name": True,
                "spawn_name_pattern": "{given}",
                "notes": "A role, not a shrine that already has a name.",
            },
        ),
        campaign_role="Enemy",
    )
    create_character(
        db,
        "Voryn\u2019s Spy",
        _sheet(
            name="Voryn\u2019s Spy",
            stat_block_status=scaffold,
            notes="Spy scaffold.",
        ),
        campaign_role="Enemy",
    )
    save_quest(
        db,
        None,
        "Lunara's Celestial Call",
        "Prepare the Veilnight ritual.",
        [serina],
        status="",
    )
    save_quest(
        db,
        None,
        "Starfall's End",
        "The shrine arc is finished.",
        [],
        status="Done",
    )
    # Elara holds the closed quest; Serina's sheet still records it as done.
    elara = db.execute(
        "SELECT id FROM characters WHERE name=?",
        ("Elara Starveil",),
    ).fetchone()["id"]
    starfall = db.execute(
        "SELECT id FROM quests WHERE name=?",
        ("Starfall's End",),
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO quest_characters (quest_id, character_id) VALUES (?, ?)",
        (starfall, elara),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "CANONICAL: Serina - Character State and Homebrew Rules",
            "11/11 HP. School of Divination. Lunar rituals use the relics. "
            "These rules do not replace the sheet's next objective.",
        ),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "[OBSOLETE REFERENCE] Serina - Quest Log",
            "**Next Objective**: Betray the circle\n"
            "Personality: treacherous and cruel.\n",
        ),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "Geography of the Wood",
            "**Next Objective**: burn the forest\n",
        ),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "The Eldenweave Pantheon – Quick Reference",
            "**Lunara** – Greater Goddess of Secrets, Foresight, Moon, Stars\n"
            "Domains: Knowledge, Arcana\n"
            "Symbols: Crescent moon\n\n"
            "**Voryn** – Intermediate God of Arcane Mysteries, Ambition\n"
            "Domains: Arcana (rival faction)\n"
            "Symbols: Black star\n",
        ),
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "Voryn's Cultist Role Scaffolds",
            "Design scaffolds, not individual characters. "
            "Voryn's Cultist roles are metadata.\n",
        ),
    )
    db.executemany(
        "INSERT INTO log (timestamp, speaker, message) VALUES ('t', ?, ?)",
        [
            (
                "Player",
                "[Play] Shall we take the other students along to aid in the ritual?",
            ),
            (
                "GM",
                "Elara studies the circle. Talia knows the sigils. "
                "Lyssia should reinforce the wards. Koren can wait.",
            ),
            (
                "Player",
                "[Play] Let us proceed with those Elara suggested: "
                "Talia and Lyssia in the inner circle, "
                "Koren fetching reagents and watching the doors.",
            ),
            ("System", "State updated"),
        ],
    )
    db.execute(
        "UPDATE world SET value=? WHERE key='location'",
        ("Ritual Hall",),
    )
    db.execute(
        """
        UPDATE object_links
        SET properties=?
        WHERE relation='contains' AND to_object_id=?
        """,
        (
            json.dumps({"role": "inventory", "quantity": 9}),
            f"inventory:{serina}",
        ),
    )
    db.commit()
    return {
        "serina": serina,
        "talia": talia,
        "lyssia": lyssia,
        "koren": koren,
    }


def _goals(brain):
    return {goal["summary"]: goal["status"] for goal in brain["goals"]}


def _affect(brain, axis):
    return [edge for edge in brain["affect"] if edge["axis"] == axis]


def test_dreamtime_catchup_fills_brains_and_leaves_scene_state(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ids = _catchup(db)
    before = _freeze(db)

    result = run_dreamtime_pass(db)

    assert _freeze(db) == before
    assert get_db_location(db) == "Ritual Hall"
    serina_row = json.loads(
        db.execute(
            "SELECT data FROM characters WHERE id=?",
            (ids["serina"],),
        ).fetchone()["data"]
    )
    assert serina_row["hp_current"] == 11
    assert serina_row["hp_max"] == 11
    statuses = {
        row["name"]: row["status"]
        for row in db.execute("SELECT name, status FROM quests")
    }
    assert statuses["Lunara's Celestial Call"] == ""
    assert statuses["Starfall's End"] == "Done"
    link = db.execute(
        """
        SELECT properties FROM object_links
        WHERE relation='contains' AND to_object_id=?
        """,
        (f"inventory:{ids['serina']}",),
    ).fetchone()
    assert json.loads(link["properties"])["quantity"] == 9

    created = set(result.created)
    assert "Serina" in created
    assert "Talia Moonsong" in created
    assert "Lyssia Dawnridge" in created
    assert "Koren Brightwind" in created
    assert "Voryn's Cult" in created
    assert "Shadow Serpent" in created
    assert "Starlight Wraith" in created
    assert "Giant Constrictor Snake" in created
    assert "Doremy Sweet" in created
    assert "Elara Starveil" not in created
    assert get_actor_by_name(db, "Elara Starveil") is None
    assert get_actor_by_name(db, "Voryn's Cult Minion") is None
    assert get_actor_by_name(db, "Voryn's Shrine Guardian") is None
    assert get_actor_by_name(db, "Voryn\u2019s Spy") is None

    serina = get_brain(db, get_actor_by_name(db, "Serina")["id"])
    assert serina["provenance"] == "derived_from_history"
    assert serina["character_id"] == ids["serina"]
    assert "Derived shadow" in serina["notes"]
    assert "Celestial Call" in serina["drive"]
    assert "Driven to uncover" in serina["drive"]
    assert "11/11" not in serina["drive"]
    assert "Betray" not in serina["drive"]
    assert "burn the forest" not in serina["drive"]
    assert "11/11" not in serina["notes"]
    assert "curious" in serina["traits"]
    assert "methodical" in serina["traits"]
    assert "ritual" in serina["methods"]
    assert "avoid needless combat" in serina["methods"]
    serina_goals = _goals(serina)
    assert serina_goals["Lunara's Celestial Call"] == "active"
    assert serina_goals["Starfall's End"] == "done"
    assert serina_goals["Retrieve Lunara's relics"] == "done"
    assert all("Betray" not in summary for summary in serina_goals)
    goal_count = len(serina["goals"])

    talia = get_brain(db, get_actor_by_name(db, "Talia Moonsong")["id"])
    assert talia["character_id"] == ids["talia"]
    assert "student" in talia["traits"]
    assert "gentle" in talia["traits"]
    assert any("inner circle" in summary.casefold() for summary in _goals(talia))
    assert "sigil" in talia["drive"].casefold()
    love = _affect(talia, "love")
    assert love and love[0]["level"] == 2
    assert love[0]["to_actor_id"] == serina["id"]

    lyssia = get_brain(db, get_actor_by_name(db, "Lyssia Dawnridge")["id"])
    assert "student" in lyssia["traits"]
    assert any("wards" in summary.casefold() for summary in _goals(lyssia))
    trust = _affect(lyssia, "trust")
    assert trust and trust[0]["level"] == 1
    assert _affect(lyssia, "loyalty")[0]["level"] == 1

    koren = get_brain(db, get_actor_by_name(db, "Koren Brightwind")["id"])
    assert "student" in koren["traits"]
    assert any("reagent" in summary.casefold() for summary in _goals(koren))
    assert any("relic quest" in summary.casefold() for summary in _goals(koren))
    assert _affect(koren, "trust")[0]["level"] == 1
    assert _affect(koren, "love") == []

    cult = get_brain(db, get_actor_by_name(db, "Voryn's Cult")["id"])
    assert cult["kind"] == "org"
    assert cult["provenance"] == "derived_from_history"
    assert "arcane mysteries" in cult["drive"].casefold()
    assert "rival faction" in cult["drive"].casefold()
    assert "black star" in cult["traits"]
    assert "cult" in cult["traits"]
    assert "minion" in cult["methods"]
    assert "spy" in cult["methods"]
    assert any(
        "rival faction" in summary.casefold() for summary in _goals(cult)
    )
    cult_trust = _affect(cult, "trust")
    assert cult_trust and cult_trust[0]["level"] == -1
    assert cult_trust[0]["to_actor_id"] == serina["id"]
    assert get_actor_for_role(db, "Voryn's Cult Minion")["id"] == cult["id"]
    assert get_actor_for_role(db, "Voryn\u2019s Spy")["id"] == cult["id"]

    serpent = get_brain(db, get_actor_by_name(db, "Shadow Serpent")["id"])
    assert serpent["kind"] == "other"
    assert "guardian" in serpent["traits"]
    assert "appeased" in serpent["traits"]
    assert "third star" in serpent["drive"].casefold()
    assert "remain passive" in serpent["drive"].casefold()
    assert _goals(serpent)["Remain passive."] == "active"
    assert _affect(serpent, "trust")[0]["level"] == 1
    assert _affect(serpent, "trust")[0]["to_actor_id"] == serina["id"]

    snake = get_brain(db, get_actor_by_name(db, "Giant Constrictor Snake")["id"])
    assert snake["kind"] == "other"
    assert "appeased" not in snake["traits"]
    assert "Appeased" not in snake["drive"]
    assert "first star" in snake["drive"].casefold()
    assert snake["affect"] == []
    assert any("identity" in summary.casefold() for summary in _goals(snake))

    doremy = get_brain(db, get_actor_by_name(db, "Doremy Sweet")["id"])
    assert doremy["kind"] == "person"
    assert doremy["mortality"] == "banish_only"
    assert "youkai" in doremy["traits"]
    assert "guardian" in doremy["traits"]
    assert "design scaffold" in doremy["notes"]
    assert any("escort" in summary.casefold() for summary in _goals(doremy))

    summary = format_dreamtime_summary(result)
    assert "Serina" in summary
    assert "not changed" in summary

    again = run_dreamtime_pass(db)
    assert again.created == []
    assert again.filled == []
    assert _freeze(db) == before
    rerun = get_brain(db, serina["id"])
    assert len(rerun["goals"]) == goal_count
    assert rerun["drive"] == serina["drive"]
    assert len(get_brain(db, talia["id"])["goals"]) == len(talia["goals"])
    assert len(get_brain(db, cult["id"])["affect"]) == len(cult["affect"])

    update_actor(db, talia["id"], drive="")
    update_actor(db, serina["id"], drive="I wrote this.")
    filled = run_dreamtime_pass(db)
    assert "Talia Moonsong" in filled.filled
    assert "Serina" not in filled.filled
    assert get_brain(db, serina["id"])["drive"] == "I wrote this."
    assert get_brain(db, talia["id"])["drive"].strip()
    assert "sigil" in get_brain(db, talia["id"])["drive"].casefold()
    assert len(get_brain(db, serina["id"])["goals"]) == goal_count
    assert _freeze(db) == before
    db.close()


def test_authored_rows_stay_blank_and_are_not_duplicated(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ids = _catchup(db)
    actor_id = create_actor(
        db,
        "Serina",
        kind="person",
        character_id=ids["serina"],
        drive="",
        provenance="authored",
        traits=["ink"],
        notes="Handwritten.",
    )
    from sakaki_gm.actor_db import add_brain_goal

    add_brain_goal(db, actor_id, "Stay handwritten.", status="active", priority=3)
    other = create_actor(db, "Notebook", kind="other", provenance="authored")
    set_affect_edge(db, actor_id, "fear", 3, to_actor_id=other)
    before = get_brain(db, actor_id)

    result = run_dreamtime_pass(db)

    assert "Serina" in result.skipped_authored
    assert "Serina" not in result.created
    assert get_actor_by_name(db, "Serina")["id"] == actor_id
    brain = get_brain(db, actor_id)
    assert brain["drive"] == ""
    assert brain["traits"] == ["ink"]
    assert brain["notes"] == "Handwritten."
    assert _goals(brain) == {"Stay handwritten.": "active"}
    assert brain["affect"] == before["affect"]
    assert brain["provenance"] == "authored"
    assert get_actor_by_name(db, "Talia Moonsong") is not None
    db.close()


def test_existing_derived_cult_keeps_provenance_and_gains_only_gaps(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    _catchup(db)
    cult_id = create_actor(
        db,
        "Voryn's Cult",
        kind="org",
        drive="",
        provenance="plot_generated",
        notes="Already named.",
    )
    result = run_dreamtime_pass(db)
    assert "Voryn's Cult" in result.filled
    assert "Voryn's Cult" not in result.created
    brain = get_brain(db, cult_id)
    assert brain["provenance"] == "plot_generated"
    assert brain["notes"] == "Already named."
    assert "rival faction" in brain["drive"].casefold()
    assert get_actor_for_role(db, "Voryn's Cult Minion")["id"] == cult_id
    again = run_dreamtime_pass(db)
    assert "Voryn's Cult" not in again.filled
    assert get_brain(db, cult_id)["drive"] == brain["drive"]
    db.close()


def test_canonical_objective_wins_when_the_sheet_has_none(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_character(
        db,
        "Nia",
        _sheet(
            name="Nia",
            **{
                "class": "Wizard",
                "notes": "Personality: Nia is curious and methodical.",
            },
        ),
        campaign_role="Protagonist",
    )
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        (
            "CANONICAL: Nia",
            "**Next Objective**: Ring the bell\n",
        ),
    )
    db.commit()
    run_dreamtime_pass(db)
    brain = get_brain(db, get_actor_by_name(db, "Nia")["id"])
    assert "Ring the bell" in brain["drive"]
    assert "Derived shadow" in brain["notes"]
    db.close()


def test_empty_campaign_creates_nothing(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    result = run_dreamtime_pass(db)
    assert result.created == []
    assert result.filled == []
    assert db.execute("SELECT COUNT(*) FROM actors").fetchone()[0] == 0
    assert "No missing actor brains" in format_dreamtime_summary(result)
    db.close()


def get_db_location(db):
    return db.execute(
        "SELECT value FROM world WHERE key='location'"
    ).fetchone()["value"]

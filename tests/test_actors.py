from sakaki_gm.actor_db import (
    bind_role_to_actor,
    create_actor_for_plot,
    ensure_derived_character_actor,
    get_actor_for_role,
    get_brain,
    set_affect_edge,
    sync_goals_from_quests,
)
from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db, migrate
from sakaki_gm.quest_db import save_quest


def test_schema_creates_actor_tables(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 18
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "actors" in tables
    assert "brain_goals" in tables
    assert "actor_traits" in tables
    assert "affect_edges" in tables
    assert "actor_role_bindings" in tables
    db.close()


def test_plot_generated_actor_and_role_binding(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    actor_id = create_actor_for_plot(
        db,
        "Voryn Cult",
        kind="org",
        drive="Spread the black star",
        methods="recruit, ritual, spy",
        traits=["zealot", "secretive"],
    )
    bind_role_to_actor(db, "Voryn's Cult Minion", actor_id)
    brain = get_brain(db, actor_id)
    assert brain["provenance"] == "plot_generated"
    assert brain["kind"] == "org"
    assert "zealot" in brain["traits"]
    assert get_actor_for_role(db, "Voryn's Cult Minion")["id"] == actor_id
    db.close()


def test_derived_character_actor_syncs_quest_goals(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    char_id = create_character(
        db,
        "Serina",
        {
            "name": "Serina",
            "class": "Wizard",
            "notes": "Curious and methodical.",
        },
        campaign_role="Protagonist",
    )
    save_quest(
        db,
        None,
        "Lunara's Celestial Call",
        "Prepare the Veilnight ritual.",
        [char_id],
        status="",
    )
    save_quest(
        db,
        None,
        "Starfall's End",
        "Done shrine arc.",
        [char_id],
        status="Done",
    )
    actor_id = ensure_derived_character_actor(
        db,
        char_id,
        drive="Uncover arcane secrets; serve Lunara's path.",
        methods="ritual prep, investigation, avoid needless combat",
        traits=["curious", "methodical", "cautious"],
        confidence=0.55,
        notes="Seeded from sheet + quest log.",
    )
    brain = get_brain(db, actor_id)
    assert brain["provenance"] == "derived_from_history"
    assert brain["character_id"] == char_id
    summaries = {goal["summary"]: goal["status"] for goal in brain["goals"]}
    assert summaries["Lunara's Celestial Call"] == "active"
    assert summaries["Starfall's End"] == "done"
    # Idempotent sync
    again = sync_goals_from_quests(db, actor_id, char_id)
    assert again == []
    db.close()


def test_affect_edge_and_pantheon_mortality(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    okina_id = create_actor_for_plot(
        db,
        "Okina Matara",
        kind="pantheon",
        drive="Hidden seasons and back doors",
        mortality="unkillable",
    )
    cult_id = create_actor_for_plot(
        db,
        "Voryn's Chosen",
        kind="person",
        drive="Serve Voryn",
    )
    set_affect_edge(db, cult_id, "fear", 2, to_actor_id=okina_id)
    brain = get_brain(db, cult_id)
    assert brain["affect"][0]["axis"] == "fear"
    assert brain["affect"][0]["level"] == 2
    assert get_brain(db, okina_id)["mortality"] == "unkillable"
    db.close()


def test_actor_tables_survive_user_version_rewind(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    create_actor_for_plot(db, "Council of Eldenport", kind="political")
    db.execute("PRAGMA user_version = 19")
    db.commit()
    migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 24
    assert (
        db.execute(
            "SELECT COUNT(*) FROM actors WHERE name=?",
            ("Council of Eldenport",),
        ).fetchone()[0]
        == 1
    )
    db.close()

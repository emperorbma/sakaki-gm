"""GM-private log lines stay off player desks and player scene packs."""

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db, migrate
from sakaki_gm.gm import _recent_log, build_scene_pack
from sakaki_gm.seat_db import (
    claim_player_character,
    ensure_default_session,
    get_seat_by_role,
    set_gm_control_character,
)
from sakaki_gm.ui.log_panel import LOG_VISIBILITY_GM, LOG_VISIBILITY_TABLE


def test_migrate_marks_system_rows_gm_private(tmp_path):
    db = get_db(tmp_path / "vis.db")
    migrate(db)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 24
    cols = {row[1] for row in db.execute("PRAGMA table_info(log)").fetchall()}
    assert "visibility" in cols

    db.execute(
        "INSERT INTO log (timestamp, speaker, message, visibility) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:00", "System", "Asking GM…", LOG_VISIBILITY_GM),
    )
    db.execute(
        "INSERT INTO log (timestamp, speaker, message, visibility) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:01", "GM", "The hall is quiet.", LOG_VISIBILITY_TABLE),
    )
    db.execute(
        "INSERT INTO log (timestamp, speaker, message, visibility) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:02", "Player", "I look around.", LOG_VISIBILITY_TABLE),
    )
    db.commit()

    # Backfill path: speaker=System without explicit visibility becomes gm on migrate.
    # Fresh inserts already set visibility; assert filter behavior.
    gm_pack = _recent_log(db, 10, include_gm_private=True)
    assert any(e["speaker"] == "System" for e in gm_pack)
    player_pack = _recent_log(db, 10, include_gm_private=False)
    assert all(e["speaker"] != "System" for e in player_pack)
    assert any(e["speaker"] == "GM" for e in player_pack)
    db.close()


def test_player_scene_pack_strips_gm_private_log(tmp_path):
    db = get_db(tmp_path / "scene_vis.db")
    migrate(db)
    ensure_default_session(db)
    serina = create_character(
        db, "Serina", {"name": "Serina"}, campaign_role="Protagonist"
    )
    elara = create_character(
        db, "Elara Starveil", {"name": "Elara Starveil"}, campaign_role="Ally"
    )
    claim_player_character(db, serina, "")
    player = get_seat_by_role(db, "player")

    db.execute(
        "INSERT INTO log (timestamp, speaker, message, visibility) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:00", "System", "GM controlling Elara (temporary lease)", "gm"),
    )
    db.execute(
        "INSERT INTO log (timestamp, speaker, message, visibility) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:01", "GM", "Moonlight pools on the floor.", "table"),
    )
    db.commit()

    player_scene = build_scene_pack(db, "I wait.", seat_id=player["id"])
    assert all(
        entry.get("speaker") != "System" for entry in player_scene["recent_log"]
    )
    assert any(
        "Moonlight" in (entry.get("message") or "")
        for entry in player_scene["recent_log"]
    )

    gm = set_gm_control_character(db, elara)
    gm_scene = build_scene_pack(db, "I wait.", seat_id=gm["id"])
    assert any(entry.get("speaker") == "System" for entry in gm_scene["recent_log"])
    db.close()


def test_legacy_system_rows_backfilled_on_migrate(tmp_path):
    """v23 bottles gain visibility=gm for existing System lines."""
    import sqlite3

    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        PRAGMA user_version = 23;
        CREATE TABLE log (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            speaker TEXT,
            message TEXT
        );
        INSERT INTO log (timestamp, speaker, message)
        VALUES ('t0', 'System', 'old meta'), ('t1', 'GM', 'table line');
        """
    )
    raw.commit()
    raw.close()

    db = get_db(path)
    migrate(db)
    rows = {
        row["speaker"]: row["visibility"]
        for row in db.execute("SELECT speaker, visibility FROM log").fetchall()
    }
    assert rows["System"] == "gm"
    assert rows["GM"] == "table"
    db.close()

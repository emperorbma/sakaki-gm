from sakaki_gm.db import get_db
from sakaki_gm.llm_config import load_config, save_config
from sakaki_gm.seat_db import (
    authenticate_seat,
    ensure_default_session,
    hash_seat_password,
    seat_requires_password,
    set_seat_password,
    verify_password_hash,
)
from sakaki_gm.ui.seat_login import (
    apply_seat_choice,
    parent_is_withdrawn,
    seat_login_label,
)


def test_seat_login_labels():
    assert seat_login_label("gm") == "Game Master"
    assert seat_login_label("PLAYER") == "Player"
    assert seat_login_label("nope") == "Game Master"


def test_parent_is_withdrawn_for_first_sit_spoiler_shield():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        assert parent_is_withdrawn(root) is True
        root.deiconify()
        root.update_idletasks()
        assert parent_is_withdrawn(root) is False
    finally:
        root.destroy()


def test_apply_seat_choice_persists_desk_mode(tmp_path):
    config = tmp_path / "config.json"
    cfg = load_config(config)
    apply_seat_choice(cfg, "player")
    save_config(cfg, config)
    again = load_config(config)
    assert again["ui"]["desk_mode"] == "player"
    apply_seat_choice(again, "gm")
    save_config(again, config)
    assert load_config(config)["ui"]["desk_mode"] == "gm"


def test_open_seat_needs_no_password(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_default_session(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] >= 22
    assert seat_requires_password(db, "gm") is False
    assert seat_requires_password(db, "player") is False
    seat = authenticate_seat(db, "player", "")
    assert seat["role"] == "player"
    db.close()


def test_password_required_when_set(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    ensure_default_session(db)
    set_seat_password(db, "gm", "table-secret")
    assert seat_requires_password(db, "gm") is True
    try:
        authenticate_seat(db, "gm", "wrong")
        assert False, "expected auth failure"
    except ValueError:
        pass
    assert authenticate_seat(db, "gm", "table-secret")["role"] == "gm"
    set_seat_password(db, "gm", "")
    assert seat_requires_password(db, "gm") is False
    assert authenticate_seat(db, "gm", "")["role"] == "gm"
    db.close()


def test_password_hash_round_trip():
    stored = hash_seat_password("abc")
    assert verify_password_hash(stored, "abc") is True
    assert verify_password_hash(stored, "nope") is False
    assert verify_password_hash("", "abc") is False

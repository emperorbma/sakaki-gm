import json

from sakaki_gm.llm_config import desk_mode, load_config, save_config
from sakaki_gm.ui.desk_audience import (
    DESK_SURFACES,
    normalize_desk_mode,
    shared_is_stripped,
    surface_visible,
)


def test_normalize_and_visibility():
    assert normalize_desk_mode("PLAYER") == "player"
    assert normalize_desk_mode("nope") == "gm"
    assert surface_visible("characters", "player") is True
    assert surface_visible("actors", "player") is False
    assert surface_visible("actors", "gm") is True
    assert surface_visible("settings", "player") is False
    assert surface_visible("ask_gm", "player") is True
    assert shared_is_stripped("player") is True
    assert shared_is_stripped("gm") is False


def test_gm_only_surfaces_are_classified():
    gm_only = {
        key for key, audience in DESK_SURFACES.items() if audience == "gm_only"
    }
    assert "actors" in gm_only
    assert "settings" in gm_only
    assert "documents" in gm_only
    assert "geography" not in gm_only  # shared; knowledge-filtered for players
    assert "items" in gm_only
    assert "skills" in gm_only
    assert "spells" in gm_only
    assert "characters" not in gm_only
    assert "quests" not in gm_only
    assert "log" not in gm_only
    assert surface_visible("geography", "player") is True


def test_desk_mode_persists_in_config(tmp_path):
    config = tmp_path / "config.json"
    loaded = load_config(config)
    assert desk_mode(loaded) == "gm"
    loaded["ui"]["desk_mode"] = "player"
    save_config(loaded, config)
    again = load_config(config)
    assert again["ui"]["desk_mode"] == "player"
    assert desk_mode(again) == "player"

    config.write_text(
        json.dumps({"ui": {"desk_mode": "PLAYER", "show_gm_reply_debug": True}}),
        encoding="utf-8",
    )
    normalized = load_config(config)
    assert normalized["ui"]["desk_mode"] == "player"
    assert normalized["ui"]["show_gm_reply_debug"] is True

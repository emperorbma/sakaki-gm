import json

from sakaki_gm.llm_config import (
    active_llm_settings,
    allocate_model_id,
    get_model,
    list_model_choices,
    load_config,
    remove_model,
    resolve_ask_model,
    save_config,
    set_default_model,
    upsert_model,
)


def test_legacy_llm_migrates_into_models_list(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "openai_compatible",
                    "base_url": "http://localhost:1234/v1",
                    "model": "local-model",
                    "api_key": "secret",
                    "rag_mode": "context",
                    "enabled_ai_tools": ["search_documents"],
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert loaded["default_model_id"] == "primary"
    assert len(loaded["models"]) == 1
    entry = loaded["models"][0]
    assert entry["id"] == "primary"
    assert entry["model"] == "local-model"
    assert entry["rag_mode"] == "context"
    assert entry["enabled_ai_tools"] == ["search_documents"]
    # Deprecated mirror stays in sync for older readers.
    assert loaded["llm"]["model"] == "local-model"
    assert active_llm_settings(loaded)["model"] == "local-model"


def test_multi_model_default_and_player_cannot_override(tmp_path):
    config = tmp_path / "config.json"
    cfg = load_config(config)
    upsert_model(
        cfg,
        {
            "id": "muse",
            "label": "Muse",
            "purpose": "narration",
            "provider": "spacexai",
            "base_url": "https://api.x.ai/v1",
            "model": "grok-muse",
            "api_key": "",
            "rag_mode": "context",
            "enabled_ai_tools": [],
        },
        make_default=False,
    )
    set_default_model(cfg, "primary")
    save_config(cfg, config)
    again = load_config(config)
    assert again["default_model_id"] == "primary"
    assert {entry["id"] for entry in again["models"]} == {"primary", "muse"}
    assert list_model_choices(again)[0][0] in {"primary", "muse"}

    # GM may request a specific model id.
    assert resolve_ask_model(again, desk_mode="gm", model_id="muse")["id"] == "muse"
    # Player seat ignores overrides and always gets the configured default.
    assert (
        resolve_ask_model(again, desk_mode="player", model_id="muse")["id"]
        == "primary"
    )
    assert get_model(again)["id"] == "primary"


def test_remove_last_model_refused(tmp_path):
    cfg = load_config(tmp_path / "config.json")
    try:
        remove_model(cfg, cfg["models"][0]["id"])
        assert False, "expected ValueError"
    except ValueError:
        pass
    new_id = allocate_model_id({cfg["models"][0]["id"]})
    upsert_model(
        cfg,
        {
            "id": new_id,
            "label": "Extra",
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama",
        },
    )
    remove_model(cfg, new_id)
    assert len(cfg["models"]) == 1

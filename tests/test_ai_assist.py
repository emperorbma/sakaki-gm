from sakaki_gm.llm_client import selected_campaign_tools
from sakaki_gm.llm_config import (
    AI_TOOL_NAMES,
    ai_assist_mode,
    enabled_ai_tools,
    load_config,
    normalize_ai_assist_mode,
    normalize_enabled_ai_tools,
    resolve_ask_gm_mode,
    save_config,
)
from sakaki_gm.rag_tools import CAMPAIGN_TOOLS


def test_ai_assist_mode_defaults_and_normalizes(tmp_path):
    config = tmp_path / "config.json"
    loaded = load_config(config)
    assert ai_assist_mode(loaded) == "manager"
    assert normalize_ai_assist_mode("MUSE") == "muse"
    assert normalize_ai_assist_mode("nope") == "manager"
    loaded["ui"]["ai_assist_mode"] = "muse"
    save_config(loaded, config)
    assert ai_assist_mode(load_config(config)) == "muse"


def test_enabled_ai_tools_defaults_and_filters():
    assert normalize_enabled_ai_tools(None) == list(AI_TOOL_NAMES)
    assert normalize_enabled_ai_tools(["search_documents", "nope", "search_documents"]) == [
        "search_documents"
    ]
    settings = {"enabled_ai_tools": ["inspect_location", "search_objects"]}
    assert enabled_ai_tools(settings) == ["inspect_location", "search_objects"]
    selected = selected_campaign_tools(settings)
    names = [tool["function"]["name"] for tool in selected]
    assert names == ["search_objects", "inspect_location"]
    assert len(CAMPAIGN_TOOLS) == len(AI_TOOL_NAMES)


def test_muse_and_empty_tools_force_context_mode():
    strong = {
        "provider": "spacexai",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
        "rag_mode": "tools",
        "enabled_ai_tools": list(AI_TOOL_NAMES),
    }
    assert resolve_ask_gm_mode(strong, {}, assist_mode="muse") == "context"
    assert (
        resolve_ask_gm_mode(
            {**strong, "enabled_ai_tools": []}, {}, assist_mode="manager"
        )
        == "context"
    )
    assert resolve_ask_gm_mode(strong, {}, assist_mode="manager") == "tools"

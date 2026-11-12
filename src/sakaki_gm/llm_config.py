# llm_config.py — app config shared by the LLM settings and the campaign registry
import json
import os
from copy import deepcopy
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "sakaki-gm" / "config.json"
LEGACY_CONFIG_PATH = Path.home() / ".config" / "pygm" / "config.json"
CAMPAIGNS_DIR = Path.home() / ".local" / "share" / "sakaki-gm" / "campaigns"
LEGACY_CAMPAIGNS_DIR = Path.home() / ".local" / "share" / "pygm" / "campaigns"

PRESETS = {
    "spacexai": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
    },
    "openai_compatible": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "",
    },
}

RAG_MODES = {
    "tools": "Model-initiated tools",
    "context": "App-retrieved context",
}

# Muse = inspiration / narration; Manager = may propose state writes + tools.
AI_ASSIST_MODES = {
    "manager": "Manager (may change state)",
    "muse": "Muse (narration only)",
}

AI_TOOL_NAMES = (
    "search_documents",
    "search_objects",
    "inspect_object",
    "inspect_location",
)

AI_TOOL_LABELS = {
    "search_documents": "Search documents",
    "search_objects": "Search objects",
    "inspect_object": "Inspect object",
    "inspect_location": "Inspect location",
}

MODEL_CAPABILITIES = {
    "chat": "Chat",
    "query_decomposition": "Query decomposition",
    "tool_use": "Tool use",
    "structured_json": "Structured JSON",
    "vision": "Vision",
}

DEFAULT_CONFIG = {
    "active_campaign": None,
    "campaigns": [],
    "model_profiles": {},
    "llm": {
        "provider": "spacexai",
        "base_url": PRESETS["spacexai"]["base_url"],
        "model": PRESETS["spacexai"]["model"],
        "api_key": "",
        "rag_mode": "tools",
        "enabled_ai_tools": list(AI_TOOL_NAMES),
    },
    "ui": {
        "show_gm_reply_debug": False,
        "ai_assist_mode": "manager",
        "desk_mode": "gm",
    },
}


def load_config(path=None) -> dict:
    migrate_legacy = (
        path is None
        and not CONFIG_PATH.is_file()
        and LEGACY_CONFIG_PATH.is_file()
    )
    if migrate_legacy:
        path = LEGACY_CONFIG_PATH
    else:
        path = Path(path) if path else CONFIG_PATH
    cfg = deepcopy(DEFAULT_CONFIG)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            if "active_campaign" in loaded:
                cfg["active_campaign"] = loaded.get("active_campaign")
            campaigns = loaded.get("campaigns")
            if isinstance(campaigns, list):
                cfg["campaigns"] = [
                    {"id": str(item["id"]), "path": str(item["path"])}
                    for item in campaigns
                    if isinstance(item, dict) and item.get("id") and item.get("path")
                ]
            profiles = loaded.get("model_profiles")
            if isinstance(profiles, dict):
                cfg["model_profiles"] = {
                    key: list(dict.fromkeys(
                        capability for capability in capabilities
                        if isinstance(capability, str)
                        and capability in MODEL_CAPABILITIES
                    ))
                    for key, capabilities in profiles.items()
                    if isinstance(key, str)
                    and isinstance(capabilities, list)
                }
            llm = loaded.get("llm")
            if isinstance(llm, dict):
                for key in cfg["llm"]:
                    if key in llm and llm[key] is not None:
                        cfg["llm"][key] = llm[key]
            ui = loaded.get("ui")
            if isinstance(ui, dict):
                for key in cfg["ui"]:
                    if key in ui and ui[key] is not None:
                        cfg["ui"][key] = ui[key]
    if (
        not isinstance(cfg["llm"]["rag_mode"], str)
        or cfg["llm"]["rag_mode"] not in RAG_MODES
    ):
        cfg["llm"]["rag_mode"] = "tools"
    cfg["llm"]["enabled_ai_tools"] = normalize_enabled_ai_tools(
        cfg["llm"].get("enabled_ai_tools")
    )
    cfg["ui"]["show_gm_reply_debug"] = bool(cfg["ui"].get("show_gm_reply_debug"))
    cfg["ui"]["ai_assist_mode"] = normalize_ai_assist_mode(
        cfg["ui"].get("ai_assist_mode")
    )
    from sakaki_gm.ui.desk_audience import normalize_desk_mode

    cfg["ui"]["desk_mode"] = normalize_desk_mode(cfg["ui"].get("desk_mode"))
    if migrate_legacy:
        save_config(cfg, CONFIG_PATH)
    return cfg


def normalize_ai_assist_mode(value) -> str:
    mode = str(value or "manager").strip().lower()
    if mode not in AI_ASSIST_MODES:
        return "manager"
    return mode


def normalize_enabled_ai_tools(value) -> list[str]:
    if value is None:
        return list(AI_TOOL_NAMES)
    if not isinstance(value, list):
        return list(AI_TOOL_NAMES)
    selected = []
    for name in value:
        if isinstance(name, str) and name in AI_TOOL_NAMES and name not in selected:
            selected.append(name)
    return selected


def show_gm_reply_debug(cfg: dict | None = None) -> bool:
    """Return whether the AI reply intercept / debug panel should be shown."""
    if cfg is None:
        cfg = load_config()
    ui = cfg.get("ui") if isinstance(cfg, dict) else None
    if not isinstance(ui, dict):
        return False
    return bool(ui.get("show_gm_reply_debug"))


def ai_assist_mode(cfg: dict | None = None) -> str:
    """Return ``manager`` or ``muse`` for Ask / Run as GM."""
    if cfg is None:
        cfg = load_config()
    ui = cfg.get("ui") if isinstance(cfg, dict) else None
    if not isinstance(ui, dict):
        return "manager"
    return normalize_ai_assist_mode(ui.get("ai_assist_mode"))


def enabled_ai_tools(settings: dict | None = None) -> list[str]:
    """Return which campaign RAG tools the model may call this turn."""
    if not isinstance(settings, dict):
        return list(AI_TOOL_NAMES)
    return normalize_enabled_ai_tools(settings.get("enabled_ai_tools"))


def desk_mode(cfg: dict | None = None) -> str:
    """Return the desk audience mode: ``gm`` or ``player``."""
    from sakaki_gm.ui.desk_audience import desk_mode_from_config

    if cfg is None:
        cfg = load_config()
    return desk_mode_from_config(cfg)


def model_profile_key(settings: dict) -> str:
    """Return a stable key for capabilities assigned to a model endpoint."""
    return json.dumps(
        [
            str(settings.get("provider") or "").strip().casefold(),
            str(settings.get("base_url") or "").strip().rstrip("/"),
            str(settings.get("model") or "").strip(),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def save_config(cfg: dict, path=None) -> None:
    path = Path(path) if path else CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def resolve_api_key(llm: dict) -> str:
    key = str(llm.get("api_key") or "").strip()
    if key:
        return key
    env = os.environ.get("XAI_API_KEY", "").strip()
    if env:
        return env
    if llm.get("provider") == "openai_compatible":
        return "local"
    raise RuntimeError("Set XAI_API_KEY or enter an API key in Settings.")


def model_capabilities(settings: dict, profiles: dict | None = None) -> list[str]:
    """Return declared/probed capability labels for this provider/endpoint/model."""
    if profiles is None:
        profiles = load_config().get("model_profiles", {})
    if not isinstance(profiles, dict):
        return []
    raw = profiles.get(model_profile_key(settings), [])
    if not isinstance(raw, list):
        return []
    return [
        capability for capability in raw
        if isinstance(capability, str) and capability in MODEL_CAPABILITIES
    ]


def resolve_ask_gm_mode(
    settings: dict,
    profiles: dict | None = None,
    *,
    assist_mode: str | None = None,
) -> str:
    """Choose tools vs app-retrieved context from settings and capability labels.

    Local OpenAI-compatible endpoints stay on app-retrieved context unless both
    tool_use and structured_json are labeled for that model. SpaceXAI/Grok may
    use tools when the user selected that mode and either has no profile yet or
    has both required labels. Missing labels never invent capability.

    Muse assist mode and an empty enabled-tool list always force context (no
    model-initiated RAG tools).
    """
    if normalize_ai_assist_mode(assist_mode) == "muse":
        return "context"
    if not enabled_ai_tools(settings):
        return "context"

    requested = settings.get("rag_mode", "tools")
    if requested not in RAG_MODES:
        requested = "tools"
    if requested != "tools":
        return "context"

    caps = set(model_capabilities(settings, profiles))
    provider = str(settings.get("provider") or "").strip().casefold()
    required = {"tool_use", "structured_json"}

    if provider == "openai_compatible":
        # Weak/local default: app retrieves unless this exact model was labeled.
        if required <= caps:
            return "tools"
        return "context"

    # Strong providers (e.g. spacexai): honor tools when unlabeled (user choice)
    # or when both tool labels are present; otherwise fall back to context.
    if not caps or required <= caps:
        return "tools"
    return "context"

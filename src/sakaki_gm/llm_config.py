# llm_config.py — app config shared by the LLM settings and the campaign registry
import json
import os
import secrets
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

# Connection + per-model RAG settings (shape passed to complete / resolve_api_key).
MODEL_SETTING_KEYS = (
    "provider",
    "base_url",
    "model",
    "api_key",
    "rag_mode",
    "enabled_ai_tools",
)

DEFAULT_MODEL_ID = "primary"


def _default_model_entry() -> dict:
    return {
        "id": DEFAULT_MODEL_ID,
        "label": "Default",
        "purpose": "",
        "provider": "spacexai",
        "base_url": PRESETS["spacexai"]["base_url"],
        "model": PRESETS["spacexai"]["model"],
        "api_key": "",
        "rag_mode": "tools",
        "enabled_ai_tools": list(AI_TOOL_NAMES),
    }


DEFAULT_CONFIG = {
    "active_campaign": None,
    "campaigns": [],
    "model_profiles": {},
    "default_model_id": DEFAULT_MODEL_ID,
    "models": [_default_model_entry()],
    # Deprecated: migrated into models[] on load; kept as a mirror of the
    # active model for older readers until they switch to active_llm_settings().
    "llm": None,
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
    loaded = {}
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
            ui = loaded.get("ui")
            if isinstance(ui, dict):
                for key in cfg["ui"]:
                    if key in ui and ui[key] is not None:
                        cfg["ui"][key] = ui[key]
            if "default_model_id" in loaded and loaded["default_model_id"]:
                cfg["default_model_id"] = str(loaded["default_model_id"]).strip()
            if isinstance(loaded.get("models"), list) and loaded["models"]:
                cfg["models"] = [
                    normalize_model_entry(item, fallback_index=index)
                    for index, item in enumerate(loaded["models"])
                    if isinstance(item, dict)
                ]
            elif isinstance(loaded.get("llm"), dict):
                # Deprecated single-model blob → first/default entry.
                cfg["models"] = [
                    normalize_model_entry(
                        {
                            "id": DEFAULT_MODEL_ID,
                            "label": "Default",
                            **{
                                key: loaded["llm"][key]
                                for key in MODEL_SETTING_KEYS
                                if key in loaded["llm"]
                                and loaded["llm"][key] is not None
                            },
                        },
                        fallback_index=0,
                    )
                ]
                cfg["default_model_id"] = DEFAULT_MODEL_ID

    if not cfg["models"]:
        cfg["models"] = [_default_model_entry()]
    cfg["models"] = _dedupe_model_ids(cfg["models"])
    cfg["default_model_id"] = _resolve_default_model_id(
        cfg["default_model_id"], cfg["models"]
    )
    # Deprecated mirror so leftover cfg["llm"] readers keep working.
    cfg["llm"] = model_settings_dict(get_model(cfg))
    cfg["ui"]["show_gm_reply_debug"] = bool(cfg["ui"].get("show_gm_reply_debug"))
    cfg["ui"]["ai_assist_mode"] = normalize_ai_assist_mode(
        cfg["ui"].get("ai_assist_mode")
    )
    from sakaki_gm.ui.desk_audience import normalize_desk_mode

    cfg["ui"]["desk_mode"] = normalize_desk_mode(cfg["ui"].get("desk_mode"))
    if migrate_legacy:
        save_config(cfg, CONFIG_PATH)
    return cfg


def normalize_model_entry(value, *, fallback_index: int = 0) -> dict:
    """Normalize one models[] row; unknown keys are ignored."""
    raw = value if isinstance(value, dict) else {}
    entry = _default_model_entry()
    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        model_id = (
            DEFAULT_MODEL_ID if fallback_index == 0 else f"model-{fallback_index + 1}"
        )
    entry["id"] = model_id
    label = str(raw.get("label") or "").strip()
    entry["label"] = label or (
        str(raw.get("model") or "").strip() or f"Model {fallback_index + 1}"
    )
    entry["purpose"] = str(raw.get("purpose") or "").strip()
    provider = str(raw.get("provider") or entry["provider"]).strip() or "spacexai"
    entry["provider"] = provider
    entry["base_url"] = str(raw.get("base_url") or entry["base_url"]).strip()
    entry["model"] = str(raw.get("model") or "").strip()
    entry["api_key"] = str(raw.get("api_key") or "")
    rag_mode = raw.get("rag_mode", entry["rag_mode"])
    if not isinstance(rag_mode, str) or rag_mode not in RAG_MODES:
        rag_mode = "tools"
    entry["rag_mode"] = rag_mode
    entry["enabled_ai_tools"] = normalize_enabled_ai_tools(
        raw.get("enabled_ai_tools")
    )
    return entry


def _dedupe_model_ids(models: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for index, entry in enumerate(models):
        model_id = entry["id"]
        if model_id in seen:
            model_id = allocate_model_id(seen | {entry["id"]})
            entry = {**entry, "id": model_id}
        seen.add(model_id)
        result.append(entry)
    return result


def _resolve_default_model_id(default_id, models: list[dict]) -> str:
    ids = {entry["id"] for entry in models}
    candidate = str(default_id or "").strip()
    if candidate in ids:
        return candidate
    return models[0]["id"]


def allocate_model_id(existing_ids) -> str:
    existing = set(existing_ids or ())
    for _ in range(32):
        candidate = f"m-{secrets.token_hex(3)}"
        if candidate not in existing:
            return candidate
    # Extremely unlikely fallback.
    index = 1
    while f"model-{index}" in existing:
        index += 1
    return f"model-{index}"


def model_settings_dict(entry: dict | None) -> dict:
    """Return the complete()/resolve_api_key settings shape for a model entry."""
    source = entry if isinstance(entry, dict) else _default_model_entry()
    return {
        "provider": source.get("provider") or "spacexai",
        "base_url": str(source.get("base_url") or ""),
        "model": str(source.get("model") or ""),
        "api_key": str(source.get("api_key") or ""),
        "rag_mode": (
            source.get("rag_mode")
            if source.get("rag_mode") in RAG_MODES
            else "tools"
        ),
        "enabled_ai_tools": normalize_enabled_ai_tools(
            source.get("enabled_ai_tools")
        ),
    }


def get_model(cfg: dict | None = None, model_id: str | None = None) -> dict:
    """Return a models[] entry by id, or the default when id is omitted."""
    if cfg is None:
        cfg = load_config()
    models = cfg.get("models") if isinstance(cfg, dict) else None
    if not isinstance(models, list) or not models:
        return _default_model_entry()
    if model_id is None:
        model_id = cfg.get("default_model_id")
    model_id = str(model_id or "").strip()
    for entry in models:
        if isinstance(entry, dict) and entry.get("id") == model_id:
            return normalize_model_entry(entry)
    return normalize_model_entry(models[0], fallback_index=0)


def active_llm_settings(cfg: dict | None = None) -> dict:
    """Settings dict for the default model (Ask GM / Run as GM)."""
    return model_settings_dict(get_model(cfg))


def resolve_ask_model(
    cfg: dict | None = None,
    *,
    desk_mode: str | None = None,
    model_id: str | None = None,
) -> dict:
    """Choose which model entry powers an Ask / Run-as-GM turn.

    Players never pick a model in the UI. Their Ask always uses the configured
    default (or, later, GM-manager routing from config). A human GM seat may
    pass an explicit ``model_id`` when selecting directly; otherwise the
    default is used.
    """
    from sakaki_gm.ui.desk_audience import normalize_desk_mode

    if cfg is None:
        cfg = load_config()
    role = normalize_desk_mode(desk_mode if desk_mode is not None else "gm")
    # Player seat: ignore any requested override; GM logic / config owns choice.
    if role == "player":
        return get_model(cfg)
    if model_id:
        return get_model(cfg, model_id)
    return get_model(cfg)


def model_display_label(entry: dict) -> str:
    """Human label for pickers: 'Label (model)' or id fallback."""
    if not isinstance(entry, dict):
        return "Model"
    label = str(entry.get("label") or "").strip()
    model = str(entry.get("model") or "").strip()
    model_id = str(entry.get("id") or "").strip()
    if label and model and label != model:
        return f"{label} ({model})"
    return label or model or model_id or "Model"


def list_model_choices(cfg: dict | None = None) -> list[tuple[str, str]]:
    """Return [(id, display_label), ...] for UI pickers."""
    if cfg is None:
        cfg = load_config()
    models = cfg.get("models") if isinstance(cfg, dict) else None
    if not isinstance(models, list):
        return []
    choices = []
    for entry in models:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        choices.append((str(entry["id"]), model_display_label(entry)))
    return choices


def upsert_model(cfg: dict, entry: dict, *, make_default: bool = False) -> dict:
    """Insert or replace a model entry; returns the updated config dict."""
    models = list(cfg.get("models") or [])
    normalized = normalize_model_entry(entry, fallback_index=len(models))
    replaced = False
    for index, existing in enumerate(models):
        if isinstance(existing, dict) and existing.get("id") == normalized["id"]:
            models[index] = normalized
            replaced = True
            break
    if not replaced:
        models.append(normalized)
    cfg["models"] = _dedupe_model_ids(models)
    if make_default or not cfg.get("default_model_id"):
        cfg["default_model_id"] = normalized["id"]
    else:
        cfg["default_model_id"] = _resolve_default_model_id(
            cfg.get("default_model_id"), cfg["models"]
        )
    cfg["llm"] = model_settings_dict(get_model(cfg))
    return cfg


def remove_model(cfg: dict, model_id: str) -> dict:
    """Remove a model entry; refuses to delete the last remaining model."""
    model_id = str(model_id or "").strip()
    models = [
        entry
        for entry in (cfg.get("models") or [])
        if isinstance(entry, dict) and entry.get("id") != model_id
    ]
    if not models:
        raise ValueError("At least one model configuration is required.")
    cfg["models"] = models
    cfg["default_model_id"] = _resolve_default_model_id(
        cfg.get("default_model_id"), models
    )
    cfg["llm"] = model_settings_dict(get_model(cfg))
    return cfg


def set_default_model(cfg: dict, model_id: str) -> dict:
    model_id = str(model_id or "").strip()
    ids = {
        entry.get("id")
        for entry in (cfg.get("models") or [])
        if isinstance(entry, dict)
    }
    if model_id not in ids:
        raise ValueError(f"Unknown model id {model_id!r}.")
    cfg["default_model_id"] = model_id
    cfg["llm"] = model_settings_dict(get_model(cfg))
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
    to_save = deepcopy(cfg)
    # Persist canonical multi-model schema; keep deprecated llm mirror in file
    # only as a convenience for hand-edited configs / older tools.
    if isinstance(to_save.get("models"), list) and to_save["models"]:
        to_save["models"] = [
            normalize_model_entry(item, fallback_index=index)
            for index, item in enumerate(to_save["models"])
            if isinstance(item, dict)
        ]
        to_save["default_model_id"] = _resolve_default_model_id(
            to_save.get("default_model_id"), to_save["models"]
        )
        to_save["llm"] = model_settings_dict(get_model(to_save))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
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

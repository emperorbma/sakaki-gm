from sakaki_gm.llm_client import strip_thinking_text
from sakaki_gm.llm_config import (
    chat_extra_body,
    load_config,
    model_settings_dict,
    normalize_thinking_mode,
    upsert_model,
)


def test_normalize_thinking_mode_aliases():
    assert normalize_thinking_mode(None) == "default"
    assert normalize_thinking_mode("OFF") == "off"
    assert normalize_thinking_mode("false") == "off"
    assert normalize_thinking_mode("on") == "on"
    assert normalize_thinking_mode("enabled") == "on"
    assert normalize_thinking_mode("nope") == "default"


def test_chat_extra_body_omits_default_and_sets_qwen_flags():
    assert chat_extra_body({"thinking": "default"}) == {}
    assert chat_extra_body({"thinking": "off"}) == {
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert chat_extra_body({"thinking": "on"}) == {
        "enable_thinking": True,
        "chat_template_kwargs": {"enable_thinking": True},
    }


def test_model_settings_persist_thinking(tmp_path):
    config = tmp_path / "config.json"
    cfg = load_config(config)
    upsert_model(
        cfg,
        {
            "id": "qwen-fast",
            "label": "Qwen 3.5 9B fast",
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "qwen3.5:9b",
            "thinking": "off",
        },
        make_default=True,
    )
    from sakaki_gm.llm_config import save_config

    save_config(cfg, config)
    again = load_config(config)
    settings = model_settings_dict(
        next(entry for entry in again["models"] if entry["id"] == "qwen-fast")
    )
    assert settings["thinking"] == "off"
    assert chat_extra_body(settings)["enable_thinking"] is False


def test_strip_thinking_text_removes_qwen_blocks():
    raw = (
        "<think>\nplan the scene\n</think>\n"
        '{"narration": "Rain on the slate roof.", "updates": {}, '
        '"cited_record_ids": [], "proposals": []}'
    )
    cleaned = strip_thinking_text(raw)
    assert "<think>" not in cleaned
    assert cleaned.startswith("{")


def test_complete_passes_thinking_extra_body():
    from sakaki_gm.llm_client import complete

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)

        class _Msg:
            content = '{"narration": "Hi", "updates": {}, "cited_record_ids": [], "proposals": []}'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    complete(
        {
            "base_url": "http://127.0.0.1:9/v1",
            "model": "qwen3.5:9b",
            "thinking": "off",
        },
        "system",
        "user",
        create=fake_create,
    )
    assert captured["extra_body"]["enable_thinking"] is False
    assert (
        captured["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    )

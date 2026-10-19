"""Scratch workspace and Ask GM capability-gate tests."""
import json

import pytest

from sakaki_gm.character_db import create_character
from sakaki_gm.db import get_db
from sakaki_gm.llm_client import complete
from sakaki_gm.llm_config import (
    model_capabilities,
    model_profile_key,
    resolve_ask_gm_mode,
)
from sakaki_gm.object_graph_db import create_object
from sakaki_gm.rag_tools import execute_campaign_tool, retrieve_campaign_context
from sakaki_gm.scratch_context import ScratchBudgetExceeded, ScratchContext


def test_scratch_stages_stable_record_ids_and_export_pack():
    scratch = ScratchContext(context_id="turn-1")
    doc_id = scratch.stage_document({
        "id": 42,
        "title": "Abbey lore",
        "excerpt": "Moonlight seals the vault.",
    })
    obj_id = scratch.stage_object({
        "object_id": "abbey",
        "name": "Moon Abbey",
        "object_type": "location",
        "geography_kind": "site",
        "description": "An old lunar shrine.",
    })

    assert doc_id == "document:42"
    assert obj_id == "object:abbey"
    assert scratch.record_ids() == ["document:42", "object:abbey"]
    assert scratch.get("document:42")["snapshot"]["title"] == "Abbey lore"
    assert scratch.unknown_citations(["document:42", "missing"]) == ["missing"]

    pack = scratch.export_prompt_pack()
    assert pack["context_id"] == "turn-1"
    assert pack["documents"][0]["record_id"] == "document:42"
    assert pack["objects"][0]["record_id"] == "object:abbey"
    assert pack["record_count"] == 2


def test_scratch_enforces_record_and_char_budgets():
    scratch = ScratchContext(max_records=1, max_chars=10_000)
    scratch.stage_document({"id": 1, "title": "A", "excerpt": "one"})
    with pytest.raises(ScratchBudgetExceeded):
        scratch.stage_document({"id": 2, "title": "B", "excerpt": "two"})

    tight = ScratchContext(max_records=10, max_chars=40)
    with pytest.raises(ScratchBudgetExceeded):
        tight.stage_document({
            "id": 9,
            "title": "Long",
            "excerpt": "x" * 200,
        })
    assert tight.record_ids() == []


def test_scratch_round_budget_and_context_id_check():
    scratch = ScratchContext(max_rounds=2)
    scratch.begin_round()
    scratch.begin_round()
    with pytest.raises(ScratchBudgetExceeded):
        scratch.begin_round()
    with pytest.raises(ValueError):
        scratch.require_context_id("other-id")
    scratch.require_context_id(scratch.context_id)


def test_resolve_ask_gm_mode_gates_local_vs_spacexai():
    local = {
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama-8b",
        "rag_mode": "tools",
    }
    strong = {
        "provider": "spacexai",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
        "rag_mode": "tools",
    }
    key_local = model_profile_key(local)
    key_strong = model_profile_key(strong)

    # Local defaults to app-retrieved until both tool labels exist.
    assert resolve_ask_gm_mode(local, {}) == "context"
    assert resolve_ask_gm_mode(
        local,
        {key_local: ["chat", "tool_use"]},
    ) == "context"
    assert resolve_ask_gm_mode(
        local,
        {key_local: ["tool_use", "structured_json"]},
    ) == "tools"

    # SpaceXAI honors tools when unlabeled or fully labeled.
    assert resolve_ask_gm_mode(strong, {}) == "tools"
    assert resolve_ask_gm_mode(
        strong,
        {key_strong: ["tool_use", "structured_json", "chat"]},
    ) == "tools"
    assert resolve_ask_gm_mode(
        strong,
        {key_strong: ["chat", "vision"]},
    ) == "context"

    # Explicit context mode always wins.
    assert resolve_ask_gm_mode({**local, "rag_mode": "context"}, {}) == "context"
    assert resolve_ask_gm_mode({**strong, "rag_mode": "context"}, {}) == "context"

    assert model_capabilities(local, {key_local: ["tool_use", "bogus"]}) == [
        "tool_use"
    ]


def test_retrieve_and_tools_stage_into_scratch(tmp_path):
    db = get_db(tmp_path / "campaign.db")
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Reliquary history", "The lunar reliquary was sealed beneath the abbey."),
    )
    create_object(db, "reliquary", "Lunar Reliquary", "item", "A sealed relic.")
    db.commit()

    scratch = ScratchContext(context_id="retrieve-turn")
    pack = retrieve_campaign_context(db, "lunar reliquary", scratch=scratch)
    assert pack["context_id"] == "retrieve-turn"
    assert pack["documents"][0]["record_id"] == f"document:{pack['documents'][0]['id']}"
    assert any(item["record_id"] == "object:reliquary" for item in pack["objects"])
    assert scratch.has("object:reliquary")

    tool_scratch = ScratchContext(context_id="tool-turn")
    docs = execute_campaign_tool(
        db,
        "search_documents",
        {"query": "lunar reliquary", "context_id": "tool-turn"},
        scratch=tool_scratch,
    )
    assert docs["context_id"] == "tool-turn"
    assert docs["rounds_used"] == 1
    assert docs["documents"][0]["record_id"].startswith("document:")

    with pytest.raises(ValueError):
        execute_campaign_tool(
            db,
            "search_documents",
            {"query": "lunar", "context_id": "wrong"},
            scratch=tool_scratch,
        )
    db.close()


def test_complete_local_defaults_to_app_retrieved_scratch(tmp_path):
    db_path = tmp_path / "campaign.db"
    db = get_db(db_path)
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Abbey", "The abbey stands beneath the silver moon."),
    )
    create_object(db, "abbey", "Moon Abbey", "location", "An old lunar shrine.")
    db.commit()
    db.close()
    calls = []

    class ResponseMessage:
        content = '{"narration":"Moonlight on the abbey.","updates":{}}'

    class Response:
        choices = [type("Choice", (), {"message": ResponseMessage()})()]

    def create(**kwargs):
        calls.append(kwargs)
        assert "tools" not in kwargs
        prompt = kwargs["messages"][1]["content"]
        assert "CAMPAIGN REFERENCE CONTEXT" in prompt
        assert "silver moon" in prompt
        assert '"context_id"' in prompt
        assert "record_id" in prompt
        return Response()

    reply = complete(
        {
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama-8b",
            "api_key": "",
            "rag_mode": "tools",
        },
        "system",
        "CURRENT STATE\nCharacters: []\nPLAYER ACTION\n"
        "Ask about the abbey silver moon. Do not change campaign state.",
        create=create,
        tool_db_path=db_path,
        model_profiles={},
    )

    assert "Moonlight" in reply
    assert len(calls) == 1


def test_complete_tools_path_stages_scratch_when_capable(tmp_path):
    db_path = tmp_path / "campaign.db"
    db = get_db(db_path)
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Abbey", "The abbey stands beneath the silver moon."),
    )
    db.commit()
    db.close()
    calls = []
    settings = {
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "capable-local",
        "api_key": "",
        "rag_mode": "tools",
    }
    profiles = {
        model_profile_key(settings): ["tool_use", "structured_json"],
    }

    class ResponseMessage:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class Response:
        def __init__(self, message):
            self.choices = [type("Choice", (), {"message": message})()]

    def create(**kwargs):
        calls.append(kwargs)
        assert "tools" in kwargs
        if len(calls) == 1:
            system = next(
                message for message in kwargs["messages"]
                if message["role"] == "system"
                and "scratch context_id" in message["content"]
            )
            assert "scratch context_id is" in system["content"]
            call = type("ToolCall", (), {
                "id": "lookup-1",
                "function": type("Function", (), {
                    "name": "search_documents",
                    "arguments": '{"query":"silver moon"}',
                })(),
            })()
            return Response(ResponseMessage(tool_calls=[call]))
        tool_message = next(
            message for message in kwargs["messages"]
            if message["role"] == "tool"
        )
        payload = json.loads(tool_message["content"])
        assert "context_id" in payload
        assert payload["documents"][0]["record_id"].startswith("document:")
        assert "silver moon" in tool_message["content"]
        return Response(ResponseMessage(
            '{"narration":"The abbey shines beneath the moon.","updates":{}}'
        ))

    reply = complete(
        settings,
        "system",
        "Do not change campaign state.",
        create=create,
        tool_db_path=db_path,
        model_profiles=profiles,
    )

    assert "abbey shines" in reply
    assert len(calls) == 2


def test_complete_spacexai_uses_tools_when_unlabeled(tmp_path):
    db_path = tmp_path / "campaign.db"
    db = get_db(db_path)
    create_character(db, "Serina", {"name": "Serina"})
    db.execute(
        "INSERT INTO notes (title, content) VALUES (?, ?)",
        ("Crown", "The lunar crown is silver."),
    )
    db.commit()
    db.close()
    calls = []

    class ResponseMessage:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class Response:
        def __init__(self, message):
            self.choices = [type("Choice", (), {"message": message})()]

    def create(**kwargs):
        calls.append(kwargs)
        assert "tools" in kwargs
        if len(calls) == 1:
            return Response(ResponseMessage(tool_calls=[
                type("ToolCall", (), {
                    "id": "c1",
                    "function": type("Function", (), {
                        "name": "search_documents",
                        "arguments": '{"query":"lunar crown"}',
                    })(),
                })()
            ]))
        return Response(ResponseMessage(
            '{"narration":"Silver light.","updates":{}}'
        ))

    reply = complete(
        {
            "provider": "spacexai",
            "base_url": "https://api.x.ai/v1",
            "model": "grok-4.7",
            "api_key": "test",
            "rag_mode": "tools",
        },
        "system",
        "Look up the crown. Do not change campaign state.",
        create=create,
        tool_db_path=db_path,
        model_profiles={},
    )
    assert "Silver light" in reply
    assert len(calls) == 2

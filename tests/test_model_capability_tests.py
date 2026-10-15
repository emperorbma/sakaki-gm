import json
from types import SimpleNamespace

from model_capability_tests import run_capability_tests


def make_response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)]
    )


def make_tool_call(name, arguments, call_id="probe-call"):
    function = SimpleNamespace(
        name=name,
        arguments=json.dumps(arguments),
    )
    return SimpleNamespace(id=call_id, function=function)


def valid_responses():
    return [
        make_response("READY"),
        make_response('{"status":"ready","count":3}'),
        make_response("Green"),
        make_response(json.dumps({
            "actor": "Ada",
            "action": "gives",
            "item": "silver keys",
            "quantity": 1,
            "source": "her backpack",
            "target": "Mira",
            "unresolved": [],
        })),
        make_response(tool_calls=[
            make_tool_call(
                "lookup_inventory",
                {"actor": "Ada", "item": "silver keys"},
            )
        ]),
        make_response("Ada has 3 silver keys."),
    ]


def test_capability_suite_passes_using_isolated_fixed_probes():
    pending = valid_responses()
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return pending.pop(0)

    results = run_capability_tests(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "probe-model",
        },
        create=create,
    )

    assert [result["capability"] for result in results] == [
        "chat",
        "structured_json",
        "vision",
        "query_decomposition",
        "tool_use",
    ]
    assert len(results) == 5
    assert [result["status"] for result in results] == ["PASS"] * 5
    assert pending == []
    assert "response_format" in requests[1]
    image_content = requests[2]["messages"][1]["content"]
    assert image_content[1]["type"] == "image_url"
    assert image_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert requests[4]["tools"][0]["function"]["name"] == "lookup_inventory"
    assert requests[5]["messages"][-1]["role"] == "tool"
    assert json.loads(requests[5]["messages"][-1]["content"]) == {
        "actor": "Ada",
        "item": "silver key",
        "quantity": 3,
    }


def test_capability_suite_reports_invalid_json_and_tool_arguments():
    pending = [
        make_response("READY"),
        make_response('{"status":"ready","count":"3"}'),
        make_response("Green"),
        make_response(json.dumps({
            "actor": "Ada",
            "action": "gives",
            "item": "silver key",
            "quantity": 1,
            "source": "backpack",
            "target": "Mira",
            "unresolved": [],
        })),
        make_response(tool_calls=[
            make_tool_call(
                "lookup_inventory",
                {"actor": "Mira", "item": "silver key"},
            )
        ]),
    ]

    def create(**_kwargs):
        return pending.pop(0)

    results = run_capability_tests(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "probe-model",
        },
        create=create,
    )

    assert [result["status"] for result in results] == [
        "PASS",
        "FAIL",
        "PASS",
        "PASS",
        "FAIL",
    ]
    assert "count" in results[1]["detail"]
    assert "actor='Ada'" in results[4]["detail"]
    assert len(pending) == 0


def test_decomposition_still_rejects_a_conflicting_source():
    pending = [
        make_response("READY"),
        make_response('{"status":"ready","count":3}'),
        make_response("Green"),
        make_response(json.dumps({
            "actor": "Ada",
            "action": "gives",
            "item": "silver key",
            "quantity": 1,
            "source": "pocket",
            "target": "Mira",
            "unresolved": [],
        })),
        make_response(tool_calls=[
            make_tool_call(
                "lookup_inventory",
                {"actor": "Ada", "item": "silver key"},
            )
        ]),
        make_response("Ada has 3 silver keys."),
    ]

    def create(**kwargs):
        return pending.pop(0)

    results = run_capability_tests(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "probe-model",
        },
        create=create,
    )

    assert [result["status"] for result in results] == [
        "PASS",
        "PASS",
        "PASS",
        "FAIL",
        "PASS",
    ]
    assert "source='backpack'" in results[3]["detail"]


def test_vision_probe_fails_when_center_color_is_misidentified():
    pending = [
        make_response("READY"),
        make_response('{"status":"ready","count":3}'),
        make_response("Blue"),
        make_response(json.dumps({
            "actor": "Ada",
            "action": "gives",
            "item": "silver key",
            "quantity": 1,
            "source": "backpack",
            "target": "Mira",
            "unresolved": [],
        })),
        make_response(tool_calls=[
            make_tool_call(
                "lookup_inventory",
                {"actor": "Ada", "item": "silver key"},
            )
        ]),
        make_response("Ada has 3 silver keys."),
    ]

    def create(**_kwargs):
        return pending.pop(0)

    results = run_capability_tests(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "probe-model",
        },
        create=create,
    )

    assert results[2]["capability"] == "vision"
    assert results[2]["status"] == "FAIL"
    assert "center square to be green" in results[2]["detail"]


def test_capability_suite_requires_model_and_endpoint():
    for settings, message in (
        ({"base_url": "http://localhost"}, "Select a model"),
        ({"model": "probe-model"}, "Set a base URL"),
    ):
        try:
            run_capability_tests(settings, create=lambda **_kwargs: None)
        except RuntimeError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"Expected validation for: {settings!r}")

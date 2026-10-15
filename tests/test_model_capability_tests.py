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
        "query_decomposition",
        "tool_use",
    ]
    assert [result["status"] for result in results] == ["PASS"] * 4
    assert pending == []
    assert "response_format" in requests[1]
    assert requests[3]["tools"][0]["function"]["name"] == "lookup_inventory"
    assert requests[4]["messages"][-1]["role"] == "tool"
    assert json.loads(requests[4]["messages"][-1]["content"]) == {
        "actor": "Ada",
        "item": "silver key",
        "quantity": 3,
    }


def test_capability_suite_reports_invalid_json_and_tool_arguments():
    pending = [
        make_response("READY"),
        make_response('{"status":"ready","count":"3"}'),
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
        "FAIL",
    ]
    assert "count" in results[1]["detail"]
    assert "actor='Ada'" in results[3]["detail"]
    assert len(pending) == 0


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

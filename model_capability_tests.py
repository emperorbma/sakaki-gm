"""Isolated, read-only probes for configured chat model capabilities."""
import json
import time

from llm_config import resolve_api_key


_LOOKUP_TOOL = [{
    "type": "function",
    "function": {
        "name": "lookup_inventory",
        "description": (
            "Look up the quantity of one named item held by one named character."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "item": {"type": "string"},
            },
            "required": ["actor", "item"],
            "additionalProperties": False,
        },
    },
}]


def run_capability_tests(settings: dict, *, create=None) -> list[dict]:
    """Run independent probes; the tool-use probe uses a fixed in-memory result."""
    model = str(settings.get("model") or "").strip()
    base_url = str(settings.get("base_url") or "").strip()
    if not model:
        raise RuntimeError("Select a model before testing capabilities.")
    if not base_url:
        raise RuntimeError("Set a base URL before testing capabilities.")
    if create is None:
        create = _create_function(settings)

    return [
        _run_probe("chat", lambda: _test_chat(create, model)),
        _run_probe("structured_json", lambda: _test_structured_json(create, model)),
        _run_probe(
            "query_decomposition",
            lambda: _test_query_decomposition(create, model),
        ),
        _run_probe("tool_use", lambda: _test_tool_use(create, model)),
    ]


def _run_probe(capability: str, probe) -> dict:
    started = time.monotonic()
    try:
        detail = probe()
    except Exception as exc:
        return {
            "capability": capability,
            "status": "FAIL",
            "detail": str(exc) or type(exc).__name__,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
    return {
        "capability": capability,
        "status": "PASS",
        "detail": detail,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


def _create_function(settings: dict):
    from openai import OpenAI

    client = OpenAI(
        api_key=resolve_api_key(settings),
        base_url=settings["base_url"],
        timeout=90,
    )

    def create(**kwargs):
        return client.chat.completions.create(**kwargs)

    return create


def _request(create, model: str, messages: list[dict], **options):
    return create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=160,
        **options,
    )


def _message(response):
    try:
        return response.choices[0].message
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("The model returned no message.") from exc


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _content(message) -> str:
    value = _field(message, "content")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [
            text for text in (
                _field(item, "text", "") for item in value
            )
            if isinstance(text, str) and text
        ]
        return "\n".join(parts).strip()
    return ""


def _test_chat(create, model: str) -> str:
    response = _request(
        create,
        model,
        [
            {
                "role": "system",
                "content": "Follow the user's exact instruction. Do not add anything.",
            },
            {"role": "user", "content": "Reply with exactly: READY"},
        ],
    )
    answer = _content(_message(response))
    if answer != "READY":
        raise RuntimeError(f"Expected exactly READY; received {answer[:240]!r}.")
    return "Followed the exact-response chat probe."


def _test_structured_json(create, model: str) -> str:
    response = _request(
        create,
        model,
        [
            {
                "role": "system",
                "content": (
                    "Return one JSON object only, with exactly the keys "
                    '"status" and "count".'
                ),
            },
            {
                "role": "user",
                "content": (
                    'Return {"status":"ready","count":3}. Use a JSON string '
                    "for status and a JSON number for count."
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    raw = _content(_message(response))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Response was not JSON: {raw[:240]!r}.") from exc
    if parsed != {"status": "ready", "count": 3}:
        raise RuntimeError(f"Unexpected JSON object: {parsed!r}.")
    return "Returned the expected JSON object with correct field types."


def _test_query_decomposition(create, model: str) -> str:
    response = _request(
        create,
        model,
        [
            {
                "role": "system",
                "content": (
                    "Extract only facts stated in the user's sentence. Do not "
                    "adjudicate or add unstated details. Return one JSON object "
                    "with string fields actor, action, item, source, target; "
                    "integer quantity; and an array unresolved. Use an empty "
                    "string for any unstated string field."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Ada gives Mira one silver key from her backpack. "
                    "The other two keys remain with Ada."
                ),
            },
        ],
    )
    raw = _content(_message(response))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse the decomposition JSON: {raw[:240]!r}."
        ) from exc
    expected = {
        "actor": "Ada",
        "action": "gives",
        "item": "silver key",
        "quantity": 1,
        "source": "backpack",
        "target": "Mira",
        "unresolved": [],
    }
    if not isinstance(parsed, dict):
        raise RuntimeError("Decomposition response was not a JSON object.")
    for field, wanted in expected.items():
        actual = parsed.get(field)
        if field == "quantity":
            matches = actual == wanted and not isinstance(actual, bool)
        elif field == "unresolved":
            matches = actual == wanted
        else:
            matches = (
                isinstance(actual, str)
                and actual.strip().casefold() == wanted.casefold()
            )
        if not matches:
            raise RuntimeError(
                f"Expected {field}={wanted!r}; received {actual!r}."
            )
    return "Extracted all explicit action fields without unresolved details."


def _test_tool_use(create, model: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Use the provided lookup_inventory tool to answer the question. "
                "Do not guess. After receiving its result, answer in one short "
                "sentence and do not call another tool."
            ),
        },
        {
            "role": "user",
            "content": "How many silver keys does Ada have?",
        },
    ]
    response = _request(create, model, messages, tools=_LOOKUP_TOOL)
    message = _message(response)
    tool_calls = _field(message, "tool_calls", []) or []
    if len(tool_calls) != 1:
        raise RuntimeError(
            f"Expected one lookup tool call; received {len(tool_calls)}."
        )
    call = tool_calls[0]
    call_id = _field(call, "id")
    function = _field(call, "function")
    name = _field(function, "name")
    if name != "lookup_inventory":
        raise RuntimeError(f"Selected unexpected tool {name!r}.")
    raw_arguments = _field(function, "arguments", "")
    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"Tool arguments were not valid JSON: {raw_arguments!r}."
        ) from exc
    if not isinstance(arguments, dict):
        raise RuntimeError("Tool arguments were not a JSON object.")
    if arguments.get("actor", "").strip().casefold() != "ada":
        raise RuntimeError(
            f"Expected actor='Ada'; received {arguments.get('actor')!r}."
        )
    if arguments.get("item", "").strip().casefold() != "silver key":
        raise RuntimeError(
            f"Expected item='silver key'; received {arguments.get('item')!r}."
        )

    messages.extend([
        {
            "role": "assistant",
            "content": _field(message, "content"),
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps({
                "actor": "Ada",
                "item": "silver key",
                "quantity": 3,
            }),
        },
    ])
    final_message = _message(_request(
        create,
        model,
        messages,
        tools=_LOOKUP_TOOL,
    ))
    if _field(final_message, "tool_calls", []) or []:
        raise RuntimeError("Model requested another tool after receiving the result.")
    answer = _content(final_message).casefold()
    if "silver key" not in answer or "3" not in answer:
        raise RuntimeError(
            "Final answer did not correctly use the sandbox result: "
            f"{answer[:240]!r}."
        )
    return "Called the sandbox lookup with correct arguments and used its result."

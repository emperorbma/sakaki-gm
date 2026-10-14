# llm_client.py — OpenAI-compatible chat for SpaceXAI and local servers
import json
import urllib.error
import urllib.request

from llm_config import resolve_api_key


def complete(settings: dict, system: str, user: str, *, create=None) -> str:
    model = str(settings.get("model") or "").strip()
    base_url = str(settings.get("base_url") or "").strip()
    if not model:
        raise RuntimeError("Set a model in Settings.")
    if not base_url:
        raise RuntimeError("Set a base URL in Settings.")
    if create is None:
        create = _openai_create(settings)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        response = create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = create(model=model, messages=messages)
    return _message_text(response)


def list_models(settings: dict, *, opener=None) -> list[str]:
    base_url = str(settings.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("Set a base URL in Settings.")
    key = resolve_api_key(settings)
    url = base_url + "/models"
    request = urllib.request.Request(url)
    if key:
        request.add_header("Authorization", "Bearer " + key)
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected model list from {url}.")
    entries = payload.get("data", payload.get("models"))
    if not isinstance(entries, list):
        raise RuntimeError(f"No model list was returned by {url}.")
    models = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("id") or entry.get("name") or entry.get("model")
        if isinstance(name, str) and name.strip() and name.strip() not in models:
            models.append(name.strip())
    return sorted(models, key=str.casefold)


def test_connection(settings: dict, *, opener=None) -> str:
    base_url = str(settings.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("Set a base URL in Settings.")
    key = resolve_api_key(settings)
    url = base_url + "/models"
    request = urllib.request.Request(url)
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=20) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc
    try:
        payload = json.loads(body)
        count = len(payload.get("data", [])) if isinstance(payload, dict) else 0
    except json.JSONDecodeError:
        count = 0
    return f"HTTP {status} from {url} ({count} models listed)"


def _openai_create(settings: dict):
    from openai import OpenAI

    client = OpenAI(api_key=resolve_api_key(settings), base_url=settings["base_url"])

    def create(**kwargs):
        return client.chat.completions.create(**kwargs)

    return create


def _message_text(response) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("The model returned no message.") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", "")
            if text:
                parts.append(text)
        return "\n".join(parts)
    return "" if content is None else str(content)

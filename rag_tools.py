"""Read-only campaign document and object-graph lookup for GM tool calls."""
import json
import re


_CONTEXT_STOP_WORDS = {
    "a", "about", "after", "all", "also", "an", "and", "answer", "answering",
    "any", "are", "as", "at", "be", "before", "by", "can", "campaign",
    "change", "conditions", "could", "do", "does", "exact", "for", "from",
    "full", "four", "get", "give", "how", "in", "inspect", "is", "it",
    "items", "lookup", "me", "must", "of", "on", "or", "play", "please",
    "search", "separate", "show", "state", "tell", "that", "the", "their",
    "then", "there", "these", "this", "those", "to", "tools", "use", "was",
    "what", "when", "where", "which", "while", "who", "why", "with", "would",
    "you", "benefit", "conditional",
}


CAMPAIGN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search campaign reference documents for lore, rules, history, "
                "and other written facts. Use this when a question depends on "
                "campaign-specific information not already in the current state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Distinctive names and terms to search for.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_objects",
            "description": (
                "Search named campaign objects such as characters, items, "
                "locations, containers, and other graph entities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Object names or descriptive terms to search.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_object",
            "description": (
                "Get one campaign object's description and its direct incoming "
                "and outgoing graph relationships. Use an exact object ID from "
                "search_objects or current state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "object_id": {
                        "type": "string",
                        "description": "The exact campaign object ID.",
                    },
                },
                "required": ["object_id"],
                "additionalProperties": False,
            },
        },
    },
]


def retrieve_campaign_context(db, query: str, document_limit=4, object_limit=8) -> dict:
    terms = list(dict.fromkeys(
        term for term in re.findall(r"[\w'-]+", query.casefold())
        if term not in _CONTEXT_STOP_WORDS
    ))
    if not terms:
        raise ValueError("The request does not contain searchable campaign terms.")
    search_query = " ".join(terms[:12])
    return {
        "documents": search_documents(db, search_query, document_limit),
        "objects": search_objects(db, search_query, object_limit),
    }


def execute_campaign_tool(db, name: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be a JSON object.")
    if name == "search_documents":
        return {"documents": search_documents(
            db, arguments.get("query"), arguments.get("limit", 5)
        )}
    if name == "search_objects":
        return {"objects": search_objects(
            db, arguments.get("query"), arguments.get("limit", 5)
        )}
    if name == "inspect_object":
        return inspect_object(db, arguments.get("object_id"))
    raise ValueError(f"Unknown campaign lookup tool {name!r}.")


def search_documents(db, query, limit=5) -> list[dict]:
    terms = _query_terms(query)
    limit = _limit(limit)
    results = []
    for row in db.execute("SELECT id, title, content FROM notes"):
        title = row["title"] or ""
        if title.casefold().startswith("[obsolete reference]"):
            continue
        content = row["content"] or ""
        score = _score(terms, f"{title}\n{content}")
        if not score:
            continue
        results.append({
            "id": row["id"],
            "title": title,
            "excerpt": _excerpt(content, terms),
            "_score": score,
        })
    results.sort(key=lambda item: (-item["_score"], item["title"].casefold(), item["id"]))
    return [{key: value for key, value in row.items() if key != "_score"}
            for row in results[:limit]]


def search_objects(db, query, limit=5) -> list[dict]:
    terms = _query_terms(query)
    limit = _limit(limit)
    results = []
    for row in db.execute(
        """
        SELECT object_id, name, object_type, geography_kind, description,
               properties
        FROM objects
        """
    ):
        description = row["description"] or ""
        score = _score(
            terms,
            " ".join((
                row["name"] or "",
                row["object_type"] or "",
                row["geography_kind"] or "",
                description,
            )),
        )
        if not score:
            continue
        results.append({
            "object_id": row["object_id"],
            "name": row["name"],
            "object_type": row["object_type"],
            "geography_kind": row["geography_kind"],
            "description": description[:1200],
            "properties": json.loads(row["properties"]),
            "_score": score,
        })
    results.sort(
        key=lambda item: (
            -item["_score"],
            item["name"].casefold(),
            item["object_id"],
        )
    )
    return [{key: value for key, value in row.items() if key != "_score"}
            for row in results[:limit]]


def inspect_object(db, object_id) -> dict:
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError("object_id must be a non-empty string.")
    object_id = object_id.strip()
    row = db.execute(
        """
        SELECT object_id, name, object_type, geography_kind, description,
               properties
        FROM objects WHERE object_id=?
        """,
        (object_id,),
    ).fetchone()
    if row is None:
        return {"error": f"No campaign object found for ID {object_id!r}."}
    result = {
        "object": {
            key: row[key]
            for key in (
                "object_id", "name", "object_type", "geography_kind",
                "description",
            )
        },
        "properties": json.loads(row["properties"]),
        "relationships": [],
    }
    for link in db.execute(
        """
        SELECT link.relation, link.from_object_id, source.name AS from_name,
               link.to_object_id, target.name AS to_name, link.properties,
               link.notes
        FROM object_links AS link
        JOIN objects AS source ON source.object_id=link.from_object_id
        JOIN objects AS target ON target.object_id=link.to_object_id
        WHERE link.from_object_id=? OR link.to_object_id=?
        ORDER BY link.id LIMIT 40
        """,
        (object_id, object_id),
    ):
        relationship = dict(link)
        relationship["properties"] = json.loads(relationship["properties"])
        result["relationships"].append(relationship)
    return result


def _query_terms(query) -> list[str]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must be a non-empty string.")
    terms = list(dict.fromkeys(re.findall(r"[\w'-]+", query.casefold())))
    if not terms:
        raise ValueError("Search query must contain searchable terms.")
    return terms[:24]


def _score(terms: list[str], text: str) -> int:
    tokens = re.findall(r"[\w'-]+", text.casefold())
    return sum(tokens.count(term) for term in terms)


def _excerpt(content: str, terms: list[str]) -> str:
    lines = content.splitlines()
    scored = [
        (sum(term in line.casefold() for term in terms), index, line.strip())
        for index, line in enumerate(lines)
    ]
    matches = [
        line for score, _index, line in sorted(
            (entry for entry in scored if entry[0]),
            key=lambda entry: (-entry[0], entry[1]),
        )[:3]
    ]
    if matches:
        return "\n".join(matches)[:1200]
    return content.strip()[:1200]


def _limit(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer between 1 and 8.")
    if not 1 <= value <= 8:
        raise ValueError("limit must be an integer between 1 and 8.")
    return value

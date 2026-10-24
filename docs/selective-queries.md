# Sakaki selective queries

Status: implemented for Ask GM scene packs. Sakaki stays the campaign retriever. Ask GM calls a model that only generates. Do not add a vector index, Haystack, or LanceDB inside this app. Do not send the same notes to a remote RAG.

Repo: `/home/emperorbma/sakaki-gm`. Campaign data is one SQLite bottle per campaign. The live local preset is OpenAI-compatible at `http://127.0.0.1:11434/v1`, model `llama3.1:8b`, context 8192. One completion per Ask GM turn.

## The turn

`build_messages` in `src/sakaki_gm/gm.py` should load a scene, not the bottle.

Always include:

- Campaign instructions, the world prompt when it is non-empty, and `JSON_CONTRACT`.
- The acting character's short state: hit points, armor class, and the gear that matters for this action.
- The current place, plus the routes that touch that place.
- The last few log lines.
- The player action, still prefixed with `[Play]`.

Run a lore query only when the action names something outside that pack: another character, a shrine, a relic, a note title, or an item that is not already in the scene. That query is the one in `retrieve_campaign_context` (`src/sakaki_gm/rag_tools.py`): terms from the action, at most four note excerpts and eight objects. Each excerpt is cut to about 1,200 characters. Place and item names stay exact strings. A paraphrase such as "the docks" versus "Tide's Rest" is an alias row, not an embedding.

Stable geography stays in the world prompt once. Do not resend the gazetteer, the other shrines, or every character sheet on a turn that does not name them.

## Modes

`llm.rag_mode` is `context` or `tools`. `resolve_ask_gm_mode` in `src/sakaki_gm/llm_config.py` chooses.

- Context mode is the local path. The app runs the selective reads, attaches one pack, and makes one JSON call. A local OpenAI-compatible model stays here unless that model is labeled for both `tool_use` and `structured_json`.
- Tools mode is for Grok. The first prompt leaves notes and the item catalog out. The model may call `search_documents`, `search_objects`, `inspect_object`, and `inspect_location` (`CAMPAIGN_TOOLS` in `rag_tools.py`), up to the scratch round limit. The reply is still one JSON object.

Both modes search the local SQLite file. Neither is a switch that skips Sakaki and lets a remote index search instead.

## What the code does now

Ask GM (`ui/main_window.py`, `ask_gm`) calls `build_messages(..., include_reference_material=False)`, then `complete()` in `src/sakaki_gm/llm_client.py`.

With that flag false, `build_messages` builds a **scene pack**: acting character short state (hp/ac + gear that matters), resolved place plus routes, and the last few log lines. It does not dump every character sheet, quest list, notes, or the item catalog. Full notes/catalog search stays on the lookup tools (tools mode) or on a gated lore attach (context mode).

Context mode calls `out_of_scene_lore_query` first. Only when the action names a character, object, note title, or quest outside the scene pack does it run `retrieve_campaign_context` and append `CAMPAIGN REFERENCE CONTEXT`. Otherwise it skips the lore pack.

Tools mode adds a second system message and lets the model call `CAMPAIGN_TOOLS` for full document/object/location search. That remains the path for broad queries.

Clipboard copy uses `include_reference_material=True` and still dumps the larger bottle view. Leave that path alone unless asked.

## Reply contract

Keep `JSON_CONTRACT`. The model returns one object with `narration`, `updates`, `cited_record_ids`, and `proposals`. `parse_reply` in `gm.py` accepts that object. Character keys are exact names. Hit points are `hp_current` and `hp_max`. A string in `world.events` is appended. A list replaces the log. Citation ids that are not this turn's scratch record ids are ignored. Proposals are not writes until approved.

## Leave out

- A vector index of notes the bottle already holds. Embeddings blur nearby shrines. Keyword misses are alias rows or extra search terms.
- A second retrieval pass in front of `llama3.1:8b`. Character sheets, object links, quests, the log tail, and the JSON contract are not visible to a generic RAG layer.
- A server-wide "skip RAG" flag on port 11434. That port is shared with clients that have no campaign bottle. A future "already retrieved" bit would be per request on a shared retriever, not a property of the Ollama preset or of `rag_mode`.
- Chat-completions fields that ask a remote proxy to limit or skip its own chunks. Extra keys are ignored unless that proxy was built to honor them. Point Ask GM at a generator.

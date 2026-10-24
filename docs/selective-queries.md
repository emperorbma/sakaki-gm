# Sakaki selective queries

Status: agreed, not implemented. Sakaki stays the campaign retriever. Ask GM calls a model that only generates. Do not add a vector index, Haystack, or LanceDB inside this app. Do not send the same notes to a remote RAG.

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

`build_messages` still serializes every character (sheet data, proficient skills, and carried items), every quest, the location string, the event list, and the last twelve log rows. Notes and the item catalog are omitted on that path. A line tells the model that documents can be searched with the lookup tools.

Context mode then keyword-scores the text after `PLAYER ACTION` and appends `CAMPAIGN REFERENCE CONTEXT`: up to four note excerpts and eight objects. Scoring is term count over the whole notes table and objects table (`search_documents`, `search_objects`). It does not first restrict the query to terms missing from the scene.

Tools mode adds a second system message and lets the model pick those tools. That is selective, and it is the Grok path. The local path still pays for the full character and log dump plus the keyword pack.

Clipboard copy uses `include_reference_material=True` and is much larger. Leave that path alone unless asked.

## Reply contract

Keep `JSON_CONTRACT`. The model returns one object with `narration`, `updates`, `cited_record_ids`, and `proposals`. `parse_reply` in `gm.py` accepts that object. Character keys are exact names. Hit points are `hp_current` and `hp_max`. A string in `world.events` is appended. A list replaces the log. Citation ids that are not this turn's scratch record ids are ignored. Proposals are not writes until approved.

## Leave out

- A vector index of notes the bottle already holds. Embeddings blur nearby shrines. Keyword misses are alias rows or extra search terms.
- A second retrieval pass in front of `llama3.1:8b`. Character sheets, object links, quests, the log tail, and the JSON contract are not visible to a generic RAG layer.
- A server-wide "skip RAG" flag on port 11434. That port is shared with clients that have no campaign bottle. A future "already retrieved" bit would be per request on a shared retriever, not a property of the Ollama preset or of `rag_mode`.
- Chat-completions fields that ask a remote proxy to limit or skip its own chunks. Extra keys are ignored unless that proxy was built to honor them. Point Ask GM at a generator.

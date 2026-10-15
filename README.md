# Sakaki GM

Sakaki GM is a local Game Master desk. Each campaign is its own SQLite file, one bottle per campaign. The model that answers player actions is chosen in Settings.

## License

This repository is proprietary. Copyright © 2026 Brian Angeletti. All rights reserved. Repository access for review does not grant permission to reuse, modify, or redistribute its contents. See [LICENSE](LICENSE). Third-party components remain subject to their respective licenses.

## Linux

```bash
./start-sakaki-gm.sh
```

The script creates `.venv`, installs the Python packages, and opens the window. If it stops early on Debian, install the missing package and run it again:

```bash
sudo apt install python3-venv python3-tk
```

`sakaki-gm.desktop` starts the same script without a terminal.

## Windows

Double-click `start-sakaki-gm.bat`. Python 3 must be on `PATH` as `py` or `python`. The batch file creates `.venv` next to itself and installs the packages.

## Campaigns

The first window opens `pygm_campaign.db` in this folder when the campaign list is still empty. **New** adds another campaign file and leaves the current one in place. The campaign list, **Open**, **Rename**, and **Remove** are in the header. **Remove** deletes a file only when Sakaki GM created it under `~/.local/share/sakaki-gm/campaigns/`.

On the Characters tab, **Import Files** lets you select individual Markdown, text, and CSV files to import as character sheets or reference notes. The Documents tab's **Add Files** imports selected reference files only; detected character sheets are skipped. Select multiple files in either dialog to import them together. Documents can be renamed from the Documents tab.

The **Items** tab is the campaign-wide catalog of reusable item objects. Tag an item as a container to create a fresh container object whenever it is added to an inventory. Each character record is linked to a character object, which contains an inventory container object; containers can be expanded in the character editor to view and rearrange nested contents. Quantity, attunement, and equipment location are stored on containment links. Locations and other containers can also contain objects: a place may contain bags and loose items, while a bag contains its contents. Drag catalog items into containers to add them, and drag owned contents between containers to move them. While dragging, the item is shown beside the pointer; drop near a row's top or bottom edge to reorder it among siblings, or over the middle of a container to move it inside. Drop an owned item on Remove to take it out of inventory. The old free-form Inventory and Attuned fields are no longer shown or included in the GM prompt.

The Settings tab's **Campaign RAG** selector controls how Ask GM uses campaign references. **Model-initiated tools** omits reference documents and the full item catalog from the initial turn, then lets the model search documents and named graph objects and inspect object relationships through read-only tools, including explicit character-presence links for a location. **App-retrieved context** searches documents and objects before the model request, then includes bounded matching excerpts in the prompt without requiring model tool calls. Both modes retain current character state, carried items, quests, world events, and recent log. Retrieval uses keyword matching and may return noisy or incomplete results; neither mode modifies campaign state through retrieval. A recorded `located_at` relationship is evidence of presence; no link means unknown, not absent or impossible. Encounter likelihood is a GM judgment, not inferred from a missing link. Copy Prompt remains a static prompt with reference text included and no live lookup tools.

The character editor's **Skills** tab uses campaign skill definitions loaded with the selected ruleset. Mark proficiency or expertise per character; bonuses are calculated from the governing ability score and proficiency bonus. Existing free-form skill text is preserved in character Notes during schema upgrade and can be manually reassigned.

Characters have a manually editable **Campaign role** field for labels such as party member, ally, antagonist, or other. It is not inferred from how the character was created or imported.

The **Quests** tab stores campaign quests by name, free-text status, and description. Add or remove participating characters for each quest; a character can participate in multiple quests. Quest details and linked character names are included in the GM prompt.

Use **Backup** in the campaign header to create a timestamped SQLite snapshot of the current campaign under its `backups` folder. Pending database changes are committed before copying. Backups are not added to the campaign list; use **Open** to restore one as a campaign.

The **Geography** tab is a query view over objects classified with geographic kinds such as region, settlement, area, or point. Objects remain part of a shared object graph and may have other relationships; the geography view does not constrain them to map-only links or grid/hex geometry. Entering a container through a `contains` relationship also offers an inferred exit to its parent when both objects are navigable; it is labeled as inferred rather than treated as a recorded route. Contained items do not automatically become navigable exits.

## Model

Settings has two presets:

- SpaceXAI: `https://api.x.ai/v1`, model `grok-4.7`. Leave the key blank to use `XAI_API_KEY`, or type a key in the password field.
- OpenAI-compatible: `http://127.0.0.1:11434/v1` for a local server. A key is optional.

**Test** checks the `/models` endpoint. **Ask GM** sends the action as a `[Play]` turn.

Choose **Model-initiated tools** for models verified to use lookup tools reliably, or **App-retrieved context** for models that cannot initiate tool calls. Existing configurations default to Model-initiated tools.

Settings also provides user-declared per-model capability labels for **Chat**, **Query decomposition**, **Tool use**, **Structured JSON**, and **Vision**. Labels are saved per provider, endpoint, and model. **Test capabilities** runs isolated probes for the selected model, including a generated image and a tool round-trip with a fixed sandbox result; it does not access campaign data or change the labels. Results are informational and should be treated as evidence from one run, not a guarantee or automatic routing policy.

The key and the campaign list are stored in `~/.config/sakaki-gm/config.json`. On first launch after this rename, settings are migrated from `~/.config/pygm/config.json` if the new config does not exist; the old file is left in place. Campaigns created by the app are stored under `~/.local/share/sakaki-gm/campaigns/`. `pygm_campaign.db` and `.venv` are gitignored.

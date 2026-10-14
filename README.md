# Sakaki GM

Sakaki GM is a local Game Master desk. Each campaign is its own SQLite file, one bottle per campaign. The model that answers player actions is chosen in Settings.

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

The **Items** tab is the campaign-wide catalog of reusable item names and descriptions. Add catalog items there, then assign them to characters in the character editor's **Inventory** tab with a quantity and attuned flag. Character inventories use this catalog as their standard item list; the old free-form Inventory and Attuned fields are no longer shown or included in the GM prompt.

The **Quests** tab stores campaign quests by name, free-text status, and description. Add or remove participating characters for each quest; a character can participate in multiple quests. Quest details and linked character names are included in the GM prompt.

## Model

Settings has two presets:

- SpaceXAI: `https://api.x.ai/v1`, model `grok-4.7`. Leave the key blank to use `XAI_API_KEY`, or type a key in the password field.
- OpenAI-compatible: `http://127.0.0.1:11434/v1` for a local server. A key is optional.

**Test** checks the `/models` endpoint. **Ask GM** sends the action as a `[Play]` turn.

The key and the campaign list are stored in `~/.config/sakaki-gm/config.json`. On first launch after this rename, settings are migrated from `~/.config/pygm/config.json` if the new config does not exist; the old file is left in place. Campaigns created by the app are stored under `~/.local/share/sakaki-gm/campaigns/`. `pygm_campaign.db` and `.venv` are gitignored.

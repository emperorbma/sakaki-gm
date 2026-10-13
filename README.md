# PyGM Core

PyGM Core is a local Game Master desk. Each campaign is its own SQLite file, one bottle per campaign. The model that answers player actions is chosen in Settings.

## Linux

```bash
./start-pygm.sh
```

The script creates `.venv`, installs the Python packages, and opens the window. If it stops early on Debian, install the missing package and run it again:

```bash
sudo apt install python3-venv python3-tk
```

`sakaki-gm.desktop` starts the same script without a terminal.

## Windows

Double-click `start-pygm.bat`. Python 3 must be on `PATH` as `py` or `python`. The batch file creates `.venv` next to itself and installs the packages.

## Campaigns

The first window opens `pygm_campaign.db` in this folder when the campaign list is still empty. **New** adds another campaign file and leaves the current one in place. The campaign list, **Open**, **Rename**, and **Remove** are in the header. **Remove** deletes a file only when PyGM created it under `~/.local/share/pygm/campaigns/`.

**Import Folder** reads a binder of markdown sheets and notes into the campaign that is open. The Eldenweave binder for the database already in this folder is `/home/emperorbma/Grok Adventure A1`.

## Model

Settings has two presets:

- SpaceXAI: `https://api.x.ai/v1`, model `grok-4.7`. Leave the key blank to use `XAI_API_KEY`, or type a key in the password field.
- OpenAI-compatible: `http://127.0.0.1:11434/v1` for a local server. A key is optional.

**Test** checks the `/models` endpoint. **Ask GM** sends the action as a `[Play]` turn.

The key and the campaign list are stored in `~/.config/pygm/config.json`. That file is not part of the git repository. `pygm_campaign.db` and `.venv` are gitignored.

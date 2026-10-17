#!/usr/bin/env bash
# Start Sakaki GM from this directory, with its own virtualenv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || {
    echo "Could not create .venv. On Debian install python3-venv and run this again." >&2
    exit 1
  }
fi

if [[ ! -f .venv/requirements.stamp || requirements.txt -nt .venv/requirements.stamp || pyproject.toml -nt .venv/requirements.stamp ]]; then
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/requirements.stamp
fi

if ! .venv/bin/python -c "import tkinter" >/dev/null 2>&1; then
  echo "Tkinter is missing. On Debian install python3-tk and run this again." >&2
  exit 1
fi

DESKTOP="$ROOT/sakaki-gm.desktop"
if [[ -f "$DESKTOP" ]]; then
  sed -i "s|^Exec=.*|Exec=$ROOT/start-sakaki-gm.sh|" "$DESKTOP"
  sed -i "s|^Path=.*|Path=$ROOT|" "$DESKTOP"
fi

exec .venv/bin/python -m sakaki_gm

#!/usr/bin/env bash
# Headless campaign table server (no Tk lobby or desk).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/start-sakaki-gm.sh" --server "$@"

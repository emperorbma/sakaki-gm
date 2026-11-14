#!/usr/bin/env bash
# Host a campaign table server (passes --server through to Sakaki GM).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/start-sakaki-gm.sh" --server "$@"

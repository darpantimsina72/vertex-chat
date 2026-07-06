#!/usr/bin/env bash
# Vertex Chat — macOS / Linux launcher.
set -euo pipefail
cd "$(dirname "$0")"

# python3.13 preferred — some newer local python3 builds fail ensurepip in venv.
PY="$(command -v python3.13 || command -v python3 || command -v python)"
exec "$PY" start.py

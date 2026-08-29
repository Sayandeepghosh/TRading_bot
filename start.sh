#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Equity Analyser launcher
#
#  Creates the virtualenv on first run, installs dependencies if they are
#  missing, then starts the dashboard. Safe to run repeatedly.
#
#    ./start.sh                 start the dashboard
#    ./start.sh --scan          one scan, printed to the terminal
#    ./start.sh --holdings      review recorded positions
#    ./start.sh --stock TITAN   analyse a single symbol
#
#  Any arguments are passed straight through to run.py.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"
STAMP="$VENV/.deps-installed"

# --- pick an interpreter -----------------------------------------------------
if [ ! -x "$PY" ]; then
  BOOTSTRAP=""
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then BOOTSTRAP="$candidate"; break; fi
  done
  if [ -z "$BOOTSTRAP" ]; then
    echo "Error: no Python 3 interpreter found. Install Python 3.11 or newer." >&2
    exit 1
  fi

  echo "Creating virtual environment in $VENV ..."
  "$BOOTSTRAP" -m venv "$VENV"
  "$PY" -m pip install --upgrade pip --quiet
fi

# --- install dependencies once -----------------------------------------------
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Installing dependencies (one-off, takes a minute) ..."
  "$PY" -m pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

# --- go ----------------------------------------------------------------------
exec "$PY" run.py "$@"

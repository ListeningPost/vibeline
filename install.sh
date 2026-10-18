#!/usr/bin/env bash
# Install vibeline as a Hermes plugin. Safe to re-run.
#   ./install.sh                 # link + enable + self-check
#   ./install.sh --db /path/to/trace.db   # also configure the ledger path
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DB=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db) DB="$2"; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null; then echo "python3 is required" >&2; exit 1; fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "python >= 3.10 required" >&2; exit 1; }
if ! command -v hermes >/dev/null; then
  echo "hermes not on PATH. Install Hermes first (https://github.com/NousResearch/hermes-agent), then re-run." >&2
  exit 1
fi

mkdir -p "$HERMES_HOME/plugins"
LINK="$HERMES_HOME/plugins/vibeline"
if [ -e "$LINK" ] && [ "$(readlink -f "$LINK")" != "$HERE" ]; then
  echo "refusing to replace $LINK -> $(readlink -f "$LINK"); remove it first" >&2
  exit 1
fi
ln -sfn "$HERE" "$LINK"
echo "linked $LINK -> $HERE"

hermes plugins enable vibeline
if [ -n "$DB" ]; then
  [ -f "$DB" ] || { echo "ledger not found: $DB" >&2; exit 1; }
  hermes config set "plugins.entries.vibeline.settings.db" "$DB" 2>/dev/null \
    || echo "could not set config automatically; add plugins.entries.vibeline.settings.db: $DB to $HERMES_HOME/config.yaml"
fi

echo
hermes vibeline check ${DB:+--db "$DB"}
echo
echo "next: run 'hermes --tui' and type /rails, or 'hermes vibeline web --open'"

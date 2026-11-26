#!/usr/bin/env bash
# Runs a Python hook script with the caller's interpreter. pre-commit's `language: script` runs this
# file directly, and the docs hooks need the repository's own environment (docs-python-imports
# imports the documented package), not an isolated one.
# Order: $DOCS_HOOKS_PYTHON, the active virtualenv, python3, python. A Windows virtualenv has no
# python3, so the virtualenv is checked before PATH.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$1"
shift
py="${DOCS_HOOKS_PYTHON:-}"
if [ -z "$py" ] && [ -n "${VIRTUAL_ENV:-}" ]; then
  for c in "$VIRTUAL_ENV/bin/python" "$VIRTUAL_ENV/Scripts/python.exe"; do
    if [ -x "$c" ]; then py="$c"; break; fi
  done
fi
if [ -z "$py" ]; then
  py="$(command -v python3 || command -v python || true)"
fi
if [ -z "$py" ]; then
  echo "run-python.sh: no Python interpreter found (set DOCS_HOOKS_PYTHON)" >&2
  exit 2
fi
exec "$py" "$here/$script" "$@"

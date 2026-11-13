#!/usr/bin/env bash
# Usage: scan-secrets.sh <file | directory | -> ...
#
# Exits 1 when any input contains something token-shaped, 2 when an input cannot be read, 0 when
# everything is clean. It prints "<kind> <file>" for each hit and NEVER the match itself, so the
# job log cannot become the leak. `-` reads stdin. A directory is scanned recursively.
#
# What the review loop posts (a review, a response comment, an issue, a pushed commit) is written
# by an agent that reads untrusted PR text. If a prompt injection gets it to read a credential, this
# is the last stop before the text leaves the runner. It is a second line behind the deny rules in
# claude-code-review.yml, and only as good as its patterns: it does not decode base64, and a token
# split over lines or characters passes. The real fix is that no credential is reachable.
#
# Shapes: GitHub tokens (ghp_ gho_ ghu_ ghs_ ghr_, github_pat_), Anthropic keys and Claude OAuth
# tokens (sk-ant-...), PEM private keys, and a GitHub App JWT (three base64url parts, the first two
# starting `eyJ`). SCAN_ENV_NAMES (space separated variable names) adds an exact-value check, so a
# secret with no recognisable shape, such as the job's own CLAUDE_CODE_OAUTH_TOKEN, is caught too.
set -uo pipefail

PATTERNS=(
  'github-token|gh[pousr]_[A-Za-z0-9]{20,}'
  'github-fine-grained-pat|github_pat_[A-Za-z0-9_]{20,}'
  'anthropic-key-or-oauth-token|sk-ant-[A-Za-z0-9_-]{20,}'
  'private-key|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----'
  'app-jwt|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
)

status=0

scan_one() { # <label> <path | ->
  local label="$1" src="$2" entry kind re name val
  for entry in "${PATTERNS[@]}"; do
    kind="${entry%%|*}"
    re="${entry#*|}"
    if grep -aEq -- "$re" "$src"; then
      printf '%s %s\n' "$kind" "$label"
      status=1
    fi
  done
  for name in ${SCAN_ENV_NAMES:-}; do
    val="${!name:-}"
    # An empty or tiny value would match everything; real secrets are longer than this.
    if [ "${#val}" -ge 8 ] && grep -aFq -- "$val" "$src"; then
      printf 'env-secret:%s %s\n' "$name" "$label"
      status=1
    fi
  done
}

[ "$#" -gt 0 ] || {
  echo "usage: scan-secrets.sh <file | directory | ->..." >&2
  exit 2
}

stdin_copy=""
trap '[ -z "$stdin_copy" ] || rm -f "$stdin_copy"' EXIT

for arg in "$@"; do
  if [ "$arg" = - ]; then
    stdin_copy=$(mktemp)
    cat > "$stdin_copy"
    scan_one stdin "$stdin_copy"
  elif [ -d "$arg" ]; then
    while IFS= read -r -d '' f; do
      scan_one "$f" "$f"
    done < <(find "$arg" -type f -print0)
  elif [ -f "$arg" ]; then
    scan_one "$arg" "$arg"
  else
    echo "scan-secrets: cannot read $arg" >&2
    exit 2
  fi
done
exit "$status"

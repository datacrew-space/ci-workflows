#!/usr/bin/env bash
# Usage: review-skip.sh <statuses_json_file> <orchestrator_login> <run_attempt>
# Decides whether the paid review can be skipped because this exact head SHA already has a
# finished verdict. The first argument is the JSON array from
# `gh api repos/OWNER/REPO/commits/SHA/statuses`. Prints "<skip|run>|<reason>".
#
# skip only when ALL hold:
#   - this is the first attempt of the workflow run (run_attempt 1). A manual re-run is the
#     documented fix for a tooling failure, so a re-run always reviews;
#   - the newest `review-verdict` status is `success` or `failure` (terminal);
#   - that status was created by the orchestrator App (<login>[bot]). The agent tokens cannot write
#     statuses, but any other writer could, and only the orchestrator's verdict is trusted.
# Everything else runs the review: pending, error, no status, another creator, unreadable input.
# Fail open: this saves tokens, it is never a gate, so a doubt means review.
set -uo pipefail
f="${1:-}"
login="${2:-}"
attempt="${3:-1}"
case "$attempt" in ''|*[!0-9]*) attempt=1 ;; esac

if [ "$attempt" -gt 1 ]; then
  echo "run|run attempt $attempt is a re-run, which always reviews"
  exit 0
fi
if [ -z "$login" ] || [ -z "$f" ] || [ ! -f "$f" ]; then
  echo "run|no statuses to read"
  exit 0
fi

line=$(jq -r '
  if type != "array" then empty else
    [.[] | select(.context == "review-verdict")] | sort_by(.created_at, .id) | last
    | select(. != null) | "\(.state)|\(.creator.login // "")"
  end' "$f" 2>/dev/null) || line=""

if [ -z "$line" ]; then
  echo "run|no review-verdict status on this head"
  exit 0
fi
state="${line%%|*}"
creator="${line#*|}"

if [ "$creator" != "$login[bot]" ]; then
  echo "run|newest review-verdict ($state) was not set by $login[bot]"
  exit 0
fi
case "$state" in
  success|failure) echo "skip|this head already has a finished review-verdict ($state) from $login[bot]" ;;
  *) echo "run|newest review-verdict is $state, not finished" ;;
esac

#!/usr/bin/env bash
# Usage: git log --format=%B <base>..<work> | reject-closing-keywords.sh
#
# Reads commit messages on stdin and exits 1, printing each offending line, when any line would close
# an issue or PR once the commit reaches the default branch: close/closes/closed, fix/fixes/fixed or
# resolve/resolves/resolved followed by a #N, an owner/repo#N reference or an issues/pull URL.
#
# Why: sanitize-pr-text.sh cleans the PR BODY, but GitHub also closes issues named in COMMIT
# messages, and a squash merge copies the commit messages into the squash body by default. The
# dispatcher adds the one real `Closes #N` to the PR; the agent must not add another. Fail closed:
# the caller refuses to push (the `report` job then hands the issue to a human).
# Matching is case-insensitive and errs toward rejecting. Tested in tests/test_implementer_scripts.py.
set -uo pipefail

if grep -Eiw -e '(close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)[: ]+(#[0-9]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+|https?://[^ ]*/(issues|pull)/[0-9]+)'; then
  exit 1
fi
exit 0

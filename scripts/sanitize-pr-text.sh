#!/usr/bin/env bash
# Usage: sanitize-pr-text.sh < agent-pr-text.md > safe-pr-text.md
#
# The agent's PR text is untrusted: it is written by a model that read a ticket, a repo and web
# search results. Before the dispatcher publishes it:
#   1. a line that would close an issue or PR is dropped (close/closes/closed, fix/fixes/fixed,
#      resolve/resolves/resolved, with a #N or an issues/pull URL on the same line). The dispatcher
#      adds the one real `Closes #N` itself. A line that merely mentions #12, or says "fix" with no
#      reference, is kept. This errs toward dropping.
#   2. an @name is wrapped in backticks so it notifies nobody. An email address and a name that is
#      already in backticks are left alone.
# GNU sed (the `I` flag and \x60 for a backtick). Tested in tests/test_dispatch_scripts.py.
set -uo pipefail

sed -E \
  -e '/\b(close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\b.*(#[0-9]+|\/(issues|pull)\/[0-9]+)/Id' \
  -e 's/(^|[^A-Za-z0-9_\x60])@([A-Za-z0-9_][A-Za-z0-9_\/-]*)/\1\x60@\2\x60/g'

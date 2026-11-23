#!/usr/bin/env bash
# Usage: sanitize-pr-text.sh < agent-pr-text.md > safe-pr-text.md
#
# The agent's PR text is untrusted: it is written by a model that read a ticket, a repo and web
# search results. Before the dispatcher publishes it:
#   1. a line that would close an issue or PR is dropped (close/closes/closed, fix/fixes/fixed,
#      resolve/resolves/resolved, with a #N or an issues/pull URL on the same line). The dispatcher
#      adds the one real `Closes #N` itself. A line that merely mentions #12, or says "fix" with no
#      reference, is kept. This errs toward dropping.
#   2. no @name can notify anyone. HTML character references for `@` (&#64; &#x40; &commat;) are
#      decoded first, then a zero-width space is put after every `@` that starts a name, which breaks
#      the mention in any Markdown or HTML context (inline code and fences are NOT reliable inside an
#      HTML block, so quoting would not be enough). An email address is left alone: GitHub does not
#      treat it as a mention. The name still reads the same.
# GNU sed (the `I` flag and \x escapes). Tested in tests/test_implementer_scripts.py.
set -uo pipefail

sed -E \
  -e '/\b(close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\b.*(#[0-9]+|\/(issues|pull)\/[0-9]+)/Id' \
  -e 's/&#0*64;|&#x0*40;|&commat;/@/Ig' \
  -e 's/(^|[^A-Za-z0-9_])@([A-Za-z0-9_])/\1@\xe2\x80\x8b\2/g'

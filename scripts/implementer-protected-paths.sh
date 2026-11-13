#!/usr/bin/env bash
# Usage: git diff -z --no-renames --name-only <base>...<work> | implementer-protected-paths.sh
#
# Reads changed paths separated by NUL and exits 1, naming each offender on stdout, when any of
# them is a file that grades the agent. Exits 0 when none is.
#
# NUL, not newline, and never git's default text output: git QUOTES a path that has a non-ASCII
# byte (core.quotePath), so `.claude/é.sh` prints as `".claude/\303\251.sh"` and a pattern anchored
# on `^\.claude/` misses it. With -z every path arrives raw, one record each, and a newline inside
# a name cannot start a second record.
#
# The caller must run `git diff` ON ITS OWN and refuse to push when it fails: this script only judges
# what it is given, and an empty list is a pass. (tests/test_implementer_scripts.py)
#
# Matching is case-insensitive so a case-folding checkout cannot hide `.GitHub/` or `Agents.md`.
# Instruction files (AGENTS.md, CLAUDE.md, CLAUDE.local.md, .mcp.json) are protected at EVERY depth:
# Claude Code loads a nested CLAUDE.md, so a ticket could otherwise plant instructions aimed at the
# reviewer that checks out its PR.
# Not protected on purpose: dependency manifests and lockfiles (a ticket may add a dependency), tests/,
# and docs. A human reads those in review. (Contrast scripts/protected-paths.sh, the stricter default-deny
# allowlist the review responder uses.)
#
# EXTRA_PROTECTED_PATHS: optional, whitespace-separated shell globs (case-insensitive, `*` crosses `/`) added to
# the list below, e.g. the path of your agent prompt or your own check scripts. Set by the workflow from its
# `extra-protected-paths` input.
set -uo pipefail

found=0
read -r -a extra <<< "${EXTRA_PROTECTED_PATHS:-}"

# Judge one path. Prints it and sets found=1 when it is protected.
judge() {
  local path=$1 lower pattern
  lower="${path,,}"
  case "$lower" in
    .github/* | .agents/* | .claude/* | scripts/check[-_]*) ;;
    agents.md | claude.md | claude.local.md | .mcp.json) ;;
    */agents.md | */claude.md | */claude.local.md | */.mcp.json) ;;
    .pre-commit-config.yaml | ruff.toml | .secrets.baseline) ;;
    *)
      for pattern in "${extra[@]}"; do
        pattern="${pattern,,}"
        # shellcheck disable=SC2053  # the unquoted right side is the glob on purpose
        if [[ "$lower" == $pattern ]]; then
          printf '%s\n' "$path"
          found=1
          return
        fi
      done
      return
      ;;
  esac
  printf '%s\n' "$path"
  found=1
}

# `|| [[ -n $path ]]`: a final record with no trailing NUL is still judged, never dropped.
while IFS= read -r -d '' path || [[ -n $path ]]; do
  judge "$path"
done

exit "$found"

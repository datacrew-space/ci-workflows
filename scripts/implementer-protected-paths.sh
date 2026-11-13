#!/usr/bin/env bash
# Usage: git diff -z --no-renames --name-only <base>...<work> | protected-paths.sh
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
while IFS= read -r -d '' path; do
  lower="${path,,}"
  case "$lower" in
    .github/* | .agents/agents/* | .agents/skills/* | .claude/* | scripts/check_* |       agents.md | claude.md | .pre-commit-config.yaml | ruff.toml | .secrets.baseline)
      printf '%s
' "$path"
      found=1
      continue
      ;;
  esac
  for pattern in "${extra[@]}"; do
    pattern="${pattern,,}"
    # shellcheck disable=SC2053  # the unquoted right side is the glob on purpose
    if [[ "$lower" == $pattern ]]; then
      printf '%s
' "$path"
      found=1
      break
    fi
  done
done

exit "$found"

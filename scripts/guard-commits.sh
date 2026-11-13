#!/usr/bin/env bash
# Usage: guard-commits.sh <repo> <base> <tip>
#
# Judges EVERY commit in <base>..<tip>, not only the net change, and exits non-zero on the first
# problem. protected-paths.sh (found next to this script) decides what a commit may touch.
#
# Why per commit: a push guard that diffs only <base> against <tip> sees the end state. A commit that
# changes a protected file and a later commit that puts it back leave a clean net diff, but both
# commits land in history when the PR is merged with a merge commit or rebased. This script refuses
# that. It also refuses a merge commit: the responder makes linear fixes, and a merge can carry any
# change from another branch past a per-commit diff.
#
# PROTECTED_EXTRA is passed through to protected-paths.sh (see that script).
set -uo pipefail

repo="${1:?repo}"
base="${2:?base}"
tip="${3:?tip}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/protected-paths.sh"

if [ ! -f "$guard" ]; then
  echo "::error::protected-paths.sh is missing next to guard-commits.sh; is tooling-ref older than the workflow?" >&2
  exit 1
fi

g() { git -C "$repo" -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat "$@"; }

if ! commits=$(g rev-list --reverse "$base..$tip"); then
  echo "::error::cannot list the commits in $base..$tip; refusing" >&2
  exit 1
fi

for c in $commits; do
  parents=$(g rev-list --parents -n 1 "$c" | wc -w)
  if [ "$parents" -ne 2 ]; then
    echo "::error::commit $c is a merge or a root commit; only linear fix commits are accepted" >&2
    exit 1
  fi
  changed=$(mktemp)
  # -z and --no-renames: see protected-paths.sh. An empty list is a pass, so a failing diff must stop us.
  if ! g diff -z --no-renames --name-only "$c^" "$c" > "$changed"; then
    echo "::error::git diff failed for commit $c; refusing" >&2
    rm -f "$changed"
    exit 1
  fi
  # A symlink (120000) or a gitlink/submodule (160000) is judged by NAME only above, but its target
  # is what matters: a link at an allowed path can point at a protected file. Fix commits are plain
  # files, so refuse any commit that creates either.
  if ! raw=$(g diff --raw --no-renames "$c^" "$c"); then
    echo "::error::git diff --raw failed for commit $c; refusing" >&2
    rm -f "$changed"
    exit 1
  fi
  if printf '%s\n' "$raw" | awk '/^:/ { if ($2 == "120000" || $2 == "160000") bad = 1 } END { exit !bad }'; then
    echo "::error::commit $c adds a symlink or a submodule; refusing" >&2
    rm -f "$changed"
    exit 1
  fi
  if ! bash "$guard" < "$changed"; then
    echo "::error::commit $c touches protected files (listed above); refusing" >&2
    rm -f "$changed"
    exit 1
  fi
  rm -f "$changed"
done
exit 0

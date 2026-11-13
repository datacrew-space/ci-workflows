#!/usr/bin/env bash
# Usage: fetch-agent-prompt.sh <repo> <base-branch> <path> <dest> <fallback>
#
# Copies an agent prompt file from the BASE branch (`origin/<base-branch>`) of <repo> to <dest>, so
# the instructions an agent follows are the ones already reviewed and merged, never the ones in the
# pull request under review. A PR that edits its own code-reviewer.md therefore changes nothing
# about how it is reviewed; the edit takes effect once a person has merged it.
#
# Exit 0: <dest> holds the prompt; stdout says `source=base` or `source=fallback`.
# Exit 1: refused, reason on stderr; <dest> is not created.
#
# Validation, all fail-closed: <path> is a plain relative path with no `..`; the base ref resolves;
# on the base the entry is a regular file (not a symlink, not a directory), at most 100 KB, not
# empty, text (no NUL byte), and starts with a `---` front-matter line like the shipped templates.
#
# First adoption: when <path> does not exist on the base branch yet (the PR that adds the loop adds
# the prompt files), the generic template <fallback> is used instead, with a visible warning. The
# template comes from the pinned tooling checkout, which a PR cannot touch, so this is no way for a PR
# to choose its own reviewer. Failing closed there would leave the adoption PR red on a tooling
# failure with nothing to fix, and review is advisory next to the required checks. A file that EXISTS
# on the base but is invalid is never replaced by the fallback: that is an error.
set -uo pipefail

repo="${1:?repo}"
base="${2:?base-branch}"
path="${3:?path}"
dest="${4:?dest}"
fallback="${5:?fallback}"
max_bytes=100000

fail() {
  echo "::error::$1" >&2
  exit 1
}

[[ "$base" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "base-branch must be a literal branch name ([A-Za-z0-9._/-]+)"
[[ "$path" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "agent prompt path '$path' must match [A-Za-z0-9._/-]+"
case "$path" in
  /* | ../* | */../* | */.. | .. | ./* | */./* | */. | . | *//*) fail "agent prompt path '$path' must be a plain relative path" ;;
esac

g() { git -C "$repo" -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat "$@"; }

ref="refs/remotes/origin/$base"
g rev-parse --verify --quiet "$ref^{commit}" > /dev/null || fail "base branch origin/$base is not available in the checkout (fetch-depth 0 needed)"

# `ls-tree` is the only way to tell "absent" from "present but bad" without parsing error text.
entry=$(g ls-tree "$ref" -- "$path") || fail "cannot read origin/$base"
mkdir -p "$(dirname "$dest")" || fail "cannot create the destination folder"

if [ -z "$entry" ]; then
  [ -f "$fallback" ] || fail "'$path' does not exist on origin/$base and the fallback template '$fallback' is missing"
  cp "$fallback" "$dest" || fail "cannot copy the fallback template"
  echo "::warning title=generic agent prompt::'$path' does not exist on origin/$base yet, so the generic template shipped with the review loop is used. This is expected for the PR that adopts the loop. Merge your own prompt, then the next PR uses it." >&2
  echo "source=fallback"
  exit 0
fi

# <mode> SP <type> SP <blob id> TAB <path>
mode=${entry%% *}
rest=${entry#* }
type=${rest%% *}
rest=${rest#* }
tab=$(printf '\t')
blob=${rest%%"$tab"*}
[ "$mode" = "100644" ] || [ "$mode" = "100755" ] || fail "'$path' on origin/$base is not a regular file (mode $mode)"
[ "$type" = "blob" ] || fail "'$path' on origin/$base is not a file"

tmp=$(mktemp) || fail "cannot create a temporary file"
trap 'rm -f "$tmp"' EXIT
# By blob id, not `<ref>:<path>`: no colon in the argument, so no path-list rewriting by a Windows shell.
g cat-file blob "$blob" > "$tmp" || fail "cannot read '$path' from origin/$base"
size=$(wc -c < "$tmp")
[ "$size" -gt 0 ] || fail "'$path' on origin/$base is empty"
[ "$size" -le "$max_bytes" ] || fail "'$path' on origin/$base is larger than $max_bytes bytes"
# A NUL byte means binary data, not a prompt (bash strings cannot hold one, so compare the lengths).
[ "$(tr -d '\000' < "$tmp" | wc -c)" -eq "$size" ] || fail "'$path' on origin/$base is not a text file"
[ "$(head -n 1 "$tmp" | tr -d '\r')" = "---" ] || fail "'$path' on origin/$base does not start with a '---' front-matter line"

cp "$tmp" "$dest" || fail "cannot write $dest"
echo "source=base"
exit 0

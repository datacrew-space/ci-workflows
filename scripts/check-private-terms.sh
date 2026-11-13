#!/usr/bin/env bash
# Usage: check-private-terms.sh [--require] --staged
#        check-private-terms.sh [--require] --message FILE
#        check-private-terms.sh [--require] --range BASE..HEAD
#        check-private-terms.sh [--require] --all-history
#
# Refuses text that matches a private-terms denylist: names of private repositories, internal
# hostnames, account names, local path patterns, personal email domains. Secret scanners look for
# tokens; this catches the names that reveal a private layout.
#
# THE DENYLIST MUST NOT LIVE IN A PUBLIC REPOSITORY, or it would publish the names it guards. It is
# read from, in order: the PRIVATE_TERMS variable (CI: a repository secret), then the file named by
# PRIVATE_TERMS_FILE, then ~/.config/private-terms.txt. One POSIX extended regular expression per
# line, matched case-insensitively against lower-cased text; `#` comments and blank lines are
# ignored. Use `(^|[^a-z0-9])name([^a-z0-9]|$)` for a whole word (no `\b`: it is not POSIX).
#
# THE OUTPUT NEVER CONTAINS A MATCHED TERM, A MATCHED LINE OR A MATCHED PATH. CI logs of a public
# repository are public, so a report that quoted the match would leak it a second time. It names
# only the commit (short sha), the file and line, and the number of the denylist entry. When the
# path itself matches, the path is not printed either.
#
# What is scanned: for --staged the added lines of the index; for --message one commit message;
# for --range and --all-history every commit's added lines, its message and its author/committer
# names, plus the net diff of the range (which also covers merge results).
#
# With no denylist: --require fails closed (exit 2, for CI); without it the check is skipped with a
# warning (exit 0, for a developer machine that has no list yet).
# Exit: 0 clean, 1 a match, 2 a configuration problem.
set -uo pipefail

require=0
mode=""
arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --require) require=1 ;;
    --staged | --all-history) mode="$1" ;;
    --message | --range)
      mode="$1"
      arg="${2:?$1 needs a value}"
      shift
      ;;
    *)
      echo "usage: check-private-terms.sh [--require] (--staged | --message FILE | --range A..B | --all-history)" >&2
      exit 2
      ;;
  esac
  shift
done
[ -n "$mode" ] || { echo "check-private-terms.sh: choose a mode" >&2; exit 2; }

terms=$(mktemp)
trap 'rm -f "$terms"' EXIT

src="${PRIVATE_TERMS:-}"
if [ -z "$src" ]; then
  f="${PRIVATE_TERMS_FILE:-${HOME:-/nonexistent}/.config/private-terms.txt}"
  [ -f "$f" ] && src=$(cat "$f")
fi
printf '%s\n' "$src" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' |
  grep -vE '^(#|$)' | tr 'A-Z' 'a-z' > "$terms" || true

if [ ! -s "$terms" ]; then
  if [ "$require" = 1 ]; then
    echo "::error::no private-terms denylist is configured; refusing (fail closed)" >&2
    exit 2
  fi
  echo "warning: no private-terms denylist configured; check skipped" >&2
  exit 0
fi

# A malformed pattern must not silently match nothing.
if ! awk -v f="$terms" 'BEGIN { while ((getline p < f) > 0) { "" ~ p } }' 2> /dev/null; then
  echo "::error::the denylist holds an invalid regular expression; refusing" >&2
  exit 2
fi

# Reads a unified diff (git log -p / git diff, -U0) on stdin; prints hits without any matched text.
scan_patch() {
  awk -v terms="$terms" '
    BEGIN { n = 0; while ((getline p < terms) > 0) pats[++n] = p; hit = 0 }
    function entry(text,   i, t) {
      t = tolower(text)
      for (i = 1; i <= n; i++) if (t ~ pats[i]) return i
      return 0
    }
    /^commit [0-9a-f]+$/ { commit = substr($2, 1, 8); next }
    /^diff --git / {
      file = $0; sub(/^.* b\//, "", file)
      pathhit = entry(file); shown = pathhit ? "<path hidden>" : file
      if (pathhit) { printf "%s: a changed path matches denylist entry #%d\n", (commit ? "commit " commit : "diff"), pathhit; hit = 1 }
      next
    }
    /^@@ / { if (match($0, /\+[0-9]+/)) line = substr($0, RSTART + 1, RLENGTH - 1) + 0; next }
    /^\+\+\+ / { next }
    /^\+/ {
      e = entry(substr($0, 2))
      if (e) { printf "%s%s:%d: matches denylist entry #%d\n", (commit ? "commit " commit " " : ""), shown, line, e; hit = 1 }
      line++
      next
    }
    END { exit hit }
  '
}

# Scans commit metadata lines on stdin (name, email, message); prints hits without matched text.
scan_lines() { # $1 label
  awk -v terms="$terms" -v label="$1" '
    BEGIN { n = 0; while ((getline p < terms) > 0) pats[++n] = p; hit = 0 }
    {
      t = tolower($0)
      for (i = 1; i <= n; i++) if (t ~ pats[i]) { printf "%s line %d: matches denylist entry #%d\n", label, NR, i; hit = 1; break }
    }
    END { exit hit }
  '
}

status=0
case "$mode" in
  --staged)
    git diff --cached -U0 --no-color --diff-filter=ACMR | scan_patch || status=1
    ;;
  --message)
    [ -f "$arg" ] || { echo "check-private-terms.sh: no such file" >&2; exit 2; }
    grep -v '^#' "$arg" | scan_lines "commit message" || status=1
    ;;
  --range | --all-history)
    if [ "$mode" = --range ]; then
      revs="$arg"
      commits=$(git rev-list "$revs") || { echo "::error::cannot list $revs; refusing" >&2; exit 2; }
      git log --reverse -p -U0 --no-color --format='commit %H' "$revs" | scan_patch || status=1
      base="${arg%%..*}"
      tip="${arg##*..}"
      git diff -U0 --no-color "$base" "$tip" | scan_patch || status=1
    else
      commits=$(git rev-list --all) || { echo "::error::cannot list history; refusing" >&2; exit 2; }
      git log --all --reverse -p -U0 --no-color --format='commit %H' | scan_patch || status=1
    fi
    for c in $commits; do
      git log -1 --format='%an%n%ae%n%cn%n%ce%n%B' "$c" | scan_lines "commit ${c:0:8} author/committer/message" || status=1
    done
    ;;
esac

if [ "$status" -ne 0 ]; then
  echo "private-terms: refused (see the entries above; matched text is deliberately not shown)" >&2
fi
exit "$status"

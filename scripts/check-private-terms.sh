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
# line, matched case-insensitively (ASCII letters; non-ASCII letters match only if the system awk
# lowercases them, which gawk does in a UTF-8 locale and mawk does not) against lower-cased text;
# `#` comments and blank lines are ignored. Backslash escapes in a pattern are kept as written. Use `(^|[^a-z0-9])name([^a-z0-9]|$)` for a whole word (no `\b`: it is not POSIX).
#
# THE OUTPUT NEVER CONTAINS A MATCHED TERM, A MATCHED LINE OR A MATCHED PATH. CI logs of a public
# repository are public, so a report that quoted the match would leak it a second time. It names
# only the commit (short sha), the file and line, and the number of the denylist entry. When the
# path itself matches, the path is not printed either.
#
# What is scanned: for --staged the added lines of the index; for --message one commit message
# (comment lines and everything below a scissors line are ignored); for --range and --all-history
# every commit's added lines (binary files as text, merge commits against their first parent), its
# message and its author/committer names, plus, for --range, the net diff from the merge base.
# NOT scanned: branch and tag names, and a term split across two lines.
#
# The report names the commit, the file and line, and the entry number: whoever reads a public log
# can correlate public text with entry numbers, though never learns the entry itself.
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
  grep -vE '^(#|$)' > "$terms" || true

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

# awk helper shared by both scanners: lowercase a pattern without touching its backslash escapes.
# The same awk lowercases the text, so pattern and text always agree on what "lower" means.
# awk stderr goes to /dev/null: a warning about a pattern would print a fragment of the denylist.
# A bad pattern is already refused above, and an awk that dies still fails the check (exit 2).
AWK_LOWER='function lower(p,   i, c, o, n) {
  o = ""; n = length(p)
  for (i = 1; i <= n; i++) { c = substr(p, i, 1); if (c == "\\") { o = o c substr(p, i + 1, 1); i++ } else o = o tolower(c) }
  return o
}'

# Reads a unified diff (git log -p / git diff, -U0) on stdin; prints hits without any matched text.
scan_patch() {
  awk -v terms="$terms" "$AWK_LOWER"'
    BEGIN { n = 0; while ((getline p < terms) > 0) pats[++n] = lower(p); hit = 0 }
    function entry(text,   i, t) {
      t = tolower(text)
      for (i = 1; i <= n; i++) if (t ~ pats[i]) return i
      return 0
    }
    /^commit [0-9a-f]+$/ { commit = substr($2, 1, 8); inhdr = 0; next }
    /^diff --git / {
      inhdr = 1
      file = $0; sub(/^.* b\//, "", file)
      pathhit = entry(file); shown = pathhit ? "<path hidden>" : file
      if (pathhit) { printf "%s: a changed path matches denylist entry #%d\n", (commit ? "commit " commit : "diff"), pathhit; hit = 1 }
      next
    }
    /^@@ / { inhdr = 0; if (match($0, /\+[0-9]+/)) line = substr($0, RSTART + 1, RLENGTH - 1) + 0; next }
    inhdr { next }
    /^\+/ {
      e = entry(substr($0, 2))
      if (e) { printf "%s%s:%d: matches denylist entry #%d\n", (commit ? "commit " commit " " : ""), shown, line, e; hit = 1 }
      line++
      next
    }
    END { exit hit }
  ' 2> /dev/null
}

# Scans commit metadata lines on stdin (name, email, message); prints hits without matched text.
scan_lines() { # $1 label
  awk -v terms="$terms" -v label="$1" "$AWK_LOWER"'
    BEGIN { n = 0; while ((getline p < terms) > 0) pats[++n] = lower(p); hit = 0 }
    {
      t = tolower($0)
      for (i = 1; i <= n; i++) if (t ~ pats[i]) { printf "%s line %d: matches denylist entry #%d\n", label, NR, i; hit = 1; break }
    }
    END { exit hit }
  ' 2> /dev/null
}

# -c core.quotePath=false keeps non-ASCII paths readable to the matcher; --text scans binary
# content as text (NUL bytes become spaces so awk keeps each line whole).
G="git -c core.quotePath=false"
nul() { tr '\0' ' '; }

status=0
case "$mode" in
  --staged)
    $G diff --cached -U0 --text --no-color --diff-filter=ACMR | nul | scan_patch || status=1
    ;;
  --message)
    [ -f "$arg" ] || { echo "check-private-terms.sh: no such file" >&2; exit 2; }
    sed -e '/^# -* >8 -*$/,$d' -e '/^#/d' "$arg" | scan_lines "commit message" || status=1
    ;;
  --range | --all-history)
    if [ "$mode" = --range ]; then
      revs="$arg"
      commits=$(git rev-list "$revs") || { echo "::error::cannot list $revs; refusing" >&2; exit 2; }
      $G log --reverse -p -U0 --text --no-color --diff-merges=first-parent --format='commit %H' "$revs" | nul | scan_patch || status=1
      base="${arg%%..*}"
      tip="${arg##*..}"
      # From the merge base, so text the base branch removed after the branch point is not reported as added.
      $G diff -U0 --text --no-color "$base...$tip" | nul | scan_patch || status=1
    else
      commits=$(git rev-list --all) || { echo "::error::cannot list history; refusing" >&2; exit 2; }
      $G log --all --reverse -p -U0 --text --no-color --diff-merges=first-parent --format='commit %H' | nul | scan_patch || status=1
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

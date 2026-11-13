#!/usr/bin/env bash
# Usage: check-commit-identity.sh --identity
#        check-commit-identity.sh --range BASE..HEAD
#        check-commit-identity.sh --all-history
#
# Refuses a commit whose author, committer or Co-authored-by email is not an allowed address. In a
# public repository a personal email in a commit is public forever; use the GitHub noreply address.
#
# ALLOWED_EMAIL_REGEX (POSIX extended, matched case-insensitively against the whole address)
# defaults to GitHub's noreply addresses and the Claude co-author address.
#
# The output never prints an email address (CI logs of a public repository are public): it names
# the commit (short sha) and which field failed.
# Exit: 0 clean, 1 a disallowed email, 2 a configuration problem.
set -uo pipefail

allowed="${ALLOWED_EMAIL_REGEX:-(@users\.noreply\.github\.com|^noreply@github\.com|^noreply@anthropic\.com)$}"
mode="${1:-}"
arg="${2:-}"

ok() { printf '%s\n' "$1" | tr 'A-Z' 'a-z' | grep -Eq "$allowed"; }

# An invalid ALLOWED_EMAIL_REGEX must not silently allow or refuse everything.
printf 'x\n' | grep -Eq "$allowed" > /dev/null 2>&1
if [ $? -eq 2 ]; then
  echo "::error::ALLOWED_EMAIL_REGEX is not a valid regular expression; refusing" >&2
  exit 2
fi

status=0
check_commit() { # $1 label, $2 author email, $3 committer email, $4 message body
  local label="$1" ae="$2" ce="$3" body="$4" e
  ok "$ae" || { echo "$label: author email is not an allowed address" >&2; status=1; }
  ok "$ce" || { echo "$label: committer email is not an allowed address" >&2; status=1; }
  while IFS= read -r e; do
    [ -n "$e" ] || continue
    ok "$e" || { echo "$label: a Co-authored-by email is not an allowed address" >&2; status=1; }
  done < <(printf '%s\n' "$body" | sed -n 's/^[Cc]o-[Aa]uthored-[Bb]y:.*<\([^>]*\)>.*$/\1/p')
}

case "$mode" in
  --identity)
    ae=$(git var GIT_AUTHOR_IDENT | sed -n 's/^.*<\(.*\)>.*$/\1/p')
    ce=$(git var GIT_COMMITTER_IDENT | sed -n 's/^.*<\(.*\)>.*$/\1/p')
    check_commit "this commit" "$ae" "$ce" ""
    ;;
  --range | --all-history)
    if [ "$mode" = --range ]; then
      [ -n "$arg" ] || { echo "--range needs BASE..HEAD" >&2; exit 2; }
      commits=$(git rev-list "$arg") || { echo "::error::cannot list $arg; refusing" >&2; exit 2; }
    else
      commits=$(git rev-list --all) || { echo "::error::cannot list history; refusing" >&2; exit 2; }
    fi
    for c in $commits; do
      ae=$(git log -1 --format=%ae "$c")
      ce=$(git log -1 --format=%ce "$c")
      body=$(git log -1 --format=%B "$c")
      check_commit "commit ${c:0:8}" "$ae" "$ce" "$body"
    done
    ;;
  *)
    echo "usage: check-commit-identity.sh (--identity | --range A..B | --all-history)" >&2
    exit 2
    ;;
esac

[ "$status" -eq 0 ] || echo "commit-identity: refused (addresses are deliberately not shown)" >&2
exit "$status"

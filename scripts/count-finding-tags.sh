#!/usr/bin/env bash
# Usage: count-finding-tags.sh <review.md>
# Prints "mechanizable=N standards-gap=N judgment=N unnamed-check=N unrecognized=N".
#
# A finding is a list item (`-`, `*` or `1.`) that starts with a class tag, case-insensitive:
#   [mechanizable: <check>]  a lint rule or guard would have caught it; names that check
#   [standards-gap]          a written rule in the repository's standards docs is missing
#   [judgment]               stays with the reviewer
# unnamed-check counts mechanizable findings with no check name (also counted in mechanizable).
# unrecognized counts items that start with some other [tag]. Items with no tag count nowhere, so an
# older untagged review reads as all zeros. Exit 1 when the file cannot be read.
set -uo pipefail
file="${1:-}"
[ -r "$file" ] || { echo "count-finding-tags: cannot read '$file'" >&2; exit 1; }
awk '
  BEGIN { m = s = j = u = r = 0 }
  {
    line = tolower($0)
    if (!match(line, /^[ \t]*([-*]|[0-9]+[.)])[ \t]+\[[^]]*\]/)) next
    tag = substr(line, RSTART, RLENGTH)
    sub(/^[ \t]*([-*]|[0-9]+[.)])[ \t]+\[/, "", tag); sub(/\]$/, "", tag)
    if (tag ~ /^mechanizable[ \t]*(:|$)/) {
      m++
      name = tag; sub(/^mechanizable[ \t]*:?[ \t]*/, "", name)
      if (name == "") u++
    } else if (tag == "standards-gap") s++
    else if (tag == "judgment") j++
    else r++
  }
  END { printf "mechanizable=%d standards-gap=%d judgment=%d unnamed-check=%d unrecognized=%d\n", m, s, j, u, r }
' "$file"

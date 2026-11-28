#!/usr/bin/env bash
# Usage: review-carry.sh <repo_dir> <head_sha> <base_ref> <orchestrator_login> <run_attempt>
# Env:   REPO  OWNER/NAME, for `gh api repos/$REPO/commits/<sha>/statuses`.
# Decides whether the review-verdict of an earlier commit of the PR can be carried to the new head
# because nothing but clean merges of the base branch came since. Prints "<carry|run>|<sha>|<reason>";
# <sha> is the commit that holds the carried verdict (empty on run).
#
# Walk first-parent from the head. The head itself and every commit before the anchor must be a
# clean base merge:
#   - exactly 2 parents;
#   - the second parent is an ancestor of <base_ref> (for example origin/main);
#   - `git merge-tree --write-tree <p1> <p2>` produces exactly the commit's tree, so the merge needed
#     no conflict resolution and holds no edit. Tree equality with the automatic merge is what makes
#     the carry safe: a carried verdict can never hide code the reviewer has not seen.
# The anchor is the first older commit whose newest `review-verdict` was set by the orchestrator App
# (<login>[bot]) as `success`. Only `success` carries. An orchestrator verdict of any other state on
# the way (failure, error, pending) stops the walk. A verdict set by anyone else is ignored.
# Anything else runs the review: a re-run (run_attempt > 1), a non-merge commit, a merge of
# something other than the base, a tree mismatch, no anchor within the walk limit, and any error.
# Fail toward reviewing: this saves tokens, it is never a gate.
set -uo pipefail
dir="${1:-}"
head="${2:-}"
base_ref="${3:-}"
login="${4:-}"
attempt="${5:-1}"
max_walk=50
case "$attempt" in ''|*[!0-9]*) attempt=1 ;; esac

run() { echo "run||$1"; exit 0; }

[ "$attempt" -gt 1 ] && run "run attempt $attempt is a re-run, which always reviews"
{ [ -n "$dir" ] && [ -d "$dir" ] && [ -n "$head" ] && [ -n "$base_ref" ] && [ -n "$login" ] && [ -n "${REPO:-}" ]; } \
  || run "missing input"
git -C "$dir" rev-parse -q --verify "$base_ref^{commit}" > /dev/null 2>&1 || run "base ref $base_ref is not available"

# Newest review-verdict on a commit as "<state>|<creator>", empty when there is none.
verdict_of() {
  gh api --paginate "repos/$REPO/commits/$1/statuses?per_page=100" --jq '.[]' 2> /dev/null | jq -rs '
    [.[] | select(.context == "review-verdict")] | sort_by(.created_at, .id) | last
    | select(. != null) | "\(.state)|\(.creator.login // "")"' 2> /dev/null
}

commit="$head"
n=0
while [ "$n" -lt "$max_walk" ]; do
  if [ "$commit" != "$head" ]; then
    v=$(verdict_of "$commit") || run "could not read the statuses of ${commit:0:7}"
    if [ -n "$v" ] && [ "${v#*|}" = "$login[bot]" ]; then
      state="${v%%|*}"
      if [ "$state" = success ]; then
        echo "carry|$commit|only clean merges of the base since ${commit:0:7}"
        exit 0
      fi
      run "${commit:0:7} has a $state review-verdict, which is not carried"
    fi
  fi
  parents=$(git -C "$dir" rev-list --parents -n 1 "$commit" 2> /dev/null) || run "cannot read ${commit:0:7}"
  # shellcheck disable=SC2086
  set -- $parents
  shift
  [ "$#" -eq 2 ] || run "${commit:0:7} is not a merge commit"
  p1="$1"
  p2="$2"
  git -C "$dir" merge-base --is-ancestor "$p2" "$base_ref" 2> /dev/null \
    || run "${commit:0:7} merges something other than $base_ref"
  merged=$(git -C "$dir" merge-tree --write-tree "$p1" "$p2" 2> /dev/null | head -n 1) \
    || run "${commit:0:7} does not merge cleanly without edits"
  actual=$(git -C "$dir" rev-parse "$commit^{tree}" 2> /dev/null) || run "cannot read the tree of ${commit:0:7}"
  [ -n "$merged" ] && [ "$merged" = "$actual" ] || run "${commit:0:7} differs from the automatic merge of its parents"
  commit="$p1"
  n=$((n + 1))
done
run "no verdict to carry within $max_walk commits"

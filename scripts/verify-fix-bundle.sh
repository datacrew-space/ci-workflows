#!/usr/bin/env bash
# Usage: verify-fix-bundle.sh <bundle> <reviewed-sha> <branch> [reviewed-repo]
#
# Turns a git bundle written by an agent job into a verified, push-ready ref, or refuses.
#
#   exit 0, ready=true   /refs/heads/fix in the repository named by `clean` is safe to push
#   exit 0, ready=false  the bundle holds no new commit; there is nothing to push
#   exit 1               refused; the reason is on stderr and pushed=false is written
#
# Results go to $GITHUB_OUTPUT when it is set (`clean`, `count`, `ready`, `pushed`), so the workflow
# step needs only this call and the final non-force `git push`.
#
# The bundle is data from a job where an agent ran: it is never trusted and nothing in it is run.
# Checks, in order, each fail-closed:
#   1. branch is a plain branch name (it is attacker-controlled text in the workflow)
#   2. bundle exists and is at most MAX_BUNDLE_BYTES (default 20 MB)
#   3. a FRESH repository (default config, no hooks) fetches <reviewed-repo> HEAD and that is exactly
#      <reviewed-sha>; the reviewed repo is this job's own checkout, where no agent ever ran
#   4. `git bundle verify` (pack integrity and prerequisites present in that fresh repository)
#   5. the bundle's tip descends from <reviewed-sha>
#   6. at most MAX_COMMITS commits (default 20); zero commits is "nothing to push"
#   7. the net diff reviewed-sha..tip passes protected-paths.sh (the endpoint guard)
#   8. every commit passes guard-commits.sh (protected paths per commit, no merge, no symlink)
#   9. no commit message or patch holds something token-shaped (scan-secrets.sh)
# The guard and scanner are read from the directory this script lives in, which the workflow
# checks out from the pinned tooling ref, never from the PR or the agent's workspace.
#
# PROTECTED_EXTRA is passed through to the guards (see protected-paths.sh).
set -uo pipefail

bundle="${1:?bundle}"
reviewed="${2:?reviewed-sha}"
branch="${3:?branch}"
source_repo="${4:-${GITHUB_WORKSPACE:-}}"
max_bytes="${MAX_BUNDLE_BYTES:-20000000}"
max_commits="${MAX_COMMITS:-20}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

emit() { [ -z "${GITHUB_OUTPUT:-}" ] || echo "$1" >> "$GITHUB_OUTPUT"; }
refuse() {
  echo "::error::$1; refusing to push" >&2
  emit "pushed=false"
  emit "ready=false"
  exit 1
}

[ -n "$source_repo" ] || refuse "no reviewed repository given (4th argument or GITHUB_WORKSPACE)"
[[ "$reviewed" =~ ^[0-9a-f]{40}$ ]] || refuse "reviewed-sha is not a full commit id"
# Reject a leading `-` too: the branch is later used as a ref name and must never read as a flag.
if [[ "$branch" == -* ]] || ! git check-ref-format --branch "$branch" > /dev/null 2>&1; then
  refuse "branch name is not a plain branch name"
fi

for f in protected-paths.sh guard-commits.sh scan-secrets.sh; do
  [ -f "$here/$f" ] || refuse "$f is missing next to verify-fix-bundle.sh"
done

if [ ! -s "$bundle" ] || [ "$(wc -c < "$bundle")" -gt "$max_bytes" ]; then
  refuse "bundle missing or larger than $max_bytes bytes"
fi

clean=$(mktemp -d) || refuse "cannot create a scratch repository"
g() { git -C "$clean" -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat "$@"; }
git init -q "$clean" || refuse "cannot init the scratch repository"
g config user.name "ci-review-responder[bot]"
g config user.email "355840606+ci-review-responder[bot]@users.noreply.github.com"

if ! g fetch -q "$source_repo" HEAD 2> /dev/null; then
  refuse "cannot fetch the reviewed commit"
fi
if [ "$(g rev-parse FETCH_HEAD)" != "$reviewed" ]; then
  refuse "checkout is not the reviewed commit"
fi

if ! g bundle verify "$bundle" > /dev/null 2>&1; then
  refuse "bundle failed verification"
fi
if ! g fetch -q "$bundle" HEAD 2> /dev/null; then
  refuse "cannot fetch the bundle"
fi
g update-ref refs/heads/fix FETCH_HEAD || refuse "cannot create the fix ref"
if ! g merge-base --is-ancestor "$reviewed" refs/heads/fix; then
  refuse "the bundle does not descend from the reviewed commit"
fi

if ! count=$(g rev-list --count "$reviewed..refs/heads/fix"); then
  refuse "cannot count the commits"
fi
if [ "$count" -gt "$max_commits" ]; then
  refuse "$count commits is more than a round of fixes should be"
fi
if [ "$count" -eq 0 ]; then
  emit "pushed=false"
  emit "ready=false"
  echo "no new commits; nothing to push"
  exit 0
fi

# -z and --no-renames: a move OUT of a protected path lists only its destination otherwise, and git
# quotes non-ASCII names. An empty list is a pass, so a failing `git diff` must stop us on its own.
changed=$(mktemp)
if ! g diff -z --no-renames --name-only "$reviewed" refs/heads/fix > "$changed"; then
  refuse "git diff failed"
fi
if ! bash "$here/protected-paths.sh" < "$changed"; then
  refuse "the responder changed protected files (listed above)"
fi
if ! bash "$here/guard-commits.sh" "$clean" "$reviewed" refs/heads/fix; then
  refuse "a responder commit was refused by guard-commits.sh (reason above)"
fi

# Pipe status matters: `git log` failing must not read as a clean scan.
log=$(mktemp)
if ! g log -p --format=full "$reviewed..refs/heads/fix" > "$log"; then
  refuse "git log failed"
fi
if ! bash "$here/scan-secrets.sh" - < "$log"; then
  refuse "the responder's commits hold something token-shaped"
fi

emit "clean=$clean"
emit "count=$count"
emit "ready=true"
echo "verified $count commit(s) for $branch"
exit 0

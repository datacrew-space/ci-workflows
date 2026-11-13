#!/usr/bin/env bash
# Usage (run inside a clone with full history; all input is environment):
#   PROMOTE_SHA=<commit> promote-deploy.sh
#
# Advances the deploy branch to PROMOTE_SHA and tags the promotion, or refuses. The deploy branch is
# the pointer a host-side deployer follows; it only ever moves forward to a commit that is already
# on the main branch, so the deployer never sees an unreviewed or out-of-order commit.
#
# Environment:
#   PROMOTE_SHA    commit to promote (required)
#   MAIN_BRANCH    branch PROMOTE_SHA must already be on        (default main)
#   DEPLOY_BRANCH  the pointer branch                            (default deploy)
#   TAG_PREFIX     tag name prefix, ending in a slash or dash    (default deploy/)
#   REMOTE         git remote to read and push                   (default origin)
#   PROMOTE_DATE   YYYY-MM-DD used in the tag                    (default: today, UTC)
#   DRY_RUN=1      decide and report, push nothing
#
# The tag is <TAG_PREFIX><date>-<sha7>. A tag is never moved: an existing tag on a different commit
# is refused. The push is one atomic, non-force push, so a lost race fails instead of overwriting.
# Re-running is safe: promoting the commit deploy already points at, or an older one, is a no-op,
# and a run that pushed the tag but not the branch finishes on retry. A no-op pushes nothing, tags
# included.
#
# Output: key=value lines (result, sha, previous, tag) on stdout, and appended to $GITHUB_OUTPUT
# when set. result is "promoted" or "noop". previous is the sha deploy pointed at before, if any.
# Exit: 0 promoted or no-op, 2 bad configuration, 3 sha not on main, 4 deploy is not an ancestor of
# sha (not a fast-forward), 5 tag exists on another commit, 6 push failed.
set -uo pipefail

die() {
  echo "::error::$2" >&2
  echo "$2" >&2
  exit "$1"
}

main_branch="${MAIN_BRANCH:-main}"
deploy_branch="${DEPLOY_BRANCH:-deploy}"
tag_prefix="${TAG_PREFIX:-deploy/}"
remote="${REMOTE:-origin}"
date_utc="${PROMOTE_DATE:-$(date -u +%Y-%m-%d)}"
dry_run="${DRY_RUN:-0}"

[ -n "${PROMOTE_SHA:-}" ] || die 2 "PROMOTE_SHA is required"
printf '%s' "$date_utc" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' || die 2 "PROMOTE_DATE must be YYYY-MM-DD"
git check-ref-format --branch "$main_branch" > /dev/null 2>&1 || die 2 "MAIN_BRANCH is not a valid branch name"
git check-ref-format --branch "$deploy_branch" > /dev/null 2>&1 || die 2 "DEPLOY_BRANCH is not a valid branch name"
[ "$main_branch" != "$deploy_branch" ] || die 2 "DEPLOY_BRANCH must differ from MAIN_BRANCH"
git check-ref-format "refs/tags/${tag_prefix}2000-01-01-0000000" > /dev/null 2>&1 || die 2 "TAG_PREFIX is not a valid tag prefix"

sha="$(git rev-parse --verify -q "${PROMOTE_SHA}^{commit}")" || die 2 "PROMOTE_SHA is not a commit in this clone"
short="${sha:0:7}"
tag="${tag_prefix}${date_utc}-${short}"

git fetch --quiet "$remote" "+refs/heads/${main_branch}:refs/remotes/${remote}/${main_branch}" \
  || die 6 "could not fetch ${main_branch} from ${remote}"
git merge-base --is-ancestor "$sha" "refs/remotes/${remote}/${main_branch}" \
  || die 3 "${short} is not on ${main_branch}; only commits already on ${main_branch} can be promoted"

previous=""
if git ls-remote --exit-code --heads "$remote" "refs/heads/${deploy_branch}" > /dev/null 2>&1; then
  git fetch --quiet "$remote" "+refs/heads/${deploy_branch}:refs/remotes/${remote}/${deploy_branch}" \
    || die 6 "could not fetch ${deploy_branch} from ${remote}"
  previous="$(git rev-parse "refs/remotes/${remote}/${deploy_branch}")"
fi

emit() {
  printf 'result=%s\nsha=%s\nprevious=%s\ntag=%s\n' "$1" "$sha" "$previous" "$tag" | tee -a "${GITHUB_OUTPUT:-/dev/null}"
}

push_branch=1
if [ -n "$previous" ]; then
  if [ "$previous" = "$sha" ] || git merge-base --is-ancestor "$sha" "$previous"; then
    push_branch=0
  elif ! git merge-base --is-ancestor "$previous" "$sha"; then
    die 4 "${deploy_branch} (${previous:0:7}) is not an ancestor of ${short}: not a fast-forward; refusing"
  fi
fi

# deploy is already at or past this commit: nothing to move, and no tag, because a tag would claim
# a promotion that never happened.
if [ "$push_branch" = 0 ]; then
  echo "${deploy_branch} is already at or past ${short}; nothing to promote"
  emit noop
  exit 0
fi

# Existing tag: same commit is fine (a retry), a different commit is never overwritten.
remote_tag="$(git ls-remote "$remote" "refs/tags/${tag}" | cut -f1)"
push_tag=1
if [ -n "$remote_tag" ]; then
  [ "$remote_tag" = "$sha" ] || die 5 "tag ${tag} already exists on another commit; refusing to move it"
  push_tag=0
fi

refspecs=()
refspecs+=("${sha}:refs/heads/${deploy_branch}")
if [ "$push_tag" = 1 ]; then
  git update-ref "refs/tags/${tag}" "$sha"
  refspecs+=("refs/tags/${tag}:refs/tags/${tag}")
fi

if [ "$dry_run" = 1 ]; then
  echo "dry run: would push ${refspecs[*]} to ${remote}"
  emit noop
  exit 0
fi

git push --atomic --quiet "$remote" "${refspecs[@]}" \
  || die 6 "push to ${remote} failed (a concurrent promotion or a protected ref); nothing was changed"

emit promoted

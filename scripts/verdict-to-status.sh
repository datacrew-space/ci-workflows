#!/usr/bin/env bash
# Usage: verdict-to-status.sh <verdict_file> [prior_changes_requested_count]
# Prints "<state>|<outcome>|<description>". The workflow reads only <outcome>; the status itself is
# decided by review-report.sh. state and description stay as a readable summary for the tests.
#
# The rule for the whole review flow: a PR ends green (ready to merge) or red (a human must act).
# Pending means an agent is working. Nothing waits red for an agent.
#
# outcome is one of: approved-for-merge, changes-requested, escalate-to-human, no-verdict.
#   approved-for-merge  success  the only way to go green; needs an explicit `approved`
#   changes-requested   pending  round 1: the review-responder is fixing; the next push re-reviews
#   escalate-to-human   failure  a second `changes-requested`: a person decides
#   no-verdict          failure  missing, empty or ambiguous verdict: a person looks
#
# Fail closed: only an explicit `approved` yields success. A missing file, an empty file, extra
# words, a different case, or any other text is `no-verdict`, so an unreviewed or ambiguous
# review can never make a PR mergeable.
#
# Two-round cap: the workflow, not the agent, counts rounds. A `changes-requested` verdict when a
# prior round already requested changes ends the loop as `escalate-to-human`.
set -uo pipefail
f="${1:-}"
prior="${2:-0}"
case "$prior" in ''|*[!0-9]*) prior=0 ;; esac

verdict=""
if [ -n "$f" ] && [ -f "$f" ]; then
  verdict=$(head -c 64 "$f" | tr -d '[:space:]')
fi
case "$verdict" in
  approved)
    echo "success|approved-for-merge|Reviewer found no blocking findings" ;;
  changes-requested)
    if [ "$prior" -ge 1 ]; then
      echo "failure|escalate-to-human|Needs a human: two review rounds did not converge"
    else
      echo "pending|changes-requested|Round 1 of 2: review-responder is fixing the findings"
    fi ;;
  *)
    echo "failure|no-verdict|Needs a human: review finished without a verdict" ;;
esac

#!/usr/bin/env bash
# Usage: review-report.sh <outcome> <round> <responder_pushed> <commit_count>
#   responder_pushed: true, false (the responder ran and pushed nothing), or failed (it never
#   reached its push step).
# Prints "<state>|<label>|<ping>|<message>": what the ci-review-orchestrator reports at the end of
# a review run. The `report` job in claude-code-review.yml acts on it; this script decides.
#
#   state    the `review-verdict` commit status: success, pending or failure
#   label    `needs-human`: add or remove
#   ping     yes: @mention the owner in this run's comment
#   message  the line for this run's `Review loop` comment
#
# `needs-human` means the CODE needs a person's decision: two rounds did not converge, or the
# responder pushed nothing. A tooling failure is red but gets no label and no ping, because the
# fix is re-running the workflow, not reading the code.
#
# outcome comes from the claude-review job: approved-for-merge, changes-requested,
# escalate-to-human, no-verdict, or anything else (tooling-failure, empty) for a review that did
# not complete.
set -uo pipefail
outcome="${1:-}"
round="${2:-1}"
pushed="${3:-false}"
count="${4:-0}"
case "$round" in 1 | 2) ;; *) round=1 ;; esac
case "$count" in '' | *[!0-9]*) count=0 ;; esac

r="Round $round of 2"
case "$outcome" in
  approved-for-merge)
    echo "success|remove|no|$r: approved. Ready to merge." ;;
  changes-requested)
    if [ "$pushed" = unconfigured ]; then
      echo "failure|add|yes|$r: changes requested, and the review-responder is not enabled: the push-runs-on input of the review workflow (a runner label of its own, different from runs-on) is unset. Nothing was fixed."
    elif [ "$pushed" = true ]; then
      echo "pending|remove|no|$r: changes requested. The review-responder pushed $count commit(s); round 2 of 2 (the last) reviews them next."
    elif [ "$pushed" = failed ]; then
      echo "failure|remove|no|$r: changes requested, and the review-responder did not finish. That is a tooling problem, not the code: re-run the workflow."
    else
      echo "failure|add|yes|$r: changes requested, and the review-responder pushed no fixes (it declined, deferred or failed; see its response table)."
    fi ;;
  escalate-to-human)
    echo "failure|add|yes|$r: still changes requested after the review-responder's fixes. The two rounds did not converge." ;;
  no-verdict)
    echo "failure|remove|no|$r: the review finished without a verdict. That is a tooling problem, not the code: re-run the workflow." ;;
  *)
    echo "failure|remove|no|$r: the review did not complete. That is a tooling problem, not the code: re-run the workflow." ;;
esac

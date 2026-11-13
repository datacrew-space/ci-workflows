# Legible runs

A run is legible when someone who opens only the run page (or the PR comment) learns what was
decided, why, and what to do next, without opening a step log. Each pattern below comes from a run
that had to be dug through.

## Patterns

1. **One decision line per job in `$GITHUB_STEP_SUMMARY`.** `select: dispatching [9]`,
   `verdict: changes-requested (round 1)`, `report: red, needs-human, ping`. This repository's review
   workflow already writes them; every job that decides something should.
2. **Annotations that name the cause.** `echo "::error title=<short cause>::<what happened and what
   to do>"`. "A CI job failed or was cancelled" is not a cause; "job `web` failed" is.
3. **Name the failing step in the PR comment.** The loop's comment should carry the step name and the
   first error line, so the PR says "review refused: bot actor not in allowed_bots" instead of
   "tooling problem".
4. **Separate failure classes, and say different things for each.**

   | Class | Example | Message | Re-run helps? |
   |---|---|---|---|
   | code | a test fails, the reviewer requests changes | what failed, on which head | no, push a fix |
   | config | `allowed_bots` refusal, missing permission, wrong secret name | the setting to change | no |
   | tooling | usage limit, auth outage, runner died (1 turn, $0, under a second) | "not a review result" | yes |
   | cancelled | superseded head, manual cancel | "cancelled before <step>; nothing changed" | n/a |

   A configuration refusal reported as "tooling problem, re-run the workflow" sends people to re-run
   something that cannot pass. A cancelled run reported as "the agent opened no PR" relabels issues
   and pings the owner for nothing: the report job must read `needs.<job>.result` and treat
   `cancelled` as its own case.
5. **One verdict per run.** The classify step, the status and the PR comment must agree. A run where
   the classify step said "real review error" while the comment said "tooling problem" left nobody
   sure which to trust.
6. **Surface the cause from swallowed failures.** A step with `continue-on-error: true` shows green;
   the step after it must lift the cause (grep the action's output for the refusal line) into an
   annotation. A script wrapping `gh` must print `gh`'s stderr, not just "gh pr list failed".
7. **Group long output** with `::group::<name>` / `::endgroup::` so the decision lines are not buried.
8. **Job and step names read as a story.** "Fetch CI_CODE_REVIEWER_APP_ID", "Mint the ci-code-reviewer
   agent token", "Drop the app credentials from the job environment", "Run code-reviewer agent",
   "Work out the verdict", "Post the review". A failure then reads as a sentence: the run stopped at
   "Mint the ... token", so the App ID or key is wrong.
9. **Record agent cost in the summary**: turns, duration and cost from the action's execution file,
   one row per issue or PR. Without it, "how much of the run was orientation?" cannot be answered.
10. **A short "how to read this run" note in the workflow header**: which job decides, which status is
    authoritative, which classes exist. This repository's review workflow header is the model.

## Public logs

In a public repository logs are public. Never echo a secret, an untrusted PR field, or a matched
private term. Report a location (commit, file, line, entry number), never the match: see
`scripts/check-private-terms.sh`.

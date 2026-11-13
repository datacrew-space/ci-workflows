# The PR flow: issues, drafts, review loop, stacks

## Work starts from an issue

- The queue is a label (`ready-for-agent`). Before building, name the issue the work belongs to; file
  one first if it is new work.
- Branch `<type>/<issue>-<slug>` (for example `feat/12-forecast`). The branch name is the flag that
  ties a worktree to its issue; a SessionStart hook can print that one issue instead of a whole queue.
- The PR body carries `Closes #N`, or `Refs #N` for partial work. A PreToolUse hook that refuses
  `gh pr create` without a link turns this from a habit into a guarantee.
- `Closes #N` only when the merge itself meets every acceptance criterion. Read the criteria before
  choosing. A criterion that needs a post-merge check (for example "a real bot-initiated review
  reaches a verdict") means `Refs #N`; close the issue by hand after the check, saying what you saw.

## Drafts and the ready signal

- Open PRs as drafts. The review job skips drafts, so early pushes cost nothing.
- When the work is done, the author adds a `ready-for-review` label. A workflow marks the draft ready
  once `ci-ok` is green on its head, which starts the review, then removes the label. For a stacked
  PR it waits until the PR below is ready. A failed flip or a failed label removal fails the job red.
- That workflow uses `pull_request_target`/`workflow_run`, so it first runs after it merges. Label one
  green draft right after merging and watch that run; until then it is unverified.

## The review loop

Read the header of `.github/workflows/claude-code-review.yml` for the flow. The parts that matter
when configuring or operating it:

- A PR ends **green** (approved, ready to merge) or **red**. Red with `needs-human` and an @mention
  means the code needs a decision; red without the label must say which failure class it is (see
  [legible-logs.md](legible-logs.md)).
- Round 2 exists only after a responder push (the event sender is authenticated by GitHub). Any other
  push starts again at round 1, so the two-round cap does not bound an author who keeps pushing.
- **Head binding.** Each push cancels the previous review run, but a "Review loop: approved" comment
  can still arrive for a superseded commit. Only the `review-verdict` commit status is bound to a SHA.
  Before merging, read `review-verdict` and `ci-ok` on the current head SHA. When building the loop,
  skip posting the comment (and the responder's table) when the PR head no longer matches the
  reviewed SHA, and post the responder's table only after its push succeeded: a refused push otherwise
  leaves "Fixed in <sha>" for a commit that never landed.
- **One fixer per round.** If the author also pushes while the responder works, both fix the same
  findings in different checkouts. Let the responder finish, or hold the PR as a draft.
- **Approvals with findings.** The responder runs only on `changes-requested`, so judgement calls on
  an approval sit unhandled, and some are real bugs. Until the loop handles them, the rule is: for
  every review, approved or not, fix each finding, decline it with a one-line reason, or file it as
  an issue, and say which on the PR. Never "fix" a finding you disagree with just to clear it.
- **Cost of rounds.** Each push after an approval buys another full review. Triage a round in one
  push. After an approval that carries only judgement calls, file them instead of pushing again
  unless one is a real defect.
- Make `review-verdict` a required check only after head binding and failure classes work; until
  then a PR (or a whole stack) can merge without any review having run, which happened to a draft
  child merged with its stack.

## Stacked PRs

- Default: one PR per issue off the default branch, started when its blockers have merged. Use a stack
  only for a true linear chain where waiting on review would block you. A fan-in (one issue blocked
  by several) cannot be a stack.
- Open the child with its base set to the parent's branch, then register the stack (`gh stack link
  <bottom> <top>`). GitHub does not infer a stack from bases; an unregistered chain loses the
  auto-retarget, and merging the parent with branch deletion closes the child for good.
- Merge bottom-up. A stacked PR cannot be merged with `gh pr merge`; it needs GitHub's asynchronous
  merge endpoint with the head SHA pinned, or the web UI. If your merge tooling blocks that endpoint,
  a human merges the stack in the UI.
- A stack must stay linear. When the trunk moves, use "Rebase stack" (or `gh stack rebase`); this is a
  force-push of every branch, so it is the owner's call. Merging the trunk into a stacked branch
  instead breaks linearity.
- Review each layer: a child merged while still a draft was never reviewed.
- Keep unrelated work (hooks, tooling) out of a stacked feature branch; reviewers flag it every round.

## "Are we done?"

Answer from facts GitHub records, not from memory: PR merged or draft, `ci-ok`, `review-verdict`,
`needs-human`, issue closed. One line per issue with the next action. A PR owns an issue only through
`Closes #N` or a numbered branch; `Refs` is related work.

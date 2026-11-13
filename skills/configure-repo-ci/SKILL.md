---
name: configure-repo-ci
description: Set up or audit a repository's CI, CD and branch configuration with this repository's conventions (one `ci-ok` gate, pre-commit as the lint definition, per-role GitHub Apps, the review loop, public-repo guards). Use when adding CI to a repo, wiring the review loop or an issue dispatcher, creating a GitHub App or token for automation, setting branch protection, merge or stack rules, or reading a confusing workflow run.
---

# Configure a repository's CI, CD and branches

The reference implementation of the review loop is this repository: `.github/workflows/claude-code-review.yml`,
the scripts in `scripts/`, the prompts in `examples/`, the guards in `docs/guards.md`. Copy shapes from
there; this skill says which shape, in what order, and what bit us. This repository's own `ci.yml`
has no aggregate `ci-ok` job and does not run pre-commit: for those two, follow
[references/baseline.md](references/baseline.md), or the reusable Python CI workflow and its
scaffold once they are merged here.

## Steps

1. **Survey before changing.** Record the repo's default branch, visibility, plan (free personal
   accounts return 404/403 for protection on private repositories: write "not available", never
   "none"), merge settings,
   workflows, pre-commit config and existing bots. Compare against
   [references/known-variations.md](references/known-variations.md) to tell an intentional difference
   from drift. Done when every row of that file's checklist has a value.
2. **Build the gate.** One workflow, every job on the runner variable, one aggregate `ci-ok` job,
   lint = `pre-commit run --all-files`. See [references/baseline.md](references/baseline.md).
   Done when `ci-ok` goes green on a PR and red on a PR with a deliberately failing test.
3. **Protect the default branch** to require `ci-ok` only, no force-push, no deletion. Ask the
   owner before changing protection, rulesets or merge settings: they are the owner's call.
4. **Add automation one role at a time.** Each role (review, respond, report, implement, publish) gets
   its own GitHub App and per-job tokens. Follow [references/github-apps.md](references/github-apps.md).
   Done when the role's "Mint ... token" step is green on one controlled run.
5. **Wire the PR flow**: review loop, ready label, issue links, stacks. See
   [references/review-loop.md](references/review-loop.md).
6. **Make every run legible**: one decision line per job in the step summary, annotations that name
   the cause. See [references/legible-logs.md](references/legible-logs.md). Done when a failed run's
   cause is readable from the run page without opening a step log.
7. **Write down what agents need.** Agent prompts, AGENTS.md rules, hooks. See
   [references/agents-and-skills.md](references/agents-and-skills.md).
8. **Prove it live, smallest first.** One structural test per invariant, then one real run with the
   smallest blast radius, then a "validated / not verified" list in the PR body.

## Rules that cost us when broken

- **Name every bot** that opens or pushes PRs in `allowed_bots`, and pin the list with a test. The
  review action refuses bot actors otherwise, and the failure looks like a tooling problem.
- **Act only on the head SHA.** An approval comment can arrive for a superseded commit. Check the
  `review-verdict` status on the current head before merging.
- **Give the reviewer `issues: read`**, or the Spec half of every review is silently skipped.
- **One secret per fetch.** Fetch each credential by name; a folder fetch leaves other roles'
  secrets in an agent's environment.
- **Read the switch-on order before flipping a variable**: set a dispatch limit of 1 before enabling.
- **`Closes #N` only when every acceptance criterion is met by the merge.** A criterion that needs a
  post-merge check means `Refs #N`, closed by hand after the check.
- **A rerun is not a test of a workflow fix**: it reuses the original workflow file. A human-triggered
  event is not a test of a bot-path fix.
- **Public repo**: no internal names, hosts, emails or account names anywhere, including PR text and
  logs; run the guards. Self-hosted runners never serve a public repo.
- **Never merge your own PR**, never bypass a red required check, never `--no-verify`.

# Baseline: the gate, the runners, the branch

The shape every repository starts from. Each rule says why, so a variation can be judged against it.

## The aggregate gate: `ci-ok`

- One CI workflow on `pull_request`, `push` to the default branch and `workflow_dispatch`.
- Real jobs (`lint`, `test`, and per-stack jobs such as `web`) run in parallel.
- A final job named `ci-ok` with `if: always()` and `needs:` every real job. It fails when any needed
  job's result is `failure` or `cancelled` (the condition used in production is
  `contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')`); a skipped job
  passes. Make its error line name the failing job, not just "a job failed".
- Branch protection requires `ci-ok` and nothing else. Adding, renaming or splitting a job then never
  touches protection. A required check that stops running blocks every PR, so renaming a required
  check is a migration, not an edit.
- A change classifier job (outputs which areas changed) may skip heavy jobs; skipped counts as passing.
  `on.paths` on the workflow does not work with a required check: the check never reports.

## Lint is pre-commit

- `.pre-commit-config.yaml` is the single definition of the checks. CI's lint job runs
  `pre-commit run --all-files --show-diff-on-failure`, so local hooks and CI cannot drift and a
  `--no-verify` commit is still caught on the PR.
- Skip in CI only what another job runs anyway (for example `SKIP=pytest` when `test` runs the suite).
- Install both stages once per clone (`default_install_hook_types: [pre-commit, pre-push]`): fast
  checks on commit, slow checks (browser tests in the CI image) on push.
- Include `actionlint` for workflows, a secret scanner, and a large-file check.

## Runners

- Every job uses `runs-on: ${{ fromJSON(vars.CI_RUNS_ON || '"ubuntu-latest"') }}`. Moving between
  runner pools is then a repository variable, not an edit.
- A private repository may use a self-hosted pool. A **public** repository must not: a fork PR would
  run a stranger's code on the host. Restrict a self-hosted runner group to private repositories.
- If hosted runners are unavailable to an account, the fallback label can never go green; say so in
  the workflow header so nobody debugs it.
- Toolchains come from setup actions (`setup-uv`, `setup-python`, `setup-node` with
  `node-version-file`), not from the runner image, so local and CI versions come from the same file.

## Workflow hygiene

- `permissions: contents: read` at the top; widen per job.
- `timeout-minutes` on every job.
- `concurrency` cancels superseded PR heads and never a run on the default branch:
  `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`.
- Pin third-party actions by commit SHA with a version comment (this repo's
  `tests/test_review_hardening.py` pins its action SHAs). Lint workflows with actionlint.
- No untrusted event text (`github.event.pull_request.title`, bodies, branch names) interpolated
  into a `run:` line; pass it through `env:`.
- `pull_request_target` and `workflow_run` run the copy of the workflow on the default branch. Such a
  workflow cannot be tested on its own PR; it runs for the first time after merge. Plan that first run.

## Branch and merge settings

| Setting | Baseline | Why |
|---|---|---|
| Required checks | `ci-ok` only | Job changes never touch protection. |
| Required reviews | 0 | Agent and bot reviews are status-based, not GitHub approvals. |
| Force push, deletion | blocked | |
| `enforce_admins` | the owner's call | Whatever it is set to, nobody merges with `--admin`: fix the red check instead. |
| Delete branch on merge | on | |
| Auto-merge | off, or gated on review | Auto-merge on green CI alone merges unreviewed work. Where it is on, a draft is the only hold. |
| Merge method | squash for feature PRs | Stacks need the method GitHub's stack merge accepts; check before merging one. |

Protection and rulesets are not available on a free personal account's private repositories: the API
answers 404 or 403. Report that as "not available", not "no protection". Changing any of these is the
owner's decision.

## CD

- Packages: build and publish on a version tag, from a workflow that holds the publish credential only
  in the publish step.
- Static sites: deploy on push to the default branch behind a GitHub Environment and `concurrency`.
- Long-running services on your own hosts: deploy from the host's canonical checkout with its own
  deploy script, not from a CI job, a worktree or a temp directory. Persistent settings live in the
  host repo's compose and config, reviewed.
- Recurring application jobs belong on a scheduler built for them, not on Actions cron. Repository
  hygiene jobs (for example a weekly guard run over the full history) stay in Actions.

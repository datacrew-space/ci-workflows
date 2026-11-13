# GitHub Apps and tokens

Automation acts through GitHub Apps, one App per role, so the PR timeline says who did what and no
agent job ever holds write power. The reference is `.github/workflows/claude-code-review.yml` in this
repository; read its header before changing anything here.

## One App per role

| App | Role | Agent token (the agent's step) | Second token (a step with no agent) |
|---|---|---|---|
| `ci-code-reviewer` | reviews a PR | contents, pull-requests, **issues**, checks, actions, statuses: read | pull-requests: write, to post the review the agent wrote to a file |
| `ci-review-responder` | answers a `changes-requested` review | contents read; pull-requests and issues write (its reply); checks, actions, statuses read | contents: write, in a separate push job |
| `ci-review-orchestrator` | owns the loop: the `review-verdict` status, the "Review loop" comment, the `needs-human` label | none (no agent runs in its job) | statuses, pull-requests, issues: write |
| `ci-implementer` | builds an issue into a PR (dispatcher) | contents, issues, pull-requests, checks, actions, statuses: read | contents and pull-requests write, issues read, in a separate publish job |

- No App gets the **Workflows** permission. GitHub then refuses any push that touches
  `.github/workflows/`, a second lock behind the workflow's own protected-path guard
  (`scripts/protected-paths.sh`).
- The reviewer needs `issues: read`. Without it the linked issue "does not resolve", the Spec axis is
  skipped on every review, and the reviewer reports it as if the PR were at fault. This
  repository's reusable workflow does not grant it yet (2026-11-13); add it with a test when you
  adopt the loop.
- Do not reuse one role's App for another job's write (for example the orchestrator's token to flip a
  draft): it blurs the timeline and widens what one leaked key reaches. Accept it only as a written,
  temporary decision.

## Minting tokens

- Mint per job with `actions/create-github-app-token`, scoped by `repositories:` (the one repository)
  and by `permission-*` inputs. A token requesting a permission the App lacks is refused, which is a
  useful check.
- Agent step first with the read token; anything that needs write power uses a token minted **after**
  the agent step, from keys fetched again.
- Before the agent runs, clear the App keys from the environment. Prefer fetching only the secrets
  that job needs: this repository fetches **one secret per step by name** (`secret-name`, secrets
  action pinned by SHA), and `tests/test_review_workflow.py` pins the list of names each job may hold.
  A whole-folder fetch plus a "drop names matching `_PRIVATE_KEY`" step leaves every other secret in
  that folder (for example another agent's service token) in the agent's environment.
- The agent's commits carry the App's bot identity. The author id comes from the `responder-bot-id`
  input (its default is the stock responder App's id), so a caller with its own App must set it.
- The agent never pushes. It commits locally, hands a git bundle to a job with no agent, and that job
  verifies the bundle, runs the protected-path guard and the secret scan, and pushes without force.

## `allowed_bots`: the trap

`anthropics/claude-code-action` refuses to run when the triggering actor is a bot that is not listed in
`allowed_bots`. Every bot whose push or PR can trigger a review must be named: the responder (its push
starts round 2) and the implementer (it opens PRs). Never `*`.

- Pin the list with a test that fails if a PR-opening bot is missing.
- The refusal appears only in the action step's log. With `continue-on-error` on that step the job
  can report it as a generic failure; classify it as a configuration failure (see
  [legible-logs.md](legible-logs.md)).
- Verifying the fix needs a **bot-initiated** event. Closing and reopening the PR as a human passes the
  bot check whether or not the bot is listed, and re-running the old run reuses the old workflow file.

## Where keys live

- App ID and private key per App in the secret store, under one folder for GitHub Apps, fetched by
  exact name. The repository holds only the secret store's machine identity as repository secrets.
- Never print a key or token. Prove presence by length or by the mint step going green.
- After storing a new `.pem`, delete it from disk: the secret store is its only home.
- Long-lived personal access tokens in repository secrets are drift. Replace them with an App token
  scoped to the job.

## Install scope

Install each App on the selected repositories it serves. "All repositories" works because every
token names its repository, but a leaked private key then reaches everything the App is installed on.
Record the choice and the date in the setup doc.

## Create and validate a new App

1. Create the App on the owning account: webhook inactive, installable only on that account, the
   permission rows from the table above, nothing else.
2. Note the App ID, generate a private key, install on the selected repositories.
3. Store `<ROLE>_APP_ID` and `<ROLE>_PRIVATE_KEY` in the secret store; delete the `.pem`.
4. Add the bot to `allowed_bots` if it pushes or opens PRs, with the pinning test.
5. Validate with the smallest real run: the "Mint ... token" step goes green. A red mint step means a
   wrong App ID, a truncated key, or the App not installed on the repository.
6. Write the steps, permissions table and validation result into a setup doc in the consuming
   repository, so the next agent does not have to rediscover whether the App exists.

An App that is private does not appear on the public `apps/<slug>` endpoint (404). That proves
nothing about whether it exists.

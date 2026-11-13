# ci-workflows

Reusable GitHub Actions workflows for an agentic software factory: the **Claude code-review loop**, an
**issue dispatcher** that starts agents on ready issues, and a **ready-when-green** gate. Each is a
`workflow_call` workflow you pin by commit SHA; nothing here names a host, an account or a secret store.
See [Factory pieces](#factory-pieces) for the rest.

A pull request gets an independent review from a reviewer agent. If the reviewer asks for changes,
a responder agent may push small fixes to the PR branch, and the PR is reviewed once more. The loop
ends **green** (ready to merge) or **red** with a `needs-human` label. See the header of
[`claude-code-review.yml`](.github/workflows/claude-code-review.yml) for the full flow.

> **Status: unproven end to end.** The scripts and the workflow's structure are covered by tests
> (`tests/`), but the workflow has not been run against a live repository yet. Do a dry run on a test
> repository before relying on it. Treat it as v0.

## Use it from a repository

1. Create the three GitHub Apps (`ci-code-reviewer`, `ci-review-responder`, `ci-review-orchestrator`)
   and store their `*_APP_ID` / `*_PRIVATE_KEY` pairs, plus `CLAUDE_CODE_OAUTH_TOKEN`, in your
   Infisical project (see the workflow header for the exact names and folders).
2. Add the repo secrets `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET` (a machine identity).
3. Copy [`examples/caller.yml`](examples/caller.yml) to `.github/workflows/claude-code-review.yml`
   and fill in the placeholders. Pin the `uses:` ref **and** `tooling-ref` to the same commit SHA.
4. Copy the two agent prompts from [`examples/`](examples) to `.agents/agents/` (merge them first: the loop reads them from the base branch) and edit the rules to
   fit the repository. They are templates; the parts marked "CI contract" are read by the workflow.
5. Set the repository variable `CLAUDE_REVIEW_ENABLED=true` when you want it on.

Updating every repository is then a one-line bump of the pinned SHA in each caller.

## Inputs

| Input | Meaning |
|---|---|
| `infisical-domain`, `infisical-project-slug` | Where the secrets live (required, yours) |
| `infisical-env-slug` | Default `prod` |
| `infisical-claude-path`, `infisical-github-path` | Folders for the OAuth token and the Apps; default `/claude`, `/github` |
| `runs-on` | JSON runner label for the review, respond and report jobs; default `"ubuntu-latest"` |
| `push-runs-on` | JSON runner label for the push job. Must differ from `runs-on`. Empty disables the responder |
| `base-branch` | Branch PRs must target for the loop to run; default `main`. A PR on any other base (a stacked PR) is skipped, because the reviewer's diff would include its parent's commits |
| `protected-paths-extra` | Extra always-protected paths, one bash glob per line, for files inside otherwise editable directories (auth modules, generated files). Adds to the default-deny guard and can never allow a path |
| `tooling-ref` | Commit of this repository to take the guard, scanner and report scripts from |
| `reviewer-agent`, `responder-agent` | Agent prompt paths in the calling repository. They are read from the **base branch**, not from the PR (see design notes). If a file is not on the base yet, the generic template from `tooling-ref` is used, with a warning |
| `allowed-bots` | Comma-separated logins of every other bot that opens or pushes PRs (see the lesson below). Letters, digits and `-` only; `*` is refused. Default empty |

## Upgrading: the reviewer App needs Issues: read

The reviewer's token now asks for `issues: read`, so the Spec axis can read the issue a PR closes.
`actions/create-github-app-token` **fails when it asks for a permission the installed App does not have**,
so an existing `ci-code-reviewer` App must be granted **Issues: read** (and the installation must accept the
change) before you move a caller to this version. Until then the review job fails at the token step.

## Lesson learned: `allowed-bots`

`claude-code-action` refuses to start on a PR whose actor is a bot unless that bot is listed in
`allowed_bots`. The review loop always allows its own responder. **Every other bot that opens or pushes
PRs must be added too**, for example the issue dispatcher's GitHub App: otherwise its PR gets no review,
the run fails before it starts, and `review-verdict` never leaves `pending`. This workflow exposes it as
the `allowed-bots` input (added together with the dispatcher; before that only the responder could be
allowed): `allowed-bots: my-implementer-app` (the app's slug, without `[bot]`). It only adds to the list
and never accepts `*`. **Not yet validated:** the dispatcher run described below stopped at PR-open; a
bot-initiated review of that PR has not been observed.

## Factory pieces

Three more pieces, built and exercised in another repository (author: Jae Wilson) and generalised
here. All share one convention: the caller owns the triggers, secrets are plain GitHub secrets by
default (Infisical is an option), runners come from a `runs-on` JSON input, and the decision scripts are
checked out from this repository at `tooling-ref`, never from the repository being worked on.

| Piece | Status |
|---|---|
| Issue dispatcher | Partly validated live; see below |
| Ready when green | **Never run live**; unit-tested only |
| Work status script | Unit-tested against canned `gh` output |
| Claude Code hooks (examples) | Unit-tested; used in interactive sessions before being ported here |

### Issue dispatcher (`dispatch-ready-issues.yml`)

**What it does.** When a ticket changes in a way that can unblock work, it finds every open issue with
the `ready-for-agent` label whose blockers are all closed, claims it (`agent-dispatched`), runs an
implementer agent on it, and opens a PR with `Closes #N`. An issue that ends with no PR gets
`needs-human` and a comment mentioning the owner; removing the label retries it.

**Setup.**

1. Create a GitHub App installed on the repository with: contents write, pull requests write, issues
   read, checks/actions/statuses read. Not `workflows`: GitHub then refuses a push that edits workflow
   files. Store `IMPLEMENTER_APP_ID` and `IMPLEMENTER_APP_PRIVATE_KEY` as repository secrets (only the
   `publish` job reads them), plus `CLAUDE_CODE_OAUTH_TOKEN`.
2. Copy [`examples/dispatch-caller.yml`](examples/dispatch-caller.yml) to
   `.github/workflows/dispatch-ready-issues.yml` and pin `uses:` and `tooling-ref` to one commit SHA.
3. Copy [`examples/ticket-implementer.md`](examples/ticket-implementer.md) to
   `.agents/agents/ticket-implementer.md` and edit it. The part marked "CI contract" is read by the workflow.
4. Add the app's slug to `allowed-bots` in your review caller (see the lesson above).
5. Set the repository variable `ISSUE_DISPATCH_ENABLED=true`.

**Inputs** (all optional). `runs-on` (JSON, default `"ubuntu-latest"`), `tooling-ref`, `agent-prompt`
(default `.agents/agents/ticket-implementer.md`), `ready-label`, `claimed-label`, `needs-human-label`,
`owner-handle` (empty = repository owner) and `mention-owner`, `draft` (default `true`: see below),
`dispatch-limit` (default 2),
`setup-command` (for example `uv sync --frozen`), `extra-allowed-tools`, `extra-protected-paths`,
`mcp-config` and `mcp-allowed-tools` (an MCP server for the agent; **off by default**), and
`secret-source` (`github` by default, or `infisical` with `infisical-domain`, `infisical-project-slug`,
`infisical-env-slug`, `infisical-claude-path`, `infisical-github-path`).

**Draft PRs.** The agent's PR opens as a **draft** by default (`draft: true`). In a repository whose
automation merges any non-draft PR on green CI, "ready" can mean "merged", so an agent-written PR must
not become ready without a recorded review. A draft is skipped by the review loop until it is marked ready;
`ready-when-green` (below) is the documented path from draft to ready, once a human or an author adds its
label. Set `draft: false` only if nothing in your repository merges on green CI alone.

**Concurrency.** Select-and-claim is serialised inside the workflow (one group per repository), so two
overlapping runs cannot claim the same issue. The example caller's own `concurrency` block uses a different
group name on purpose, and the two label names in the example callers' `if:` are coupled to the
`ready-label` and `needs-human-label` inputs: if you rename a label there, rename it in the caller's `if:` too.

**Security model.** The job boundary is the boundary.

- `select` runs no agent. It picks (`scripts/select_dispatchable.py`) and claims.
- `implement` is the only job with an agent. It holds the job's own read-only `GITHUB_TOKEN` and **no
  GitHub App key**: an agent that can run code can read the secrets of its own job, so the key lives
  only in `publish`. With `secret-source: infisical` it uses a separate machine identity
  (`INFISICAL_AGENT_CLIENT_ID`/`_SECRET`) that can read only the model credential (and `MCP_TOKEN`)
  under `infisical-claude-path`. Its git rules are exact commands (no `git fetch:*`, which admits
  `--upload-pack=CMD`), its edits are confined to the workspace, and `.git/` is denied. It hands over a
  git bundle and the PR text as an artifact.
- `publish` runs no agent and executes nothing the agent wrote. It rebuilds the branch in a fresh
  repository, judges the diff against the default branch as GitHub reports it now with
  `scripts/implementer-protected-paths.sh` (NUL-separated paths, fails closed), refuses a commit message
  that would close an issue (`scripts/reject-closing-keywords.sh`; a squash merge copies commit messages
  into its body), sanitises the PR text (`scripts/sanitize-pr-text.sh`: no stray `Closes`, no live
  `@mentions`), then pushes without force and opens the PR with the only write token. It ignores global
  git config.
- `report` runs no agent and makes sure every claimed issue ends with a PR or a human.
- The agent may not change `.github/`, `.claude/`, agent prompts and skills, `AGENTS.md`/`CLAUDE.md`/
  `CLAUDE.local.md`/`.mcp.json` at any depth, lint config, `scripts/check-*` and `scripts/check_*`, all of `.agents/`, its own prompt, or anything
  in `extra-protected-paths`.
- **Linux runners only.** The tool paths and `base64 -w0` assume GNU tools on Linux.
- **Runners: GitHub-hosted or ephemeral self-hosted only.** On a persistent runner the agent can leave
  files behind (`~/.gitconfig`, a binary earlier on `PATH`) for the next job; a persistent runner is not
  a boundary.
- Accepted risk: the agent runs your repository's code (your tests) with the OAuth token in its
  environment, as the review loop does. Use a token that can write nothing that matters. If you wire an
  MCP server, give it a read-only identity and list read-only tools only.

**Validated vs unverified.** An earlier form of this workflow was run live once, in the repository it
came from: select, claim, implement, publish and opening the PR all worked. **That run used an earlier design** in which the agent
job minted a read-only App token; this version drops the App key from the agent job (it uses the job's
own read-only `GITHUB_TOKEN`, commits as `github-actions[bot]`) and tightens the agent's tool list, a
separate Infisical identity for the agent, commit-message and nested-instruction-file checks. None of
that has run live. **Not validated:** a bot-initiated review of the
resulting PR. The generalised inputs in this repository (defaults, `secret-source: github`, `mcp-config`,
`extra-*`) have only had structural tests (`tests/test_factory_workflows.py`) and no live run, and
`secret-source: infisical` has not been run for this workflow either. Dry-run on a test repository first.

### Ready when green (`ready-when-green.yml`)

**What it does.** An author that has finished adds the `ready-for-review` label to its draft PR. Once
the `ci-ok` check is green on its head, the PR is marked ready (which starts the review loop) and the
label is removed. A stacked PR waits while the PR it is based on is still a draft. No label, no flip.

**Setup.** Make a GitHub App (pull requests write, issues write, checks read, statuses read; it can be the same app as another) and
store `READY_APP_ID` and `READY_APP_PRIVATE_KEY`. Copy
[`examples/ready-when-green-caller.yml`](examples/ready-when-green-caller.yml), set the CI workflow name
it listens to, pin the SHA, and set `READY_WHEN_GREEN_ENABLED=true`. Your CI must publish a check called
`ci-ok` (or set `ci-check`): one required job that depends on all the others.

**Inputs.** `runs-on`, `tooling-ref`, `ready-label` (default `ready-for-review`), `ci-check`
(default `ci-ok`), `secret-source` and the Infisical coordinates.

**Security model.** `pull_request_target` and `workflow_run` run the caller file from the default branch;
the job checks out only this tooling repository, never PR code, and no event text reaches a shell. The
app token is needed because a PR marked ready with `GITHUB_TOKEN` would start no workflow. Fork PRs are
never touched, and a missing or pending `ci-ok` is never read as green.

**Validated vs unverified.** Unit-tested (`tests/test_ready_when_green.py`). **It has never run live**, so the trigger wiring, the token scopes and the flip itself are
unverified.

### Work status (`scripts/work_status.py`)

`python3 scripts/work_status.py [ISSUE ...]` prints one line per issue: its PR, whether `ci-ok` and
`review-verdict` are green, and the next action (`mark ready`, `fix CI`, `waits on #N`, `a human merges`,
and so on). Every field is a fact GitHub records; nothing depends on an author ticking a box. Needs
`gh` logged in. Names are overridable with `WORK_QUEUE_LABEL`, `WORK_CI_CHECK`, `WORK_VERDICT_CHECK` and
`WORK_NEEDS_HUMAN_LABEL`. Unit-tested against canned `gh` output only.

### Claude Code hooks (copy-me examples in `examples/hooks/`)

Not reusable workflows: copy the two scripts to `.claude/hooks/` and the entries from
[`settings.example.json`](examples/hooks/settings.example.json) into `.claude/settings.json`.

- `require_issue_link.py` (PreToolUse on Bash) blocks `gh pr create` unless that command names an issue
  (`Closes #N`, `Refs #N`). It reads the command's own text or `--body-file`, ignores text that merely
  mentions a PR create, and fails closed on input it cannot parse.
- `session_start_issues.py` (SessionStart) tells the session which issue it is on, from a
  `feat/<issue>-<slug>` branch name, or lists the `ready-for-agent` queue, in at most ten lines. It is
  silent on any failure.

Both assume the label `ready-for-agent` and that branch convention; edit the constants. Unit-tested.

## Design notes

- **The gate cannot be weakened by the PR it grades.** The protected-path guard, the secret scanner
  and the report script are checked out from this repository at `tooling-ref`, never from the
  pull request.
- **The agents never hold write power.** They get read-mostly tokens, and neither can run
  repository code. The responder's commits travel as a git bundle to a separate job that holds no
  agent, verifies the bundle, runs the guard and the secret scan, and pushes without force.
- **A PR cannot edit its own reviewer.** The reviewer and responder prompts are taken from
  `origin/<base-branch>` ([`scripts/fetch-agent-prompt.sh`](scripts/fetch-agent-prompt.sh)),
  validated (regular file, text, at most 100 KB, starts with a `---` front-matter line) and copied
  to a runner temp folder that the agent follows. A change to `code-reviewer.md` takes effect for
  the PRs after the one that merges it. *First adoption:* when the file does not exist on the base
  yet (the PR that adds the loop), the review falls back to the generic template in
  [`examples/`](examples) from the pinned tooling checkout, with a visible warning and a line in the
  step summary. That template is not controlled by the PR, so this does not let a PR pick its own
  reviewer. Failing closed instead would leave the adoption PR red with nothing to fix, and the
  review is advisory next to the required checks. A file that exists on the base but is invalid is
  an error, never silently replaced. Merge your own prompt files first to get your own rules.
- **The push job verifies, then pushes.** All checks on the agent's bundle live in one script,
  [`scripts/verify-fix-bundle.sh`](scripts/verify-fix-bundle.sh): size cap, bundle integrity, the
  reviewed commit, ancestry, a 20-commit cap, the guard on the net diff and on every commit, and the
  secret scan, all in a fresh repository. It runs before any token is minted; the workflow keeps only
  token minting, the call and the final non-force `git push`. It is tested with real bundles
  (`tests/test_verify_fix_bundle.py`).
- **Default-deny guard.** [`scripts/protected-paths.sh`](scripts/protected-paths.sh) allows only
  source, test and documentation paths; scripts, workflows, Dockerfiles, compose files, configs,
  dependency manifests and agent instructions are always refused.
- **Every commit is judged, not just the net change.** [`scripts/guard-commits.sh`](scripts/guard-commits.sh)
  checks each responder commit against the guard and refuses merge commits, so a protected file
  changed in one commit and put back in the next cannot reach history on a merge or rebase.
- **Merging while the review is pending.** If your repository auto-merges ready PRs, make the review
  gate part of the required check (for example, call this workflow as a job of your main CI workflow
  and add it to the aggregate job's `needs`; a skipped review then counts as a pass, which is also the
  rollback). Keep `edited` in the caller's `types` (see `examples/caller.yml`): a stacked PR that GitHub
  retargets to the base branch fires only `edited`, and without a run on it the green status from the
  skipped run would let it merge unreviewed. A required status that is never posted blocks every PR.
- **One secret per fetch.** Each secret is fetched by name so an agent's environment holds only what
  it needs. The Infisical action is pinned by commit SHA to a version that honours `secret-name`.

## Develop

```bash
pip install pytest pyyaml
python -m pytest tests -q
bash tests/test-classify-claude-review.sh   # needs jq
```

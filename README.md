# ci-workflows

Reusable GitHub Actions workflows. Today it holds one: the **Claude code-review loop**.

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

## Skill: configure a repository's CI

[`skills/configure-repo-ci/`](skills/configure-repo-ci/SKILL.md) is an agent skill that applies these
conventions to another repository: the `ci-ok` gate, pre-commit as the lint definition, per-role
GitHub Apps and tokens, the review loop, legible run logs, and the known variations and drift seen
across repositories. Copy the folder into your agent's skills directory.

## Develop

```bash
pip install pytest pyyaml
python -m pytest tests -q
bash tests/test-classify-claude-review.sh   # needs jq
```

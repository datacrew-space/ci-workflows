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
4. Copy the two agent prompts from [`examples/`](examples) to `.agents/agents/` and edit the rules to
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
| `tooling-ref` | Commit of this repository to take the guard, scanner and report scripts from |
| `reviewer-agent`, `responder-agent` | Agent prompt paths in the calling repository |

## Design notes

- **The gate cannot be weakened by the PR it grades.** The protected-path guard, the secret scanner
  and the report script are checked out from this repository at `tooling-ref`, never from the
  pull request.
- **The agents never hold write power.** They get read-mostly tokens, and neither can run
  repository code. The responder's commits travel as a git bundle to a separate job that holds no
  agent, verifies the bundle, runs the guard and the secret scan, and pushes without force.
- **Default-deny guard.** [`scripts/protected-paths.sh`](scripts/protected-paths.sh) allows only
  source, test and documentation paths; scripts, workflows, Dockerfiles, compose files, configs,
  dependency manifests and agent instructions are always refused.
- **One secret per fetch.** Each secret is fetched by name so an agent's environment holds only what
  it needs. The Infisical action is pinned by commit SHA to a version that honours `secret-name`.

## Develop

```bash
pip install pytest pyyaml
python -m pytest tests -q
bash tests/test-classify-claude-review.sh   # needs jq
```

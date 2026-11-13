# ci-workflows

Reusable GitHub Actions workflows: a Claude code-review loop (reviewer agent, optional responder
agent, status report). `README.md` explains usage; the header of
`.github/workflows/claude-code-review.yml` explains the flow.

## Layout

- `.github/workflows/` the reusable workflow and this repo's own CI.
- `scripts/` guard, scanner and report scripts. Callers pin and run these at a commit SHA.
- `tests/` pytest and shell tests for the scripts and the workflow structure.
- `examples/` caller workflow and CI agent prompt templates. `agents/` on-demand reviewer agent.
- `skills/` agent skills. `skills/configure-repo-ci/` sets up or audits a repository's CI, CD and
  branch configuration with this repository's conventions.

## Commands

```bash
python -m pytest tests -q                    # needs pytest and pyyaml
bash tests/test-classify-claude-review.sh    # needs jq
pre-commit install --hook-type pre-commit --hook-type commit-msg   # local guards, see docs/guards.md
```

CI (`.github/workflows/ci.yml`) runs the tests, actionlint and both public-repo guards. With no
denylist on your machine, the private-terms hook skips with a warning; in CI it fails closed.

## Rules

Org-wide rules: [`docs/org/working-rules.md`](docs/org/working-rules.md). Vocabulary:
[`GLOSSARY.md`](GLOSSARY.md). Org context and the repository map: [`docs/org/README.md`](docs/org/README.md).

Specific to this repository:

- It is public, and so are its PR text and CI logs. Write nothing private (see *Public by default*
  in `docs/org/working-rules.md`); use placeholders. Never echo a secret or an untrusted PR field
  into a log.
- Commit with a GitHub noreply address. Repository git config is not versioned, so set it on each
  fresh clone: `git config user.email <id>+<login>@users.noreply.github.com`. The `commit-identity`
  hook refuses anything else.
- The guard, scanner and report scripts live in `scripts/`, and callers run them pinned to a commit
  SHA (`tooling-ref`). This repository's own CI runs them from `main` by design; see
  [`docs/guards.md`](docs/guards.md). Never weaken one to make a change pass. Rules, hooks and the CI gate are in
  [`docs/guards.md`](docs/guards.md).
- Never force-push, never push to `main`, never merge your own PR.

## Agent skills

### Issue tracker

GitHub Issues on `datacrew-space/ci-workflows`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root, and `docs/adr/` (not created yet). See
`docs/agents/domain.md`.

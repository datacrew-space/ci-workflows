# ci-workflows

Reusable GitHub Actions workflows: a Claude code-review loop (reviewer agent, optional responder
agent, status report). `README.md` explains usage; the header of
`.github/workflows/claude-code-review.yml` explains the flow.

## Layout

- `.github/workflows/` the reusable workflow and this repo's own CI.
- `scripts/` guard, scanner and report scripts. Callers pin and run these at a commit SHA.
- `tests/` pytest and shell tests for the scripts and the workflow structure.
- `examples/` caller workflows and CI agent prompt templates. `agents/` on-demand reviewer agent.

## Commands

```bash
python -m pytest tests -q                    # needs pytest and pyyaml
bash tests/test-classify-claude-review.sh    # needs jq
pre-commit install --hook-type pre-commit --hook-type commit-msg   # local guards, see docs/guards.md
```

## Public repo rules

This repository is public. Everything in it, in commits, in PR text and in CI logs is world-readable.

- No private names: no internal hostnames, account or repo names other than this one, personal
  emails, local paths or usernames, internal tool names. Use placeholders.
- Commit with the noreply identity already set in the repo's git config. Do not change it.
- CI logs are public. Never echo a secret or an untrusted PR field into a log.
- Never force-push, never push to `main`, never merge your own PR.
- Guard scripts live in `scripts/`. Do not weaken them to make a change pass. Rules, hooks and the CI gate are in `docs/guards.md`.

## Agent skills

### Issue tracker

GitHub Issues on `datacrew-space/ci-workflows`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

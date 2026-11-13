# Public-repo guards

These guards keep private information out of public repositories: private repository, host or
account names, personal email addresses and tokens. They run twice: on a developer machine as git
hooks, and in CI as a reusable workflow.

| Check | What it refuses | Where it is defined |
| --- | --- | --- |
| `private-terms` | text matching your private denylist (staged lines, commit messages, commit history) | `scripts/check-private-terms.sh` |
| `commit-identity` | an author, committer or `Co-authored-by` email that is not an allowed address | `scripts/check-commit-identity.sh` |
| `detect-private-key`, `gitleaks` | private keys and tokens | pre-commit hooks |

## Install the hooks

```bash
pip install pre-commit   # or: uv tool install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Both hook types are needed: `private-terms-message` runs at `commit-msg`, the rest at `pre-commit`.

## The denylist

The denylist must never live in a public repository, because the list itself would publish the names
it guards. The scripts read it from, in order:

1. the `PRIVATE_TERMS` environment variable (in CI: a repository secret),
2. the file named by `PRIVATE_TERMS_FILE`,
3. `~/.config/private-terms.txt`.

Line format: one POSIX extended regular expression per line, matched case-insensitively. Blank lines
and lines starting with `#` are ignored. Use `(^|[^a-z0-9])name([^a-z0-9]|$)` to match a whole word
(`\b` is not POSIX). An invalid pattern fails the check instead of silently matching nothing.

With no denylist, a developer machine skips the check with a warning. CI passes `--require`, so a
missing denylist fails closed.

## Why output never shows matches

CI logs of a public repository are public. A report that quoted the matched term, line, path or
address would leak it a second time. The scripts name only the short commit sha, the file and line,
and the number of the denylist entry (or which email field failed). To find the match, open that
file and line and compare it with your own denylist.

## Adopt the guards in another repository

Hooks, in the repository's `.pre-commit-config.yaml` (pin `rev` to a commit SHA you have reviewed):

```yaml
repos:
  - repo: https://github.com/datacrew-space/ci-workflows
    rev: <commit-sha>
    hooks:
      - id: private-terms
      - id: private-terms-message
      - id: commit-identity
```

CI, a workflow that calls the reusable one:

```yaml
jobs:
  guards:
    uses: datacrew-space/ci-workflows/.github/workflows/guards.yml@<commit-sha>
    with:
      tooling-ref: <commit-sha>   # same commit as above
    secrets:
      PRIVATE_TERMS: ${{ secrets.PRIVATE_TERMS }}
```

Inputs: `tooling-ref` (default `main`), `allowed-email-regex` (default: GitHub noreply and the Claude
co-author address), `all-history` (scan every commit; also used on schedule and manual runs). Create
the `PRIVATE_TERMS` repository secret from your denylist. Pull requests from forks receive no
secrets, so they fail closed with a clear message; that is intended.

The scripts and config come from `tooling-ref`, never from the repository under test, so a pull
request cannot weaken the gate that checks it. This repository runs a weekly full-history scan as
well (`guards-history.yml`).

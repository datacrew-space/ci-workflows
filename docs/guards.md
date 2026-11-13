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
pip install pre-commit==4.3.0   # or: uv tool install pre-commit==4.3.0
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
and lines starting with `#` are ignored. Backslash escapes are kept as written. Case-insensitivity
is guaranteed for ASCII letters only; non-ASCII letters are folded only where the system `awk`
does it (gawk in a UTF-8 locale does, mawk does not), so list both spellings of a non-ASCII term. Use `(^|[^a-z0-9])name([^a-z0-9]|$)` to match a whole word
(`\b` is not POSIX). An invalid pattern fails the check instead of silently matching nothing.

With no denylist, a developer machine skips the check with a warning. CI passes `--require`, so a
missing denylist fails closed.

## Why output never shows matches

CI logs of a public repository are public. A report that quoted the matched term, line, path or
address would leak it a second time. The scripts name only the short commit sha, the file and line,
and the number of the denylist entry (or which email field failed). To find the match, open that
file and line and compare it with your own denylist. Be aware that this still tells a reader of the
public log which lines and entry numbers matched, so public text can be correlated with entry
numbers; the entries themselves are never shown. awk's own stderr is discarded for the same reason.

Not scanned: branch and tag names, and a term split across two lines. In `--range` mode a term added
in a merge commit and removed again by a later commit of the same range is not seen (only commits
and the final net diff are); `--all-history` checks every merge against its first parent.

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

CI, a workflow that calls the reusable one (see `examples/guards-caller.yml`):

```yaml
jobs:
  guards:
    uses: datacrew-space/ci-workflows/.github/workflows/guards.yml@main
    with:
      tooling-ref: main
    secrets:
      PRIVATE_TERMS: ${{ secrets.PRIVATE_TERMS }}
```

Inputs: `tooling-ref` (default `main`), `allowed-email-regex` (default: GitHub noreply and the Claude
co-author address; matched case-insensitively), `all-history` (scan every commit; also used on
schedule and manual runs). Create the `PRIVATE_TERMS` repository secret from your denylist. A missing
secret fails the job closed, and so do pull requests from forks, which receive no secrets; that is
intended. `pull_request_target` is not supported and fails the job.

### Trust: who controls the scripts

The scripts and config are read from `tooling-ref`, not from the checkout under test. That protects
the gate only when the caller pins BOTH the `uses:` ref and `tooling-ref` to a trusted ref such as the
base branch. A caller that uses a local `./.github/workflows/guards.yml` or passes a pull request's
head sha runs the pull request's own scripts, so the pull request can make the check pass without a
denylist. Such a caller is a bootstrap, not a gate, and must not be a required check.

This repository's own `ci.yml` uses the trusted `@main` form (workflow and `tooling-ref` both `main`),
so it is a real gate and `guards` can be a required check. The first revision of the guards could not
use that form because the workflow did not exist on `main` yet; it ran the pull request's own copy
and was explicitly not a gate. The weekly `guards-history.yml` run executes on the default branch and
scans the full history.

`ci.yml` and `guards-history.yml` pass `allowed-email-regex` so the owner's own address is accepted
alongside the noreply forms: a squash merge made from the web UI is attributed to the merging account's
primary address, not to the noreply identity the commits were made with.

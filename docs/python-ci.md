# Python CI for uv projects

A reusable workflow, a base pre-commit config and a base ruff config, so every Python repository
lints and tests the same way. The code-review loop and the public-repo guards are separate; this is
the lint and test foundation under them.

| Piece | Path |
|---|---|
| Reusable workflow (`lint`, `test`) | `.github/workflows/python-ci.yml` |
| Base pre-commit config | `configs/python-pre-commit.yaml` |
| Base ruff rules | `configs/ruff-base.toml` |
| Copy-and-edit scaffold | `examples/python-uv-repo/` |

## Adopt it

1. Copy the four files of [`examples/python-uv-repo/`](../examples/python-uv-repo) into your repository
   (the README there lists where each goes).
2. Pick one reviewed commit SHA of this repository. Replace **both** `<commit-sha>` placeholders in
   `ci.yml`: the `uses:` ref and `config-ref`.
3. Create the pre-commit secret baseline once: `uvx detect-secrets scan > .secrets.baseline`.
4. Run `uvx pre-commit run --all-files` and fix or grandfather what the new rules report (see
   [Ruff](#ruff)), then commit.
5. Make `ci-ok` the one required status check.

## The reusable workflow

Two jobs. `lint` runs `pre-commit run --all-files` at the repository root. `test` runs
`uv sync` then `pytest` in `working-directory`.

| Input | Default | Meaning |
|---|---|---|
| `runs-on` | `"ubuntu-latest"` | **JSON** label or label array for both jobs |
| `python-version` | `3.13` | Python for uv |
| `uv-version`, `pre-commit-version` | pinned | Tool versions, pinned so runs are reproducible |
| `working-directory` | `.` | Directory with `pyproject.toml` and `uv.lock`, for `test` |
| `sync-args` | `--locked --dev` | Arguments for `uv sync`. `--locked` fails on a stale `uv.lock` |
| `pytest-args` | `-q --tb=short` | Arguments for pytest (shell-style quoting works) |
| `skip-hooks` | empty | Hook ids to skip in CI (`SKIP`), for hooks the `test` job already runs |
| `config-ref` | empty | Commit whose `configs/ruff-base.toml` the lint job compares your copy to. Empty skips the check |
| `ruff-base-path` | `ruff-base.toml` | Where your copy lives |

Built in, each because a repository once needed it: read-only token, `persist-credentials: false`,
a timeout on every job, scratch files in `RUNNER_TEMP`, uv HTTP retries, a pre-commit environment
cache with retried `install-hooks` (the hook run itself is never retried), and the uv cache only on
GitHub-hosted runners.

### The `ci-ok` gate lives in the caller

A job of a called workflow is reported as `<caller job> / <job>`. A gate defined inside the reusable
workflow would therefore rename the required status check in every repository. So the caller defines
`ci-ok`, `needs` the call and any repository-specific jobs, and branch protection requires only
`ci-ok`. The scaffold's gate uses `if: always()` and fails when a needed job failed or was cancelled;
a gate that is skipped counts as a pass.

### Runners and billing

`runs-on` defaults to GitHub-hosted `ubuntu-latest`. If your account cannot use hosted runners (for
example billing is blocked), a job that cannot start looks like a red or stuck check, not a test
failure. Set a repository variable, for example `CI_RUNS_ON` to `["self-hosted","linux"]`, and pass
`runs-on: ${{ vars.CI_RUNS_ON || '"ubuntu-latest"' }}`, as the scaffold does. The value is JSON, so a
single label needs its quotes. Do not point a **public** repository at a self-hosted runner: a pull
request from a fork could run code on it. A job that died at setup did not pass.

## Pin to a commit SHA

Pin the `uses:` ref and `config-ref` to the same full commit SHA, never `@main`: the workflow runs in
your repository with your token. Updating is one reviewed change per repository, a new SHA in both
places. While this repository's own pull request is open, a consumer may pin the pull request's head
commit; bump it to the merged commit afterwards.

## Pre-commit

pre-commit cannot include another file, so `configs/python-pre-commit.yaml` is a scaffold you copy and
own. Hook versions are pinned. The slots you edit are marked with comments.

### The vendor exclude slot

Vendored or third-party code is copied byte for byte, often with a hash manifest and a test. No hook
may rewrite it. The top-level `exclude` in the config applies to **every** hook and defaults to
`(^|/)(vendor|third_party)/`. Extend that regex for other directories (`generated/`, a fixture dir with
deliberate bad whitespace). A hook-level `exclude` is not enough: you would have to repeat it on every
hook, and a hook you forget rewrites the files.

The ruff base does the same with `extend-exclude = ["**/vendor/**", "**/third_party/**"]` and
`force-exclude = true`. Without `force-exclude`, ruff lints a file it was passed explicitly, which is
how pre-commit passes files, even when the file is excluded.

### Secrets

`detect-secrets` scans every file, **including workflow files**, because a hardcoded credential in a
workflow is a common mistake. Only lines that pin a commit SHA (`uses:` or `config-ref:`) are skipped,
since the entropy plugins flag SHAs. Use `${{ secrets.NAME }}` instead of a literal value.

## Ruff

`configs/ruff-base.toml` pins an explicit rule set (`E4 E7 E9 F BLE S110 S112 ASYNC T20 TRY002 TID251`, line
length 120, `py313`) instead of ruff's default, which changes between releases. Ruff cannot extend a
URL, so each repository keeps a byte-identical copy as `ruff-base.toml`, and its own `ruff.toml` does
`extend = "ruff-base.toml"`.

- Add rules with `extend-select`. **Never write `select =`** in `ruff.toml`: it replaces the base.
- Grandfather existing violations in `[lint.extend-per-file-ignores]`, one file per line. The table
  may only shrink. Never use a repository-wide `ignore` for a base rule.
- When the new rules report their first large number, the checker is what is under test: cluster the
  findings, then baseline. Do not silence the rule.
- Pass `config-ref` and the lint job fails when your copy differs from the pinned upstream file.
- **Logging:** `TID251` bans `logging`, `logging.handlers`, `logging.config`, `loguru` and `structlog`:
  every repository logs through the shared logging package. List legacy files in
  `[lint.extend-per-file-ignores]` with `TID251`, and shrink the list.
- Keep the ruff version in your dev dependencies equal to the `ruff-pre-commit` `rev`.

## Changing the base

A change to a base file is a pull request here, followed by a SHA bump in each consumer. Keep
`examples/python-uv-repo/` copies equal to `configs/`; a test enforces it.

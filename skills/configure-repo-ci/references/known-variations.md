# Known variations

From a read-only survey of about 55 repositories that follow (or predate) this baseline, 2026-11-13.
Use the checklist to survey a repository, then the two tables to decide whether a difference is
intentional or drift. Repository names are left out on purpose; the patterns are what matter.

## Survey checklist (one value per row)

- Default branch; visibility; owner type and plan (protection available or not)
- Protection or ruleset on the default branch: required checks, reviews, `enforce_admins`, linear history
- Merge methods, delete-branch-on-merge, auto-merge
- Workflows and their triggers; single `ci-ok` aggregate or many required checks
- Runners: the runner variable, a self-hosted literal, or hosted
- `.pre-commit-config.yaml` present, and run in CI
- Python floor (`requires-python`, setup versions) and Node source (`.nvmrc`, setup versions)
- Review automation: none, advisory single job, or the full loop; `allowed_bots`
- Tokens: per-role Apps, the Claude App via OIDC, or long-lived personal tokens in repository secrets
- Secret source and how it is fetched (by name or by folder)
- CD: tag release, deploy on push, host deploy outside CI, none
- CODEOWNERS and path protection
- Branch naming (`<type>/<issue>-` share), `Closes` links, stacked bases
- `timeout-minutes`, `concurrency`, SHA-pinned actions, step summaries

Read protection with `gh api repos/OWNER/REPO/branches/BRANCH/protection` and `.../rulesets`; a 404 or
403 on a free personal account means "not available". Read variable and secret names, never values.

## Intentional variations (keep, with the reason written down)

| Variation | Why it is fine |
|---|---|
| Advisory single-job review (Claude App via OIDC, comment only) instead of the full loop | The loop is still a pilot; copying it multiplies its open bugs. Advisory review never blocks. |
| A public tooling repository requires its few jobs by name and runs on hosted runners | Public code must not run on self-hosted runners; with three jobs an aggregate is optional. |
| Packages release on a version tag; a static site deploys on push to the default branch | Those are the only artifacts CD'd from Actions. |
| Service hosts deploy from their canonical checkout by a deploy script, outside CI | Deploys need the host, its secrets and a reviewed compose file. |
| No protection on a free personal account's private repository | Not available on that plan. Compensate with merge discipline and the review status. |
| A change classifier skips heavy jobs | Skipped counts as passing in `ci-ok`. |

## Drift (fix it)

| Drift seen | Risk | Fix |
|---|---|---|
| Auto-merge of any non-draft PR on green CI, using a long-lived personal token | Unreviewed merges; a broad credential in a workflow | Gate on `review-verdict` or a reviewed label; mint an App token. Until then, drafts are the only hold. |
| Reviewer token without `issues: read` | Spec review silently skipped | Grant it; pin it with a test. |
| Whole-folder secret fetch plus a name-pattern drop step | Other roles' secrets stay in an agent's env | One secret per fetch, by name. |
| Actions pinned by tag; no workflow security lint | Supply-chain drift | SHA pins with version comments; actionlint plus a workflow security linter. |
| No pre-commit, or pre-commit not run in CI | Local and CI checks drift; `--no-verify` slips through | Add the config; lint job runs it. |
| Hosted runners on an account where they cannot run | PRs only merge by bypass | Move to the runner variable and `ci-ok`, or archive the repository. |
| Python floor below the organisation's minimum | Version skew | Raise it, or write the pin's reason and lift condition. |
| No issue-numbered branches, no `Closes` links | Nobody can say what a PR finishes | The "work starts from an issue" rule and the PR-link hook. |
| `allowed_bots` differs per copy of the loop | Reviews of bot PRs fail as "tooling" | Name every PR-opening bot; pin with a test. |
| Deploy on push without an Environment or `concurrency` | Overlapping deploys; no approval point | Add both. |
| Jobs without `timeout-minutes` | A hung job holds a shared runner | Timeouts everywhere. |
| No CODEOWNERS or path protection anywhere | Workflow and agent-prompt changes are reviewed like any file | Protect `.github/workflows/` and agent prompts at least, by the protected-path guard or CODEOWNERS plus a required review. |

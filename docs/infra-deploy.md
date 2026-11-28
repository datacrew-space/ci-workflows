# Infrastructure deploys: promote, then deploy

An infrastructure repository (compose files, host config) is deployed, not depended on, so it has no
semantic versions. Its release is a **promotion**: a commit on `main` that passed CI is recorded by
moving a protected `deploy` branch to it and tagging it. A deployer on the host follows `deploy`.

```
merge to main -> CI (ci-ok green) -> promote -> deploy branch + tag -> host deployer -> services
```

## What the reusable workflow does

[`infra-promote.yml`](../.github/workflows/infra-promote.yml) runs as a job of the repository's own CI,
after the aggregate check. It:

1. verifies the required check (default `ci-ok`) is green on that exact commit, through the API;
2. refuses a commit that is not already on `main`;
3. fast-forwards `deploy` to the commit with one atomic, non-force push, and creates the tag
   `deploy/<date>-<sha7>` in the same push.

It does not deploy and holds no host details. The logic is in
[`scripts/promote-deploy.sh`](../scripts/promote-deploy.sh) and
[`scripts/check_required_green.py`](../scripts/check_required_green.py), both tested against real
repositories and a fake API (`tests/test_promote_deploy.py`, `tests/test_check_required_green.py`).

| Situation | Result |
|---|---|
| Commit is ahead of `deploy` | `deploy` and a new tag move forward (`result=promoted`) |
| Commit is `deploy` or older (a re-run, or CI finishing out of order) | nothing pushed, no tag (`result=noop`) |
| Commit is not on `main` | refused (exit 3) |
| `deploy` is not an ancestor of the commit (someone pushed to it) | refused (exit 4) |
| The tag exists on another commit | refused (exit 5); tags are never moved |
| Required check red | the job fails at once |
| Required check missing or running | retried for three minutes, then fails |

Promotions are serialised per repository and branch; the newest commit wins.

## Rollback

`previous` (an output and a line in the job summary) is the commit `deploy` pointed at before. Rolling
back means moving `deploy` to an earlier promotion tag. A fast-forward-only rule would refuse that, so
a rollback is a deliberate, logged human action on the protected branch, not something the workflow does.

## Set up a repository

1. Add the `promote` job from [`examples/infra-promote-caller.yml`](../examples/infra-promote-caller.yml)
   to the repository's CI, pinning both refs to one reviewed commit SHA. Try it once with `dry-run: true`.
2. Protect `deploy`: no force-push, no deletion, and restrict who may push it. If the protection cannot be
   bypassed by `GITHUB_TOKEN`, create a token that may push it and pass it as `PROMOTE_TOKEN`
   (a repository secret, passed by name; `secrets: inherit` does not cross owners).
3. Make the deployer follow `deploy`, never `main`, and deploy only from a dedicated checkout nobody edits.
4. Without hosted runner minutes (a private repository on an account whose Actions billing is blocked),
   pass your own runner pool: `runs-on: '["self-hosted", "linux"]'`. The promote job needs no container
   engine access, so a sandboxed runner is enough.

A private repository on a personal account can call this workflow: a public repository's reusable
workflows can be called from anywhere. Org-level rulesets, org secrets and workflow templates do not
apply to a personal-account repository, so set the protection and secrets in that repository.

## What the deployer must do (not part of this repository)

The host-side deployer is repository-specific and lives with the infrastructure it deploys. The contract
this workflow gives it: `deploy` only moves forward to a commit that was green and on `main`, so the
deployer can treat every new `deploy` commit as approved, map the changed paths to services, and report
its own failures. Hardening that belongs there: roll back to the previous image on a failed health
check, back off and cap retries, pin one commit per run, and keep a kill switch.

## Libraries and packages

Libraries have consumers, so they use semantic versions and a release flow. That is a separate
workflow and is not covered here.

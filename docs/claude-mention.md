# `@claude` mention workflow

`.github/workflows/claude-mention.yml` lets someone summon Claude by writing `@claude` in an issue
comment, a pull request comment, a review comment, the summary of a review, or an issue's title or
body. Copy `examples/claude-mention-caller.yml` to `.github/workflows/claude.yml` and pin the `uses:`
ref to a commit SHA of this repository.

## What the caller sets

- Repository variables `INFISICAL_DOMAIN` and `INFISICAL_PROJECT_SLUG`, and secrets
  `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET`: where the Claude Code OAuth token lives. With
  any of them missing, a mention ends green and Claude is not called.
- Optional: `infisical-env-slug` (default `prod`), `infisical-claude-path` (default `/claude`),
  `trigger-phrase`, `allowed-associations` and `runs-on`.

## Requirements

- The Claude GitHub App must be installed on the adopting repository.
- No `github_token` is passed, so the action exchanges an OIDC token for an App token. That this works
  under `workflow_call` is unverified until the live run.

## Who may summon Claude

The author of the mention must be in `allowed-associations` (default `OWNER,MEMBER,COLLABORATOR`), and
must not be a bot. A comment without the phrase, or a mention from anyone else, ends green with the
reason in the log. The action then applies its own write-access check.

As reported and unverified: organization members whose membership is private may show as
`CONTRIBUTOR`, and so be refused by the default set.

## Differences between the copies it replaces

- Only some copies restricted the author. All now get the default set above.
- The runner label, the secret store domain, project and path were written into each copy. They are
  inputs now.
- Copies differed in the pin of the checkout and secret-fetch actions. All are pinned by commit SHA.
- Copies carried commented-out `prompt` and `claude_args` examples. They are dropped: a caller that
  needs them should ask for an input.

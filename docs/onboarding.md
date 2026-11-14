# Onboarding a repository onto the review loop

`scripts/onboard-review-loop.sh` walks you through the manual steps that adopting the loop needs.
It opens each page, says what to click and copy, sets the repository secrets and the variable with
`gh`, and copies the caller workflow and the two agent prompts into your repository.

> **Status:** the loop is v0 and unproven end to end, and so is this wizard. It is tested by running it
> against a stub `gh` with scripted answers (`tests/test_onboard_wizard_flow.py`) and by reading it
> against the workflow (`tests/test_onboard_wizard.py`), but nobody has run it against a live
> repository yet. Try it on a test repository first.

## Run it

You need the [GitHub CLI](https://cli.github.com), logged in (`gh auth login`) as a user with admin
rights on the target repository, and a checkout of this repository (the wizard reads `examples/`
next to itself) plus a checkout of the target repository.

```bash
bash scripts/onboard-review-loop.sh
```

Stop any time with Ctrl-C. Nothing is written to a dotenv file, so a re-run simply asks again; setting
a secret or variable twice is harmless.

## The seven stages

| # | Stage | You do | The wizard does |
|---|-------|--------|-----------------|
| 1 | Prerequisites | Name the target repository (`OWNER/NAME`) | Checks `gh` is installed and authenticated and can see the repository |
| 2 | Secret store coordinates | Give the Infisical URL, project slug, environment, the two folders and the responder App's login (Enter keeps the default) | Validates the answers; looks up the bot id when the login is not the default |
| 3 | GitHub Apps | Say whether the three Apps are already installed on the repository. If not, create them with the permissions below, generate a private key and install each | Skips this stage when they are installed; otherwise opens the creation page and prints the permission list |
| 4 | Credentials in the secret store | Make sure the seven secrets below exist in Infisical | Opens Infisical and lists the exact names and folders |
| 5 | Machine identity | Create a Universal Auth identity with read access to those folders, copy its Client ID and Client Secret | Sets repo secrets `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET` over stdin; entry is hidden. Asks first if either secret already exists |
| 6 | Caller and prompts | Give a checkout path and a commit SHA of this repository | Fills `examples/caller.yml`, copies it and both prompt templates into the checkout, never overwriting without a yes |
| 7 | Enable | Confirm | Sets repo variable `CLAUDE_REVIEW_ENABLED=true`, last, and not at all if a secret was not set |

### App permissions

An installation token can only be narrowed from the App's own grant, so each App is granted the
strongest level any of its tokens asks for in `.github/workflows/claude-code-review.yml`. Leave every
other permission at *No access*, turn the webhook off, and install on the target repository only.

| App | Repository permissions |
|-----|------------------------|
| `ci-code-reviewer` | Contents: read. Pull requests: read and write. Issues: read. Checks: read. Actions: read. Commit statuses: read |
| `ci-review-responder` | Contents: read and write. Pull requests: read and write. Issues: read and write. Checks: read. Actions: read. Commit statuses: read |
| `ci-review-orchestrator` | Commit statuses: read and write. Pull requests: read and write. Issues: read and write |

Only the responder's name matters to the workflow: it matches that bot login (and uses the bot's
numeric user id as the commit author). Both are caller inputs, `responder-bot-login` and
`responder-bot-id`, and default to `ci-review-responder` and its id. GitHub App names are unique across
GitHub, so an owner other than the default one registers their own responder App and sets both inputs;
the wizard asks for the login and writes the inputs into the caller. A private App can only be
installed on its owner's account.

### Secrets in Infisical

Folder `infisical-github-path` (default `/github`): `CI_CODE_REVIEWER_APP_ID`,
`CI_CODE_REVIEWER_PRIVATE_KEY`, `CI_REVIEW_RESPONDER_APP_ID`, `CI_REVIEW_RESPONDER_PRIVATE_KEY`,
`CI_REVIEW_ORCHESTRATOR_APP_ID`, `CI_REVIEW_ORCHESTRATOR_PRIVATE_KEY`.

Folder `infisical-claude-path` (default `/claude`): `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`).

The wizard never sees these values; you paste them into Infisical. Delete the downloaded `.pem` files
afterwards.

## What it deliberately does not do

- It does not commit, push or open a PR in your repository. It writes three files; you review and
  merge them the normal way.
- It does not read private keys, print secrets, or put secrets on a command line.
- It does not pin for you: you supply the commit SHA, and the `uses:` ref and `tooling-ref` get the same one.
- The responder only runs when the caller sets `push-runs-on` (see the comment in the caller).

## Roll back

Unset the variable and the caller job is skipped:

```bash
gh variable delete CLAUDE_REVIEW_ENABLED --repo OWNER/NAME
```

The Apps and secrets can stay in place.

## Keeping it honest

`tests/test_onboard_wizard.py` parses the reusable workflow and the caller and fails if the wizard's
secret names, folder defaults, variable name, App permission lists or caller placeholders drift from
them, if the variable is not set after the secrets, or if a secret value is echoed or traced.
`tests/test_onboard_wizard_flow.py` runs the wizard against a stub `gh` and fails if a secret reaches a
command line, an existing secret is overwritten without a yes, a re-run loses an answer, or the loop
is enabled after a secret failed.

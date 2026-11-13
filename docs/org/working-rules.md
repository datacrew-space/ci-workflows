# Working rules

Engineering rules for every agent and contributor in the org. A repository's own `AGENTS.md` wins
for anything repository-specific; read it, and the org home's, first. To change a rule, open a pull
request against this file. Don't fork it per agent or per repository.

## Public by default

- Every org repository, issue, pull request and CI log is public. Write nothing private: no internal
  hostnames, IP addresses, private repository or account names, personal emails, local paths or
  usernames. Use placeholders.
- Never weaken a guard script to make a change pass. If a guard is wrong, fix it in its own reviewed
  pull request.
- Commit with the repository's configured noreply identity.

## Stop and ask

When a decision belongs to the maintainer, ask **one** question with your recommended answer, then
stop. Don't guess and carry on.

- Never merge your own pull request. Never deploy or write to production without the maintainer's
  explicit go-ahead.
- Never delete, reset, force-push, drop stashes, `git clean` or remove worktrees that hold someone
  else's work. Snapshot it, summarize it and let the maintainer decide.

## Pull requests

- **You open your pull request.** Use a draft if it's early. The body has a Summary, Evidence and
  Merge Danger, plus `Closes #N` for the issue.
- **Someone else reviews it** on GitHub: the code-review loop or an independent reviewer. Findings
  go on the pull request and are handled before merge.
- **You never merge your own pull request.** A reviewer or the maintainer merges it
  once the review is recorded. Squash, then delete the branch.
- **Never `gh pr merge --admin`.** Fix red CI instead of bypassing it.
  - If `main` is red for reasons unrelated to your diff, fix `main` in a separate pull request, or
    file an issue and wait.
  - A job that died at setup did not pass. Report a runner that is down.
  - A review check that dies on tooling (a usage limit or an auth failure, typically one turn, no
    cost, under a second) is not a review result.
- Merge finished, reviewed, green work promptly. Land ADRs and specs on `main` before dispatching
  work that depends on them. Speed never replaces review.
- **Keep pull requests small**: one per reviewable slice. Group a theme under one tracking issue.
- Stacked pull requests: retarget every child to the final base before merging the bottom one with
  branch deletion. Deleting a base branch closes its children for good.
- Never merge on green tests alone. A reviewer diffs against live `origin/main` to look for reverted
  functionality, and re-runs the tests.
- Commit with hooks on. **Never `--no-verify`.** If a hook is wrong, fix it or file it.
- A pull request is done only when its named assertions pass, including a negative case. Report
  workarounds as bugs.

## Git

- Before reading a repository's docs or creating files, `git fetch` and compare `HEAD` with
  `origin/<default>`. Stale docs describe a repository that no longer exists.
- Resolve repository roots with `git rev-parse --show-toplevel` or the remote URL. Never hardcode a
  clone layout.
- Work in the repository the change is about, on a branch off its `main`. Never push to `main`,
  and never force-push.
- Never commit or discard changes you didn't make. Stage only your own hunks.
- Don't use `git stash` for baselines; it is shared across worktrees. Compare with
  `git show <ref>:<path>`, and park work by committing.
- Before reviving a stale branch, run `git cherry origin/main <branch>`.
- A conflict-free merge or rebase proves nothing about semantics. Run the typechecker and tests
  afterwards. Don't resolve an "additive" conflict by keeping both sides; rebuild from
  `git show origin/main:<file>` plus your change, then check that it parses.

## Secrets

- Never print a secret. No `set -x`, no `cat` of `.env` or credential files, no `env` dumps. Prove a
  secret is present with a length, an exit code or a boolean.
- Report a found secret by path, key name and length only. If a secret ever appears in output, say
  so in the same run. **A leaked secret is rotated, not just deleted.**
- Non-secret configuration (hostnames, URLs, ports, flags) goes in the repository's config file, not
  a secret store. The test: would leaking it be a security event?
- Before deleting or moving a shared secret or config key, search every repository's workflows and
  deploy scripts. Land the consumer changes first, then prune.
- Services exit non-zero with a clear error when required secrets can't be fetched. Never start
  half-configured.
- Anything that writes to a knowledge base redacts each secret match to `[REDACTED:<kind>]`,
  re-scans with the same matcher until nothing changes, and ingests the redacted text. Never ingest
  raw text, and never drop a whole item because of one match.

## Code

- Python 3.13 is the minimum: `requires-python >=3.13`, ruff `py313`, `python:3.13` images.
- Dependencies use compatible ranges with no unexplained ceiling pins. Always commit the lockfile,
  and pin images by version or digest. A hard pin carries a written reason and the condition for
  lifting it.
- One canonical virtual environment per project (`uv run`).
- Raise specific named exceptions for domain failures and catch the narrowest type. Never catch bare
  `Exception` around domain operations, and don't replace the real error with a guessed message.
- API client and routing layers raise on any non-2xx response.
- Library code logs through the project logger (crew-logger, once it is published in the org),
  never `print()`. Wrap request
  boundaries (routes, MCP tools, job entry points) with the repository's logging decorator. Never
  log secrets or raw prompts.
- No function-local imports to dodge import cycles; restructure the modules.
- Long-running jobs resume after an interruption: persist progress per item, write state
  atomically, make items idempotent, and put a deadline on every remote call.
- Derive an LLM output schema from the typed model (structured output, tool JSON Schema); never
  hand-write it inline in a prompt.
- Before writing a call to an unfamiliar API, read a working caller or the library source.
- Before hand-rolling auth, payments, queues, search or similar, name the established provider and
  explain why it doesn't fit. Use the provider unless you can.
- Apps forward the caller's own credential, and the service that owns the data authorizes once, at
  one chokepoint.
- Recurring or scheduled application jobs run on trigger.dev, not GitHub Actions. Repository
  hygiene CI (scans, guards, scheduled history checks) stays in GitHub Actions.

### mdrag

- Every action runs under a verified token. Owners are identity ids, never emails.
- Every write needs a resolved caller, or it is refused.
- Producers write only through the public ingest path. Provider and workflow code never imports the
  store, ingest internals or database clients.

## Testing and verification

- A bug fix starts with a failing reproduction, then the fix, then the reproduction passing as a
  regression test.
- Test at the highest seam (the public HTTP API, not internal functions). Every behavior gets an
  allow case and a deny case; auth checks run as a real non-admin identity.
- Frontend changes: drive the real UI in a browser. If no browser is available, say so instead of
  claiming success.
- Patch a name where it is looked up. A test that passes on an unmocked path proves nothing.
- When a new check reports its first big number, the checker is what's under test. Fix the rule,
  then set the baseline. Never re-seed a baseline to hide new drift.
- Treat as unverified until checked against the tree: a doc's claim that something doesn't exist,
  any derived number, and a closed issue's deliverables.

## Docs

- Docs (ADRs, runbooks, notes) go in the repository the work is about, in the same pull request
  family as the change.
- ADRs: one topic, one ADR. Amend the ADR that owns a topic with a dated `## Addendum (YYYY-MM-DD)`;
  never file a second ADR on the same topic, and never rewrite the original decision.
- The glossary is a glossary: definitions and words to avoid, no implementation details.
- Fix stale facts or dead paths in instruction files with small reviewed commits. Never weaken a
  safety rule because another file says so.
- In skills, don't paste untested code. Point at the real module or ship a tested script.
- When an upstream skill (`mattpocock/skills`) covers a need, use it. Put repository-specific
  behavior in `docs/agents/*.md`, never in a fork.
- When finishing existing work, don't add features mid-stream. File the idea as an issue.

## Writing

- One question per comment, with a recommended answer, most blocking first.
- Use the glossary's words. Write plainly; no filler.

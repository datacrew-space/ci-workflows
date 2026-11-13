# The datacrew-space org

Context for agents and contributors working anywhere in the org. Words in **bold** are defined in
[`GLOSSARY.md`](../../GLOSSARY.md).

## What belongs here

The **datacrew-space org** owns DataCrew's public products: the **mdrag product family** and the
crew-logger library.

- **Every repository is public, from its first push.** There are no private repositories in the
  org. Creating one is publishing it.
- **Only public content.** No personal agents, memories, host or network details, personal
  identities, private repository names or credentials. Use placeholders such as `<your-host>` or
  `example.com`.
- **Every repository runs both public-repo guards** (secret scan and private-terms scan) in
  pre-commit and CI, permanently and blocking. See [`docs/guards.md`](../guards.md).
- **Every repository passes the privacy checklist** before it is created. The checklist is not
  written yet; until it is, no new org repository is created.

## The Home Repo pattern

The org follows Matt Pocock's [Home Repo pattern](https://www.aihero.dev/the-home-repo-pattern).
An agent session starts in one **org home** repository, which holds:

- `AGENTS.md`: how to work here, plus pointers to every other org repository;
- shared skills (one copy each, never duplicated into other repositories);
- cross-repo ADRs and this glossary;
- reusable CI workflows.

Each repository is cloned once, with worktrees inside its clone. In CI the org home is the sandbox.

Skills fall into four buckets:

| Bucket | Where it lives |
|---|---|
| Shared (used across repositories) | the org home, one copy |
| Repository-specific (about one repo's code or domain) | that repository |
| Installed from a public source (for example `mattpocock/skills`) | installed, never copied |
| Duplicates | collapsed into one shared copy |

**Which repository is the org home is still open.** This repository (`ci-workflows`) is the
candidate, possibly renamed. Until that is decided, org-wide context is seeded here under
`docs/org/` and in `GLOSSARY.md`.

## Repositories

Grouped by area. Planned repositories do not exist yet; each will be created from the template
repository at a fresh v1 once it passes the privacy checklist.

**CI and scaffolding**
- `ci-workflows`: reusable workflows (Claude code-review loop, public-repo guards). Exists.
- `.github`: org profile, rulesets and org-level workflow templates. Planned.
- `repo-template`: the template repository. Planned.

**Platform (mdrag core)**
- `mdrag`: the core. Planned, v1 is the core only.
- `dc-auth`, `domain-state`: shared auth and state libraries. Planned.

**Providers**
- `mdrag-fetch-providers`: one package and one release per provider. Planned.
- `mdrag-rss-feed-factory`: the feed factory (name under review, it now runs more than RSS). Planned.
- `mdrag-transcripts`. Planned.

**Apps**
- `pattern-hunter`. Planned.

**Libraries**
- `crew-logger`: structured logging. Planned, the first repository to move (the rehearsal).

When the org home is chosen, this list becomes its "Other repos" section, one line per repository,
each ending "Clone from `https://github.com/datacrew-space/<name>` if missing."

## Where things go

| What | Where |
|---|---|
| Code, tests, a repository's own glossary terms and ADRs | that repository |
| Cross-repo ADRs, shared skills, coding standards, repository pointers | the org home |
| Runbooks for *using* mdrag | the org home |
| Deployment, host operations, incident logs, anything private | outside the org |

## Also read

- [`roadmap.md`](roadmap.md): the plan for moving the mdrag family into the org.
- [`working-rules.md`](working-rules.md): the engineering rules every agent follows here.

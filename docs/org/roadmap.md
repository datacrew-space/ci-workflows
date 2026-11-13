# Roadmap: moving the mdrag product family into the org

A public summary of the plan. The detailed spec and its tracking issues are kept privately, because
they name private repositories; this file holds only what is safe to publish. Words in **bold** are
defined in [`GLOSSARY.md`](../../GLOSSARY.md).

## Goals

1. **Publish safely.** The existing repositories carry credentials in their history and private
   details in their trees. Nothing moves until it is clean.
2. **Design mdrag for extension.** v1 is a small core; fetching, processing and apps live in
   satellites that use only the public API. Each extraction proves the core can be extended.
3. **Make onboarding a product feature,** so a new agent can find its way in without reading the
   source.
4. **Stop skills from drifting,** with one shared copy of each skill in the org home.

## Phases

Each phase finishes before the next starts, except where noted.

0. **Record the decisions** as ADRs and glossary terms.
1. **Privacy first.**
   - The private-terms denylist and scanner, kept outside the org.
   - Both **public-repo guards** on this repository, which is already public. (The guard scripts
     and reusable workflow exist; see [`docs/guards.md`](../guards.md).)
   - The written **privacy checklist** every org repository passes before its first push.
2. **Rotate credentials.** Scan the full history of every repository that is moving or being
   archived, report findings by name and path only, and **rotate** every live credential, highest
   blast radius first. A repository's v1 is blocked until its history holds no live credential.
3. **Set up the org.** The `.github` repository and rulesets, the **template repository** with both
   gates, and the org home.
4. **Rehearse on small repositories.** `crew-logger` first, then `dc-auth` and `domain-state`, each
   as a **fresh history** v1. The rehearsal must pass every gate and the release flow before the
   mdrag carve starts.
5. **Carve mdrag** in its old private repository, frozen for new features while production fixes
   continue. Each extraction is its own small pull request.
6. **Release mdrag v1** as a public repository: gates and checklist pass, a data migration dry run
   against a copy of production, the release, production pinned to that release.
7. **Clean up.** Re-file only the open issues that still matter, rewritten for the new design.
   Archive the old repositories privately.

## Decisions so far

- The org is public-only. Private work stays on personal accounts.
- Every repository that moves starts at v1 with **fresh history**. Old repositories are archived
  privately and never made public.
- Both gates are permanent and blocking on every org repository.
- The private-terms denylist never lives in a public repository.
- mdrag v1 is the **core** only:
  - it stores and serves Documents and validates registered **kinds**;
  - a Document is identified by its Collection, kind and key, and only a write of the same kind
    updates it;
  - there is one **ingest path**, which every producer uses, first-party included;
  - the core never fetches, schedules or hosts **runs**.
- Fetching lives in satellites: `mdrag-fetch-providers` (one release per **provider**) and the
  **feed factory**, which runs on trigger.dev.
- Processing after ingest is done by **mdrag workflows**; **apps** are built on workflows.
- The core's MCP is generated from its FastAPI routes (FastMCP 4), plus a `get_started` entry point
  and an onboarding skill pack that is tested in CI against a live v1.
- Every org repository builds and releases the same way, and production deploys a released
  version, never `main`. The release tool is not chosen yet.
- Feedback sent to the core is stored as Documents and announced as an event; forwarding it to any
  tracker is a workflow outside the core.

## Open

- Which repository is the org home: this one (possibly renamed `datacrew-home`) or a new one.
- A new name for `mdrag-rss-feed-factory`, which now runs more than RSS.
- How a namespace owner grants another app the right to write kinds in it.
- What happens to links into repositories that will be archived privately.

## Out of scope

- Private repositories in the org.
- Carrying old history, closed issues or old pull requests into the org.
- Optimistic concurrency for two writers in one Collection (an accepted risk).

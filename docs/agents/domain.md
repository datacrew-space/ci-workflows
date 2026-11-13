# Domain Docs

How the engineering skills consume this repo's domain documentation. Layout: **single-context**.

## Before exploring, read these

- `GLOSSARY.md` at the repo root.
- `docs/adr/` (not created yet): ADRs that touch the area you are about to work in.

If these files don't exist, proceed silently. Don't flag their absence; the domain-modeling skill
creates them lazily when terms or decisions get resolved.

```
/
├── GLOSSARY.md
├── docs/adr/          # not created yet
└── ...
```

## Use the glossary's vocabulary

When your output names a domain concept (issue title, refactor proposal, test name), use the term as
defined in `GLOSSARY.md`. If the term is missing, either reconsider it or note the gap.

## Flag ADR conflicts

If your output contradicts an existing ADR, say so explicitly rather than silently overriding it.

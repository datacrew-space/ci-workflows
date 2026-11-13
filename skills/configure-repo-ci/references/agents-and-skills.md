# Agents, skills and hooks: getting the same result every run

## What made results consistent

- **A "CI contract" section in the agent prompt.** The parts the workflow reads (output file, section
  headings, the one-word verdict file) came back in the same shape on every review. See
  `examples/code-reviewer.md` and `examples/review-responder.md`. Structure a workflow parses stays
  consistent; free prose does not need to.
- **A fixed outcome vocabulary.** The responder's table (Fixed in <sha> / Declined: <reason> /
  Deferred to #N, with evidence) made each round auditable.
- **Skills named by the repository's instructions.** Agents reached `tdd`, `pr` (Summary, Evidence,
  Merge Danger) and `code-review` reliably because `AGENTS.md` and the agent files name them. A skill
  nothing points at is not used.
- **Decisions in tested scripts**, workflows only running them. The model never decides whether to
  dispatch, flip a draft or which round it is.

## Where an agent file did not help

- **Missing capabilities look like consistent output.** The reviewer wrote "No spec available" on
  every PR because its token could not read issues. Tell the agent to fail loudly (a named, visible
  line) when an input it needs is missing, and check the first few outputs by hand for that pattern.
- **Prompts cannot enforce order across time.** "Mark the PR ready when done" failed whenever the
  session ended first. Move such steps into a workflow (the ready label) or a hook.

## Hooks force, rules nudge

| Mechanism | Use for | Evidence |
|---|---|---|
| PreToolUse hook | must-never or must-always at a tool call (PR without an issue link; raw merges) | issue links went from 0 of 2 PRs to every PR after the hook |
| SessionStart hook | short, bounded context (the current branch's issue, or a capped queue) | silent on error, number and title only, capped lines |
| `AGENTS.md` rule | judgement (triage every finding, stacks) | followed when a session remembered; approvals with findings still sat |
| Workflow | anything that must happen after the session ends | ready-when-green, the review loop |

Hooks need tests like code, with a corpus of real commands: heredocs that merely mention the
blocked command, `bash -c '...'` wrappers, credential-injecting wrappers, `cd x && ...`, a link in a
different command than the one checked. One fix for a false positive opened a bypass through the
exact wrapper the repository prescribes. A hook that blocks a legitimate path (for example the only
API that merges a stack) needs a sanctioned alternative in its message.

## Stateless agents and rediscovery

CI agents start with no memory on purpose: read-only, versioned context resists prompt injection,
keeps reruns reproducible and cannot go stale. So the repository has to be the memory:

- A **repo map** in `AGENTS.md` or `docs/agents/`: where things live and which seams to use.
- **Learnings land as reviewed text the same day**: a pilot ends in an `AGENTS.md` section (stacked
  PRs, triage), a trap ends in a test plus a one-line comment at the place it bites (`allowed_bots`).
- **Setup docs per App and per switch**, with the order of steps and the validation result, so "does
  the App exist?" is a file read, not an investigation.
- **External docs in a read-only knowledge base** the agent can query, instead of coding an API from
  memory. When a knowledge base keys documents by URL, give distinct documents distinct paths or query
  strings: a `#fragment` is usually dropped and the parts overwrite each other. Read back after ingest.
- **Measure before building more context**: record turns and cost per run; invest in a repo map when
  orientation is a large share.

## Evaluating an agent, skill or workflow change

1. A structural test that pins the invariant (the `allowed_bots` list, the token permissions, the
   secret names a job may hold, the action SHAs). This repository's `tests/` has examples.
2. One controlled live run with the smallest blast radius (one issue, limit 1, one labelled draft).
   Do not cancel it before the step it was meant to test.
3. The right trigger: a bot-path change needs a bot-initiated event; a workflow change needs a new run
   (a re-run reuses the old file).
4. A "validated / not verified" list in the PR body and the README, kept until the live run passes.

## Running agents in sessions

- A session that ends its turn "waiting for CI" does not wake itself; tell it to block on the checks.
- A push blocked by a permission prompt is the owner's decision; surface it, do not route around it.
- Side quests (a new research session nobody asked for) cost time; keep delegated scope explicit.

## Shell and platform

- Write file content with the editor tools, not shell heredocs: escape sequences become real
  characters and corrupt scripts.
- On Git Bash for Windows, set `MSYS_NO_PATHCONV=1` for commands with leading-slash or `ref:path`
  arguments (`git show origin/main:path`, API paths).
- Give run instructions in the user's shell; environment-variable syntax differs between bash and
  PowerShell.
- On Windows, send stdin to curl as bytes; text mode adds a carriage return to header lines.

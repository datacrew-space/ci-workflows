# agents

Reusable agent definitions you can copy into another repository.

| File | Purpose |
|---|---|
| [`code-review-architect.md`](code-review-architect.md) | On-demand, human-triggered reviewer: correctness, tests, then design (modules, seams, deletion test) |

## Use it in another repo

Copy the file into that repository's agent folder and commit it:

```bash
mkdir -p .agents/agents
cp path/to/ci-workflows/agents/code-review-architect.md .agents/agents/
```

If your tooling loads agents from a different folder (for example `.claude/agents/`), copy it there
instead. The file is self-contained: it needs no skill and no other local file. Then ask for a
review in a session, for example "use the code-review-architect agent on PR 42". Edit the file to
add your repository's own rules.

## How it differs from the CI reviewer

| | `agents/code-review-architect.md` | [`examples/code-reviewer.md`](../examples/code-reviewer.md) |
|---|---|---|
| Triggered by | A human, in an interactive session | The review workflow, on every pull request |
| Tools | `Read, Glob, Grep, Bash`; Bash is broad, so it can run tests in a scratch checkout | Restricted by the workflow's `--allowedTools` to the exact git and `gh` forms it allows |
| Output | A written report to whoever asked, with a verdict of APPROVE, REQUEST CHANGES or COMMENT | `review.md` and a one-word `verdict` file the workflow reads (the "CI contract") |
| Scope | Correctness, tests and architecture | Standards, Spec and Verification axes against the repo's own rules |

Do not point the workflow's `reviewer-agent` input at `code-review-architect.md`: it has no CI
contract sections, and its broad Bash access is not safe in an unattended run.

Both agents are read-only by design. Neither approves-and-merges, pushes or edits the change.

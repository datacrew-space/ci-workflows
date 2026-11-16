# Shared Claude Code settings

`configs/claude/settings.base.json` holds the Claude Code user settings worth sharing between machines, and
`scripts/bootstrap_claude_settings.py` merges them into your own settings file.

## What the base sets

- `env.CLAUDE_CODE_AUTO_COMPACT_WINDOW = "150k"`: compacts the conversation earlier, so a long session stays
  inside a window the model handles well.
- A `PreCompact` hook that prints `hooks/compact-instructions.md`. Claude Code uses that text as the
  instructions for the compaction summary, which turns the summary into a handoff for a session that has none
  of the conversation.
- A `SessionStart` hook on `compact` that prints `hooks/after-compact.md`, a reminder to re-read the handoff
  and re-check state before continuing.

The two text files live in `configs/claude/hooks/` and are copied to `<claude-dir>/hooks/`.

## Run it

```bash
git clone https://github.com/datacrew-space/ci-workflows && cd ci-workflows
python scripts/bootstrap_claude_settings.py            # dry run: prints a diff, writes nothing
python scripts/bootstrap_claude_settings.py --apply    # backs up settings.json, then writes it
```

It needs only Python 3 and the standard library. `--claude-dir` changes the target (default `~/.claude`).

Merge rules:

- Keys the base does not name are never touched, and existing hooks stay. Hook groups are appended only when
  an equal group is not already there, so a second run changes nothing and writes no new backup.
- A different value you already have for a key the base names is kept and reported. `--force` takes the base
  value instead. The same goes for a hook file in `<claude-dir>/hooks/` whose content differs.
- A missing settings file is created. A settings file or overlay that is not valid JSON stops the script with
  an error before anything is written.

## Settings that cannot live in a public repository

`--overlay <file>` merges a second JSON fragment with the same rules, after the base. Keep that file outside
this repository.

## Check it

`env` values take effect at the next Claude Code start. Start a new session and run `/context` to see the
window in use; the hooks show up under `/hooks`.

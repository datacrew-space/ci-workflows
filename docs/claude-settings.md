# Shared Claude Code settings

`configs/claude/settings.base.json` holds the Claude Code user settings worth sharing between machines, and
`scripts/bootstrap_claude_settings.py` merges them into your own settings file.

## What the base sets

- `env.CLAUDE_CODE_AUTO_COMPACT_WINDOW = "250k"`: 150k was tried first and compacted repeatedly before tasks
  finished; 250k lets a typical task complete in one window.
- A `PreCompact` hook that prints `hooks/compact-instructions.md`. Claude Code uses that text as the
  instructions for the compaction summary, which turns the summary into a handoff for a session that has none
  of the conversation.
- A `SessionStart` hook on `compact` that prints `hooks/after-compact.md`, a reminder to re-read the handoff
  and re-check state before continuing.

The two text files live in `configs/claude/hooks/` and are copied to `<claude-dir>/hooks/`. The hook commands
read them from `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/`, so they follow `CLAUDE_CONFIG_DIR` when it is set.

## Run it

```bash
git clone https://github.com/datacrew-space/ci-workflows && cd ci-workflows
python scripts/bootstrap_claude_settings.py            # dry run: prints a diff, writes nothing
python scripts/bootstrap_claude_settings.py --apply    # backs up settings.json, then writes it
```

It needs only Python 3 and the standard library; use `python3` where `python` is not on the path.
`--claude-dir` changes the target. The default is `$CLAUDE_CONFIG_DIR` when that is set, else `~/.claude`.

Merge rules:

- The base and any overlays are composed into one fragment first (a later fragment wins over an earlier one),
  and only that fragment is merged into your file. "kept your value" therefore always means a value that was
  in your file.
- Keys the fragment does not name are never touched, and existing hooks stay. A second run changes nothing and
  writes no new backup.
- A different value you already have for a key the fragment names is kept and reported. `--force` takes the
  fragment's value instead. The same goes for a hook file in `<claude-dir>/hooks/` whose content differs.
- Hook groups: one of yours counts as the shared group when it has the same `matcher` (none equals none) and one
  of its commands prints the same `hooks/<file>.md`. If it is identical, nothing happens. If it differs it is a
  conflict: kept and reported, and `--force` replaces it in place, leaving one group. Your other groups under the
  same event are never touched. A machine set up by an earlier version of this script, whose hook commands
  say `~/.claude/hooks/...`, therefore reports a conflict for the two hook groups until you run it with `--force`.
- A symlinked settings file is written through to its target, and the link stays. The backup goes beside the
  target. The file's line endings (CRLF or LF) and, on POSIX, its mode are kept. A UTF-8 BOM is accepted on read
  and not written back.
- The script builds the whole plan and checks it first (inputs, the hooks directory, the settings path), then
  writes the hook files and the settings file last. If any check fails it exits 1 and writes nothing. That
  includes invalid JSON, a directory at the settings path, and a hooks path that is not a usable directory.
- A missing settings file is created.

## Settings that cannot live in a public repository

`--overlay <file>` merges a second JSON fragment with the same rules, after the base. Keep that file outside
this repository.

## Check it

`env` values take effect at the next Claude Code start. Start a new session and run `/context` to see the
window in use; the hooks show up under `/hooks`.

"""Claude Code PreToolUse hook: a PR may not be opened without naming the issue it belongs to.

Blocks a PR create unless that command's own text (the inline body, or the file given to --body-file)
contains an issue link such as `Closes #4` or `Refs #8`. Work is taken from the open `ready-for-agent`
issues; a PR with no link means the work had no issue.

How the command is read:
  - It is split at unquoted `;`, `&`, `|` and newlines, and only the segment holding the PR create is
    checked, so a link in some other command (`echo "Closes #4"; ...`) does not count.
  - Text that merely mentions a PR create is ignored: the arguments of echo, printf and git, and the
    bodies of heredocs fed to python, cat and similar. Everything else is scanned whole, quotes
    included, so a wrapped call (`bash -c '...'`) fails closed rather than open.

A hook can only see tool calls, so this gates opening the PR, not starting the work. It fails closed:
input it cannot parse, or a body file it cannot read, blocks.

Usage (from .claude/settings.json, see settings.example.json): python3 -I .claude/hooks/require_issue_link.py
(JSON on stdin).
Exit:   0 allow, 2 block (the reason goes to stderr, where Claude reads it).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")
BODY_FILE = re.compile(r"""(?:--body-file|-F)[\s=]+("[^"]+"|'[^']+'|\S+)""")
ISSUE_LINK = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|part of)\s+(?:[\w.-]+/[\w.-]+)?#\d+", re.I)

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)
QUOTED = re.compile(r"\"(?:\\.|[^\"\\])*\"|'[^']*'")

# Commands whose arguments and heredoc bodies are text, not commands to run.
TEXT_COMMANDS = frozenset({"echo", "printf", "git"})
DATA_HEREDOC_COMMANDS = frozenset({"python", "python3", "cat", "tee", "node", "sed", "awk"})

REASON = (
    "Blocked: this PR names no issue. Work comes from the open `ready-for-agent` issues "
    "(`gh issue list --label ready-for-agent`). Add `Closes #N` (or `Refs #N` for partial work) to the "
    "PR body. If the work has no issue, file one first and ask the maintainer."
)


def _drop_data_heredocs(command: str) -> str:
    """Remove heredoc bodies fed to a data command (python, cat, ...); a body fed to a shell stays, as commands."""

    def keep_or_drop(match: re.Match) -> str:
        line_head = command[: match.start()].split("\n")[-1].split()
        return "" if any(word in DATA_HEREDOC_COMMANDS for word in line_head) else match.group(0)

    return HEREDOC.sub(keep_or_drop, command)


def _segments(command: str) -> list[str]:
    """Split at unquoted ; & | and newlines, so a quoted body containing `;` stays in one piece."""
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    skip = False
    for ch in command:
        if skip:
            skip = False
            current.append(ch)
        elif quote:
            current.append(ch)
            if ch == "\\" and quote == '"':
                skip = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in ";&|\n":
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
    segments.append("".join(current))
    return [segment for segment in segments if segment.strip()]


def _is_pr_create(segment: str) -> bool:
    words = segment.split()
    text = QUOTED.sub('""', segment) if words and words[0] in TEXT_COMMANDS else segment
    return bool(PR_CREATE.search(text))


def _check_segment(segment: str) -> str | None:
    text = segment
    match = BODY_FILE.search(segment)
    if match:
        path = Path(match.group(1).strip("\"'"))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return f"{REASON} (Could not read the body file, so its link cannot be checked.)"
    return None if ISSUE_LINK.search(text) else REASON


def check_command(command: str) -> str | None:
    """Return the reason to block this command, or None to allow it. The link must be in the PR create itself."""
    for segment in _segments(_drop_data_heredocs(command)):
        if _is_pr_create(segment):
            reason = _check_segment(segment)
            if reason:
                return reason
    return None


def main() -> int:
    try:
        command = json.load(sys.stdin)["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        sys.stderr.write("require_issue_link: could not read the hook input, so blocking.\n")
        return 2
    reason = check_command(command if isinstance(command, str) else "")
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

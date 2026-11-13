"""Claude Code SessionStart hook: tell the session which issue it is on, in as few lines as possible.

The flag is the branch name, `feat/<issue>-<slug>`.
  - numbered branch      one line: the issue's number, title and state
  - main / no number     the `ready-for-agent` queue as `#N title`, capped at MAX_LINES lines
                         (a non-main branch also gets a one-line "no issue" warning)
Never bodies, labels or comments, so the output cannot grow with the issues. Silent on any
failure (gh missing, offline, bad JSON): a session must never start with an error from this.

Usage (from .claude/settings.json, see settings.example.json):  python3 -I .claude/hooks/session_start_issues.py
Output on stdout is added to the session's context. Always exits 0.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable

MAX_LINES = 10
BRANCH_ISSUE = re.compile(r"^[a-z]+/(\d+)-")
DEFAULT_BRANCHES = frozenset({"main", "master"})

RunGh = Callable[[list[str]], str | None]


def issue_from_branch(branch: str) -> int | None:
    match = BRANCH_ISSUE.match(branch)
    return int(match.group(1)) if match else None


def _json(run_gh: RunGh, args: list[str]):
    out = run_gh(args)
    if out is None:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _queue_lines(run_gh: RunGh, room: int) -> list[str]:
    issues = _json(run_gh, ["issue", "list", "--label", "ready-for-agent", "--state", "open", "--json", "number,title"])
    if not issues:
        return []
    header = "Open ready-for-agent issues (name one before building, or ask the maintainer):"
    shown = issues if len(issues) <= room - 1 else issues[: room - 2]
    lines = [header, *(f"#{i['number']} {i['title']}" for i in shown)]
    if len(shown) < len(issues):
        lines.append(f"...and {len(issues) - len(shown)} more (gh issue list --label ready-for-agent)")
    return lines


def build_context(branch: str, run_gh: RunGh) -> str:
    number = issue_from_branch(branch)
    if number is not None:
        issue = _json(run_gh, ["issue", "view", str(number), "--json", "title,state"])
        if not isinstance(issue, dict):
            return ""
        state = str(issue.get("state", "")).lower()
        if state == "closed":
            return f"Warning: branch {branch} is for issue #{number}, which is closed. Ask the maintainer first."
        return f"Working on issue #{number}: {issue.get('title', '')} ({state})."
    warning = (
        []
        if branch in DEFAULT_BRANCHES
        else [f"Warning: branch {branch} is linked to no issue (expected feat/<issue>-<slug>)."]
    )
    queue = _queue_lines(run_gh, MAX_LINES - len(warning))
    return "\n".join([*warning, *queue]) if queue else ""


def _run_gh(args: list[str]) -> str | None:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def _branch() -> str:
    try:
        done = subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def main() -> int:
    branch = _branch()
    if branch:
        out = build_context(branch, _run_gh)
        if out:
            sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Pick the `ready-for-agent` issues the dispatcher may start now.

Reads the JSON that `gh issue list --json number,state,labels,assignees,blockedBy,subIssuesSummary` prints on
stdin and writes a JSON list of issue numbers, lowest first, on stdout. The decision is here
(tested in tests/test_select_dispatchable.py); the workflow only runs it and acts on the list.

An issue is dispatchable when all of these hold:

- it is open and labelled `ready-for-agent`;
- every issue that blocks it is closed (an empty block list counts);
- nobody is assigned to it, and it has no sub-issues (a parent spec is not a ticket);
- it has neither `agent-dispatched` (the dispatcher already claimed it) nor `needs-human`
  (an agent failed or a person has to look first). Removing `needs-human` retries it.

Usage: `python -I scripts/select_dispatchable.py [--limit N] [--ready-label L] [--exclude-label L ...]
< issues.json`. Exits 1, with one
line on stderr, when stdin is not the JSON shape `gh` prints. A wrong shape is never skipped,
because skipping would stall the factory without a sign.
"""

from __future__ import annotations

import argparse
import json
import sys

READY_LABEL = "ready-for-agent"
EXCLUDING_LABELS = frozenset({"agent-dispatched", "needs-human"})
DEFAULT_LIMIT = 2  # matches the workflow default (`dispatch-limit`)


class DispatchInputError(ValueError):
    """The issue list is not the shape `gh issue list --json` prints; the message names the part."""


def _label_names(item: dict) -> set[str]:
    try:
        return {label["name"] for label in item["labels"]}
    except (KeyError, TypeError) as err:
        raise DispatchInputError(f"issue {item.get('number')}: labels must be a list of {{name}}") from err


def _all_blockers_closed(item: dict) -> bool:
    try:
        blocked_by = item["blockedBy"]
        nodes = blocked_by["nodes"]
        # A page that holds fewer blockers than the issue has hides the rest: not provably unblocked.
        total = blocked_by.get("totalCount")
        if isinstance(total, int) and total > len(nodes):
            return False
        return all(blocker["state"] == "CLOSED" for blocker in nodes)
    except (KeyError, TypeError) as err:
        raise DispatchInputError(f"issue {item.get('number')}: blockedBy.nodes[].state is missing") from err


def _required(item: dict, field: str):
    try:
        return item[field]
    except KeyError as err:
        raise DispatchInputError(f"issue {item.get('number')}: {field} is missing") from err


def select(
    issues: list[dict],
    limit: int = DEFAULT_LIMIT,
    ready_label: str = READY_LABEL,
    excluding_labels: frozenset[str] = EXCLUDING_LABELS,
) -> list[int]:
    """Numbers of the issues to start, lowest first, at most `limit`."""
    chosen: list[int] = []
    for item in issues:
        try:
            number = item["number"]
        except (KeyError, TypeError) as err:
            raise DispatchInputError("issue without a number") from err
        labels = _label_names(item)
        if _required(item, "state") != "OPEN" or ready_label not in labels:
            continue
        if labels & excluding_labels or item.get("assignees"):
            continue
        if (_required(item, "subIssuesSummary") or {}).get("total", 0) > 0:
            continue  # a parent spec organises tickets; its sub-issues are the work
        if not _all_blockers_closed(item):
            continue
        chosen.append(number)
    return sorted(chosen)[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="most issues to start in one run")
    parser.add_argument("--ready-label", default=READY_LABEL, help="label that queues an issue (default %(default)s)")
    parser.add_argument(
        "--exclude-label",
        action="append",
        help="label that keeps an issue out (repeatable; default: " + ", ".join(sorted(EXCLUDING_LABELS)) + ")",
    )
    args = parser.parse_args(argv)
    excluding = frozenset(args.exclude_label) if args.exclude_label else EXCLUDING_LABELS
    try:
        issues = json.load(sys.stdin)
    except json.JSONDecodeError as err:
        sys.stderr.write(f"select_dispatchable: invalid JSON on stdin: {err}\n")
        return 1
    try:
        numbers = select(issues, args.limit, args.ready_label, excluding)
    except DispatchInputError as err:
        sys.stderr.write(f"select_dispatchable: {err}\n")
        return 1
    sys.stdout.write(json.dumps(numbers) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

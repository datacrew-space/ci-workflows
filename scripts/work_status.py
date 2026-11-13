"""Print one line per issue saying whether the work is done and what happens next.

Every field comes from a fact GitHub records by itself: the PR's merged/draft state, the `ci-ok`
check, the `review-verdict` status, the `needs-human` label, and whether the issue is closed.
Nothing here depends on an author remembering to tick a box or set a field.

  - issues shown: open `ready-for-agent` issues, plus any issue an open PR links
    (Closes/Fixes/Resolves #N in the body, or a `<type>/<N>-slug` branch; `Refs #N` is related, not owned)
  - arguments: issue numbers to show instead (merged ones included)
  - "done" means the PR is merged and the issue is closed

Usage:  uv run python scripts/work_status.py [ISSUE ...]
If `gh` fails the script says so on stderr and exits 1; an empty answer is never a silent failure.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Names are overridable from the environment: WORK_QUEUE_LABEL, WORK_CI_CHECK, WORK_VERDICT_CHECK,
# WORK_NEEDS_HUMAN_LABEL.
QUEUE_LABEL = os.environ.get("WORK_QUEUE_LABEL") or "ready-for-agent"
# Names the workflows own (your CI job, and claude-code-review.yml here). tests/test_work_status.py fails if the
# review names are renamed there, so this script cannot drift into silently reading "-".
CI_CHECK = os.environ.get("WORK_CI_CHECK") or "ci-ok"
VERDICT_CHECK = os.environ.get("WORK_VERDICT_CHECK") or "review-verdict"
NEEDS_HUMAN = os.environ.get("WORK_NEEDS_HUMAN_LABEL") or "needs-human"
LIMIT = 100

# Only closing keywords make a PR own an issue; `Refs #N` is related work (a parent spec, a partial slice).
ISSUE_LINK = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
BRANCH_ISSUE = re.compile(r"^[a-z]+/(\d+)-")

RunGh = Callable[[list[str]], str | None]


class WorkStatusError(Exception):
    """`gh` failed or answered something that is not the JSON we asked for."""


@dataclass(frozen=True)
class Row:
    issue: int
    title: str
    pr: int | None
    stacked_on: int | None
    state: str
    ci: str
    review: str
    next_action: str


def _json(run_gh: RunGh, args: list[str]) -> list[dict]:
    out = run_gh(args)
    if out is None:
        raise WorkStatusError(f"gh {' '.join(args[:2])} failed")
    try:
        data = json.loads(out)
    except ValueError as err:
        raise WorkStatusError(f"gh {' '.join(args[:2])} did not answer JSON") from err
    if not isinstance(data, list):
        raise WorkStatusError(f"gh {' '.join(args[:2])} did not answer a list")
    return data


def _linked_issues(pr: dict) -> set[int]:
    found = {int(n) for n in ISSUE_LINK.findall(pr.get("body") or "")}
    branch = BRANCH_ISSUE.match(pr.get("headRefName") or "")
    if branch:
        found.add(int(branch.group(1)))
    return found


def _outcome(entry: dict) -> str:
    """green, red or pending for one check entry (a CheckRun or a commit StatusContext).

    Only SUCCESS is green: a SKIPPED, NEUTRAL or CANCELLED conclusion reads red ("fix CI"), the safe
    direction, so a check that did not really pass is never shown as passing.
    """
    if entry.get("__typename") == "StatusContext":
        state = str(entry.get("state", "")).upper()
        return {"SUCCESS": "green", "FAILURE": "red", "ERROR": "red"}.get(state, "pending")
    if str(entry.get("status", "")).upper() != "COMPLETED":
        return "pending"
    return "green" if str(entry.get("conclusion", "")).upper() == "SUCCESS" else "red"


def _check(pr: dict, name: str) -> str:
    """The last entry called `name` in the rollup, or "-" when absent (absent is never green).

    This trusts `gh` to list the rollup oldest to newest. If it ever put a stale failed run after a
    green re-run, the line would show red for a green PR: wrong, but in the safe direction.
    """
    result = "-"
    for entry in pr.get("statusCheckRollup") or []:
        if name in (entry.get("name"), entry.get("context")):
            result = _outcome(entry)
    return result


def _labels(pr: dict) -> set[str]:
    return {label["name"] for label in pr.get("labels") or []}


def _pick_pr(prs: list[dict]) -> dict | None:
    """The open PR if any, else the newest merged one, else nothing (a closed PR is not progress)."""
    for state in ("OPEN", "MERGED"):
        same = [p for p in prs if p["state"] == state]
        if same:
            return max(same, key=lambda p: p["number"])
    return None


def _open_state(pr: dict, ci: str, review: str, base_pr: dict | None) -> tuple[str, str]:
    if base_pr is not None and base_pr["isDraft"]:
        # Draft or not, a PR must not move before the PR it is stacked on.
        return ("draft" if pr["isDraft"] else "in review"), f"waits on #{base_pr['number']}"
    if pr["isDraft"]:
        if ci == "green":
            return "draft", "mark ready"
        return "draft", "fix CI" if ci == "red" else "wait for CI"
    if ci == "red":
        return "in review", "fix CI"
    if ci != "green":
        return "in review", "wait for CI"
    if review == "red" and NEEDS_HUMAN in _labels(pr):
        return "needs-human", "a human decides"
    if review == "red":
        return "in review", "re-run the review workflow"
    if review == "green":  # ci is green here: red and not-green returned above
        return "ready to merge", "a human merges"
    return "in review", "wait for review"


def _row(issue: dict, prs: list[dict], open_prs: list[dict]) -> Row:
    number, title = issue["number"], issue["title"]
    closed = issue["state"].upper() == "CLOSED"
    pr = _pick_pr([p for p in prs if number in _linked_issues(p)])
    if pr is None:
        return Row(
            number, title, None, None, "closed" if closed else "no PR", "-", "-", "-" if closed else "open a draft PR"
        )
    ci, review = _check(pr, CI_CHECK), _check(pr, VERDICT_CHECK)
    if pr["state"] == "MERGED":
        state, action = ("done", "-") if closed else ("merged", "issue still open: close it or open the next PR")
        return Row(number, title, pr["number"], None, state, ci, review, action)
    base_pr = next((p for p in open_prs if p["headRefName"] == pr["baseRefName"]), None)
    stacked_on = base_pr["number"] if base_pr else None
    state, action = _open_state(pr, ci, review, base_pr)
    return Row(number, title, pr["number"], stacked_on, state, ci, review, action)


def _not_found(number: int) -> Row:
    return Row(number, "(not found)", None, None, "not found", "-", "-", f"#{number} is not in the last {LIMIT} issues")


def build_rows(
    run_gh: RunGh, numbers: Sequence[int] | None = None, warn: Callable[[str], None] = lambda _msg: None
) -> list[Row]:
    issues = _json(
        run_gh, ["issue", "list", "--state", "all", "--limit", str(LIMIT), "--json", "number,title,state,labels"]
    )
    prs = _json(
        run_gh,
        [
            "pr", "list", "--state", "all", "--limit", str(LIMIT), "--json",
            "number,title,body,headRefName,baseRefName,isDraft,state,labels,statusCheckRollup",
        ],
    )  # fmt: skip
    for kind, listed in (("issues", issues), ("PRs", prs)):
        if len(listed) >= LIMIT:
            warn(f"the list of {kind} hit the limit of {LIMIT}; older ones may be missing from this answer")
    open_prs = [p for p in prs if p["state"] == "OPEN"]
    if numbers:
        wanted = set(numbers)
    else:
        wanted = {i["number"] for i in issues if i["state"].upper() == "OPEN" and QUEUE_LABEL in _labels(i)}
        wanted |= {n for p in open_prs for n in _linked_issues(p)}
    by_number = {i["number"]: i for i in issues}
    return [_row(by_number[n], prs, open_prs) if n in by_number else _not_found(n) for n in sorted(wanted)]


def format_rows(rows: Sequence[Row]) -> str:
    lines = []
    for r in rows:
        pr = "-" if r.pr is None else f"PR #{r.pr}" + (f" (on #{r.stacked_on})" if r.stacked_on else "")
        title = r.title if len(r.title) <= 30 else r.title[:27] + "..."
        lines.append(
            f"#{r.issue:<3} {title:<30}  {pr:<16}  {r.state:<14}  "
            f"ci {r.ci:<7}  review {r.review:<7}  -> {r.next_action}"
        )
    return "\n".join(lines)


def _run_gh(args: list[str]) -> str | None:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as err:
        first_line = (err.stderr or "").strip().splitlines()[:1]
        if first_line:
            sys.stderr.write(f"work_status: gh said: {first_line[0]}\n")
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def main(argv: Sequence[str], run_gh: RunGh = _run_gh) -> int:
    try:
        numbers = [int(a.lstrip("#")) for a in argv]
    except ValueError:
        sys.stderr.write("usage: work_status.py [ISSUE_NUMBER ...]\n")
        return 2
    try:
        rows = build_rows(run_gh, numbers or None, warn=lambda msg: sys.stderr.write(f"work_status: {msg}\n"))
    except WorkStatusError as err:
        sys.stderr.write(f"work_status: {err} (is `gh` installed and logged in?)\n")
        return 1
    sys.stdout.write((format_rows(rows) if rows else "No open work found.") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

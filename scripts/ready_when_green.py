"""Mark a draft PR ready for review once its author labelled it and `ci-ok` is green on its head.

The author signals "I am done" with the `ready-for-review` label (document it for your contributors); this
script does the rest so nobody has to remember `gh pr ready`. Nothing flips without the label, so early drafts stay
cheap. The label is removed after the flip, so a PR sent back to draft is not re-flipped.

  - fork PRs are never touched (the workflow holds an app token)
  - a stacked PR waits while the PR it is based on is still a draft; the workflow re-runs when a
    PR becomes ready, so a stack cascades bottom to top
  - a missing or pending `ci-ok` is never read as green

Run by .github/workflows/ready-when-green.yml with an app token, from the base branch.
Exit 1 (message on stderr) if `gh` fails or a PR could not be marked ready.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable

# Defaults; the workflow passes its inputs as READY_LABEL and CI_CHECK.
LABEL = os.environ.get("READY_LABEL") or "ready-for-review"
CI_CHECK = os.environ.get("CI_CHECK") or "ci-ok"

RunGh = Callable[[list[str]], str | None]


class ReadyError(Exception):
    """`gh` failed or answered something that is not the JSON we asked for."""


def _ci_green(pr: dict) -> bool:
    latest = None
    for entry in pr.get("statusCheckRollup") or []:
        if CI_CHECK in (entry.get("name"), entry.get("context")):
            latest = entry
    if latest is None:
        return False
    if latest.get("__typename") == "StatusContext":
        return str(latest.get("state", "")).upper() == "SUCCESS"
    return (
        str(latest.get("status", "")).upper() == "COMPLETED" and str(latest.get("conclusion", "")).upper() == "SUCCESS"
    )


def prs_to_mark(prs: list[dict]) -> list[int]:
    draft_heads = {p["headRefName"] for p in prs if p["isDraft"]}
    return [
        p["number"]
        for p in prs
        if p["isDraft"]
        and not p.get("isCrossRepository")
        and LABEL in {label["name"] for label in p.get("labels") or []}
        and _ci_green(p)
        and p["baseRefName"] not in draft_heads
    ]


def list_open_prs(run_gh: RunGh) -> list[dict]:
    fields = "number,headRefName,baseRefName,isDraft,isCrossRepository,labels,statusCheckRollup"
    out = run_gh(["pr", "list", "--state", "open", "--limit", "100", "--json", fields])
    if out is None:
        raise ReadyError("gh pr list failed")
    try:
        prs = json.loads(out)
    except ValueError as err:
        raise ReadyError("gh pr list did not answer JSON") from err
    if not isinstance(prs, list):
        raise ReadyError("gh pr list did not answer a list")
    return prs


def _run_gh(args: list[str]) -> str | None:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, check=True)
    except subprocess.CalledProcessError as err:
        first_line = (err.stderr or "").strip().splitlines()[:1]
        if first_line:
            sys.stderr.write(f"ready_when_green: gh said: {first_line[0]}\n")
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def main(run_gh: RunGh = _run_gh) -> int:
    try:
        numbers = prs_to_mark(list_open_prs(run_gh))
    except ReadyError as err:
        sys.stderr.write(f"ready_when_green: {err}\n")
        return 1
    failed = False
    for number in numbers:
        if run_gh(["pr", "ready", str(number)]) is None:
            sys.stderr.write(f"ready_when_green: could not mark #{number} ready (label kept, will retry)\n")
            failed = True
            continue
        sys.stdout.write(f"marked #{number} ready for review\n")
        # A label left behind would re-flip the PR the next time it is sent back to draft.
        if run_gh(["pr", "edit", str(number), "--remove-label", LABEL]) is None:
            sys.stderr.write(f"ready_when_green: #{number} is ready but its {LABEL} label could not be removed\n")
            failed = True
    if not numbers:
        sys.stdout.write("no labelled draft PR with a green ci-ok\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

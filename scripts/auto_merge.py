"""Reconcile GitHub auto-merge on one pull request with its label and state.

Reads the pull request's live state, never the event that started the run, then does exactly one of:

  - open, not a draft, carries the label, auto-merge off  -> enable auto-merge
  - open and no longer qualifies, auto-merge on           -> disable auto-merge
  - anything else                                         -> nothing

A closed or merged pull request is never touched. Because the decision reads live state, a run that
was displaced or re-ordered by another run for the same pull request still reaches the right answer.

Run by .github/workflows/auto-merge.yml. Input is the environment: PR_NUMBER, GH_REPO and GH_TOKEN for
`gh`, AUTOMERGE_LABEL (default `automerge`) and MERGE_METHOD (`squash`, `merge` or `rebase`, default
`squash`). Exit 1 (message on stderr) when `gh` fails, answers the wrong shape, or an input is invalid.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable

METHODS = ("squash", "merge", "rebase")

RunGh = Callable[[list[str]], str]


class AutoMergeError(Exception):
    """`gh` failed, answered the wrong shape, or an input is invalid."""


def decide(pr: dict, label: str) -> str:
    """Return "enable", "disable" or "none" for the pull request's live state."""
    if pr["state"] != "OPEN":
        return "none"
    qualifies = not pr["isDraft"] and label in {entry["name"] for entry in pr["labels"]}
    armed = pr["autoMergeRequest"] is not None
    if qualifies and not armed:
        return "enable"
    if not qualifies and armed:
        return "disable"
    return "none"


def read_pr(run_gh: RunGh, number: str) -> dict:
    out = run_gh(["pr", "view", number, "--json", "state,isDraft,labels,autoMergeRequest"])
    try:
        pr = json.loads(out)
    except ValueError as err:
        raise AutoMergeError("gh pr view did not answer JSON") from err
    if not isinstance(pr, dict) or not {"state", "isDraft", "labels", "autoMergeRequest"} <= pr.keys():
        raise AutoMergeError("gh pr view did not answer the fields asked for")
    return pr


def _run_gh(args: list[str]) -> str:
    gh = shutil.which("gh")  # resolves `gh.cmd` shims too, which a bare "gh" does not on Windows
    if gh is None:
        raise AutoMergeError("gh is not installed")
    try:
        done = subprocess.run([gh, *args], capture_output=True, text=True, timeout=60, check=True)
    except subprocess.CalledProcessError as err:
        first_line = (err.stderr or "").strip().splitlines()[:1]
        raise AutoMergeError(f"gh {args[0]} {args[1]} failed: {first_line[0] if first_line else 'no message'}") from err
    except (OSError, subprocess.SubprocessError) as err:
        raise AutoMergeError(f"could not run gh: {type(err).__name__}: {err}") from err
    return done.stdout


def reconcile(run_gh: RunGh, number: str, label: str, method: str) -> str:
    if not number.isdigit():
        raise AutoMergeError("PR_NUMBER is not a number")
    if method not in METHODS:
        raise AutoMergeError(f"MERGE_METHOD must be one of {', '.join(METHODS)}")
    action = decide(read_pr(run_gh, number), label)
    if action == "enable":
        run_gh(["pr", "merge", number, "--auto", f"--{method}"])
    elif action == "disable":
        run_gh(["pr", "merge", number, "--disable-auto"])
    return action


def main(run_gh: RunGh = _run_gh) -> int:
    number = os.environ.get("PR_NUMBER", "")
    label = os.environ.get("AUTOMERGE_LABEL") or "automerge"
    method = os.environ.get("MERGE_METHOD") or "squash"
    try:
        action = reconcile(run_gh, number, label, method)
    except AutoMergeError as err:
        sys.stderr.write(f"auto_merge: {err}\n")
        return 1
    messages = {
        "enable": f"auto-merge enabled on #{number} ({method})",
        "disable": f"auto-merge disabled on #{number}: it no longer qualifies",
        "none": f"nothing to change on #{number}",
    }
    sys.stdout.write(messages[action] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

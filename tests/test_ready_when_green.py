"""Named assertions for scripts/ready_when_green.py: label + green ci-ok marks a draft ready, nothing else does."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ready_when_green import LABEL, ReadyError, list_open_prs, main, prs_to_mark  # noqa: E402


def ci_ok(outcome: str = "SUCCESS") -> dict:
    if outcome == "PENDING":
        return {"__typename": "CheckRun", "name": "ci-ok", "status": "IN_PROGRESS", "conclusion": ""}
    return {"__typename": "CheckRun", "name": "ci-ok", "status": "COMPLETED", "conclusion": outcome}


def pr(number=30, head="feat/5-x", base="main", draft=True, labels=(LABEL,), checks=None, fork=False):
    return {
        "number": number,
        "headRefName": head,
        "baseRefName": base,
        "isDraft": draft,
        "isCrossRepository": fork,
        "labels": [{"name": n} for n in labels],
        "statusCheckRollup": [ci_ok()] if checks is None else checks,
    }


def test_a_labelled_green_draft_is_marked_ready():
    assert prs_to_mark([pr()]) == [30]


@pytest.mark.parametrize(
    "case",
    [
        pr(labels=()),
        pr(labels=("needs-human",)),
        pr(checks=[ci_ok("FAILURE")]),
        pr(checks=[ci_ok("PENDING")]),
        pr(checks=[]),
        pr(checks=[{"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}]),
        pr(draft=False),
        pr(fork=True),
    ],
    ids=["no-label", "other-label", "red-ci", "pending-ci", "no-checks", "only-lint-green", "already-ready", "fork"],
)
def test_everything_else_is_left_alone(case):
    assert prs_to_mark([case]) == []


def test_a_stacked_pr_waits_while_its_base_pr_is_a_draft():
    base = pr(30, head="feat/5-x")
    top = pr(31, head="feat/6-y", base="feat/5-x")

    assert prs_to_mark([base, top]) == [30]


def test_a_stacked_pr_flips_once_its_base_pr_is_ready():
    base = pr(30, head="feat/5-x", draft=False, labels=())
    top = pr(31, head="feat/6-y", base="feat/5-x")

    assert prs_to_mark([base, top]) == [31]


def test_a_stacked_pr_whose_base_is_not_an_open_pr_is_not_blocked():
    assert prs_to_mark([pr(31, base="feat/gone")]) == [31]


def gh_with(prs, calls):
    def run_gh(args: list[str]) -> str | None:
        calls.append(args)
        return json.dumps(prs) if args[:2] == ["pr", "list"] else ""

    return run_gh


def test_main_marks_ready_then_removes_the_label():
    calls: list[list[str]] = []

    assert main(run_gh=gh_with([pr()], calls)) == 0

    assert ["pr", "ready", "30"] in calls
    assert ["pr", "edit", "30", "--remove-label", LABEL] in calls
    assert calls.index(["pr", "ready", "30"]) < calls.index(["pr", "edit", "30", "--remove-label", LABEL])


def test_main_changes_nothing_when_no_pr_qualifies():
    calls: list[list[str]] = []

    assert main(run_gh=gh_with([pr(labels=())], calls)) == 0

    assert [c for c in calls if c[:2] != ["pr", "list"]] == []


def test_a_failed_listing_raises_and_main_exits_nonzero(capsys):
    with pytest.raises(ReadyError):
        list_open_prs(lambda args: None)
    assert main(run_gh=lambda args: None) == 1
    assert "gh" in capsys.readouterr().err


def test_a_failed_label_removal_is_reported_and_exits_nonzero(capsys):
    calls: list[list[str]] = []

    def run_gh(args: list[str]) -> str | None:
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps([pr()])
        return None if args[:2] == ["pr", "edit"] else ""

    assert main(run_gh=run_gh) == 1
    assert ["pr", "ready", "30"] in calls  # the flip itself happened
    err = capsys.readouterr().err
    assert "#30" in err
    assert "label" in err


def test_a_failed_ready_call_is_reported_and_the_label_is_kept(capsys):
    calls: list[list[str]] = []

    def run_gh(args: list[str]) -> str | None:
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps([pr()])
        return None if args[:2] == ["pr", "ready"] else ""

    assert main(run_gh=run_gh) == 1
    assert not any(c[:2] == ["pr", "edit"] for c in calls)
    assert "#30" in capsys.readouterr().err


def test_a_failing_gh_shows_what_gh_said(monkeypatch, capsys):
    import subprocess

    import ready_when_green

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="GraphQL: Resource not accessible by integration\nmore")

    monkeypatch.setattr(ready_when_green.subprocess, "run", boom)

    assert main() == 1
    err = capsys.readouterr().err
    assert "Resource not accessible" in err
    assert "more" not in err  # only gh's first line


def test_the_workflow_token_can_read_checks_and_statuses():
    import yaml

    path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ready-when-green.yml"
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["sweep"]["steps"]
    mint = next(s for s in steps if str(s.get("uses", "")).startswith("actions/create-github-app-token"))
    assert mint["with"].get("permission-checks") == "read"
    assert mint["with"].get("permission-statuses") == "read"


def test_a_gh_that_cannot_run_reports_the_exception_not_a_guess(monkeypatch, capsys):
    import ready_when_green

    def boom(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(ready_when_green.subprocess, "run", boom)
    assert ready_when_green._run_gh(["pr", "list"]) is None
    assert "FileNotFoundError" in capsys.readouterr().err

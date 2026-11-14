"""Named assertions for scripts/session_start_issues.py, each with a negative case."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples" / "hooks"))

from session_start_issues import MAX_LINES, build_context, issue_from_branch  # noqa: E402


def gh_with(**answers):
    """A fake `gh`: maps 'view' / 'list' to a JSON-able answer, or None for a failed call."""

    def run_gh(args: list[str]) -> str | None:
        answer = answers[args[1]]
        return None if answer is None else json.dumps(answer)

    return run_gh


QUEUE = [{"number": n, "title": f"Issue {n}"} for n in (2, 4, 5)]


def test_issue_number_comes_from_the_branch_name():
    assert issue_from_branch("feat/4-forecast") == 4
    assert issue_from_branch("fix/12-null-low") == 12


def test_branches_without_a_number_have_no_issue():
    for branch in ("main", "feat/forecast-lows", "feat/basic-reading-page", "feat/4forecast", "HEAD"):
        assert issue_from_branch(branch) is None, branch


def test_numbered_branch_prints_one_line_with_the_title():
    out = build_context("feat/4-forecast", gh_with(view={"title": "Forecast", "state": "OPEN"}, list=QUEUE))

    assert out == "Working on issue #4: Forecast (open)."


def test_closed_issue_gets_a_warning_line():
    out = build_context("feat/4-forecast", gh_with(view={"title": "Forecast", "state": "CLOSED"}, list=QUEUE))

    assert "closed" in out.lower()
    assert len(out.splitlines()) == 1


def test_main_prints_the_queue_as_number_and_title_only():
    out = build_context("main", gh_with(view=None, list=QUEUE))

    assert out.splitlines()[1:] == ["#2 Issue 2", "#4 Issue 4", "#5 Issue 5"]
    assert "ready-for-agent" in out.splitlines()[0]


def test_unnumbered_feature_branch_is_warned_and_shown_the_queue():
    out = build_context("feat/forecast-lows", gh_with(view=None, list=QUEUE))

    assert "no issue" in out.lower()
    assert "#4 Issue 4" in out


def test_a_long_queue_is_capped():
    many = [{"number": n, "title": f"Issue {n}"} for n in range(1, 40)]

    out = build_context("main", gh_with(view=None, list=many))

    assert len(out.splitlines()) <= MAX_LINES
    assert "more" in out.splitlines()[-1]


def test_a_failed_gh_call_prints_nothing_never_an_error():
    assert build_context("main", gh_with(view=None, list=None)) == ""
    assert build_context("feat/4-x", gh_with(view=None, list=None)) == ""


def test_an_unparseable_answer_prints_nothing():
    assert build_context("main", lambda args: "not json") == ""


def test_an_empty_queue_prints_nothing():
    assert build_context("main", gh_with(view=None, list=[])) == ""

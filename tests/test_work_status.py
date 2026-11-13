"""Named assertions for scripts/work_status.py: state comes only from facts GitHub records itself."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import work_status  # noqa: E402
from work_status import (  # noqa: E402
    NEEDS_HUMAN,
    VERDICT_CHECK,
    WorkStatusError,
    build_rows,
    format_rows,
    main,
)


def check(name: str, outcome: str) -> dict:
    """A CheckRun; outcome is SUCCESS, FAILURE or PENDING."""
    if outcome == "PENDING":
        return {"__typename": "CheckRun", "name": name, "status": "IN_PROGRESS", "conclusion": ""}
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": outcome}


def verdict(state: str) -> dict:
    return {"__typename": "StatusContext", "context": "review-verdict", "state": state}


def pr(number=30, body="Closes #5", head="feat/5-x", base="main", draft=True, state="OPEN", labels=(), checks=()):
    return {
        "number": number,
        "title": f"PR {number}",
        "body": body,
        "headRefName": head,
        "baseRefName": base,
        "isDraft": draft,
        "state": state,
        "labels": [{"name": n} for n in labels],
        "statusCheckRollup": list(checks),
    }


def issue(number=5, state="OPEN", labeled=True):
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "labels": [{"name": "ready-for-agent"}] if labeled else [],
    }


def gh_with(issues, prs):
    def run_gh(args: list[str]) -> str | None:
        answer = {"issue": issues, "pr": prs}[args[0]]
        return None if answer is None else json.dumps(answer)

    return run_gh


def row_for(prs, issues=None, number=5, ask=False):
    rows = build_rows(gh_with(issues or [issue(number)], prs), numbers=[number] if ask else None)
    return next(r for r in rows if r.issue == number)


def test_open_issue_with_no_pr_says_so():
    row = row_for([])

    assert row.state == "no PR"
    assert row.pr is None


def test_draft_with_green_ci_says_mark_ready():
    row = row_for([pr(checks=[check("ci-ok", "SUCCESS")])])

    assert (row.state, row.ci) == ("draft", "green")
    assert "mark ready" in row.next_action


def test_draft_with_pending_or_red_ci_does_not_say_mark_ready():
    pending = row_for([pr(checks=[check("ci-ok", "PENDING")])])
    red = row_for([pr(checks=[check("ci-ok", "FAILURE")])])

    assert "wait" in pending.next_action
    assert "fix CI" in red.next_action
    assert "mark ready" not in pending.next_action + red.next_action


def test_a_missing_ci_ok_is_never_read_as_green():
    row = row_for([pr(checks=[check("lint", "SUCCESS")])])

    assert row.ci == "-"
    assert "mark ready" not in row.next_action


def test_ready_pr_with_green_ci_and_green_verdict_is_ready_to_merge():
    row = row_for([pr(draft=False, checks=[check("ci-ok", "SUCCESS"), verdict("success")])])

    assert row.state == "ready to merge"
    assert row.review == "green"


def test_green_verdict_does_not_hide_red_ci():
    row = row_for([pr(draft=False, checks=[check("ci-ok", "FAILURE"), verdict("success")])])

    assert row.state != "ready to merge"
    assert "fix CI" in row.next_action


def test_ready_pr_without_a_verdict_is_in_review_not_ready_to_merge():
    row = row_for([pr(draft=False, checks=[check("ci-ok", "SUCCESS")])])

    assert (row.state, row.review) == ("in review", "-")


def test_red_verdict_with_needs_human_label_is_needs_human():
    row = row_for([pr(draft=False, labels=["needs-human"], checks=[check("ci-ok", "SUCCESS"), verdict("failure")])])

    assert row.state == "needs-human"
    assert "human" in row.next_action


def test_red_verdict_without_the_label_is_a_tooling_failure_to_rerun():
    row = row_for([pr(draft=False, checks=[check("ci-ok", "SUCCESS"), verdict("failure")])])

    assert row.state == "in review"
    assert "re-run" in row.next_action


def test_merged_pr_with_closed_issue_is_done():
    row = row_for([pr(state="MERGED", draft=False)], [issue(5, state="CLOSED")], ask=True)

    assert (row.state, row.next_action) == ("done", "-")


def test_merged_pr_with_open_issue_is_not_done():
    row = row_for([pr(state="MERGED", draft=False)], [issue(5)])

    assert row.state == "merged"
    assert "issue" in row.next_action


def test_a_stacked_pr_whose_base_is_a_draft_waits_on_it():
    base = pr(30, body="Closes #5", head="feat/5-x", checks=[check("ci-ok", "SUCCESS")])
    top = pr(31, body="Closes #6", head="feat/6-y", base="feat/5-x", checks=[check("ci-ok", "SUCCESS")])

    row = row_for([base, top], [issue(5), issue(6)], number=6)

    assert "waits on #30" in row.next_action
    assert "mark ready" not in row.next_action


def test_a_ready_stacked_pr_with_a_draft_base_waits_instead_of_saying_merge():
    base = pr(30, head="feat/5-x")
    top = pr(
        31,
        head="feat/6-y",
        base="feat/5-x",
        draft=False,
        checks=[check("ci-ok", "SUCCESS"), verdict("success")],
    )

    row = row_for([base, top], [issue(5), issue(6)], number=6)

    assert row.state != "ready to merge"
    assert "waits on #30" in row.next_action


def test_a_skipped_ci_ok_reads_red_never_green():
    skipped = {"__typename": "CheckRun", "name": "ci-ok", "status": "COMPLETED", "conclusion": "SKIPPED"}

    assert row_for([pr(checks=[skipped])]).ci == "red"


def test_pr_is_linked_by_a_closing_keyword_or_the_branch_number():
    for p in (pr(body="Closes #5", head="x"), pr(body="fixes #5", head="x"), pr(body="", head="feat/5-slug")):
        assert row_for([p]).pr == 30


def test_refs_is_related_work_not_ownership_so_a_parent_spec_shows_no_child_pr():
    assert row_for([pr(body="Refs #5", head="feat/6-x")]).pr is None


def test_long_titles_are_cut_with_ascii_only():
    rows = build_rows(gh_with([{**issue(5), "title": "t" * 60}], []))

    assert format_rows(rows).isascii()


def test_a_pr_for_a_different_issue_is_not_linked():
    assert row_for([pr(body="Closes #50", head="feat/50-x")]).pr is None


def test_an_open_pr_adds_its_issue_even_without_the_ready_label():
    rows = build_rows(gh_with([issue(7, labeled=False)], [pr(body="Closes #7", head="feat/7-x")]))

    assert [r.issue for r in rows] == [7]


def test_closed_issues_with_no_open_pr_are_left_out_unless_asked_for():
    prs = [pr(state="MERGED", draft=False)]
    issues = [issue(5, state="CLOSED")]

    assert build_rows(gh_with(issues, prs)) == []
    assert [r.issue for r in build_rows(gh_with(issues, prs), numbers=[5])] == [5]


def test_a_failed_gh_call_raises_instead_of_printing_nothing():
    with pytest.raises(WorkStatusError):
        build_rows(gh_with(None, []))
    with pytest.raises(WorkStatusError):
        build_rows(gh_with([issue()], None))


def test_unparseable_gh_output_raises():
    with pytest.raises(WorkStatusError):
        build_rows(lambda args: "not json")


def test_format_is_one_line_per_issue_with_the_next_action():
    rows = build_rows(gh_with([issue(5)], [pr(checks=[check("ci-ok", "SUCCESS")])]))

    lines = format_rows(rows).splitlines()

    assert len(lines) == 1
    assert lines[0].startswith("#5")
    assert "PR #30" in lines[0]
    assert lines[0].rstrip().endswith("mark ready")


def test_a_requested_issue_that_is_missing_says_not_found_instead_of_nothing():
    rows = build_rows(gh_with([issue(5)], []), numbers=[99])

    assert [(r.issue, r.state) for r in rows] == [(99, "not found")]
    assert "99" in format_rows(rows)


def test_a_full_list_warns_that_it_may_be_cut_off():
    warnings: list[str] = []
    many = [issue(n) for n in range(1, 101)]

    build_rows(gh_with(many, []), warn=warnings.append)

    assert len(warnings) == 1
    assert "100" in warnings[0]


def test_a_short_list_does_not_warn():
    warnings: list[str] = []

    build_rows(gh_with([issue(5)], []), warn=warnings.append)

    assert warnings == []


def test_pending_ci_on_a_ready_pr_says_wait_for_ci_even_with_a_green_verdict():
    row = row_for([pr(draft=False, checks=[check("ci-ok", "PENDING"), verdict("success")])])

    assert row.state == "in review"
    assert row.next_action == "wait for CI"


def test_the_review_names_it_reads_still_exist_in_the_review_workflow():
    """The review workflow owns these names; a rename there must fail here, not silently read as `-`."""
    review = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "claude-code-review.yml").read_text(
        encoding="utf-8"
    )
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    reports = "\n".join(p.read_text(encoding="utf-8") for p in scripts.glob("*review*") if p.is_file())

    assert VERDICT_CHECK in review + reports
    assert NEEDS_HUMAN in review + reports


def test_a_failing_gh_shows_what_gh_said(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="gh: To use GitHub CLI, run: gh auth login\nmore")

    monkeypatch.setattr(work_status.subprocess, "run", boom)

    assert main([]) == 1
    err = capsys.readouterr().err
    assert "gh auth login" in err
    assert "more" not in err  # only gh's first line


def test_main_exits_nonzero_and_says_why_when_gh_fails(capsys):
    assert main([], run_gh=gh_with(None, [])) == 1
    assert "gh" in capsys.readouterr().err

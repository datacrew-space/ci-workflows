"""Which `ready-for-agent` issues the dispatcher may start, each rule with a negative case.

An issue is dispatchable when it is open, labelled `ready-for-agent`, every issue blocking it is
closed, nobody is assigned, and it carries neither `agent-dispatched` (already claimed) nor
`needs-human` (a person has to look first). The cap bounds how many agents one run starts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from select_dispatchable import DispatchInputError, select  # noqa: E402


def issue(
    number: int,
    labels: tuple[str, ...] = ("ready-for-agent",),
    blocked_by: tuple[str, ...] = (),
    assignees: tuple[str, ...] = (),
    state: str = "OPEN",
    sub_issues: int = 0,
) -> dict:
    """An issue as `gh issue list --json number,state,labels,assignees,blockedBy,subIssuesSummary` returns it."""
    return {
        "number": number,
        "state": state,
        "subIssuesSummary": {"total": sub_issues},
        "labels": [{"name": name} for name in labels],
        "assignees": [{"login": login} for login in assignees],
        "blockedBy": {"nodes": [{"number": 100 + i, "state": s} for i, s in enumerate(blocked_by)]},
    }


def test_an_unblocked_ready_issue_is_dispatchable():
    assert select([issue(4)]) == [4]


def test_an_issue_whose_blockers_are_all_closed_is_dispatchable():
    """The case issue #4 was in: blocked by #3, which closed."""
    assert select([issue(4, blocked_by=("CLOSED",))]) == [4]


def test_an_issue_with_an_open_blocker_waits():
    assert select([issue(8, blocked_by=("CLOSED", "OPEN"))]) == []


def test_an_issue_without_the_ready_label_is_not_dispatched():
    assert select([issue(5, labels=("bug",))]) == []


@pytest.mark.parametrize("claimed_label", ["agent-dispatched", "needs-human"])
def test_a_claimed_or_escalated_issue_is_not_dispatched_again(claimed_label):
    assert select([issue(4, labels=("ready-for-agent", claimed_label))]) == []


def test_an_assigned_issue_is_left_to_its_assignee():
    assert select([issue(4, assignees=("octocat",))]) == []


def test_a_closed_issue_is_not_dispatched():
    assert select([issue(4, state="CLOSED")]) == []


def test_a_parent_spec_is_not_a_ticket():
    """`Spec: Release v1` (#2) carries the ready label and nine sub-issues; building it whole is wrong."""
    assert select([issue(2, sub_issues=9), issue(4)]) == [4]


def test_lowest_numbers_first_and_capped():
    issues = [issue(10), issue(4), issue(7)]
    assert select(issues, limit=2) == [4, 7]


def test_nothing_to_do_is_an_empty_list():
    assert select([]) == []


def test_a_malformed_issue_raises_a_named_error_not_a_skip():
    """A wrong shape from `gh` must be loud: silently skipping would stall the factory."""
    with pytest.raises(DispatchInputError, match="number"):
        select([{"labels": []}])


@pytest.mark.parametrize("field", ["state", "subIssuesSummary"])
def test_a_missing_field_is_loud_not_permissive(field):
    """If `gh` stops returning a field, dispatching a parent spec or closed issue would be wrong."""
    item = issue(5)
    del item[field]
    with pytest.raises(DispatchInputError, match=field):
        select([item])


def test_cli_reads_gh_json_and_prints_a_json_list():
    payload = json.dumps([issue(9, blocked_by=("OPEN",)), issue(4)])
    out = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "select_dispatchable.py"), "--limit", "3"],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out) == [4]


def test_cli_exits_nonzero_on_invalid_json():
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "select_dispatchable.py")],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "invalid JSON" in result.stderr


def test_label_names_are_parameters():
    issues = [issue(1, labels=("queue",)), issue(2, labels=("queue", "hold")), issue(3)]

    assert select(issues, ready_label="queue", excluding_labels=frozenset({"hold"})) == [1]

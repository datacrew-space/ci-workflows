"""What the ci-review-orchestrator reports at the end of a review run.

`needs-human` (label and @mention) is only for a decision about the code: two rounds did not
converge, or the responder pushed nothing. A tooling failure is red with no label and no ping.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review-report.sh"


def report(outcome: str, round_: str = "1", pushed: str = "", count: str = "") -> tuple[str, str, str, str]:
    out = subprocess.run(
        ["bash", str(SCRIPT), outcome, round_, pushed, count],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    state, label, ping, message = out.split("|", 3)
    return state, label, ping, message


@pytest.mark.parametrize("round_", ["1", "2"])
def test_approved_is_green_and_clears_the_label(round_):
    state, label, ping, message = report("approved-for-merge", round_)
    assert (state, label, ping) == ("success", "remove", "no")
    assert message.startswith(f"Round {round_} of 2: approved")


def test_round_one_with_a_responder_push_is_pending_and_announces_round_two():
    state, label, ping, message = report("changes-requested", "1", "true", "3")
    assert (state, label, ping) == ("pending", "remove", "no")
    assert "pushed 3 commit(s)" in message and "round 2 of 2" in message


@pytest.mark.parametrize("pushed", ["false", "", "junk"])
def test_round_one_without_a_push_needs_a_human(pushed):
    assert report("changes-requested", "1", pushed)[:3] == ("failure", "add", "yes")


def test_a_responder_that_never_reached_its_push_is_a_tooling_failure():
    """Setup or agent error in `respond` is not a decision about the code: no label, no ping."""
    state, label, ping, message = report("changes-requested", "1", "failed")
    assert (state, label, ping) == ("failure", "remove", "no")
    assert "did not finish" in message and "re-run the workflow" in message


def test_round_two_still_requesting_changes_needs_a_human():
    state, label, ping, message = report("escalate-to-human", "2")
    assert (state, label, ping) == ("failure", "add", "yes")
    assert message.startswith("Round 2 of 2:")


@pytest.mark.parametrize("outcome", ["no-verdict", "tooling-failure", "", "anything-else"])
def test_tooling_failures_are_red_without_label_or_ping(outcome):
    state, label, ping, message = report(outcome)
    assert (state, label, ping) == ("failure", "remove", "no")
    assert "re-run the workflow" in message


@pytest.mark.parametrize(("round_", "shown"), [("2", "2"), ("", "1"), ("3", "1"), ("x", "1")])
def test_bad_round_counts_as_round_one(round_, shown):
    assert report("approved-for-merge", round_)[3].startswith(f"Round {shown} of 2")


def test_bad_count_shows_zero():
    assert "pushed 0 commit(s)" in report("changes-requested", "1", "true", "abc")[3]


def test_label_and_ping_always_go_together():
    """A label without a ping (or the reverse) is the noise this script exists to remove."""
    for outcome in (
        "approved-for-merge",
        "changes-requested",
        "escalate-to-human",
        "no-verdict",
        "",
    ):
        for pushed in ("true", "false", "failed"):
            _, label, ping, _ = report(outcome, "1", pushed, "1")
            assert (label == "add") == (ping == "yes")


def test_message_has_no_field_separator():
    """The workflow splits on `|`; a message containing one would shift the fields."""
    for outcome in (
        "approved-for-merge",
        "changes-requested",
        "escalate-to-human",
        "no-verdict",
        "",
    ):
        assert "|" not in report(outcome, "1", "true", "1")[3]

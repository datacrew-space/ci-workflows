"""The `review-verdict` status ends green or needs a human; pending only means an agent is working.

Only an explicit `approved` is success. The first `changes-requested` is pending (the
review-responder is fixing). A second one, or a missing or ambiguous verdict, needs a human.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verdict-to-status.sh"


def run(verdict_file: Path | None, prior: str | None = None) -> tuple[str, str, str]:
    args = ["bash", str(SCRIPT)]
    if verdict_file:
        args.append(str(verdict_file))
        if prior is not None:
            args.append(prior)
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()
    state, outcome, desc = out.split("|", 2)
    return state, outcome, desc


def result(tmp_path: Path, content: bytes | None, prior: str | None = None) -> tuple[str, str]:
    f = tmp_path / "verdict"
    if content is not None:
        f.write_bytes(content)
    state, outcome, _ = run(f, prior)
    return state, outcome


def test_approved_is_success(tmp_path):
    assert result(tmp_path, b"approved\n") == ("success", "approved-for-merge")


def test_approved_after_a_prior_round_is_still_success(tmp_path):
    assert result(tmp_path, b"approved", "1") == ("success", "approved-for-merge")


def test_first_changes_requested_is_pending_while_the_responder_works(tmp_path):
    assert result(tmp_path, b"changes-requested", "0") == (
        "pending",
        "changes-requested",
    )


def test_second_changes_requested_escalates_to_human(tmp_path):
    assert result(tmp_path, b"changes-requested", "1") == (
        "failure",
        "escalate-to-human",
    )
    assert result(tmp_path, b"changes-requested", "5") == (
        "failure",
        "escalate-to-human",
    )


@pytest.mark.parametrize("prior", [None, "", "abc", "-1", "1.5"])
def test_bad_prior_count_counts_as_first_round(tmp_path, prior):
    assert result(tmp_path, b"changes-requested", prior) == (
        "pending",
        "changes-requested",
    )


@pytest.mark.parametrize(
    "content",
    [
        b"",  # empty
        b"\n",
        b"Approved",  # wrong case
        b"approved but see comments",  # extra words must not count
        b"not approved",
        b"approve",
        b"lgtm",
        b"approved\nchanges-requested",  # ambiguous
        b"escalate-to-human",  # the agent cannot escalate or approve by naming the outcome
        b"approved-for-merge",
    ],
)
@pytest.mark.parametrize("prior", ["0", "1"])
def test_anything_else_needs_a_human(tmp_path, content, prior):
    assert result(tmp_path, content, prior) == ("failure", "no-verdict")


def test_missing_file_needs_a_human(tmp_path):
    assert result(tmp_path, None) == ("failure", "no-verdict")


def test_no_argument_needs_a_human():
    state, outcome, _ = run(None)
    assert (state, outcome) == ("failure", "no-verdict")


def test_only_the_responder_wait_is_pending(tmp_path):
    """Pending must never hide a dead end: no input except round-1 changes-requested yields it."""
    for content in (b"approved", b"changes-requested", b"", b"junk"):
        for prior in ("0", "1"):
            state, outcome = result(tmp_path, content, prior)
            assert (state == "pending") == (outcome == "changes-requested" and prior == "0")

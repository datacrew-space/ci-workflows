"""The review is skipped only when this head already has a finished verdict from the orchestrator."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review-skip.sh"
# Locally a missing jq skips; in CI (the `CI` env var is set) it fails, so the tests cannot go silent.
if shutil.which("jq") is None:
    if os.environ.get("CI"):
        pytest.fail("jq is missing on this CI runner: the review-skip tests cannot run", pytrace=False)
    pytestmark = pytest.mark.skip(reason="needs jq (present on CI runners)")
BOT = "ci-review-orchestrator[bot]"


def status(state: str, creator: str = BOT, created: str = "2026-01-01T00:00:00Z", id_: int = 1, context="review-verdict"):
    return {"id": id_, "context": context, "state": state, "created_at": created, "creator": {"login": creator}}


def decide(tmp_path: Path, statuses, attempt: str = "1", login: str = "ci-review-orchestrator") -> tuple[str, str]:
    f = tmp_path / "statuses.json"
    f.write_text(statuses if isinstance(statuses, str) else json.dumps(statuses))
    out = subprocess.run(["bash", str(SCRIPT), str(f), login, attempt], capture_output=True, text=True, check=True).stdout
    decision, reason = out.strip().split("|", 1)
    return decision, reason


@pytest.mark.parametrize("state", ["success", "failure"])
def test_a_finished_verdict_from_the_orchestrator_skips(tmp_path, state):
    assert decide(tmp_path, [status(state)])[0] == "skip"


@pytest.mark.parametrize("state", ["pending", "error"])
def test_an_unfinished_verdict_runs(tmp_path, state):
    assert decide(tmp_path, [status(state)])[0] == "run"


def test_no_verdict_status_runs(tmp_path):
    assert decide(tmp_path, [])[0] == "run"
    assert decide(tmp_path, [status("success", context="ci-ok")])[0] == "run"


def test_a_verdict_from_another_creator_runs(tmp_path):
    assert decide(tmp_path, [status("success", creator="github-actions[bot]")])[0] == "run"
    assert decide(tmp_path, [status("success", creator="someone")])[0] == "run"


def test_a_login_with_a_suffix_does_not_match(tmp_path):
    assert decide(tmp_path, [status("success", creator="ci-review-orchestrator-evil[bot]")])[0] == "run"


@pytest.mark.parametrize("attempt", ["2", "3"])
def test_a_rerun_always_reviews(tmp_path, attempt):
    assert decide(tmp_path, [status("success")], attempt)[0] == "run"


@pytest.mark.parametrize("attempt", ["", "junk", "0"])
def test_an_odd_attempt_counts_as_the_first(tmp_path, attempt):
    assert decide(tmp_path, [status("success")], attempt)[0] == "skip"


def test_the_newest_status_wins_whatever_the_order(tmp_path):
    old_ok = status("success", created="2026-01-01T00:00:00Z", id_=1)
    new_pending = status("pending", creator="github-actions[bot]", created="2026-01-01T01:00:00Z", id_=2)
    assert decide(tmp_path, [old_ok, new_pending])[0] == "run"
    assert decide(tmp_path, [new_pending, old_ok])[0] == "run"
    old_pending = status("pending", created="2026-01-01T00:00:00Z", id_=1)
    new_fail = status("failure", created="2026-01-01T01:00:00Z", id_=2)
    assert decide(tmp_path, [new_fail, old_pending])[0] == "skip"


def test_unreadable_input_runs(tmp_path):
    assert decide(tmp_path, "not json")[0] == "run"
    assert decide(tmp_path, '{"message": "Not Found"}')[0] == "run"
    assert subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True).stdout.startswith("run|")


def test_a_custom_orchestrator_login_is_honoured(tmp_path):
    assert decide(tmp_path, [status("success", creator="my-orch[bot]")], login="my-orch")[0] == "skip"
    assert decide(tmp_path, [status("success")], login="my-orch")[0] == "run"

"""auto-merge.yml arms GitHub auto-merge only for a labelled, ready pull request.

The decision script is run for real with a stand-in `gh` on PATH that answers fixed pull request state and
records every call. The rest pins the workflow's structure: where the script comes from and what the
token may do.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto-merge.yml"
CALLER = ROOT / "examples" / "auto-merge-caller.yml"
SCRIPT = ROOT / "scripts" / "auto_merge.py"

# `gh pr view` answers the JSON in STUB_VIEW (or fails); `gh pr merge` is recorded and may fail.
GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
    if [ "${STUB_FAIL:-}" = "view" ]; then echo "gh: Server Error (HTTP 502)" >&2; exit 1; fi
    printf '%s' "$STUB_VIEW" ;;
  "pr merge")
    if [ "${STUB_FAIL:-}" = "merge" ]; then echo "gh: auto merge is not allowed" >&2; exit 1; fi
    exit 0 ;;
  *)
    exit 1 ;;
esac
"""


def pr(state: str = "OPEN", draft: bool = False, labels: tuple[str, ...] = ("automerge",), armed: bool = False) -> str:
    return json.dumps(
        {
            "state": state,
            "isDraft": draft,
            "labels": [{"name": name} for name in labels],
            "autoMergeRequest": {"mergeMethod": "SQUASH"} if armed else None,
        }
    )


def run_script(tmp_path: Path, view: str, fail: str = "", **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the script. Returns the finished process and the `gh pr merge` calls it made."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "gh").write_text(GH_STUB, encoding="utf-8", newline="\n")
    (tmp_path / "gh").chmod(0o755)
    if sys.platform == "win32":  # CreateProcess cannot run a bash script; a shim in front of it can
        (tmp_path / "gh.cmd").write_text('@bash "%~dp0gh" %*\r\n', encoding="utf-8", newline="")
    log = tmp_path / "gh.log"
    full = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_REPO": "o/r",
        "PR_NUMBER": "7",
        "STUB_VIEW": view,
        "STUB_FAIL": fail,
        **env,
    }
    done = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, env=full, check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, [c for c in calls if c.startswith("pr merge")]


ARM = "pr merge 7 --auto --squash"
DISARM = "pr merge 7 --disable-auto"


def test_a_labelled_ready_pull_request_is_armed(tmp_path):
    done, merges = run_script(tmp_path, pr())
    assert done.returncode == 0, done.stderr
    assert merges == [ARM]


@pytest.mark.parametrize("method", ["merge", "rebase", "squash"])
def test_the_merge_method_is_an_input(tmp_path, method):
    done, merges = run_script(tmp_path, pr(), MERGE_METHOD=method)
    assert done.returncode == 0, done.stderr
    assert merges == [f"pr merge 7 --auto --{method}"]


def test_an_unknown_merge_method_fails_without_any_call(tmp_path):
    done, merges = run_script(tmp_path, pr(), MERGE_METHOD="fast-forward")
    assert done.returncode == 1
    assert merges == []


def test_a_custom_label_is_honoured(tmp_path):
    _, merges = run_script(tmp_path / "a", pr(labels=("ship",)), AUTOMERGE_LABEL="ship")
    assert merges == [ARM]
    _, merges = run_script(tmp_path / "b", pr(labels=("automerge",)), AUTOMERGE_LABEL="ship")
    assert merges == []


@pytest.mark.parametrize(
    "view",
    [
        pr(labels=()),
        pr(labels=("automerge-not",)),
        pr(draft=True),
        pr(state="CLOSED"),
        pr(state="MERGED"),
    ],
    ids=["unlabelled", "other label", "draft", "closed", "merged"],
)
def test_a_pull_request_that_does_not_qualify_is_never_armed(tmp_path, view):
    done, merges = run_script(tmp_path, view)
    assert done.returncode == 0, done.stderr
    assert merges == []


@pytest.mark.parametrize(
    "view",
    [pr(labels=(), armed=True), pr(draft=True, armed=True)],
    ids=["label removed", "converted to draft"],
)
def test_an_armed_pull_request_that_no_longer_qualifies_is_disarmed(tmp_path, view):
    done, merges = run_script(tmp_path, view)
    assert done.returncode == 0, done.stderr
    assert merges == [DISARM]


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_a_finished_pull_request_is_left_alone_even_if_it_says_armed(tmp_path, state):
    done, merges = run_script(tmp_path, pr(state=state, labels=(), armed=True))
    assert done.returncode == 0, done.stderr
    assert merges == []


def test_an_already_armed_qualifying_pull_request_is_a_no_op(tmp_path):
    done, merges = run_script(tmp_path, pr(armed=True))
    assert done.returncode == 0, done.stderr
    assert merges == []


def test_a_failing_read_makes_no_merge_call_and_fails(tmp_path):
    done, merges = run_script(tmp_path, pr(), fail="view")
    assert done.returncode == 1
    assert merges == []
    assert "502" in done.stderr


@pytest.mark.parametrize("view", ["not json", "[]", json.dumps({"state": "OPEN"})], ids=["text", "list", "missing"])
def test_an_answer_of_the_wrong_shape_makes_no_merge_call_and_fails(tmp_path, view):
    done, merges = run_script(tmp_path, view)
    assert done.returncode == 1
    assert merges == []


@pytest.mark.parametrize("view", [pr(), pr(labels=(), armed=True)], ids=["arm", "disarm"])
def test_a_failing_merge_call_fails_the_step(tmp_path, view):
    done, merges = run_script(tmp_path, view, fail="merge")
    assert done.returncode == 1
    assert len(merges) == 1


@pytest.mark.parametrize("number", ["", "7; rm -rf /", "abc"], ids=["empty", "injection", "text"])
def test_a_bad_pull_request_number_fails_before_any_call(tmp_path, number):
    done, merges = run_script(tmp_path, pr(), PR_NUMBER=number)
    assert done.returncode == 1
    assert merges == []


# --- Structure -----------------------------------------------------------------------------------------


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> dict:
    return load(WORKFLOW)


def test_it_is_a_reusable_workflow_with_a_default_squash_method(doc):
    inputs = doc[True]["workflow_call"]["inputs"]  # PyYAML reads the key `on` as True
    assert inputs["merge-method"]["default"] == "squash"
    assert inputs["tooling-ref"]["default"] == "main"
    assert inputs["label"]["default"] == "automerge"


def test_the_script_is_checked_out_from_tooling_ref_not_the_caller(doc):
    steps = doc["jobs"]["reconcile"]["steps"]
    checkout = steps[0]["with"]
    assert checkout["repository"] == "datacrew-space/ci-workflows"
    assert checkout["ref"] == "${{ inputs.tooling-ref }}"
    assert checkout["sparse-checkout"] == "scripts/auto_merge.py"
    assert checkout["persist-credentials"] is False
    assert "ci-tooling/scripts/auto_merge.py" in steps[1]["run"]


def test_permissions_are_the_least_the_merge_call_needs(doc):
    assert doc["permissions"] == {"contents": "read"}
    assert doc["jobs"]["reconcile"]["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_the_pull_request_is_never_checked_out_and_no_event_text_reaches_a_shell(doc):
    text = json.dumps(doc["jobs"])
    assert "github.head_ref" not in text
    assert "github.event.pull_request.title" not in text
    assert "github.event.pull_request.body" not in text
    step = doc["jobs"]["reconcile"]["steps"][1]
    assert "${{" not in step["run"]
    assert step["env"]["PR_NUMBER"] == "${{ github.event.pull_request.number }}"


def test_runs_for_one_pull_request_queue_instead_of_cancelling(doc):
    group = doc["jobs"]["reconcile"]["concurrency"]
    assert group["cancel-in-progress"] is False
    assert "github.event.pull_request.number" in group["group"]


def test_the_example_caller_has_no_job_level_if_that_could_skip_a_run():
    caller = load(CALLER)
    job = caller["jobs"]["auto-merge"]
    assert "if" not in job
    assert job["uses"].startswith("datacrew-space/ci-workflows/.github/workflows/auto-merge.yml@")
    types = set(caller[True]["pull_request_target"]["types"])
    assert {"labeled", "unlabeled", "ready_for_review", "converted_to_draft", "reopened", "closed"} <= types
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_the_readme_says_the_repository_setting_must_be_on_and_why():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "auto-merge.yml" in readme
    assert "Allow auto-merge" in readme

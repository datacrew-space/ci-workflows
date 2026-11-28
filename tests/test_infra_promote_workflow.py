"""Structure of the infra-promote reusable workflow and its caller example.

Assertions compare whole parsed values, not substrings, so a flipped clause fails a test.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "infra-promote.yml"
CALLER = ROOT / "examples" / "infra-promote-caller.yml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


DOC = load(WF)
CALL = (DOC.get("on") or DOC.get(True))["workflow_call"]  # YAML 1.1 parses the bare key `on` as True
JOB = DOC["jobs"]["promote"]
STEPS = JOB["steps"]


def step(name: str) -> dict:
    (found,) = [s for s in STEPS if s.get("name") == name]
    return found


def test_inputs_and_defaults():
    inputs = CALL["inputs"]
    assert inputs["tooling-ref"]["required"] is True
    assert {k: v["default"] for k, v in inputs.items() if "default" in v} == {
        "runs-on": '"ubuntu-latest"',
        "main-branch": "main",
        "deploy-branch": "deploy",
        "tag-prefix": "deploy/",
        "required-check": "ci-ok",
        "sha": "",
        "dry-run": False,
    }


def test_the_only_secret_is_an_optional_promote_token():
    assert list(CALL["secrets"]) == ["PROMOTE_TOKEN"]
    assert CALL["secrets"]["PROMOTE_TOKEN"]["required"] is False


def test_permissions_are_the_least_that_works():
    assert DOC["permissions"] == {"contents": "write", "checks": "read"}


def test_runner_comes_from_the_json_input():
    assert JOB["runs-on"] == "${{ fromJSON(inputs.runs-on) }}"


def test_promotions_are_serialised_and_never_cancelled():
    assert JOB["concurrency"] == {
        "group": "infra-promote-${{ github.repository }}-${{ inputs.deploy-branch }}",
        "cancel-in-progress": False,
    }


def test_outputs_are_forwarded_from_the_promote_step():
    assert sorted(CALL["outputs"]) == ["previous", "result", "sha", "tag"]
    for key in CALL["outputs"]:
        assert CALL["outputs"][key]["value"] == f"${{{{ jobs.promote.outputs.{key} }}}}"
        assert JOB["outputs"][key] == f"${{{{ steps.promote.outputs.{key} }}}}"


def test_no_expression_is_interpolated_into_a_run_script():
    # Untrusted or caller-supplied values reach scripts only through env, never through ${{ }} in run.
    for s in STEPS:
        assert "${{" not in s.get("run", ""), f"step {s.get('name')!r} interpolates into run"


def test_scripts_come_from_the_pinned_tooling_not_the_checkout():
    assert step("Fetch the promote tooling")["env"] == {"TOOLING_REF": "${{ inputs.tooling-ref }}"}
    for name in ("Require the check to be green on this commit", "Promote"):
        assert step(name)["env"]["TOOLING"] == "${{ runner.temp }}/ci-tooling"
    assert "$TOOLING/scripts/check_required_green.py" in step("Require the check to be green on this commit")["run"]
    assert step("Promote")["run"] == 'bash "$TOOLING/scripts/promote-deploy.sh"'


def test_the_check_is_verified_before_anything_is_pushed():
    names = [s.get("name") for s in STEPS]
    assert names.index("Require the check to be green on this commit") < names.index("Promote")


def test_a_red_check_stops_at_once_and_only_undecided_is_retried():
    run = step("Require the check to be green on this commit")["run"]
    assert 'if [ "$rc" -ne 2 ]; then exit "$rc"; fi' in run
    assert "was never green" in run


def test_checkout_uses_the_optional_token_and_full_history():
    with_ = STEPS[0]["with"]
    assert with_["fetch-depth"] == 0
    assert with_["token"] == "${{ secrets.PROMOTE_TOKEN || github.token }}"


def test_the_token_is_only_used_for_checkout():
    holders = [s["name"] for s in STEPS if "PROMOTE_TOKEN" in str(s)]
    assert holders == ["Check out the repository"]
    assert WF.read_text().count("secrets.PROMOTE_TOKEN") == 1


def test_dry_run_reaches_the_script_as_a_flag():
    assert step("Promote")["env"]["DRY_RUN"] == "${{ inputs.dry-run && '1' || '0' }}"


def test_the_only_hosts_named_are_github():
    # A public workflow must not carry a host that belongs to one user's setup.
    hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", WF.read_text() + CALLER.read_text()))
    assert hosts == {"github.com"}


def test_caller_example_runs_only_after_ci_ok_on_a_main_push():
    job = load(CALLER)["jobs"]["promote"]
    assert job["needs"] == "ci-ok"
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"contents": "write", "checks": "read"}
    assert job["uses"].startswith("datacrew-space/ci-workflows/.github/workflows/infra-promote.yml@")
    assert job["with"]["tooling-ref"] == "<COMMIT_SHA>"

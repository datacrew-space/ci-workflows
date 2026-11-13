"""Structure of the guards wiring: the reusable workflow, its callers and the pre-commit configs.

Assertions compare whole parsed values, not substrings, so a flipped clause fails a test.
"""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def on_of(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as True.
    return doc.get("on", doc.get(True))


def steps(doc: dict) -> list[dict]:
    return doc["jobs"]["guards"]["steps"]


def step(doc: dict, name: str) -> dict:
    (found,) = [s for s in steps(doc) if s.get("name") == name]
    return found


def run_lines(s: dict) -> list[str]:
    return [ln.strip() for ln in s["run"].splitlines()]


GUARDS = load(WF / "guards.yml")


def test_is_a_reusable_workflow_with_the_specified_inputs():
    call = on_of(GUARDS)["workflow_call"]
    assert call["inputs"]["tooling-ref"]["default"] == "main"
    assert call["inputs"]["allowed-email-regex"]["default"] == ""
    assert call["inputs"]["all-history"]["type"] == "boolean"
    assert call["inputs"]["all-history"]["default"] is False


def test_private_terms_secret_is_optional():
    assert on_of(GUARDS)["workflow_call"]["secrets"]["PRIVATE_TERMS"]["required"] is False


def test_checkout_is_full_depth_without_credentials():
    first = steps(GUARDS)[0]
    assert first["uses"] == "actions/checkout@v4"
    assert first["with"] == {"fetch-depth": 0, "persist-credentials": False}


def test_permissions_are_read_only():
    assert GUARDS["permissions"] == {"contents": "read"}


def test_secret_only_in_env_never_in_run():
    for s in steps(GUARDS):
        assert "secrets.PRIVATE_TERMS" not in s.get("run", "")
        assert "secrets.PRIVATE_TERMS" not in str(s.get("with", {}))
    holders = [s["name"] for s in steps(GUARDS) if "secrets.PRIVATE_TERMS" in str(s.get("env", {}))]
    assert holders == ["Private terms"]
    assert step(GUARDS, "Private terms")["env"]["PRIVATE_TERMS"] == "${{ secrets.PRIVATE_TERMS }}"
    assert (WF / "guards.yml").read_text().count("secrets.PRIVATE_TERMS") == 1  # the one in env, nowhere else


def test_no_step_echoes_the_secret_or_traces():
    for s in steps(GUARDS):
        run = s.get("run", "")
        assert "set -x" not in run
        for ln in run.splitlines():
            if "$PRIVATE_TERMS" in ln:
                assert ln.strip() == 'if [ -z "$PRIVATE_TERMS" ]; then'


def test_private_terms_step_requires_a_denylist():
    lines = run_lines(step(GUARDS, "Private terms"))
    assert 'bash "$TOOLING/scripts/check-private-terms.sh" --require --range "$RANGE"' in lines
    assert 'bash "$TOOLING/scripts/check-private-terms.sh" --require --all-history' in lines
    assert all("check-private-terms.sh" not in ln or "--require" in ln for ln in lines)


def test_identity_step_ranges_and_default_regex():
    s = step(GUARDS, "Commit identity")
    assert s["env"]["ALLOWED_EMAIL_REGEX"] == "${{ inputs.allowed-email-regex }}"
    lines = run_lines(s)
    assert 'bash "$TOOLING/scripts/check-commit-identity.sh" --range "$RANGE"' in lines
    assert 'bash "$TOOLING/scripts/check-commit-identity.sh" --all-history' in lines
    assert 'if [ -z "$ALLOWED_EMAIL_REGEX" ]; then unset ALLOWED_EMAIL_REGEX; fi' in lines


def test_range_step_expressions():
    s = step(GUARDS, "Choose the scan range")
    assert s["env"] == {
        "EVENT_NAME": "${{ github.event_name }}",
        "ALL_HISTORY": "${{ inputs.all-history }}",
        "PR_BASE": "${{ github.event.pull_request.base.sha }}",
        "PR_HEAD": "${{ github.event.pull_request.head.sha }}",
        "PUSH_BEFORE": "${{ github.event.before }}",
        "PUSH_SHA": "${{ github.sha }}",
    }
    lines = run_lines(s)
    assert 'range="${PR_BASE}..${PR_HEAD}"' in lines
    assert 'range="${PUSH_BEFORE}..${PUSH_SHA}"' in lines
    assert 'if [ "$ALL_HISTORY" != "true" ]; then' in lines
    assert lines.count("mode=all") == 1  # the default is the wide scan


def test_range_step_runs_correctly(tmp_path):
    """Execute the step's script for each event shape."""
    run = step(GUARDS, "Choose the scan range")["run"]

    def go(**env):
        out = tmp_path / "out"
        out.write_text("")
        full = {**os.environ, "GITHUB_OUTPUT": str(out), "PR_BASE": "", "PR_HEAD": "", **env}
        subprocess.run(["bash", "-c", run], check=True, env=full)
        return dict(ln.split("=", 1) for ln in out.read_text().splitlines())

    zeros = "0" * 40
    base = {"ALL_HISTORY": "false", "PUSH_BEFORE": "", "PUSH_SHA": "s"}
    assert go(**base, EVENT_NAME="pull_request", PR_BASE="a", PR_HEAD="b") == {"mode": "range", "range": "a..b"}
    assert go(**{**base, "PUSH_BEFORE": "p"}, EVENT_NAME="push") == {"mode": "range", "range": "p..s"}
    assert go(**{**base, "PUSH_BEFORE": zeros}, EVENT_NAME="push") == {"mode": "all", "range": ""}
    assert go(**base, EVENT_NAME="schedule") == {"mode": "all", "range": ""}
    assert go(**base, EVENT_NAME="workflow_dispatch") == {"mode": "all", "range": ""}
    forced = go(**{**base, "ALL_HISTORY": "true"}, EVENT_NAME="pull_request", PR_BASE="a", PR_HEAD="b")
    assert forced == {"mode": "all", "range": ""}


def test_secret_scanners_use_the_shipped_config():
    s = step(GUARDS, "Secret scanners")
    assert s["run"] == 'pre-commit run --all-files --config "$RUNNER_TEMP/ci-tooling/configs/guards-pre-commit.yaml"'
    cfg = load(ROOT / "configs" / "guards-pre-commit.yaml")
    assert [h["id"] for r in cfg["repos"] for h in r["hooks"]] == ["detect-private-key", "gitleaks"]


def test_tooling_is_fetched_from_the_input_ref():
    s = step(GUARDS, "Fetch the guard tooling")
    assert s["env"] == {"TOOLING_REF": "${{ inputs.tooling-ref }}"}
    fetch = (
        'git -C "$RUNNER_TEMP/ci-tooling" fetch -q --depth 1 '
        'https://github.com/datacrew-space/ci-workflows.git "$TOOLING_REF"'
    )
    assert fetch in run_lines(s)


OWNER_REGEX = r"(@users\.noreply\.github\.com|^noreply@github\.com|^noreply@anthropic\.com|^jaewilson07@gmail\.com)$"


def test_ci_calls_the_trusted_gate_from_main():
    """The workflow AND the tooling come from main, never from the PR being checked (a PR could
    otherwise edit its own copy to pass with no denylist). No pull request head sha anywhere."""
    job = load(WF / "ci.yml")["jobs"]["guards"]
    assert job == {
        "uses": "datacrew-space/ci-workflows/.github/workflows/guards.yml@main",
        "with": {"tooling-ref": "main", "allowed-email-regex": OWNER_REGEX},
        "secrets": {"PRIVATE_TERMS": "${{ secrets.PRIVATE_TERMS }}"},
    }
    text = (WF / "ci.yml").read_text()
    assert "head.sha" not in text
    assert "BOOTSTRAP" not in text


def test_the_owners_regex_accepts_the_default_noreply_forms_and_the_owner_only():
    import re

    rx = re.compile(OWNER_REGEX, re.IGNORECASE)
    for ok in ("a@users.noreply.github.com", "noreply@github.com", "noreply@anthropic.com", "jaewilson07@gmail.com"):
        assert rx.search(ok), ok
    for bad in ("someone@gmail.com", "jaewilson07@gmail.com.evil.example", "x-jaewilson07@gmail.com", "a@example.org"):
        assert not rx.search(bad), bad


def test_trusted_caller_example_pins_the_workflow_and_the_tooling_to_main():
    """The intended form of ci.yml once the workflow exists on main (follow-up PR)."""
    job = load(ROOT / "examples" / "guards-caller.yml")["jobs"]["guards"]
    assert job == {
        "uses": "datacrew-space/ci-workflows/.github/workflows/guards.yml@main",
        "with": {"tooling-ref": "main"},
        "secrets": {"PRIVATE_TERMS": "${{ secrets.PRIVATE_TERMS }}"},
    }


def test_empty_secret_fails_the_workflow_closed_by_itself():
    lines = run_lines(step(GUARDS, "Private terms"))
    i = lines.index('if [ -z "$PRIVATE_TERMS" ]; then')
    assert lines[i + 2] == "exit 1" and lines[i + 3] == "fi"


def test_pull_request_target_is_unsupported():
    lines = run_lines(step(GUARDS, "Choose the scan range"))
    assert "pull_request | pull_request_target)" not in lines
    i = lines.index("pull_request_target)")
    assert lines[i + 2] == "exit 1"


def test_pre_commit_is_pinned_and_hooks_declare_a_minimum_version():
    assert "python -m pip install pre-commit==4.3.0" in run_lines(step(GUARDS, "Install pre-commit"))
    for h in yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text()):
        assert h["minimum_pre_commit_version"] == "3.2.0"


def test_history_workflow_is_weekly_manual_and_scans_all_history():
    doc = load(WF / "guards-history.yml")
    on = on_of(doc)
    assert len(on["schedule"]) == 1
    assert on["schedule"][0]["cron"].split()[2:] == ["*", "*", "1"]
    assert "workflow_dispatch" in on
    job = doc["jobs"]["guards"]
    assert job["uses"] == "./.github/workflows/guards.yml"
    assert job["with"]["all-history"] is True
    assert job["secrets"] == {"PRIVATE_TERMS": "${{ secrets.PRIVATE_TERMS }}"}


def test_exported_hooks():
    hooks = {h["id"]: h for h in yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text())}
    assert set(hooks) == {"private-terms", "private-terms-message", "commit-identity"}
    assert {h["language"] for h in hooks.values()} == {"script"}
    assert hooks["private-terms"]["entry"] == "scripts/check-private-terms.sh --staged"
    assert hooks["private-terms-message"]["entry"] == "scripts/check-private-terms.sh --message"
    assert hooks["private-terms-message"]["stages"] == ["commit-msg"]
    assert hooks["commit-identity"]["entry"] == "scripts/check-commit-identity.sh --identity"


def test_local_pre_commit_config():
    cfg = load(ROOT / ".pre-commit-config.yaml")
    remote = {r["repo"]: r for r in cfg["repos"] if r["repo"] != "local"}
    assert remote["https://github.com/pre-commit/pre-commit-hooks"]["rev"] == "v6.0.0"
    assert remote["https://github.com/gitleaks/gitleaks"]["rev"] == "v8.30.1"
    (local,) = [r for r in cfg["repos"] if r["repo"] == "local"]
    by_id = {h["id"]: h for h in local["hooks"]}
    assert by_id["private-terms"]["entry"] == "bash scripts/check-private-terms.sh --staged"
    assert by_id["private-terms-message"]["entry"] == "bash scripts/check-private-terms.sh --message"
    assert by_id["private-terms-message"]["stages"] == ["commit-msg"]
    assert by_id["commit-identity"]["entry"] == "bash scripts/check-commit-identity.sh --identity"
    assert {h["language"] for h in by_id.values()} == {"system"}


def test_scripts_are_executable_in_the_index():
    out = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "-s",
            "scripts/check-private-terms.sh",
            "scripts/check-commit-identity.sh",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert [ln.split()[0] for ln in out] == ["100755", "100755"]

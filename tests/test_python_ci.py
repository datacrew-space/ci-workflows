"""Structure of the reusable Python CI workflow, the shared base configs and the scaffold.

Assertions compare whole parsed values, not substrings, so a flipped clause fails a test.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "python-ci.yml"
SCAFFOLD = ROOT / "examples" / "python-uv-repo"
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def on_of(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as True.
    return doc.get("on", doc.get(True))


def all_steps(doc: dict):
    for name, job in doc["jobs"].items():
        for s in job["steps"]:
            yield name, s


PYCI = load(WF)
CALL = on_of(PYCI)["workflow_call"]


def test_inputs_and_defaults():
    ins = CALL["inputs"]
    assert ins["runs-on"]["default"] == '"ubuntu-latest"'
    assert ins["python-version"]["default"] == "3.13"
    assert ins["working-directory"]["default"] == "."
    assert ins["config-ref"]["default"] == ""
    assert all(i["type"] == "string" for i in ins.values())


def test_has_lint_and_test_but_no_gate_job():
    # A ci-ok inside a called workflow would be reported as `<caller job> / ci-ok` and rename the
    # required check, so the gate belongs to the caller.
    assert sorted(PYCI["jobs"]) == ["lint", "test"]


def test_both_jobs_take_the_runner_from_the_input_and_have_a_timeout():
    for job in PYCI["jobs"].values():
        assert job["runs-on"] == "${{ fromJSON(inputs.runs-on) }}"
        assert isinstance(job["timeout-minutes"], int)


def test_test_job_runs_in_the_working_directory_input():
    run = PYCI["jobs"]["test"]["defaults"]["run"]
    assert run["working-directory"] == "${{ inputs.working-directory }}"


def test_lint_runs_precommit_on_all_files_without_retrying_the_run():
    (run,) = [s for _, s in all_steps(PYCI) if s.get("name") == "Run pre-commit on all files"]
    assert "run --all-files" in run["run"]
    assert "attempt" not in run["run"]


def test_permissions_are_read_only_and_credentials_are_not_persisted():
    assert PYCI["permissions"] == {"contents": "read"}
    checkouts = [s for _, s in all_steps(PYCI) if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 2
    assert all(c["with"]["persist-credentials"] is False for c in checkouts)


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = [s["uses"] for _, s in all_steps(PYCI) if "uses" in s]
    assert uses
    unpinned = [u for u in uses if not SHA.match(u)]
    assert unpinned == []


def test_inputs_never_reach_the_shell_through_expression_interpolation():
    # `${{ inputs.* }}` inside `run:` is script injection; values must travel through `env:`.
    for name, s in all_steps(PYCI):
        assert "${{" not in s.get("run", ""), f"{name}: {s.get('name')}"


def test_drift_check_is_off_unless_a_config_ref_is_given():
    (drift,) = [s for _, s in all_steps(PYCI) if str(s.get("name", "")).startswith("Check ruff-base")]
    assert drift["if"] == "inputs.config-ref != ''"


# --- base configs -------------------------------------------------------------------------------

RUFF = tomllib.loads((ROOT / "configs" / "ruff-base.toml").read_text())
PRECOMMIT = load(ROOT / "configs" / "python-pre-commit.yaml")


def test_ruff_base_pins_an_explicit_py313_rule_set():
    assert RUFF["target-version"] == "py313"
    assert RUFF["line-length"] == 120
    sel = RUFF["lint"]["select"]
    assert "ALL" not in sel
    assert {"F", "BLE", "T20", "TRY002"} <= set(sel)


def test_ruff_base_skips_vendor_even_for_explicitly_passed_files():
    assert RUFF["force-exclude"] is True
    assert {"**/vendor/**", "**/third_party/**"} <= set(RUFF["extend-exclude"])


def test_precommit_vendor_slot_matches_vendor_paths_and_not_source():
    pattern = re.compile(PRECOMMIT["exclude"])
    assert pattern.search("vendor/pkg/mod.py")
    assert pattern.search("wrappers/x/third_party/mod.py")
    assert not pattern.search("src/vendored_names.py")
    assert not pattern.search("src/app/main.py")


def test_precommit_revs_are_pinned_versions_not_branches():
    for repo in PRECOMMIT["repos"]:
        if repo["repo"] == "local":
            continue
        assert re.fullmatch(r"v?\d+\.\d+\.\d+", repo["rev"]), repo["repo"]


def test_precommit_runs_the_expected_hooks():
    ids = {h["id"] for r in PRECOMMIT["repos"] for h in r["hooks"]}
    assert {"ruff-check", "ruff-format", "actionlint", "detect-secrets", "uv-lock"} <= ids


def test_base_configs_have_no_organisation_specific_content():
    # Public repository: no logger bans, hostnames or private names in the shared base.
    text = (ROOT / "configs" / "ruff-base.toml").read_text() + (ROOT / "configs" / "python-pre-commit.yaml").read_text()
    assert "TID251" not in text
    assert "crew-logger" not in text


# --- scaffold -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scaffold_name", "canonical"),
    [("ruff-base.toml", "ruff-base.toml"), (".pre-commit-config.yaml", "python-pre-commit.yaml")],
)
def test_scaffold_copies_equal_the_canonical_configs(scaffold_name, canonical):
    assert (SCAFFOLD / scaffold_name).read_bytes() == (ROOT / "configs" / canonical).read_bytes()


def test_scaffold_ruff_extends_the_base_and_never_replaces_its_rules():
    cfg = tomllib.loads((SCAFFOLD / "ruff.toml").read_text())
    assert cfg["extend"] == "ruff-base.toml"
    assert "select" not in cfg.get("lint", {})


def test_scaffold_caller_uses_the_reusable_workflow_and_keeps_the_gate():
    caller = load(SCAFFOLD / ".github" / "workflows" / "ci.yml")
    assert sorted(caller["jobs"]) == ["ci-ok", "python"]
    assert caller["jobs"]["python"]["uses"].startswith("datacrew-space/ci-workflows/.github/workflows/python-ci.yml@")
    gate = caller["jobs"]["ci-ok"]
    assert gate["needs"] == ["python"]
    assert gate["if"] == "always()"
    assert gate["name"] == "ci-ok"
    assert caller["permissions"] == {"contents": "read"}
    # The default cancels only pull-request runs, never pushes to the default branch.
    assert caller["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"

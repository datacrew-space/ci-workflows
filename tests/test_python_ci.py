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
    assert {"F", "BLE", "T20", "TRY002", "I", "UP"} <= set(sel)


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


def test_ruff_base_bans_stdlib_and_third_party_loggers():
    banned = RUFF["lint"]["flake8-tidy-imports"]["banned-api"]
    assert {"logging", "logging.handlers", "logging.config", "loguru", "structlog"} == set(banned)
    assert "TID251" in RUFF["lint"]["select"]


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


def test_detect_secrets_scans_workflow_files_and_skips_only_sha_pin_lines():
    (hook,) = [h for r in PRECOMMIT["repos"] for h in r["hooks"] if h["id"] == "detect-secrets"]
    assert not re.search(hook["exclude"], ".github/workflows/ci.yml")
    pattern = re.compile(hook["args"][hook["args"].index("--exclude-lines") + 1])
    assert pattern.search("    uses: actions/checkout@" + "a" * 40 + " # v7.0.1")
    assert pattern.search("      config-ref: " + "b" * 40)
    assert not pattern.search("      token: ghp_" + "a" * 36)
    assert not pattern.search("        run: echo uses: " + "d" * 40)  # a secret smuggled onto a similar line
    assert not pattern.search("      TOKEN: " + "e" * 40 + " # config-ref: x")
    assert not pattern.search("      API_KEY: " + "c" * 40)


# --- behaviour of the inline scripts ------------------------------------------------------------

import shutil  # noqa: E402
import subprocess  # noqa: E402

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


def step_script(workflow: dict, job: str, name: str) -> str:
    (s,) = [s for s in workflow["jobs"][job]["steps"] if s.get("name") == name]
    return s["run"]


def run_arg_parser(env_name: str, value: str, script: str) -> subprocess.CompletedProcess:
    # Keep everything up to the tool call; replace the tool call itself with a print of the arguments.
    head = script.split('uv sync "${args[@]}"')[0].split('uv run --no-sync python -m pytest "${args[@]}"')[0]
    head = head.replace("uv run --no-project python", "python")
    program = head + 'printf "[%s]\n" "${args[@]}"\n'
    env = {**__import__("os").environ, env_name: value}
    done = subprocess.run([BASH, "-c", program], env=env, capture_output=True)
    # Python prints CRLF on Windows; the workflow runs on Linux runners, so compare LF output.
    done.stdout = done.stdout.replace(bytes([13]), b"").decode()
    return done


@needs_bash
@pytest.mark.parametrize(
    ("step", "env_name"),
    [("Sync the locked environment", "SYNC_ARGS"), ("Run pytest", "PYTEST_ARGS")],
)
def test_argument_parsing_keeps_quoted_values_together(step, env_name):
    script = step_script(PYCI, "test", step)
    done = run_arg_parser(env_name, '--locked -m "not integration"', script)
    assert done.returncode == 0
    assert done.stdout.split() == ["[--locked]", "[-m]", "[not", "integration]"] or done.stdout == (
        "[--locked]\n[-m]\n[not integration]\n"
    )


@needs_bash
@pytest.mark.parametrize(
    ("step", "env_name"),
    [("Sync the locked environment", "SYNC_ARGS"), ("Run pytest", "PYTEST_ARGS")],
)
def test_unparseable_arguments_fail_instead_of_dropping_flags(step, env_name):
    script = step_script(PYCI, "test", step)
    done = run_arg_parser(env_name, "--locked 'unterminated", script)
    assert done.returncode != 0
    assert "[" not in done.stdout  # the tool call was never reached


@needs_bash
def test_empty_arguments_pass_no_arguments():
    script = step_script(PYCI, "test", "Sync the locked environment")
    done = run_arg_parser("SYNC_ARGS", "", script)
    assert done.returncode == 0
    assert done.stdout == "[]\n" or done.stdout == ""


def test_drift_check_runs_before_the_hooks_and_demands_a_full_sha():
    names = [s.get("name") for s in PYCI["jobs"]["lint"]["steps"]]
    assert names.index("Check ruff-base.toml against the pinned upstream copy") < names.index(
        "Run pre-commit on all files"
    )
    script = step_script(PYCI, "lint", "Check ruff-base.toml against the pinned upstream copy")
    assert "40-character commit SHA" in script


def test_pre_commit_cache_is_only_used_on_hosted_runners():
    (cache,) = [s for s in PYCI["jobs"]["lint"]["steps"] if s.get("name") == "Cache pre-commit hook environments"]
    assert cache["if"] == "runner.environment == 'github-hosted'"


CALLER = load(SCAFFOLD / ".github" / "workflows" / "ci.yml")
GATE = CALLER["jobs"]["ci-ok"]


def test_scaffold_gate_runs_on_the_same_runner_variable_as_the_work():
    assert GATE["runs-on"] == "${{ fromJSON(vars.CI_RUNS_ON || '\"ubuntu-latest\"') }}"
    assert CALLER["jobs"]["python"]["with"]["runs-on"] == "${{ vars.CI_RUNS_ON || '\"ubuntu-latest\"' }}"


def test_scaffold_runs_for_pull_requests_to_any_base():
    # A branches filter would leave a stacked pull request without the required `ci-ok` check.
    assert on_of(CALLER)["pull_request"] is None


@needs_bash
@pytest.mark.parametrize(
    ("result", "code", "message"),
    [
        ("success", 0, "All needed jobs passed."),
        ("skipped", 0, "All needed jobs passed."),
        ("failure", 1, "python(failure)"),
        ("cancelled", 1, "python(cancelled)"),
    ],
)
def test_scaffold_gate_names_the_failing_job(result, code, message):
    (s,) = [s for s in GATE["steps"] if s.get("name", "").startswith("Fail if any")]
    env = {**__import__("os").environ, "RESULTS": f"python={result}\n"}
    done = subprocess.run([BASH, "-c", s["run"]], env=env, capture_output=True, text=True)
    assert done.returncode == code
    assert message in done.stdout

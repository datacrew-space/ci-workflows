"""Guards on dispatch-ready-issues.yml and ready-when-green.yml that no real run can cheaply check.

The dispatcher starts paid agent work from a ticket change, so each assertion pins a property that keeps
it safe and portable: the agent job never holds write power and never decides what gets pushed, secrets
come from plain GitHub secrets by default with Infisical optional, nothing names a host, an account or a
runner, and an issue that ends without a PR reaches a human.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
DISPATCH = WF / "dispatch-ready-issues.yml"
READY = WF / "ready-when-green.yml"
EXAMPLES = ROOT / "examples"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def jobs() -> dict:
    return load(DISPATCH)["jobs"]


@pytest.fixture(scope="module")
def inputs() -> dict:
    return load(DISPATCH)[True]["workflow_call"]["inputs"]  # PyYAML reads the key `on` as True


def step(job: dict, step_id: str) -> dict:
    return next(s for s in job["steps"] if s.get("id") == step_id)


def step_by_name(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name") == name)


# --- Portability: nothing is hard-coded to one owner's infrastructure ---------------------------------


@pytest.mark.parametrize("path", [DISPATCH, READY])
def test_every_job_takes_its_runner_from_the_runs_on_input(path):
    for name, job in load(path)["jobs"].items():
        assert job["runs-on"] == "${{ fromJSON(inputs.runs-on) }}", name


@pytest.mark.parametrize("path", [DISPATCH, READY])
def test_runs_on_defaults_to_a_github_hosted_runner(path):
    assert load(path)[True]["workflow_call"]["inputs"]["runs-on"]["default"] == '"ubuntu-latest"'


@pytest.mark.parametrize("path", [DISPATCH, READY])
def test_github_secrets_are_the_default_and_every_secret_is_optional(path):
    call = load(path)[True]["workflow_call"]
    assert call["inputs"]["secret-source"]["default"] == "github"
    assert not [name for name, spec in call["secrets"].items() if spec.get("required")]


@pytest.mark.parametrize("path", [DISPATCH, READY])
def test_every_infisical_step_is_optional_and_fetches_one_named_secret(path):
    infisical = [
        s
        for job in load(path)["jobs"].values()
        for s in job["steps"]
        if "Infisical/secrets-action" in s.get("uses", "")
    ]
    assert infisical
    for s in infisical:
        assert "inputs.secret-source == 'infisical'" in s["if"]
        assert re.fullmatch(r"Infisical/secrets-action@[0-9a-f]{40}", s["uses"])
        assert s["with"]["domain"] == "${{ inputs.infisical-domain }}"
        assert s["with"]["secret-name"]


def test_names_and_the_owner_mention_are_inputs_with_generic_defaults(inputs):
    assert inputs["ready-label"]["default"] == "ready-for-agent"
    assert inputs["claimed-label"]["default"] == "agent-dispatched"
    assert inputs["needs-human-label"]["default"] == "needs-human"
    assert inputs["owner-handle"]["default"] == ""
    assert inputs["agent-prompt"]["default"]
    run = step_by_name(load(DISPATCH)["jobs"]["report"], "Hand back any issue that has no PR")
    assert run["env"]["OWNER"] == "${{ inputs.owner-handle || github.repository_owner }}"


def test_the_mcp_server_is_off_by_default_and_never_named_in_the_workflow(inputs):
    assert inputs["mcp-config"]["default"] == ""
    assert inputs["mcp-allowed-tools"]["default"] == ""
    text = DISPATCH.read_text(encoding="utf-8")
    assert "mcp__" not in text
    assert "https://" not in text.replace("https://github.com", "")


def test_the_mcp_token_is_never_written_into_a_file(jobs):
    prep = step(jobs["implement"], "config")
    assert "MCP_TOKEN" not in prep["run"]
    assert prep["env"]["MCP_CONFIG"] == "${{ inputs.mcp-config }}"
    assert "${{" not in prep["run"], "inputs reach the script through the environment, never spliced in"


# --- Triggers and claiming ----------------------------------------------------------------------------


def test_the_reusable_workflow_leaves_triggers_and_scheduling_to_the_caller():
    on = load(DISPATCH)[True]
    assert set(on) == {"workflow_call"}


def test_issues_are_claimed_before_any_agent_starts(jobs):
    """The claim label is what stops the next run starting the same issue twice."""
    assert jobs["implement"]["needs"] == "select"
    names = [s.get("name") for s in jobs["select"]["steps"]]
    assert names.index("Claim the issues") > names.index("Select the issues to start")
    assert 'add-label "$CLAIMED_LABEL"' in step(jobs["select"], "claim")["run"]


@pytest.mark.parametrize("job_name", ["implement", "publish"])
def test_agents_run_one_at_a_time_and_one_failure_does_not_stop_the_rest(jobs, job_name):
    strategy = jobs[job_name]["strategy"]
    assert strategy["max-parallel"] == 1
    assert strategy["fail-fast"] is False


def test_a_failed_claim_does_not_strand_an_issue(jobs):
    numbers = jobs["select"]["outputs"]["numbers"]
    assert "steps.claim.outputs.numbers" in numbers
    assert "steps.select.outputs.numbers" in numbers, "an empty selection must still reach the later jobs as []"
    run = step(jobs["select"], "claim")["run"]
    assert "if gh issue edit" in run
    assert 'claimed+=("$n")' in run


# --- The agent job holds no write power ---------------------------------------------------------------


def test_the_agent_job_uses_only_its_own_read_only_token(jobs):
    """No GitHub App is minted in the agent job: an agent that runs code can read a job's secrets."""
    implement = jobs["implement"]
    assert implement["permissions"] == {
        "contents": "read",
        "issues": "read",
        "pull-requests": "read",
        "checks": "read",
        "actions": "read",
        "statuses": "read",
    }
    assert not any(str(s.get("uses", "")).startswith("actions/create-github-app-token") for s in implement["steps"])
    assert step(implement, "implement-agent")["with"]["github_token"].startswith("${{ github.token }}")


def test_the_agent_job_never_references_the_app_credentials(jobs):
    """The key must not be reachable from the agent job in either secret mode (`secrets.X` or Infisical)."""
    text = yaml.safe_dump(jobs["implement"])
    for forbidden in ("IMPLEMENTER", "PRIVATE_KEY", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET"):
        assert forbidden not in text, forbidden


def test_the_agent_job_uses_its_own_infisical_identity(jobs):
    fetches = [s for s in jobs["implement"]["steps"] if str(s.get("uses", "")).startswith("Infisical/")]
    assert fetches
    for s in fetches:
        assert s["with"]["client-id"] == "${{ secrets.INFISICAL_AGENT_CLIENT_ID }}"
        assert s["with"]["client-secret"] == "${{ secrets.INFISICAL_AGENT_CLIENT_SECRET }}"
        assert s["with"]["secret-path"] == "${{ inputs.infisical-claude-path }}"


def test_the_publish_token_is_scoped_to_this_repository_only(jobs):
    assert step(jobs["publish"], "push-token")["with"]["repositories"] == "${{ github.event.repository.name }}"


def test_only_the_publish_job_can_write_contents(jobs):
    writers = [
        (name, s["id"])
        for name, job in jobs.items()
        for s in job["steps"]
        if s.get("with", {}).get("permission-contents") == "write"
    ]
    assert writers == [("publish", "push-token")]


def test_the_agent_job_never_mints_or_mentions_a_push_token(jobs):
    assert "push-token" not in yaml.safe_dump(jobs["implement"])


def test_the_publish_job_runs_no_agent_and_never_fetches_the_claude_token(jobs):
    publish = yaml.safe_dump(jobs["publish"])
    assert "claude-code-action" not in publish
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in publish


def test_the_publish_job_ignores_git_config_a_previous_job_left_behind(jobs):
    env = step(jobs["publish"], "push")["env"]
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_the_publish_job_permissions_are_the_default_read_only(jobs):
    assert jobs["publish"]["permissions"] == {"contents": "read"}


def test_the_agent_git_rules_are_exact_commands_and_the_git_dir_is_off_limits(jobs):
    """A bare `git fetch:*` admits --upload-pack=CMD and `git diff:*` admits --output=FILE."""
    run = step(jobs["implement"], "config")["run"]
    tools = next(line for line in run.splitlines() if line.lstrip().startswith("tools="))
    for bare in ("git fetch", "git diff:*", "git log:*", "git show", "git add:*", "git commit:*"):
        assert bare not in tools, bare
    assert "Bash(git add -- *)" in tools
    claude_args = step(jobs["implement"], "implement-agent")["with"]["claude_args"]
    assert "--disallowedTools" in claude_args
    assert "Edit(/${{ github.workspace }}/.git/**)" in claude_args
    assert "Write(/${{ github.workspace }}/.git/**)" in claude_args


def test_the_agent_may_not_push_open_a_pr_or_use_the_web_by_default(jobs):
    run = step(jobs["implement"], "config")["run"]
    for forbidden in ("git push", "gh pr create", "gh issue comment", "WebFetch", "WebSearch"):
        assert forbidden not in run
    assert "--strict-mcp-config" in run
    assert "--permission-mode default" in step(jobs["implement"], "implement-agent")["with"]["claude_args"]


# --- The publish job decides what may be pushed -------------------------------------------------------


def test_the_artifact_the_agent_uploads_is_the_one_publish_downloads(jobs):
    upload = step_by_name(jobs["implement"], "Hand the output to the publish job")["with"]["name"]
    download = step(jobs["publish"], "download")["with"]["name"]
    assert upload == download == "agent-${{ matrix.issue }}"


def test_publish_never_trusts_the_agent_job_for_base_or_branch(jobs):
    run = step(jobs["publish"], "push")["run"]
    for forbidden in ("BASE_SHA", "GITHUB_ENV", "needs.implement"):
        assert forbidden not in run
    assert not any("GITHUB_ENV" in s.get("run", "") for s in jobs["publish"]["steps"])
    assert "refs/heads/${BASE_BRANCH}:refs/heads/base" in run, "the base must come from GitHub, fetched here"
    assert "refs/heads/base...refs/heads/work" in run, "judge the agent's own commits against that base"


def test_the_path_judgement_is_the_tested_script_fed_nul_separated_paths(jobs):
    run = step(jobs["publish"], "push")["run"]
    assert "diff -z --no-renames --name-only refs/heads/base...refs/heads/work" in run
    assert "bash ci-tooling/scripts/implementer-protected-paths.sh" in run
    assert "grep" not in run, "a pattern list inside the workflow is the thing that goes wrong"


def test_the_path_gate_fails_closed_when_the_diff_cannot_run(jobs):
    run = step(jobs["publish"], "push")["run"]
    merge_base = 'git -C "$clean" merge-base refs/heads/base refs/heads/work'
    capture = 'git -C "$clean" diff -z --no-renames --name-only refs/heads/base...refs/heads/work > "$changed" || {'
    judge = 'if ! bash ci-tooling/scripts/implementer-protected-paths.sh < "$changed"; then'
    assert merge_base in run
    assert capture in run
    assert not re.search(r"diff[^\n|]*\|(?!\|)", run), "the diff must not feed anything through a pipe"
    assert judge in run
    assert (
        run.index(merge_base) < run.index(capture) < run.index(judge) < run.index('git -C "$clean" -c core.hooksPath')
    )


def test_the_agent_prompt_is_protected_automatically(jobs):
    env = step(jobs["publish"], "push")["env"]
    assert env["EXTRA_PROTECTED_PATHS"] == "${{ inputs.agent-prompt }} ${{ inputs.extra-protected-paths }}"


@pytest.mark.parametrize("job_name", ["select", "publish"])
def test_the_decision_scripts_come_from_the_pinned_tooling_repository(jobs, job_name):
    checkout = jobs[job_name]["steps"][0]
    assert checkout["with"]["repository"] == "datacrew-space/ci-workflows"
    assert checkout["with"]["ref"] == "${{ inputs.tooling-ref }}"
    assert checkout["with"]["persist-credentials"] is False


def test_the_agents_pr_text_goes_through_the_sanitiser_script(jobs):
    run = step(jobs["publish"], "push")["run"]
    assert 'bash ci-tooling/scripts/sanitize-pr-text.sh < "$RUNNER_TEMP/agent/pr-body.md" > "$body"' in run
    assert 'cat "$RUNNER_TEMP/agent/pr-body.md"' not in run


def test_the_pr_links_the_issue_and_is_not_a_draft(jobs):
    run = step(jobs["publish"], "push")["run"]
    assert "Closes #%s" in run
    assert "--draft" not in run, "a draft PR is skipped by the review workflow"


def test_the_branch_name_is_built_from_trusted_values_only(jobs):
    push = step(jobs["publish"], "push")
    assert push["env"]["ISSUE"] == "${{ matrix.issue }}"
    assert 'branch="agent/issue-${ISSUE}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in push["run"]


# --- Every dispatched issue ends with a PR or a human -------------------------------------------------


def test_report_always_runs_and_hands_a_pr_less_issue_to_a_human(jobs):
    report = jobs["report"]
    assert report["needs"] == ["select", "implement", "publish"]
    assert " ".join(str(report["if"]).split()).startswith("${{ always()")
    run = report["steps"][-1]["run"]
    assert '--remove-label "$CLAIMED_LABEL" --add-label "$NEEDS_HUMAN_LABEL"' in run, "a retry needs the claim released"
    assert 'startswith(\\"agent/issue-${n}-${GITHUB_RUN_ID}-\\")' in run


# --- ready-when-green ---------------------------------------------------------------------------------


def test_ready_when_green_never_checks_out_pull_request_code():
    jobs = load(READY)["jobs"]
    checkouts = [s for s in jobs["sweep"]["steps"] if "actions/checkout" in s.get("uses", "")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["repository"] == "datacrew-space/ci-workflows"
    assert checkouts[0]["with"]["ref"] == "${{ inputs.tooling-ref }}"


def test_ready_when_green_takes_its_names_from_inputs_through_the_environment():
    sweep = load(READY)["jobs"]["sweep"]
    run_step = step_by_name(sweep, "Mark labelled green drafts ready")
    assert run_step["env"]["READY_LABEL"] == "${{ inputs.ready-label }}"
    assert run_step["env"]["CI_CHECK"] == "${{ inputs.ci-check }}"
    assert "${{" not in run_step["run"]


# --- The caller examples are the contract the reusable workflows document -----------------------------


@pytest.mark.parametrize(
    ("example", "target"),
    [("dispatch-caller.yml", DISPATCH), ("ready-when-green-caller.yml", READY)],
)
def test_the_example_caller_passes_only_inputs_and_secrets_the_workflow_declares(example, target):
    declared = load(target)[True]["workflow_call"]
    caller_jobs = load(EXAMPLES / example)["jobs"]
    for job in caller_jobs.values():
        assert set(job.get("with", {})) <= set(declared["inputs"])
        assert set(job.get("secrets", {})) <= set(declared["secrets"])


def test_no_workflow_level_env_can_hand_a_secret_to_every_job():
    """A top-level `env:` reaches the agent job too, and the per-job scans would not see it."""
    for path in (DISPATCH, READY):
        assert "env" not in load(path), path.name


def test_the_agent_cannot_edit_the_project_settings_that_hold_its_permissions(jobs):
    claude_args = step(jobs["implement"], "implement-agent")["with"]["claude_args"]
    assert "Edit(/${{ github.workspace }}/.claude/**)" in claude_args
    assert "Write(/${{ github.workspace }}/.claude/**)" in claude_args


def test_the_config_step_rejects_a_newline_that_could_forge_an_output_line(jobs):
    assert "may not contain a newline" in step(jobs["implement"], "config")["run"]


# --- The report job, run for real with a stub `gh` ---------------------------------------------------

GH_STUB = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr list")
    if [ "${FAIL_LIST_ONCE:-}" = 1 ] && [ ! -e "$GH_STATE/listed" ]; then touch "$GH_STATE/listed"; exit 1; fi
    echo 0 ;;
  "issue edit")
    if [ "$3" = "${FAIL_EDIT:-none}" ]; then exit 1; fi ;;
esac
exit 0
"""


def run_report(tmp_path, **extra_env):
    import os
    import subprocess

    script = step_by_name(load(DISPATCH)["jobs"]["report"], "Hand back any issue that has no PR")["run"]
    (tmp_path / "gh").write_text(GH_STUB, encoding="utf-8", newline="\n")
    (tmp_path / "gh").chmod(0o755)
    (tmp_path / "report.sh").write_text(script, encoding="utf-8", newline="\n")
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_STATE": str(tmp_path),
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "OWNER": "owner",
        "MENTION": "true",
        "CLAIMED_LABEL": "agent-dispatched",
        "NEEDS_HUMAN_LABEL": "needs-human",
        "NUMBERS": "[5,6]",
        "RUN_URL": "https://example.invalid/run",
        "GITHUB_RUN_ID": "1",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        **extra_env,
    }
    done = subprocess.run(["bash", "-e", str(tmp_path / "report.sh")], capture_output=True, env=env, check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, calls


def handed_over(calls):
    return [c.split()[2] for c in calls if c.startswith("issue edit")]


def test_report_hands_every_pr_less_issue_to_a_human(tmp_path):
    code, calls = run_report(tmp_path)
    assert code == 0
    assert handed_over(calls) == ["5", "6"]


def test_report_still_hands_over_the_rest_when_one_pr_lookup_fails(tmp_path):
    """Under `bash -e` a failed lookup used to end the step and leave every later issue claimed forever."""
    code, calls = run_report(tmp_path, FAIL_LIST_ONCE="1")
    assert code == 1  # the failure is visible
    assert handed_over(calls) == ["5", "6"]


def test_report_still_hands_over_the_rest_when_one_relabel_fails(tmp_path):
    code, calls = run_report(tmp_path, FAIL_EDIT="5")
    assert code == 1
    assert handed_over(calls) == ["5", "6"]

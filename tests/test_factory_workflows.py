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


def test_the_pr_links_the_issue(jobs):
    assert "Closes #%s" in step(jobs["publish"], "push")["run"]


def test_agent_prs_open_as_drafts_unless_the_caller_opts_out(jobs, inputs):
    """Where automation merges any non-draft PR on green CI, a ready agent PR could merge unreviewed."""
    assert inputs["draft"]["default"] is True
    push = step(jobs["publish"], "push")
    assert push["env"]["DRAFT"] == "${{ inputs.draft }}"
    assert '"${draft_flag[@]}"' in push["run"]
    assert '[ "$DRAFT" = "true" ] && draft_flag=(--draft)' in push["run"]


def test_the_finished_label_is_an_input_that_matches_ready_when_green(inputs):
    """ready-when-green flips only a draft that carries its `ready-label`; the two defaults must agree."""
    ready_default = load(READY)[True]["workflow_call"]["inputs"]["ready-label"]["default"]
    assert inputs["ready-for-review-label"]["default"] == ready_default == "ready-for-review"


def test_the_dispatcher_labels_the_draft_it_opened(jobs):
    """The agent has finished before publish opens the PR, so nobody else is left to add the label."""
    steps = jobs["publish"]["steps"]
    names = [s.get("id") for s in steps]
    assert names.index("label") > names.index("push")
    push_run = step(jobs["publish"], "push")["run"]
    assert 'echo "pr-url=$pr_url" >> "$GITHUB_OUTPUT"' in push_run
    # A pipe after `gh pr create` would hide its failure (the step runs without pipefail), and only the
    # last line it printed may reach $GITHUB_OUTPUT.
    assert '"${draft_flag[@]}")\n' in push_run
    assert "pr_url=\"${pr_url##*$'\\n'}\"" in push_run
    # A create that printed no URL would skip the label step and leave a draft nothing picks up.
    assert 'if [ -z "$pr_url" ]; then' in push_run
    label = step(jobs["publish"], "label")
    # Only a draft, only when a PR was opened, and an empty label turns the step off.
    assert label["if"] == (
        "${{ steps.push.outputs.pr-url != '' && inputs.draft && inputs.ready-for-review-label != '' }}"
    )
    # The app token, so the `labeled` event starts ready-when-green (GITHUB_TOKEN events start nothing).
    assert label["env"]["GH_TOKEN"] == "${{ steps.push-token.outputs.token }}"
    assert label["env"]["READY_FOR_REVIEW_LABEL"] == "${{ inputs.ready-for-review-label }}"
    assert "${{" not in label["run"]  # the label name and the PR URL reach the shell only through env


def test_the_finished_label_is_created_where_the_token_may_create_it(jobs):
    """The publish token has no issues: write; the select job's default token does, and runs no agent."""
    claim = step(jobs["select"], "claim")
    assert claim["env"]["READY_FOR_REVIEW_LABEL"] == "${{ inputs.ready-for-review-label }}"
    assert 'gh label create "$READY_FOR_REVIEW_LABEL"' in claim["run"]
    assert '[ -n "$READY_FOR_REVIEW_LABEL" ]' in claim["run"]


def test_the_claim_is_serialised_inside_the_reusable_workflow(jobs):
    """The guard must not live only in an example caller: two overlapping runs would claim one issue twice."""
    concurrency = jobs["select"]["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "${{ github.repository }}" in concurrency["group"]
    # a called workflow that reuses its caller's group is cancelled by it
    caller = yaml.safe_load((EXAMPLES / "dispatch-caller.yml").read_text(encoding="utf-8"))
    assert concurrency["group"] != caller["concurrency"]["group"]


def test_no_workflow_gives_the_agent_a_wildcard_git_rule():
    """`git fetch:*` admits --upload-pack=CMD and `git diff:*` admits --output=FILE."""
    wildcard = re.compile(r"Bash\(git [a-z-]+:\*\)")
    for path in sorted(WF.glob("*.yml")):
        assert not wildcard.search(path.read_text(encoding="utf-8")), path.name


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


# --- The label step of the publish job, run for real with a stub `gh` ----------------------------------

LABEL_GH_STUB = """#!/bin/sh
echo "$*" >> "$GH_LOG"
[ "${FAIL_LABEL:-}" = 1 ] && exit 1
exit 0
"""


def run_label(tmp_path, pr_url, **extra_env):
    import os
    import subprocess

    script = step(load(DISPATCH)["jobs"]["publish"], "label")["run"]
    (tmp_path / "gh").write_text(LABEL_GH_STUB, encoding="utf-8", newline="\n")
    (tmp_path / "gh").chmod(0o755)
    (tmp_path / "label.sh").write_text(script, encoding="utf-8", newline="\n")
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "PR_URL": pr_url,
        "READY_FOR_REVIEW_LABEL": "ready-for-review",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        **extra_env,
    }
    done = subprocess.run(["bash", "-e", str(tmp_path / "label.sh")], capture_output=True, env=env, check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, calls, done.stdout.decode()


def test_the_label_step_labels_the_pr_the_push_step_opened(tmp_path):
    code, calls, _ = run_label(tmp_path, "https://github.com/o/r/pull/12")
    assert code == 0
    assert calls == ["api --method POST repos/o/r/issues/12/labels -f labels[]=ready-for-review"]


def test_a_failed_labelling_is_visible_and_says_what_to_do(tmp_path):
    """The PR is already open; a silent miss would leave it a draft that nothing ever picks up."""
    code, calls, out = run_label(tmp_path, "https://github.com/o/r/pull/12", FAIL_LABEL="1")
    assert code == 1
    assert len(calls) == 1
    assert "::error::" in out
    assert "ready-for-review" in out
    assert "do not re-run" in out  # a re-run pushes a new branch and opens a second PR


@pytest.mark.parametrize(
    "pr_url", ["", "https://github.com/o/r/pull/", "https://github.com/o/r/pull/12/files", "12; id"]
)
def test_the_label_step_refuses_anything_that_is_not_a_pr_number(tmp_path, pr_url):
    code, calls, out = run_label(tmp_path, pr_url)
    assert code == 1
    assert calls == []
    assert "::error::" in out


# --- The allowed-bots validation in the review workflow, run for real ---------------------------------


def run_allowed_bots_check(value: str) -> int:
    import os
    import subprocess

    review = load(WF / "claude-code-review.yml")["jobs"]["claude-review"]
    script = step_by_name(review, "Validate the allowed-bots input")["run"]
    return subprocess.run(
        ["bash", "-e", "-c", script], env={**os.environ, "ALLOWED_BOTS": value}, capture_output=True, check=False
    ).returncode


@pytest.mark.parametrize("value", ["", "my-app", "app-one,app-two"])
def test_named_bot_logins_are_accepted(value):
    assert run_allowed_bots_check(value) == 0


@pytest.mark.parametrize("value", ["*", "app,*", "my app", "a,,b", "app,", "a]b", "x; echo hi"])
def test_a_wildcard_or_malformed_allowed_bots_is_refused(value):
    assert run_allowed_bots_check(value) == 1


def test_the_issue_title_goes_through_the_sanitiser_before_it_becomes_the_pr_title(jobs):
    """A title like `Fixes #99` would close #99 from the squash commit subject."""
    run = step(jobs["publish"], "push")["run"]
    assert "bash ci-tooling/scripts/sanitize-pr-text.sh)" in run
    assert 'title="Work on issue #${ISSUE}"' in run


# --- The agent's job can install uv: a runner image need not carry it ---------------------------------


def test_uv_is_installed_before_the_setup_command_and_only_when_asked(jobs, inputs):
    """A `setup-command` of `uv sync --frozen` ended in `uv: command not found` on a runner without uv."""
    assert inputs["uv-version"]["default"] == ""
    assert inputs["python-version"]["default"] == ""
    names = [s.get("name") for s in jobs["implement"]["steps"]]
    assert names.index("Install uv") < names.index("Set up the project") < names.index("Run the implementer agent")
    install = step_by_name(jobs["implement"], "Install uv")
    assert install["if"] == "${{ inputs.uv-version != '' }}"
    assert install["with"]["version"] == "${{ inputs.uv-version }}"
    assert install["with"]["python-version"] == "${{ inputs.python-version }}"


def test_the_agent_job_never_saves_a_uv_cache(jobs):
    """The action saves its cache in a post step, after the agent ran code of its own in this job."""
    install = step_by_name(jobs["implement"], "Install uv")
    assert install["with"]["enable-cache"] is False


def test_uv_comes_from_the_same_pinned_action_as_the_python_ci(jobs):
    install = step_by_name(jobs["implement"], "Install uv")
    assert re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", install["uses"])
    ci_uses = {
        s["uses"]
        for job in load(WF / "python-ci.yml")["jobs"].values()
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")
    }
    assert ci_uses == {install["uses"]}


def test_only_the_agent_job_installs_uv(jobs):
    """The publish job holds the push token and must run nothing the caller chose."""
    for name, job in jobs.items():
        if name != "implement":
            assert all("setup-uv" not in str(s.get("uses", "")) for s in job["steps"]), name


# --- The prompt names the only commit commands the tool list admits -----------------------------------


def _committing_agents() -> list[tuple[str, str, str]]:
    """(workflow file, job, step id) for every agent step that may commit only through the narrow git rules."""
    found = []
    for path in sorted(WF.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "Bash(git add -- *)" not in text:
            continue
        for job_name, job in load(path)["jobs"].items():
            for s in job.get("steps", []):
                if "claude-code-action" in str(s.get("uses", "")) and "git commit" in s["with"].get("prompt", ""):
                    found.append((path.name, job_name, s["id"]))
    return found


def test_the_agents_that_commit_are_the_implementer_and_the_responder():
    assert _committing_agents() == [
        ("claude-code-review.yml", "respond", "respond-agent"),
        ("dispatch-ready-issues.yml", "implement", "implement-agent"),
    ]


@pytest.mark.parametrize(("workflow", "job_name", "step_id"), _committing_agents())
def test_the_prompt_names_the_exact_commit_commands_the_tool_list_admits(workflow, job_name, step_id):
    """Told only "git add / git commit", an agent ran `git add <paths>`, was refused and committed nothing.

    The tool list admits `git add -- <paths>` and `git commit -m <message>` and nothing else, so two
    finished tickets (47 and 43 passing tests) were thrown away with the runner.
    """
    text = (WF / workflow).read_text(encoding="utf-8")
    assert "Bash(git add -- *),Bash(git commit -m *)" in text  # the two rules the prompt describes
    prompt = step(load(WF / workflow)["jobs"][job_name], step_id)["with"]["prompt"]
    flat = " ".join(prompt.split())
    assert "git add / git commit" not in flat
    assert "`git add -- <paths>`" in flat
    assert "other forms are refused" in flat
    # Single quotes: a backtick or `$` inside a double-quoted message is refused as a substitution,
    # and agents write backticks in commit bodies all the time.
    assert "`git commit -m '<subject>' -m '<body>'`" in flat
    assert "single quotes with no single quote inside" in flat
    for trap in ("`cd`", "heredoc", "`$(...)`"):
        assert trap in flat, trap

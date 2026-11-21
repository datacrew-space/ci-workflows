"""Guards on letta-code.yml: it stays off unless a repository opts in, and only write access starts the agent.

The job holds write access and an API key, and its triggers include comments anyone can write. The
decision step is run for real under bash, with a stand-in for the `gh` CLI; the rest pins the shape of
the workflow so a change that widens what the agent job may do is a visible edit to this file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "letta-code.yml"
CALLER = ROOT / "examples" / "letta-code-caller.yml"
ACTION = "letta-ai/letta-code-action@b50e45b91c77d5a7b8a64f4088bedcd8c442bd13"
WAITS = "${{ steps.act.outputs.act == 'true' }}"
PHRASE = "inputs.trigger-phrase"

# Everything the decision step is given. Titles, bodies and label names are absent on purpose: the
# step gets true/false answers, the actor's login and a pull request number.
DECISION_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "REPO": "${{ github.repository }}",
    "ACTOR": "${{ github.actor }}",
    "HAS_KEY": "${{ secrets.LETTA_API_KEY != '' }}",
    "HAS_AGENT_ID": "${{ inputs.agent-id != '' }}",
    "HAS_PHRASE_INPUT": "${{ inputs.trigger-phrase != '' }}",
    "EVENT_NAME": "${{ github.event_name }}",
    "EVENT_ACTION": "${{ github.event.action }}",
    "PR_NUMBER": (
        "${{ github.event.pull_request.number"
        " || (github.event.issue.pull_request && github.event.issue.number) || '' }}"
    ),
    "COMMENT_HAS_PHRASE": f"${{{{ contains(github.event.comment.body, {PHRASE}) }}}}",
    "REVIEW_HAS_PHRASE": f"${{{{ contains(github.event.review.body, {PHRASE}) }}}}",
    "ISSUE_HAS_PHRASE": (
        f"${{{{ contains(github.event.issue.title, {PHRASE}) || contains(github.event.issue.body, {PHRASE}) }}}}"
    ),
    "LABEL_MATCHES": "${{ github.event.label.name == inputs.label-trigger }}",
}


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc[True]  # PyYAML reads the key `on` as True


@pytest.fixture(scope="module")
def job() -> dict:
    return load(WORKFLOW)["jobs"]["letta"]


# --- The decision, run for real -----------------------------------------------------------------------

# Stands in for `gh api <path> --jq <filter>`: logs the call, then answers from the environment.
GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$2" in
  users/*)
    if [ -n "${STUB_WARN:-}" ]; then echo "warning: $STUB_WARN" >&2; fi
    if [ "${STUB_FAIL:-}" = "user" ]; then echo "gh: Server Error (HTTP 502)" >&2; exit 1; fi
    if [ "${STUB_FAIL:-}" = "user404" ]; then
      # what the real gh prints: the body on stdout, then the status on stderr
      printf '%s' '{"message":"Not Found","status":"404"}'
      echo "gh: Not Found (HTTP 404)" >&2
      exit 1
    fi
    echo "$STUB_TYPE" ;;
  repos/*/collaborators/*/permission)
    if [ "${STUB_FAIL:-}" = "permission" ]; then exit 1; fi
    echo "$STUB_PERMISSION" ;;
  repos/*/pulls/*)
    if [ "${STUB_FAIL:-}" = "pull" ]; then exit 1; fi
    echo "$STUB_HEAD_REPO" ;;
  *)
    exit 1 ;;
esac
"""

# Someone with write access comments on an issue, with the phrase, and the key and agent id are set.
SUMMONED = {
    "GH_TOKEN": "x",
    "REPO": "o/r",
    "ACTOR": "alice",
    "HAS_KEY": "true",
    "HAS_AGENT_ID": "true",
    "HAS_PHRASE_INPUT": "true",
    "EVENT_NAME": "issue_comment",
    "EVENT_ACTION": "created",
    "PR_NUMBER": "",
    "COMMENT_HAS_PHRASE": "true",
    "REVIEW_HAS_PHRASE": "false",
    "ISSUE_HAS_PHRASE": "false",
    "LABEL_MATCHES": "false",
    "STUB_TYPE": "User",
    "STUB_PERMISSION": "write",
    "STUB_HEAD_REPO": "o/r",
    "STUB_FAIL": "",
    "STUB_WARN": "",
}

USER_CALL = "api users/alice --jq .type"
PERMISSION_CALL = "api repos/o/r/collaborators/alice/permission --jq .permission"
PULL_CALL = "api repos/o/r/pulls/7 --jq .head.repo.full_name"


def run_step(
    job: dict, tmp_path: Path, stderr_full: bool = False, **changes: str
) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    """Run the step. Returns the finished process, the lines it wrote to GITHUB_OUTPUT and the `gh` calls it made."""
    step = job["steps"][0]
    assert set(step["env"]) == {k for k in SUMMONED if not k.startswith("STUB_")}, "set every variable the step reads"
    (tmp_path / "gh").write_text(GH_STUB, encoding="utf-8", newline="\n")
    (tmp_path / "gh").chmod(0o755)
    script = tmp_path / "decide.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    log = tmp_path / "gh.log"
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        **SUMMONED,
        **changes,
        "GH_LOG": str(log),
        "GITHUB_OUTPUT": out.as_posix(),
        "TMPDIR": (tmp_path / "tmp").as_posix(),
    }
    # The flags GitHub's runner uses for `shell: bash`.
    command = ["bash", "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()]
    if stderr_full:
        # every write to the step's stderr fails
        command = ["bash", "-c", 'exec 2>/dev/full; exec "$@"', "bash", *command]
    done = subprocess.run(command, capture_output=True, env=env, check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, out.read_text(encoding="utf-8").split(), calls


def run_decision(job: dict, tmp_path: Path, **changes: str) -> tuple[int, list[str], list[str]]:
    """Run the step. Returns its exit code, the lines it wrote to GITHUB_OUTPUT and the `gh` calls it made."""
    done, lines, calls = run_step(job, tmp_path, **changes)
    return done.returncode, lines, calls


def decide(job: dict, tmp_path: Path, **changes: str) -> str:
    code, lines, _ = run_decision(job, tmp_path, **changes)
    assert code == 0
    assert len(lines) == 1, lines
    return lines[0]


NO_COMMENT = {"COMMENT_HAS_PHRASE": "false"}
ON_PULL_REQUEST = {"PR_NUMBER": "7"}
REVIEW_COMMENT = {"EVENT_NAME": "pull_request_review_comment", **ON_PULL_REQUEST}
REVIEW = {
    "EVENT_NAME": "pull_request_review",
    "EVENT_ACTION": "submitted",
    "REVIEW_HAS_PHRASE": "true",
    **NO_COMMENT,
    **ON_PULL_REQUEST,
}
ISSUE_OPENED = {"EVENT_NAME": "issues", "EVENT_ACTION": "opened", "ISSUE_HAS_PHRASE": "true", **NO_COMMENT}
ISSUE_LABELED = {"EVENT_NAME": "issues", "EVENT_ACTION": "labeled", "LABEL_MATCHES": "true", **NO_COMMENT}
FORK = {"STUB_HEAD_REPO": "someone/r"}

ALLOW = {
    "a comment with the phrase from someone with write access": {},
    "the same from an admin": {"STUB_PERMISSION": "admin"},
    "a comment on a pull request from this repository": ON_PULL_REQUEST,
    "a pull request review comment": REVIEW_COMMENT,
    "a pull request review whose summary has the phrase": REVIEW,
    "an issue opened with the phrase": ISSUE_OPENED,
    "the trigger label is added to an issue": ISSUE_LABELED,
    "the trigger label still works when the phrase is switched off": {**ISSUE_LABELED, "HAS_PHRASE_INPUT": "false"},
    "gh prints a warning on stderr while answering": {**ON_PULL_REQUEST, "STUB_WARN": "something changed"},
}

DENY = {
    # the request cannot be answered
    "no API key": {"HAS_KEY": "false"},
    "no agent id": {"HAS_AGENT_ID": "false"},
    # who asks
    "read access only (the triage role reads as this too)": {"STUB_PERMISSION": "read"},
    "no access": {"STUB_PERMISSION": "none"},
    "an empty permission answer": {"STUB_PERMISSION": ""},
    # Not policy: the API never answers `maintain` (it reports the maintain role as `write`, so
    # maintainers are allowed). This only checks that an unexpected answer is refused.
    "an answer the API does not give": {"STUB_PERMISSION": "maintain"},
    "a bot comments with the phrase": {"ACTOR": "some-app[bot]"},
    "a bot adds the trigger label": {**ISSUE_LABELED, "ACTOR": "some-app[bot]"},
    "an account that is not a user and has no [bot] suffix": {"STUB_TYPE": "Bot"},
    "an organization account": {"STUB_TYPE": "Organization"},
    "a login the users endpoint cannot find": {"STUB_FAIL": "user404"},
    "someone with read access adds the trigger label": {**ISSUE_LABELED, "STUB_PERMISSION": "read"},
    "someone with read access opens an issue with the phrase": {**ISSUE_OPENED, "STUB_PERMISSION": "read"},
    # pull requests from forks. Only a comment gets the secret; GitHub withholds it from a review or
    # review comment on a fork's pull request, so those stop at the missing key.
    "a comment on a pull request from a fork": {**ON_PULL_REQUEST, **FORK},
    "a review comment on a pull request from a fork": {**REVIEW_COMMENT, **FORK, "HAS_KEY": "false"},
    "a review of a pull request from a fork": {**REVIEW, **FORK, "HAS_KEY": "false"},
    "a review comment from a fork, should the secret ever be given": {**REVIEW_COMMENT, **FORK},
    "a review from a fork, should the secret ever be given": {**REVIEW, **FORK},
    "a pull request whose head repository is gone": {**ON_PULL_REQUEST, "STUB_HEAD_REPO": ""},
    # the event does not ask
    "a comment without the phrase": NO_COMMENT,
    "an edited comment": {"EVENT_ACTION": "edited"},
    "a deleted comment": {"EVENT_ACTION": "deleted"},
    "an edited review comment": {**REVIEW_COMMENT, "EVENT_ACTION": "edited"},
    "a review without the phrase": {**REVIEW, "REVIEW_HAS_PHRASE": "false"},
    "an edited review": {**REVIEW, "EVENT_ACTION": "edited"},
    "a dismissed review": {**REVIEW, "EVENT_ACTION": "dismissed"},
    "the phrase only in the review field of a comment event": {**NO_COMMENT, "REVIEW_HAS_PHRASE": "true"},
    "the phrase only in the comment field of a review event": {
        **REVIEW,
        "REVIEW_HAS_PHRASE": "false",
        "COMMENT_HAS_PHRASE": "true",
    },
    "an issue opened without the phrase": {**ISSUE_OPENED, "ISSUE_HAS_PHRASE": "false"},
    "the phrase only in the comment field of an issue event": {
        **ISSUE_OPENED,
        "ISSUE_HAS_PHRASE": "false",
        "COMMENT_HAS_PHRASE": "true",
    },
    "an edited issue with the phrase": {**ISSUE_OPENED, "EVENT_ACTION": "edited"},
    "an assigned issue with the phrase": {**ISSUE_OPENED, "EVENT_ACTION": "assigned"},
    "a reopened issue with the phrase": {**ISSUE_OPENED, "EVENT_ACTION": "reopened"},
    "another label is added to an issue that has the phrase": {
        **ISSUE_LABELED,
        "LABEL_MATCHES": "false",
        "ISSUE_HAS_PHRASE": "true",
    },
    "the trigger label is removed": {**ISSUE_LABELED, "EVENT_ACTION": "unlabeled"},
    "an empty trigger phrase, which every comment contains": {"HAS_PHRASE_INPUT": "false"},
    "an empty trigger phrase on a new issue": {**ISSUE_OPENED, "HAS_PHRASE_INPUT": "false"},
    # events the action has no trigger for
    "a pull request is opened": {"EVENT_NAME": "pull_request", "EVENT_ACTION": "opened", **ON_PULL_REQUEST},
    "the trigger label on a pull_request event": {
        "EVENT_NAME": "pull_request",
        "EVENT_ACTION": "labeled",
        "LABEL_MATCHES": "true",
        **ON_PULL_REQUEST,
    },
    "a push to a pull request": {"EVENT_NAME": "pull_request", "EVENT_ACTION": "synchronize", **ON_PULL_REQUEST},
    "a pull_request_target event": {"EVENT_NAME": "pull_request_target", "EVENT_ACTION": "opened", **ON_PULL_REQUEST},
    "a push": {"EVENT_NAME": "push", "EVENT_ACTION": ""},
}

# Refused before anyone's permission is looked up.
NO_LOOKUP = [
    "no API key",
    "no agent id",
    "a bot comments with the phrase",
    "a bot adds the trigger label",
    "a comment without the phrase",
    "an edited comment",
    "an issue opened without the phrase",
    "an empty trigger phrase, which every comment contains",
    "a pull request is opened",
    "a push",
    "a review of a pull request from a fork",
    "a review comment on a pull request from a fork",
]


@pytest.mark.parametrize("case", sorted(ALLOW))
def test_a_request_from_someone_with_write_access_starts_the_agent(job, tmp_path, case):
    assert decide(job, tmp_path, **ALLOW[case]) == "act=true"


@pytest.mark.parametrize("case", sorted(DENY))
def test_anything_else_only_reports_the_check(job, tmp_path, case):
    assert decide(job, tmp_path, **DENY[case]) == "act=false"


# Each refusal after a request names its own reason in the log.
REASONS = {
    "no API key": "LETTA_API_KEY or agent-id is missing",
    "a bot comments with the phrase": "some-app[bot] is a bot",
    "an account that is not a user and has no [bot] suffix": "alice is not a user account",
    "a login the users endpoint cannot find": "GitHub has no user account named alice",
    "read access only (the triage role reads as this too)": "alice does not have write access to o/r",
    "a comment on a pull request from a fork": "pull request 7 comes from a fork",
}


@pytest.mark.parametrize("case", sorted(REASONS))
def test_a_refused_request_says_why(job, tmp_path, case):
    done, lines, _ = run_step(job, tmp_path, **DENY[case])
    assert lines == ["act=false"]
    expected = f"Not calling the agent: {REASONS[case]}. Reporting the check only."
    assert expected in done.stdout.decode("utf-8").splitlines()


@pytest.mark.parametrize(
    "changes",
    [{}, {"STUB_WARN": "something changed"}, {"STUB_FAIL": "user404"}, {"STUB_FAIL": "user"}],
    ids=["a user", "a warning", "a 404", "a failed lookup"],
)
def test_the_lookup_leaves_no_temp_file_behind(job, tmp_path, changes):
    run_step(job, tmp_path, **changes)
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.parametrize("changes", [{"STUB_WARN": "something changed"}, {"STUB_FAIL": "user"}])
def test_the_temp_file_is_removed_when_copying_it_to_the_log_fails(job, tmp_path, changes):
    done, lines, _ = run_step(job, tmp_path, stderr_full=True, **changes)
    assert done.returncode != 0
    assert lines == [], "no decision is written, so the agent steps cannot run"
    assert list((tmp_path / "tmp").iterdir()) == []


def test_a_warning_from_a_successful_lookup_reaches_the_log(job, tmp_path):
    done, lines, _ = run_step(job, tmp_path, STUB_WARN="something changed")
    assert lines == ["act=true"]
    assert "warning: something changed" in done.stderr.decode("utf-8")


@pytest.mark.parametrize("case", NO_LOOKUP)
def test_the_api_is_called_only_for_a_request_that_could_be_answered(job, tmp_path, case):
    _, _, calls = run_decision(job, tmp_path, **DENY[case])
    assert calls == []


def test_the_actor_is_looked_up_on_this_repository(job, tmp_path):
    _, _, calls = run_decision(job, tmp_path)
    assert calls == [USER_CALL, PERMISSION_CALL]


def test_a_request_on_a_pull_request_also_looks_up_where_its_branch_lives(job, tmp_path):
    _, _, calls = run_decision(job, tmp_path, **ON_PULL_REQUEST)
    assert calls == [USER_CALL, PERMISSION_CALL, PULL_CALL]


def test_a_pull_request_is_not_looked_up_for_someone_without_write_access(job, tmp_path):
    _, _, calls = run_decision(job, tmp_path, **ON_PULL_REQUEST, STUB_PERMISSION="read")
    assert calls == [USER_CALL, PERMISSION_CALL]


@pytest.mark.parametrize("account", [{"STUB_TYPE": "Bot"}, {"STUB_FAIL": "user404"}])
def test_permission_is_not_looked_up_for_an_account_that_is_not_a_user(job, tmp_path, account):
    _, _, calls = run_decision(job, tmp_path, **account)
    assert calls == [USER_CALL]


@pytest.mark.parametrize("failing", ["user", "permission", "pull"])
def test_a_failed_lookup_fails_the_step_instead_of_dropping_the_request(job, tmp_path, failing):
    code, lines, _ = run_decision(job, tmp_path, **ON_PULL_REQUEST, STUB_FAIL=failing)
    assert code != 0
    assert lines == [], "no decision is written, so the agent steps cannot run"


def test_the_decision_step_gets_answers_not_event_text_and_not_the_key(job):
    assert job["steps"][0]["env"] == DECISION_ENV


# --- The shape of the workflow ------------------------------------------------------------------------


def test_the_workflow_has_one_job_and_no_shared_environment(job):
    """A second job, or an `env` above the steps, could hold the key or event text outside the pinned steps."""
    doc = load(WORKFLOW)
    assert set(doc["jobs"]) == {"letta"}
    assert set(job) == {"runs-on", "timeout-minutes", "permissions", "steps"}
    assert "env" not in doc


def test_the_job_is_exactly_decide_checkout_agent(job):
    """A step added here runs with write access and, after the decision, the key: adding one is a reviewed edit."""
    decide_step, checkout, agent = job["steps"]
    assert decide_step["id"] == "act"
    assert decide_step["shell"] == "bash"
    assert set(decide_step) == {"name", "id", "shell", "env", "run"}
    assert checkout == {"uses": "actions/checkout@v4", "if": WAITS}, (
        "no `ref`: the event's default ref only. On a review event that is the pull request's merge ref,"
        " and the fork refusal keeps that to this repository's own branches"
    )
    assert agent == {
        "uses": ACTION,
        "if": WAITS,
        "with": {
            "letta_api_key": "${{ secrets.LETTA_API_KEY }}",
            "github_token": "${{ secrets.GITHUB_TOKEN }}",
            "agent_id": "${{ inputs.agent-id }}",
            "trigger_phrase": "${{ inputs.trigger-phrase }}",
            "label_trigger": "${{ inputs.label-trigger }}",
        },
    }, "extra inputs such as allowed_bots or allowed_non_write_users switch off the action's own checks"


def test_no_script_has_an_expression_written_into_it(job):
    for step in job["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_the_job_has_a_timeout_the_callers_runner_and_only_the_permissions_it_needs(job):
    assert job["timeout-minutes"] == 30
    assert job["runs-on"] == "${{ fromJSON(inputs.runs-on) }}"
    assert job["permissions"] == {"contents": "write", "issues": "write", "pull-requests": "write"}
    assert load(WORKFLOW)["permissions"] == {"contents": "read"}


def test_the_workflow_is_only_ever_called_and_works_without_the_secret():
    on = triggers(load(WORKFLOW))
    assert set(on) == {"workflow_call"}
    call = on["workflow_call"]
    assert call["secrets"] == {"LETTA_API_KEY": {"required": False}}, "a caller with no key must still get a green run"
    assert set(call["inputs"]) == {"runs-on", "agent-id", "trigger-phrase", "label-trigger"}
    assert call["inputs"]["trigger-phrase"]["default"] == "@letta-code"
    assert call["inputs"]["label-trigger"]["default"] == "letta-code"
    assert call["inputs"]["agent-id"]["default"] == ""


# --- The example caller -------------------------------------------------------------------------------


def test_the_example_caller_is_off_until_the_repository_variable_is_set():
    assert load(CALLER)["jobs"]["letta"]["if"] == "${{ vars.LETTA_CODE_ENABLED == 'true' }}"


def test_the_example_caller_sends_only_events_that_can_summon_the_agent():
    """No pull_request (it can never summon the agent) and no pull_request_target (write access and a secret)."""
    assert triggers(load(CALLER)) == {
        "issues": {"types": ["opened", "labeled"]},
        "issue_comment": {"types": ["created"]},
        "pull_request_review_comment": {"types": ["created"]},
        "pull_request_review": {"types": ["submitted"]},
    }


def test_the_example_caller_passes_only_what_the_workflow_declares_and_grants_what_it_asks(job):
    declared = triggers(load(WORKFLOW))["workflow_call"]
    caller = load(CALLER)["jobs"]["letta"]
    assert set(load(CALLER)["jobs"]) == {"letta"}
    assert set(caller["with"]) <= set(declared["inputs"])
    assert set(caller["secrets"]) <= set(declared["secrets"])
    assert caller["permissions"] == job["permissions"]
    assert load(CALLER)["permissions"] == {"contents": "read"}

"""Guards on letta-code.yml: it stays off unless a repository opts in, and only a maintainer's request starts the agent.

The job holds write access and an API key, and its triggers include comments anyone can write. The
decision step is run for real under bash; the rest pins the shape of the workflow so a change that
widens what the agent job may do is a visible edit to this file.
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


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc[True]  # PyYAML reads the key `on` as True


@pytest.fixture(scope="module")
def job() -> dict:
    return load(WORKFLOW)["jobs"]["letta"]


# --- The decision, run for real -----------------------------------------------------------------------

# A maintainer's comment that contains the phrase, with the key and the agent id present.
SUMMONED = {
    "LETTA_API_KEY": "k",
    "AGENT_ID": "agent-1",
    "EVENT_NAME": "issue_comment",
    "EVENT_ACTION": "created",
    "IS_PULL_REQUEST": "false",
    "COMMENT_ASSOCIATION": "MEMBER",
    "ISSUE_ASSOCIATION": "NONE",
    "COMMENT_HAS_PHRASE": "true",
    "ISSUE_HAS_PHRASE": "false",
    "LABEL_MATCHES": "false",
}


def decide(job: dict, tmp_path: Path, **changes: str) -> str:
    step = job["steps"][0]
    assert set(step["env"]) == set(SUMMONED), "the test must set every variable the step reads"
    script = tmp_path / "decide.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    env = {**os.environ, **SUMMONED, **changes, "GITHUB_OUTPUT": out.as_posix()}
    done = subprocess.run(["bash", "-e", script.as_posix()], capture_output=True, env=env, check=False)
    assert done.returncode == 0, done.stderr.decode()
    lines = out.read_text(encoding="utf-8").split()
    assert len(lines) == 1, lines
    return lines[0]


ALLOW = {
    "a member's comment with the phrase": {},
    "the owner's comment": {"COMMENT_ASSOCIATION": "OWNER"},
    "a collaborator's comment": {"COMMENT_ASSOCIATION": "COLLABORATOR"},
    "a comment on a pull request": {"IS_PULL_REQUEST": "true"},
    "a pull request review comment": {"EVENT_NAME": "pull_request_review_comment"},
    "a member opens an issue with the phrase": {
        "EVENT_NAME": "issues",
        "EVENT_ACTION": "opened",
        "ISSUE_ASSOCIATION": "MEMBER",
        "ISSUE_HAS_PHRASE": "true",
        "COMMENT_ASSOCIATION": "",
        "COMMENT_HAS_PHRASE": "false",
    },
    "the trigger label is added to an issue": {
        "EVENT_NAME": "issues",
        "EVENT_ACTION": "labeled",
        "LABEL_MATCHES": "true",
        "COMMENT_ASSOCIATION": "",
        "COMMENT_HAS_PHRASE": "false",
    },
}

ISSUE_OPENED = {
    "EVENT_NAME": "issues",
    "EVENT_ACTION": "opened",
    "COMMENT_ASSOCIATION": "",
    "COMMENT_HAS_PHRASE": "false",
}
ISSUE_LABELED = {**ISSUE_OPENED, "EVENT_ACTION": "labeled"}

DENY = {
    "no API key": {"LETTA_API_KEY": ""},
    "no agent id": {"AGENT_ID": ""},
    "an outsider's comment with the phrase": {"COMMENT_ASSOCIATION": "NONE"},
    "a contributor's comment with the phrase": {"COMMENT_ASSOCIATION": "CONTRIBUTOR"},
    "a first-time contributor's comment": {"COMMENT_ASSOCIATION": "FIRST_TIME_CONTRIBUTOR"},
    "a comment with no association at all": {"COMMENT_ASSOCIATION": ""},
    "a member's comment without the phrase": {"COMMENT_HAS_PHRASE": "false"},
    "an edited comment": {"EVENT_ACTION": "edited"},
    "an outsider opens an issue with the phrase": {
        **ISSUE_OPENED,
        "ISSUE_ASSOCIATION": "NONE",
        "ISSUE_HAS_PHRASE": "true",
    },
    "a member opens an issue without the phrase": {**ISSUE_OPENED, "ISSUE_ASSOCIATION": "MEMBER"},
    "the phrase is only in the comment field of an issue event": {
        **ISSUE_OPENED,
        "ISSUE_ASSOCIATION": "MEMBER",
        "COMMENT_ASSOCIATION": "MEMBER",
        "COMMENT_HAS_PHRASE": "true",
    },
    "another label is added to an issue": {**ISSUE_LABELED, "ISSUE_ASSOCIATION": "MEMBER", "ISSUE_HAS_PHRASE": "true"},
    "the trigger label is added to a pull request": {
        **ISSUE_LABELED,
        "LABEL_MATCHES": "true",
        "IS_PULL_REQUEST": "true",
    },
    "a pull request is opened": {"EVENT_NAME": "pull_request", "EVENT_ACTION": "opened"},
    "the trigger label on a pull_request event": {
        "EVENT_NAME": "pull_request",
        "EVENT_ACTION": "labeled",
        "LABEL_MATCHES": "true",
    },
    "a push to a pull request": {"EVENT_NAME": "pull_request", "EVENT_ACTION": "synchronize"},
    "a pull request is reopened": {"EVENT_NAME": "pull_request", "EVENT_ACTION": "reopened"},
    "an event the caller should not send": {"EVENT_NAME": "push", "EVENT_ACTION": ""},
}


@pytest.mark.parametrize("case", sorted(ALLOW))
def test_a_maintainers_request_starts_the_agent(job, tmp_path, case):
    assert decide(job, tmp_path, **ALLOW[case]) == "act=true"


@pytest.mark.parametrize("case", sorted(DENY))
def test_anything_else_only_reports_the_check(job, tmp_path, case):
    assert decide(job, tmp_path, **DENY[case]) == "act=false"


def test_the_decision_step_gets_answers_not_event_text(job):
    """Titles, bodies and label names never enter the environment: only associations and true/false."""
    env = job["steps"][0]["env"]
    assert env["COMMENT_HAS_PHRASE"] == "${{ contains(github.event.comment.body, inputs.trigger-phrase) }}"
    assert env["ISSUE_HAS_PHRASE"] == (
        "${{ contains(github.event.issue.title, inputs.trigger-phrase)"
        " || contains(github.event.issue.body, inputs.trigger-phrase) }}"
    )
    assert env["LABEL_MATCHES"] == "${{ github.event.label.name == inputs.label-trigger }}"
    assert env["COMMENT_ASSOCIATION"] == "${{ github.event.comment.author_association }}"
    assert env["ISSUE_ASSOCIATION"] == "${{ github.event.issue.author_association }}"
    assert env["IS_PULL_REQUEST"] == "${{ github.event.issue.pull_request != null }}"


# --- The shape of the job -----------------------------------------------------------------------------


def test_the_job_is_exactly_decide_checkout_agent(job):
    """A step added here runs with write access and, after the decision, the key: adding one is a reviewed edit."""
    decide_step, checkout, agent = job["steps"]
    assert decide_step["id"] == "act"
    assert set(decide_step) == {"name", "id", "env", "run"}
    assert checkout == {"uses": "actions/checkout@v4", "if": WAITS}, "no `ref`: never check out pull request code"
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


def test_the_job_has_a_timeout_and_only_the_permissions_it_needs(job):
    assert job["timeout-minutes"] == 30
    assert job["permissions"] == {"contents": "write", "issues": "write", "pull-requests": "write"}
    assert load(WORKFLOW)["permissions"] == {"contents": "read"}
    assert "env" not in load(WORKFLOW), "a workflow-level env would hand the key to every step"


def test_the_workflow_is_only_ever_called():
    assert set(triggers(load(WORKFLOW))) == {"workflow_call"}


# --- The example caller -------------------------------------------------------------------------------


def test_the_example_caller_is_off_until_the_repository_variable_is_set():
    assert load(CALLER)["jobs"]["letta"]["if"] == "${{ vars.LETTA_CODE_ENABLED == 'true' }}"


def test_the_example_caller_sends_only_events_that_can_summon_the_agent():
    """No pull_request (it can never summon the agent) and no pull_request_target (write access and a secret)."""
    assert triggers(load(CALLER)) == {
        "issues": {"types": ["opened", "labeled"]},
        "issue_comment": {"types": ["created"]},
        "pull_request_review_comment": {"types": ["created"]},
    }


def test_the_example_caller_passes_only_what_the_workflow_declares_and_grants_what_it_asks(job):
    declared = triggers(load(WORKFLOW))["workflow_call"]
    caller = load(CALLER)["jobs"]["letta"]
    assert set(caller["with"]) <= set(declared["inputs"])
    assert set(caller["secrets"]) <= set(declared["secrets"])
    assert caller["permissions"] == job["permissions"]
    assert load(CALLER)["permissions"] == {"contents": "read"}

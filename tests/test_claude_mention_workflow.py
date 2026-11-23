"""Guards on claude-mention.yml: only the right people summon Claude, and the job holds nothing it does not need.

The job holds the Claude token once it is summoned, and its triggers include comments anyone may write.
The decision step is run for real under bash; the rest pins the shape of the workflow so a change that
widens what the job may do is a visible edit to this file.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "claude-mention.yml"
CALLER = ROOT / "examples" / "claude-mention-caller.yml"
SHA_PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
WAITS = "${{ steps.act.outputs.act == 'true' }}"
PHRASE = "inputs.trigger-phrase"

DECISION_ENV = {
    "ACTOR": "${{ github.actor }}",
    "HAS_CREDENTIALS": "${{ secrets.INFISICAL_CLIENT_ID != '' && secrets.INFISICAL_CLIENT_SECRET != '' }}",
    "HAS_STORE": "${{ inputs.infisical-domain != '' && inputs.infisical-project-slug != '' }}",
    "HAS_PHRASE_INPUT": "${{ inputs.trigger-phrase != '' }}",
    "EVENT_NAME": "${{ github.event_name }}",
    "EVENT_ACTION": "${{ github.event.action }}",
    "COMMENT_HAS_PHRASE": f"${{{{ contains(github.event.comment.body, {PHRASE}) }}}}",
    "REVIEW_HAS_PHRASE": f"${{{{ contains(github.event.review.body, {PHRASE}) }}}}",
    "ISSUE_HAS_PHRASE": (
        f"${{{{ contains(github.event.issue.title, {PHRASE}) || contains(github.event.issue.body, {PHRASE}) }}}}"
    ),
    "COMMENT_ASSOCIATION": "${{ github.event.comment.author_association }}",
    "REVIEW_ASSOCIATION": "${{ github.event.review.author_association }}",
    "ISSUE_ASSOCIATION": "${{ github.event.issue.author_association }}",
    "ALLOWED_ASSOCIATIONS": "${{ inputs.allowed-associations }}",
}


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc[True]  # PyYAML reads the key `on` as True


@pytest.fixture(scope="module")
def job() -> dict:
    return load(WORKFLOW)["jobs"]["claude"]


# --- The decision, run for real -----------------------------------------------------------------------

# A collaborator writes a comment with the mention; the secret store is configured.
SUMMONED = {
    "ACTOR": "alice",
    "HAS_CREDENTIALS": "true",
    "HAS_STORE": "true",
    "HAS_PHRASE_INPUT": "true",
    "EVENT_NAME": "issue_comment",
    "EVENT_ACTION": "created",
    "COMMENT_HAS_PHRASE": "true",
    "REVIEW_HAS_PHRASE": "false",
    "ISSUE_HAS_PHRASE": "false",
    "COMMENT_ASSOCIATION": "COLLABORATOR",
    "REVIEW_ASSOCIATION": "",
    "ISSUE_ASSOCIATION": "",
    "ALLOWED_ASSOCIATIONS": "OWNER,MEMBER,COLLABORATOR",
}


def run_step(job: dict, tmp_path: Path, **changes: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    step = job["steps"][0]
    assert set(step["env"]) == set(SUMMONED), "set every variable the step reads"
    script = tmp_path / "decide.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    env = {**os.environ, **SUMMONED, **changes, "GITHUB_OUTPUT": out.as_posix()}
    # The flags GitHub's runner uses for `shell: bash`.
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
        capture_output=True,
        env=env,
        check=False,
    )
    return done, out.read_text(encoding="utf-8").split()


def decide(job: dict, tmp_path: Path, **changes: str) -> str:
    done, lines = run_step(job, tmp_path, **changes)
    assert done.returncode == 0, done.stderr
    assert len(lines) == 1, lines
    return lines[0]


REVIEW = {
    "EVENT_NAME": "pull_request_review",
    "EVENT_ACTION": "submitted",
    "REVIEW_HAS_PHRASE": "true",
    "REVIEW_ASSOCIATION": "MEMBER",
    "COMMENT_HAS_PHRASE": "false",
    "COMMENT_ASSOCIATION": "",
}
REVIEW_COMMENT = {"EVENT_NAME": "pull_request_review_comment"}
ISSUE_OPENED = {
    "EVENT_NAME": "issues",
    "EVENT_ACTION": "opened",
    "ISSUE_HAS_PHRASE": "true",
    "ISSUE_ASSOCIATION": "OWNER",
    "COMMENT_HAS_PHRASE": "false",
    "COMMENT_ASSOCIATION": "",
}

ALLOW = {
    "a mention in a comment from a collaborator": {},
    "the same from the owner": {"COMMENT_ASSOCIATION": "OWNER"},
    "the same from a member": {"COMMENT_ASSOCIATION": "MEMBER"},
    "a mention in a pull request review comment": REVIEW_COMMENT,
    "a mention in the summary of a review": REVIEW,
    "an issue opened with the mention": ISSUE_OPENED,
    "an issue assigned with the mention in it": {**ISSUE_OPENED, "EVENT_ACTION": "assigned"},
    "a contributor, when the caller allows contributors": {
        "COMMENT_ASSOCIATION": "CONTRIBUTOR",
        "ALLOWED_ASSOCIATIONS": "OWNER, CONTRIBUTOR",
    },
}

DENY = {
    # the request cannot be answered
    "no credentials": {"HAS_CREDENTIALS": "false"},
    "no secret store settings": {"HAS_STORE": "false"},
    # who asks: a mention from an author outside the allowed set
    "a contributor": {"COMMENT_ASSOCIATION": "CONTRIBUTOR"},
    "a first-time contributor": {"COMMENT_ASSOCIATION": "FIRST_TIME_CONTRIBUTOR"},
    "someone with no association": {"COMMENT_ASSOCIATION": "NONE"},
    "an event with no association": {"COMMENT_ASSOCIATION": ""},
    "a review from someone outside the set": {**REVIEW, "REVIEW_ASSOCIATION": "NONE"},
    "an issue opened by someone outside the set": {**ISSUE_OPENED, "ISSUE_ASSOCIATION": "CONTRIBUTOR"},
    "an association that only contains an allowed one": {"COMMENT_ASSOCIATION": "OWNERS"},
    "an empty allowed set": {"ALLOWED_ASSOCIATIONS": ""},
    "the issue author, not the commenter, is allowed": {
        "COMMENT_ASSOCIATION": "NONE",
        "ISSUE_ASSOCIATION": "OWNER",
    },
    "the comment author, not the review author, is allowed": {
        **REVIEW,
        "REVIEW_ASSOCIATION": "NONE",
        "COMMENT_ASSOCIATION": "OWNER",
    },
    "a bot comments with the mention": {"ACTOR": "some-app[bot]"},
    # the event does not ask: a comment without the mention
    "a comment without the mention": {"COMMENT_HAS_PHRASE": "false"},
    "an edited comment": {"EVENT_ACTION": "edited"},
    "a deleted comment": {"EVENT_ACTION": "deleted"},
    "a review without the mention": {**REVIEW, "REVIEW_HAS_PHRASE": "false"},
    "an edited review": {**REVIEW, "EVENT_ACTION": "edited"},
    "the mention only in the review field of a comment event": {
        "COMMENT_HAS_PHRASE": "false",
        "REVIEW_HAS_PHRASE": "true",
    },
    "an issue opened without the mention": {**ISSUE_OPENED, "ISSUE_HAS_PHRASE": "false"},
    "the mention only in the issue of a comment event": {"COMMENT_HAS_PHRASE": "false", "ISSUE_HAS_PHRASE": "true"},
    "an edited issue with the mention": {**ISSUE_OPENED, "EVENT_ACTION": "edited"},
    "an empty trigger phrase, which every comment contains": {"HAS_PHRASE_INPUT": "false"},
    # events the job has no trigger for
    "a pull request is opened": {"EVENT_NAME": "pull_request", "EVENT_ACTION": "opened"},
    "a pull_request_target event": {"EVENT_NAME": "pull_request_target", "EVENT_ACTION": "opened"},
    "a push": {"EVENT_NAME": "push", "EVENT_ACTION": ""},
}


@pytest.mark.parametrize("case", sorted(ALLOW))
def test_a_mention_from_the_allowed_set_starts_claude(job, tmp_path, case):
    assert decide(job, tmp_path, **ALLOW[case]) == "act=true"


@pytest.mark.parametrize("case", sorted(DENY))
def test_anything_else_does_nothing(job, tmp_path, case):
    assert decide(job, tmp_path, **DENY[case]) == "act=false"


REASONS = {
    "no credentials": "the secret store settings or credentials are missing",
    "no secret store settings": "the secret store settings or credentials are missing",
    "a bot comments with the mention": "some-app[bot] is a bot",
    "a contributor": "the author's association (CONTRIBUTOR) is not in the allowed set",
    "an event with no association": "the author's association (none) is not in the allowed set",
    "a comment without the mention": "the comment does not contain the trigger phrase",
    "a pull request is opened": "pull_request/opened never summons Claude",
}


@pytest.mark.parametrize("case", sorted(REASONS))
def test_a_refusal_says_why(job, tmp_path, case):
    done, lines = run_step(job, tmp_path, **DENY[case])
    assert lines == ["act=false"]
    assert f"Not calling Claude: {REASONS[case]}." in done.stdout.decode("utf-8").splitlines()


def test_the_decision_step_gets_answers_not_event_text_and_no_secret(job):
    assert job["steps"][0]["env"] == DECISION_ENV


# --- The shape of the workflow ------------------------------------------------------------------------


def test_the_workflow_has_one_job_and_no_shared_environment(job):
    doc = load(WORKFLOW)
    assert set(doc["jobs"]) == {"claude"}
    assert set(job) == {"runs-on", "timeout-minutes", "permissions", "steps"}
    assert "env" not in doc


def test_the_job_is_decide_checkout_fetch_token_run_claude(job):
    decide_step, checkout, token, claude = job["steps"]
    assert decide_step["id"] == "act"
    assert decide_step["shell"] == "bash"
    assert set(decide_step) == {"name", "id", "shell", "env", "run"}
    for step in (checkout, token, claude):
        assert step["if"] == WAITS, step["name"]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"fetch-depth": 1, "persist-credentials": False}
    assert token["uses"].startswith("Infisical/secrets-action@")
    assert token["with"]["secret-name"] == "CLAUDE_CODE_OAUTH_TOKEN", "fetch one secret, not the folder"
    assert claude["uses"].startswith("anthropics/claude-code-action@")
    assert set(claude["with"]) == {
        "claude_code_oauth_token",
        "trigger_phrase",
        "additional_permissions",
    }, "extra inputs such as allowed_bots or allowed_non_write_users switch off the action's own checks"


def test_every_action_is_pinned_by_commit_sha(job):
    for step in job["steps"]:
        if "uses" in step:
            assert SHA_PINNED.match(step["uses"]), f"{step['name']} must pin by commit SHA"


def test_the_token_is_fetched_only_after_the_decision_and_used_only_by_the_action(job):
    token_step = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("Infisical/"))
    assert token_step["if"] == WAITS
    for step in job["steps"]:
        if step is not job["steps"][-1]:
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in str(step.get("env", {})), step["name"]
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in step.get("run", ""), step["name"]


def test_no_script_has_an_expression_written_into_it(job):
    for step in job["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_the_job_has_a_timeout_the_callers_runner_and_only_the_permissions_it_needs(job):
    assert job["timeout-minutes"] == 30
    assert job["runs-on"] == "${{ fromJSON(inputs.runs-on) }}"
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "read",
        "id-token": "write",
        "actions": "read",
    }
    assert load(WORKFLOW)["permissions"] == {"contents": "read"}


def test_the_workflow_is_only_ever_called_and_works_without_the_secrets():
    call = triggers(load(WORKFLOW))["workflow_call"]
    assert set(triggers(load(WORKFLOW))) == {"workflow_call"}
    assert call["secrets"] == {
        "INFISICAL_CLIENT_ID": {"required": False},
        "INFISICAL_CLIENT_SECRET": {"required": False},
    }, "a caller that has not set them up yet must still get a green run"
    assert set(call["inputs"]) == {
        "runs-on",
        "infisical-domain",
        "infisical-project-slug",
        "infisical-env-slug",
        "infisical-claude-path",
        "trigger-phrase",
        "allowed-associations",
    }
    assert call["inputs"]["trigger-phrase"]["default"] == "@claude"
    assert call["inputs"]["allowed-associations"]["default"] == "OWNER,MEMBER,COLLABORATOR"
    assert call["inputs"]["infisical-domain"]["default"] == ""
    assert call["inputs"]["infisical-project-slug"]["default"] == ""


def test_the_workflow_names_no_host_and_no_private_place():
    text = WORKFLOW.read_text(encoding="utf-8") + CALLER.read_text(encoding="utf-8")
    assert not re.search(r"https?://(?!github\.com/)", text)
    assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", text)


# --- The example caller -------------------------------------------------------------------------------


def test_the_example_caller_sends_only_events_that_can_summon_claude():
    """No pull_request_target (a write-capable context with a secret)."""
    assert triggers(load(CALLER)) == {
        "issue_comment": {"types": ["created"]},
        "pull_request_review_comment": {"types": ["created"]},
        "issues": {"types": ["opened"]},
        "pull_request_review": {"types": ["submitted"]},
    }


def test_the_example_caller_passes_only_what_the_workflow_declares_and_grants_what_it_asks(job):
    declared = triggers(load(WORKFLOW))["workflow_call"]
    caller = load(CALLER)["jobs"]["claude"]
    assert set(load(CALLER)["jobs"]) == {"claude"}
    assert set(caller["with"]) <= set(declared["inputs"])
    assert set(caller["secrets"]) <= set(declared["secrets"])
    assert caller["permissions"] == job["permissions"]
    assert load(CALLER)["permissions"] == {"contents": "read"}


def test_the_example_caller_pins_the_workflow_by_commit():
    uses = load(CALLER)["jobs"]["claude"]["uses"]
    assert uses == "datacrew-space/ci-workflows/.github/workflows/claude-mention.yml@<COMMIT_SHA>"

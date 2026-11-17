"""Guards on letta-code.yml: it stays off unless a repository opts in, and the agent job is hard to misuse.

The job holds write access and an API key, and its triggers include comments anyone can write, so each
assertion pins a property that no cheap real run can check.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "letta-code.yml"
CALLER = ROOT / "examples" / "letta-code-caller.yml"
ACTION = re.compile(r"^letta-ai/letta-code-action@[0-9a-f]{40}$")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    return doc[True]  # PyYAML reads the key `on` as True


def agent_steps(job: dict) -> list[dict]:
    return [s for s in job["steps"] if "uses" in s]


def test_the_example_caller_is_off_until_the_repository_variable_is_set():
    job = load(CALLER)["jobs"]["letta"]
    assert job["if"] == "${{ vars.LETTA_CODE_ENABLED == 'true' }}"


def test_the_example_caller_passes_only_inputs_and_secrets_the_workflow_declares():
    declared = triggers(load(WORKFLOW))["workflow_call"]
    job = load(CALLER)["jobs"]["letta"]
    assert set(job["with"]) <= set(declared["inputs"])
    assert set(job["secrets"]) <= set(declared["secrets"])


def test_neither_file_uses_pull_request_target():
    """A write-capable job with a secret must never run for a fork's pull request."""
    assert set(triggers(load(WORKFLOW))) == {"workflow_call"}
    assert "pull_request_target" not in triggers(load(CALLER))


def test_the_caller_grants_exactly_the_permissions_the_job_asks_for():
    wanted = load(WORKFLOW)["jobs"]["letta"]["permissions"]
    assert wanted == {"contents": "write", "issues": "write", "pull-requests": "write"}
    assert load(CALLER)["jobs"]["letta"]["permissions"] == wanted
    assert load(WORKFLOW)["permissions"] == {"contents": "read"}


def test_the_action_is_pinned_by_commit_sha():
    uses = [s["uses"] for s in agent_steps(load(WORKFLOW)["jobs"]["letta"]) if "letta" in s["uses"]]
    assert len(uses) == 1
    assert ACTION.match(uses[0])


def test_every_agent_step_waits_for_the_decision():
    """With no key, no agent id, or a plain push, nothing after the decision step may run."""
    job = load(WORKFLOW)["jobs"]["letta"]
    assert job["steps"][0]["id"] == "act"
    for s in agent_steps(job):
        assert s["if"] == "${{ steps.act.outputs.act == 'true' }}", s["uses"]


def test_the_decision_step_reads_everything_through_the_environment():
    """No secret and no event text is written into the script itself."""
    decide = load(WORKFLOW)["jobs"]["letta"]["steps"][0]
    assert "${{" not in decide["run"]
    assert decide["env"] == {
        "LETTA_API_KEY": "${{ secrets.LETTA_API_KEY }}",
        "AGENT_ID": "${{ inputs.agent-id }}",
        "EVENT_ACTION": "${{ github.event.action }}",
    }


def test_no_workflow_level_env_holds_the_key():
    assert "env" not in load(WORKFLOW)

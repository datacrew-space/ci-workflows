"""Each CI job holds only the secrets it needs, one Infisical fetch per secret, and the reviewer can read issues.

Infisical/secrets-action v1.0.15 has no `secret-name` input: it exports EVERY secret under the path, so
an agent job that fetched a whole folder held every app key. v1.0.17 is the first tag that honours
`secret-name`; the steps pin v1.0.18 by commit SHA.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SHA_PINNED = re.compile(r"^Infisical/secrets-action@[0-9a-f]{40}$")

# The only secrets each job may hold. Adding one is a reviewed change to this table.
ALLOWED = {
    "claude-code-review.yml": {
        "claude-review": {"CLAUDE_CODE_OAUTH_TOKEN", "CI_CODE_REVIEWER_APP_ID", "CI_CODE_REVIEWER_PRIVATE_KEY"},
        "respond": {"CLAUDE_CODE_OAUTH_TOKEN", "CI_REVIEW_RESPONDER_APP_ID", "CI_REVIEW_RESPONDER_PRIVATE_KEY"},
        "push": {"CI_REVIEW_RESPONDER_APP_ID", "CI_REVIEW_RESPONDER_PRIVATE_KEY"},
        "report": {"CI_REVIEW_ORCHESTRATOR_APP_ID", "CI_REVIEW_ORCHESTRATOR_PRIVATE_KEY"},
    },
    "dispatch-ready-issues.yml": {
        "implement": {"CLAUDE_CODE_OAUTH_TOKEN", "MCP_TOKEN"},
        "publish": {"IMPLEMENTER_APP_ID", "IMPLEMENTER_APP_PRIVATE_KEY"},
    },
    "ready-when-green.yml": {
        "sweep": {"READY_APP_ID", "READY_APP_PRIVATE_KEY"},
    },
}


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def fetch_steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith("Infisical/secrets-action")]


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_every_infisical_fetch_names_one_secret_and_is_pinned_by_sha(name):
    for job_id, job in load(name)["jobs"].items():
        for step in fetch_steps(job):
            label = f"{name}:{job_id}:{step.get('name')}"
            assert SHA_PINNED.match(step["uses"]), f"{label} must pin the action by commit SHA"
            assert step["with"].get("secret-name"), f"{label} exports a whole folder: set `secret-name`"


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_each_job_fetches_exactly_the_secrets_it_is_allowed(name):
    jobs = load(name)["jobs"]
    held = {
        job_id: {s["with"]["secret-name"] for s in fetch_steps(job)} for job_id, job in jobs.items() if fetch_steps(job)
    }
    assert held == ALLOWED[name]


def test_only_the_implement_job_holds_the_mcp_token():
    for name in ALLOWED:
        for job_id, job in load(name)["jobs"].items():
            names = {s["with"]["secret-name"] for s in fetch_steps(job)}
            if "MCP_TOKEN" in names:
                assert (name, job_id) == ("dispatch-ready-issues.yml", "implement")


def test_the_reviewer_token_can_read_issues_so_the_spec_axis_runs():
    steps = load("claude-code-review.yml")["jobs"]["claude-review"]["steps"]
    mints = [
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/create-github-app-token")
        and "CI_CODE_REVIEWER_APP_ID" in str(s["with"].get("app-id"))
        and s["with"].get("permission-contents") == "read"
    ]
    assert mints, "the reviewer's read-only token step moved: update this test"
    for step in mints:
        assert step["with"].get("permission-issues") == "read"

"""Every workflow triggered by `pull_request` cancels superseded pull-request runs, and only those.

A push to a pull request makes the run on the previous head worthless, so it should be cancelled.
A push to the default branch must never be cancelled, or a commit on it is left without a complete
record. Workflow-level concurrency groups are shared across a repository, so two workflows that
could run on the same pull request must not share a group, or one cancels the other.

The review caller is the one exception: it keeps its own, stricter rule (see its own tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
OWN = ROOT / ".github" / "workflows"
CANCEL_ONLY_PULL_REQUESTS = "${{ github.event_name == 'pull_request' }}"
# The review caller cancels on every event except the responder's push; tested in its own suite.
OWN_RULE = {"examples/caller.yml"}


def _on(doc: dict):
    # YAML 1.1 parses the bare key `on` as True.
    return doc.get("on", doc.get(True))


def _triggers(doc: dict) -> set[str]:
    on = _on(doc)
    if on is None:  # not a workflow (an example that only holds other config)
        return set()
    if isinstance(on, str):
        return {on}
    return set(on)


def _pull_request_workflows() -> list[tuple[str, dict]]:
    found = []
    for path in sorted([*EXAMPLES.rglob("*.y*ml"), *OWN.glob("*.y*ml")]):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and "pull_request" in _triggers(doc):
            found.append((path.relative_to(ROOT).as_posix(), doc))
    return found


def missing_pull_request_concurrency(doc: dict) -> str | None:
    """Why `doc` breaks the rule, or None when it follows it."""
    group = (doc.get("concurrency") or {}).get("group")
    if not group:
        return "no workflow-level concurrency group"
    if doc["concurrency"].get("cancel-in-progress") != CANCEL_ONLY_PULL_REQUESTS:
        return "cancel-in-progress must cancel pull_request runs only"
    if "github.event.pull_request.number" not in group:
        return "group must be per pull request"
    return None


WORKFLOWS = _pull_request_workflows()


def test_the_walk_finds_the_known_pull_request_workflows():
    names = {name for name, _ in WORKFLOWS}
    assert {
        ".github/workflows/ci.yml",
        "examples/caller.yml",
        "examples/guards-caller.yml",
        "examples/python-uv-repo/.github/workflows/ci.yml",
    } <= names


@pytest.mark.parametrize(("name", "doc"), [w for w in WORKFLOWS if w[0] not in OWN_RULE], ids=lambda v: v if isinstance(v, str) else "")
def test_pull_request_workflow_cancels_only_superseded_pull_request_runs(name, doc):
    assert missing_pull_request_concurrency(doc) is None, name


def _resolved_group(doc: dict) -> str:
    # `github.workflow` evaluates to the workflow's name, so resolve it before comparing groups.
    return doc["concurrency"]["group"].replace("${{ github.workflow }}", doc.get("name", ""))


def test_groups_are_distinct_so_workflows_never_cancel_each_other():
    groups = [_resolved_group(doc) for _, doc in WORKFLOWS if doc.get("concurrency")]
    assert len(groups) == len(set(groups)), groups


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        ({"on": {"pull_request": None}}, "no workflow-level concurrency group"),
        (
            {"concurrency": {"group": "ci-${{ github.event.pull_request.number }}", "cancel-in-progress": True}},
            "cancel-in-progress must cancel pull_request runs only",
        ),
        (
            {"concurrency": {"group": "ci-${{ github.ref }}", "cancel-in-progress": CANCEL_ONLY_PULL_REQUESTS}},
            "group must be per pull request",
        ),
    ],
)
def test_the_rule_rejects_a_workflow_that_breaks_it(doc, reason):
    assert missing_pull_request_concurrency(doc) == reason

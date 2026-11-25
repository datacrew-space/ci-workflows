"""Every workflow triggered by `pull_request` cancels superseded pull-request runs, and only those.

A push to a pull request makes the run on the previous head worthless, so it should be cancelled.
Any other run must never be cancelled or queued behind another: `cancel-in-progress` protects only
the run in progress, so a group shared by pushes to main would cancel a *pending* run there. And
workflow-level groups are shared across a repository (case-insensitively), so two workflows that run
on the same pull request must not share one.

The rule is one whole value, compared exactly, so a dropped or flipped clause fails. The review
caller is the one exception: it keeps its own, stricter rule (see its own tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
OWN = ROOT / ".github" / "workflows"
CANONICAL = {
    # The workflow name keeps workflows apart; run_id gives every non-pull-request run its own group.
    "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}",
    "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
}
# The review caller cancels on every event except the responder's push; tested in its own suite.
OWN_RULE = {"examples/caller.yml"}


def _triggers(doc: dict) -> set[str]:
    on = doc.get("on", doc.get(True))  # YAML 1.1 parses the bare key `on` as True.
    if on is None:  # not a workflow (an example that only holds other config)
        return set()
    if isinstance(on, str):
        return {on}
    return set(on)


def _pull_request_workflows(paths) -> list[tuple[str, dict]]:
    found = []
    for path in sorted(paths):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and "pull_request" in _triggers(doc):
            found.append((path.relative_to(ROOT).as_posix(), doc))
    return found


def pull_request_concurrency_problem(doc: dict) -> str | None:
    """Why `doc` breaks the rule, or None when it follows it."""
    conc = doc.get("concurrency")
    if conc is None:
        return "no workflow-level concurrency"
    if not isinstance(conc, dict):
        return "concurrency must be a mapping with group and cancel-in-progress"
    if conc != CANONICAL:
        return f"concurrency must be exactly {CANONICAL}"
    return None


def workflow_identity(name: str, doc: dict) -> str:
    """What `github.workflow` evaluates to: the `name:`, else the file path. Groups ignore case."""
    return str(doc.get("name") or name).lower()


EXAMPLE_WORKFLOWS = _pull_request_workflows(EXAMPLES.rglob("*.y*ml"))
OWN_WORKFLOWS = _pull_request_workflows(OWN.glob("*.y*ml"))
CHECKED = [w for w in EXAMPLE_WORKFLOWS + OWN_WORKFLOWS if w[0] not in OWN_RULE]


def test_the_walk_finds_the_known_pull_request_workflows():
    assert {name for name, _ in EXAMPLE_WORKFLOWS + OWN_WORKFLOWS} >= {
        ".github/workflows/ci.yml",
        "examples/caller.yml",
        "examples/guards-caller.yml",
        "examples/python-uv-repo/.github/workflows/ci.yml",
    }


@pytest.mark.parametrize(("name", "doc"), CHECKED, ids=[name for name, _ in CHECKED])
def test_pull_request_workflow_cancels_only_superseded_pull_request_runs(name, doc):
    assert pull_request_concurrency_problem(doc) is None, name


# Examples land in one caller repository together; this repository's own workflows run here. The two
# sets never share a repository, so each is checked on its own.
@pytest.mark.parametrize("population", [EXAMPLE_WORKFLOWS, OWN_WORKFLOWS], ids=["examples", "own"])
def test_workflows_in_one_repository_never_share_a_group(population):
    identities = [workflow_identity(name, doc) for name, doc in population if name not in OWN_RULE]
    assert len(identities) == len(set(identities)), identities


def test_an_unnamed_workflow_is_identified_by_its_path():
    assert workflow_identity("examples/a.yml", {}) != workflow_identity("examples/b.yml", {})
    assert workflow_identity("x.yml", {"name": "CI"}) == workflow_identity("y.yml", {"name": "ci"})


@pytest.mark.parametrize(
    ("concurrency", "reason"),
    [
        (None, "no workflow-level concurrency"),
        ("some-group", "concurrency must be a mapping with group and cancel-in-progress"),
        ({**CANONICAL, "cancel-in-progress": True}, "concurrency must be exactly"),
        # A group shared by every push cancels a pending run on main.
        (
            {**CANONICAL, "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"},
            "exactly",
        ),
        # No fallback: every push, on every branch, shares one group.
        ({**CANONICAL, "group": "${{ github.workflow }}-${{ github.event.pull_request.number }}"}, "exactly"),
        # No workflow name: two workflows on one pull request cancel each other.
        ({**CANONICAL, "group": "ci-${{ github.event.pull_request.number || github.run_id }}"}, "exactly"),
    ],
)
def test_the_rule_rejects_a_workflow_that_breaks_it(concurrency, reason):
    doc = {} if concurrency is None else {"concurrency": concurrency}
    assert reason in (pull_request_concurrency_problem(doc) or "")

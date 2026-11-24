"""Every example caller grants the scopes the reusable workflow it calls asks for.

A called job cannot hold a scope its caller did not grant. GitHub refuses such a run before any job
starts ("startup_failure", "This run likely failed because of a workflow file issue"), with nothing in
a log, so a caller copied from an example that grants too little never runs at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
EXAMPLES = ROOT / "examples"

LEVELS = {"none": 0, "read": 1, "write": 2}
USES = re.compile(r"/\.github/workflows/([A-Za-z0-9_.-]+\.ya?ml)@")


def _calls() -> list[tuple[str, str, str]]:
    """(example file, caller job, reusable workflow file) for every call an example makes into this repo."""
    found = []
    # rglob: an example repository keeps its callers under its own .github/workflows/.
    for path in sorted(EXAMPLES.rglob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in (doc.get("jobs") or {}).items():
            match = USES.search(str(job.get("uses", "")))
            if match and (WF / match.group(1)).exists():
                found.append((path.relative_to(EXAMPLES).as_posix(), name, match.group(1)))
    return found


def _granted(example: str, job_name: str) -> dict:
    doc = yaml.safe_load((EXAMPLES / example).read_text(encoding="utf-8"))
    job = doc["jobs"][job_name]
    # A job-level block replaces the workflow-level one; it does not add to it. Stricter than GitHub on
    # purpose: an example must spell its scopes out, so no `read-all` string and no missing block.
    granted = job["permissions"] if "permissions" in job else doc.get("permissions")
    assert isinstance(granted, dict), f"{example}: job {job_name} must state its permissions as a mapping"
    return granted


def _requested(workflow: str) -> dict[str, tuple[str, str]]:
    """scope -> (highest level any job asks for, the job that asks)."""
    doc = yaml.safe_load((WF / workflow).read_text(encoding="utf-8"))
    top = doc.get("permissions")
    needed: dict[str, tuple[str, str]] = {}
    for name, job in doc["jobs"].items():
        perms = job["permissions"] if "permissions" in job else top
        assert isinstance(perms, dict), f"{workflow}: job {name} must state its permissions as a mapping"
        for scope, level in perms.items():
            if scope not in needed or LEVELS[level] > LEVELS[needed[scope][0]]:
                needed[scope] = (level, name)
    return needed


def test_the_examples_call_reusable_workflows():
    assert _calls(), "no example caller was found, so the test below would check nothing"


@pytest.mark.parametrize(("example", "job_name", "workflow"), _calls())
def test_an_example_caller_grants_every_scope_the_called_jobs_ask_for(example, job_name, workflow):
    granted = _granted(example, job_name)
    missing = {
        scope: f"{level} (job {asker})"
        for scope, (level, asker) in _requested(workflow).items()
        if LEVELS[granted.get(scope, "none")] < LEVELS[level]
    }
    assert not missing, f"{example}: job {job_name} calls {workflow} but does not grant {missing}"

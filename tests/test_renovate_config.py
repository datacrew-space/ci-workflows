"""Renovate must not move CI off the declared Python floor.

The floor (3.13) is the `python-ci.yml` default. This repository's own setup-python steps run on it, and
Renovate's github-actions manager would otherwise propose every new Python minor for them (#55).
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

FLOOR_RULE = {
    "matchManagers": ["github-actions"],
    "matchDepNames": ["python"],
    "enabled": False,
}


def on_of(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as True.
    return doc.get("on", doc.get(True))


def floor() -> str:
    doc = yaml.safe_load((WORKFLOWS / "python-ci.yml").read_text())
    return on_of(doc)["workflow_call"]["inputs"]["python-version"]["default"]


def test_renovate_does_not_bump_python_in_workflows():
    config = json.loads((ROOT / "renovate.json").read_text())
    rules = [{k: r[k] for k in FLOOR_RULE if k in r} for r in config["packageRules"]]
    assert FLOOR_RULE in rules


def test_setup_python_steps_run_on_the_floor():
    versions = {}
    for name in ("ci.yml", "guards.yml"):
        doc = yaml.safe_load((WORKFLOWS / name).read_text())
        for job_id, job in doc["jobs"].items():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/setup-python"):
                    versions[f"{name}:{job_id}"] = step["with"]["python-version"]
    assert versions, "no setup-python step found"
    assert set(versions.values()) == {floor()}, versions

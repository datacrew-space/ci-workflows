"""The optional pull-request title check (Conventional Commits).

The step's script runs here exactly as written in the workflow. The title is untrusted, so the tests
also prove it is matched as data: shell metacharacters in it are never executed, and it is not
printed back into the log.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-title.yml").read_text(encoding="utf-8"))
CALLER = yaml.safe_load((ROOT / "examples" / "pr-title-caller.yml").read_text(encoding="utf-8"))
(STEP,) = WF["jobs"]["title"]["steps"]
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")
DEFAULT_TYPES = WF.get("on", WF.get(True))["workflow_call"]["inputs"]["types"]["default"]


def check(title: str, types: str = DEFAULT_TYPES) -> subprocess.CompletedProcess:
    env = {**os.environ, "TITLE": title, "TYPES": types}
    return subprocess.run([BASH, "-c", STEP["run"]], env=env, capture_output=True, text=True)


def test_title_reaches_the_shell_only_through_env_and_nothing_is_granted():
    assert STEP["env"]["TITLE"] == "${{ github.event.pull_request.title }}"
    assert "${{" not in STEP["run"]
    assert WF["permissions"] == {}
    assert WF["jobs"]["title"]["permissions"] == {}
    assert "uses" not in STEP  # no third-party action


def test_default_types_are_the_conventional_commits_set():
    assert DEFAULT_TYPES.split() == sorted(
        ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test"]
    )


def test_example_reruns_on_title_edits_and_grants_nothing():
    on = CALLER.get("on", CALLER.get(True))
    assert on["pull_request"]["types"] == ["opened", "edited", "synchronize", "reopened"]
    assert CALLER["permissions"] == {}
    assert CALLER["jobs"]["title"]["uses"].startswith("datacrew-space/ci-workflows/.github/workflows/pr-title.yml@")


@needs_bash
@pytest.mark.parametrize(
    "title",
    [
        "fix: keep quoted args together",
        "feat(python-ci): opt-in docs-only skip",
        "refactor!: drop the old input",
        "chore(deps)!: bump uv to 0.14",
        "docs: explain `paths-ignore` & why; not (here)",
    ],
)
def test_valid_titles_pass(title):
    done = check(title)
    assert done.returncode == 0, done.stderr


@needs_bash
@pytest.mark.parametrize(
    ("title", "message"),
    [
        ("Add a docs-only skip", "type(optional scope)!: subject"),
        ("fix:no space", "type(optional scope)!: subject"),
        ("fix:    ", "type(optional scope)!: subject"),
        ("Fix: capitalised type", "type(optional scope)!: subject"),
        ("fix(): empty scope", "type(optional scope)!: subject"),
        ("wip: not a type", "Type 'wip' is not allowed"),
        ("", "No pull request title"),
    ],
)
def test_invalid_titles_fail_and_state_the_rule(title, message):
    done = check(title)
    assert done.returncode != 0
    assert message in done.stderr


@needs_bash
def test_types_input_narrows_the_allowed_set():
    assert check("feat: x", types="fix\ndocs").returncode != 0
    assert check("docs: x", types="fix\ndocs").returncode == 0


@needs_bash
def test_an_empty_types_input_fails_instead_of_allowing_everything():
    done = check("feat: x", types="  \n")
    assert done.returncode != 0
    assert "lists no types" in done.stderr


@needs_bash
def test_title_is_matched_as_data_and_never_echoed(tmp_path):
    marker = tmp_path / "ran"
    title = f"fix: $(touch {marker.as_posix()}) `touch {marker.as_posix()}` ::warning::forged"
    done = check(title)
    assert done.returncode == 0, done.stderr
    assert not marker.exists()
    assert "forged" not in done.stdout + done.stderr

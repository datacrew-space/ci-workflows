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


RULE = "type(optional scope)!: subject"


def check(title: str, types: str = DEFAULT_TYPES) -> subprocess.CompletedProcess:
    env = {**os.environ, **{k: v for k, v in STEP["env"].items() if "${{" not in v}, "TITLE": title, "TYPES": types}
    return subprocess.run([BASH, "-c", STEP["run"]], env=env, capture_output=True, text=True)


def test_the_check_name_and_runner_are_part_of_the_interface():
    # Enforcing the check means requiring `title / title`: the caller job, then this job's name.
    job = WF["jobs"]["title"]
    assert job["name"] == "title"
    assert job["runs-on"] == "${{ fromJSON(inputs.runs-on) }}"
    assert job["timeout-minutes"] == 5
    assert STEP["env"]["LC_ALL"] == "C"
    assert CALLER["jobs"]["title"]["with"]["runs-on"] == "${{ vars.CI_RUNS_ON || '\"ubuntu-latest\"' }}"


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
        "fix(a-b_c.d/e): scope with punctuation",
    ],
)
def test_valid_titles_pass(title):
    done = check(title)
    assert done.returncode == 0, done.stderr


@needs_bash
@pytest.mark.parametrize(
    ("title", "message"),
    [
        ("Add a docs-only skip", RULE),
        ("fix:no space", RULE),
        ("fix:    ", RULE),
        ("Fix: capitalised type", RULE),
        ("fix(): empty scope", RULE),
        ("fix( ): blank scope", RULE),
        ("fix(a b): spaced scope", RULE),
        ("feat(a(b)): nested scope", RULE),
        ("fix(a)!!: two bangs", RULE),
        ("WIP fix: prefix before the type", RULE),
        ('Revert "feat: x"', RULE),
        ("Bump actions/checkout from 4 to 5", RULE),
        ("wip: not a type", "Type 'wip' is not allowed"),
        ("wip: not a type", RULE),
        ("", "No pull request title"),
    ],
)
def test_invalid_titles_fail_and_state_the_rule(title, message):
    done = check(title)
    assert done.returncode != 0
    assert "::error::" in done.stdout
    assert message in done.stdout


@needs_bash
@pytest.mark.parametrize("types", ["fix\ndocs", "fix docs", "fix, docs", "fix,docs", " fix\t,\n docs "])
def test_types_input_narrows_the_allowed_set_in_any_separator(types):
    assert check("feat: x", types=types).returncode != 0
    assert check("docs: x", types=types).returncode == 0
    assert check("fix: x", types=types).returncode == 0


@needs_bash
@pytest.mark.parametrize("types", ["fix Docs", "fix;docs", "fix ci/cd"])
def test_a_types_entry_that_is_not_lowercase_letters_fails(types):
    done = check("fix: x", types=types)
    assert done.returncode != 0
    assert "not lowercase letters" in done.stdout


@needs_bash
def test_an_empty_types_input_fails_instead_of_allowing_everything():
    done = check("feat: x", types="  \n")
    assert done.returncode != 0
    assert "lists no types" in done.stdout


@needs_bash
@pytest.mark.parametrize("prefix", ["fix: ", "Fix: ", "wip: "])
def test_title_is_matched_as_data_and_never_echoed(tmp_path, prefix):
    # Passing, malformed and unknown-type titles: none runs a command or reaches the log.
    marker = tmp_path / "ran"
    title = f"{prefix}$(touch {marker.as_posix()}) `touch {marker.as_posix()}` ::warning::forged"
    done = check(title)
    assert done.returncode == (0 if prefix == "fix: " else 1), done.stdout + done.stderr
    assert not marker.exists()
    assert "forged" not in done.stdout + done.stderr

"""The opt-in docs-only skip of the reusable Python CI workflow.

A pull request whose changed files all match `skip-paths` skips lint and test as jobs, so GitHub lists
them as skipped. The skip must never hide work: anything unclassifiable runs the jobs, and an API error
fails the run. The classify step's script is run here as written in the workflow, with a stub `gh`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYCI = yaml.safe_load((ROOT / ".github" / "workflows" / "python-ci.yml").read_text(encoding="utf-8"))
JOBS = PYCI["jobs"]
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")
RUNS_WHEN_NOT_CLASSIFIED_AS_DOCS = "needs.classify.outputs.code != 'false'"


def _inputs() -> dict:
    on = PYCI.get("on", PYCI.get(True))
    return on["workflow_call"]["inputs"]


def test_skip_paths_is_an_optional_string_that_never_skips_by_default():
    assert _inputs()["skip-paths"] == {**_inputs()["skip-paths"], "type": "string", "default": ""}


def test_classify_runs_only_on_pull_requests_that_set_skip_paths():
    assert JOBS["classify"]["if"] == "inputs.skip-paths != '' && github.event_name == 'pull_request'"
    assert JOBS["classify"]["outputs"] == {"code": "${{ steps.classify.outputs.code }}"}


def test_lint_and_test_are_skipped_as_jobs_only_when_classify_found_no_code():
    assert JOBS["lint"]["needs"] == "classify"
    assert JOBS["lint"]["if"] == "${{ !cancelled() && " + RUNS_WHEN_NOT_CLASSIFIED_AS_DOCS + " }}"
    assert JOBS["test"]["needs"] == "classify"
    assert JOBS["test"]["if"] == "${{ !cancelled() && inputs.run-test && " + RUNS_WHEN_NOT_CLASSIFIED_AS_DOCS + " }}"


@pytest.mark.parametrize("job", ["lint", "test"])
def test_no_step_skips_itself_on_the_classification(job):
    # A step that exits early would report the job as a pass of work that never ran.
    for s in JOBS[job]["steps"]:
        assert "classify" not in str(s.get("if", "")) + str(s.get("run", "")), s.get("name")


def test_classify_reads_only_and_takes_untrusted_values_through_env():
    (step,) = JOBS["classify"]["steps"]
    assert "${{" not in step["run"]
    assert step["env"]["SKIP_PATHS"] == "${{ inputs.skip-paths }}"
    assert "permissions" not in JOBS["classify"]  # inherits the workflow's contents: read
    assert PYCI["permissions"] == {"contents": "read"}


def classify(tmp_path: Path, files: list[str], patterns: str, *, count: int | None = None, fail: bool = False):
    listing = tmp_path / "listing"
    listing.write_text("\n".join([str(len(files) if count is None else count), *files]) + "\n", encoding="utf-8")
    # A shell function shadows any real gh on PATH, on every platform.
    stub = "gh() { return 1; }\n" if fail else f'gh() {{ cat "{listing.as_posix()}"; }}\n'
    out = tmp_path / "output"
    summary = tmp_path / "summary"
    env = {
        **os.environ,
        "SKIP_PATHS": patterns,
        "REPO": "o/r",
        "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    (step,) = JOBS["classify"]["steps"]
    program = stub + step["run"]
    done = subprocess.run([BASH, "-c", program], env=env, capture_output=True, text=True)
    output = out.read_text() if out.exists() else ""
    return done, output, summary.read_text() if summary.exists() else ""


@needs_bash
def test_docs_only_change_skips_and_says_why(tmp_path):
    done, output, summary = classify(tmp_path, ["README.md", "docs/guide/a.md", "img/x.png"], "*.md\nimg/*\n")
    assert done.returncode == 0, done.stderr
    assert output == "code=false\n"
    assert "Lint and test skipped: all 3 changed files match skip-paths." in summary


@needs_bash
def test_one_code_file_runs_and_names_it(tmp_path):
    done, output, summary = classify(tmp_path, ["README.md", "src/app.py"], "*.md")
    assert done.returncode == 0, done.stderr
    assert output == "code=true\n"
    assert "src/app.py matches no skip-paths pattern" in summary


@needs_bash
def test_patterns_ignore_comments_blank_lines_crlf_and_padding(tmp_path):
    done, output, _ = classify(tmp_path, ["docs/a.txt"], "# not code\r\n\r\n   docs/*   \r\n")
    assert done.returncode == 0, done.stderr
    assert output == "code=false\n"


@needs_bash
@pytest.mark.parametrize(
    ("files", "patterns", "count", "why"),
    [
        (["README.md"], "# only a comment\n", None, "skip-paths holds no patterns"),
        ([], "*.md", None, "no changed files were listed"),
        (["README.md"], "*.md", 300, "300 or more files"),
    ],
)
def test_anything_unclassifiable_runs_the_jobs(tmp_path, files, patterns, count, why):
    done, output, summary = classify(tmp_path, files, patterns, count=count)
    assert done.returncode == 0, done.stderr
    assert output == "code=true\n"
    assert why in summary


@needs_bash
def test_an_api_error_fails_the_job_and_never_skips(tmp_path):
    done, output, _ = classify(tmp_path, ["README.md"], "*.md", fail=True)
    assert done.returncode != 0
    assert "code=false" not in output


@needs_bash
def test_a_pattern_is_matched_not_executed(tmp_path):
    marker = tmp_path / "ran"
    done, output, _ = classify(tmp_path, ["a.md"], f"$(touch {marker.as_posix()})\n*.md")
    assert done.returncode == 0, done.stderr
    assert not marker.exists()
    assert output == "code=false\n"

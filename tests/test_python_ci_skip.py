"""The opt-in docs-only skip of the reusable Python CI workflow.

A pull request whose changed files all match `skip-paths` skips lint and test as jobs, so GitHub lists
them as skipped. The skip must never hide work: anything unclassifiable runs the jobs, and an API error
fails the run. The classify step's script is run here as written in the workflow, with a stub `gh`.
"""

from __future__ import annotations

import json
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
JQ = shutil.which("jq")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")
RUNS_WHEN_NOT_CLASSIFIED_AS_DOCS = "needs.classify.outputs.code != 'false'"


def _inputs() -> dict:
    on = PYCI.get("on", PYCI.get(True))
    return on["workflow_call"]["inputs"]


def test_skip_paths_is_an_optional_string_that_never_skips_by_default():
    assert _inputs()["skip-paths"] == {**_inputs()["skip-paths"], "type": "string", "default": ""}


def test_classify_runs_only_on_pull_requests_to_the_default_branch_that_set_skip_paths():
    # Other bases (a stacked pull request) always run: retargeting one reruns nothing.
    assert JOBS["classify"]["if"] == (
        "inputs.skip-paths != '' && github.event_name == 'pull_request'"
        " && github.event.pull_request.base.ref == github.event.repository.default_branch"
    )
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
    assert step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "REPO": "${{ github.repository }}",
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
        "SKIP_PATHS": "${{ inputs.skip-paths }}",
        "RUN_TEST": "${{ inputs.run-test }}",
    }
    assert "permissions" not in JOBS["classify"]  # inherits the workflow's contents: read
    assert PYCI["permissions"] == {"contents": "read"}


def classify(
    tmp_path: Path,
    files: list[str],
    patterns: str,
    *,
    count: int | None = None,
    fail: bool = False,
    compare: dict | None = None,
    run_test: str = "true",
):
    """Run the step with a stub `gh` that records its arguments.

    By default the stub prints `files` as the `--jq` expression would. With `compare`, it runs the
    step's real `--jq` expression over that compare-API response instead (needs jq).
    """
    listing = tmp_path / "listing"
    listing.write_text("\n".join([str(len(files) if count is None else count), *files]) + "\n", encoding="utf-8")
    args = tmp_path / "args"
    record = f'printf "%s\\n" "$@" > "{args.as_posix()}"'
    if fail:
        body = "return 1"
    elif compare is not None:
        fixture = tmp_path / "compare.json"
        fixture.write_text(json.dumps(compare), encoding="utf-8")
        body = f'while [ "$1" != --jq ]; do shift; done; jq -r "$2" "{fixture.as_posix()}"'
    else:
        body = f'cat "{listing.as_posix()}"'
    # A shell function shadows any real gh on PATH, on every platform.
    stub = f"gh() {{ {record}; {body}; }}\n"
    out = tmp_path / "output"
    summary = tmp_path / "summary"
    env = {
        **os.environ,
        "SKIP_PATHS": patterns,
        "REPO": "o/r",
        "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40,
        "RUN_TEST": run_test,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    (step,) = JOBS["classify"]["steps"]
    program = stub + step["run"]
    done = subprocess.run([BASH, "-c", program], env=env, capture_output=True, text=True)
    output = out.read_text() if out.exists() else ""
    classify.args = args.read_text(encoding="utf-8").splitlines() if args.exists() else []
    return done, output, summary.read_text() if summary.exists() else ""


@needs_bash
def test_docs_only_change_skips_and_says_why(tmp_path):
    done, output, summary = classify(tmp_path, ["README.md", "docs/guide/a.md", "img/x.png"], "*.md\nimg/*\n")
    assert done.returncode == 0, done.stderr
    assert output == "code=false\n"
    assert "Lint and test skipped: all 3 changed files match skip-paths (`*.md` `img/*`)." in summary
    assert classify.args[:2] == ["api", f"repos/o/r/compare/{'a' * 40}...{'b' * 40}"]
    assert classify.args[2] == "--jq"


@needs_bash
def test_with_run_test_off_the_summary_names_only_lint(tmp_path):
    _, _, summary = classify(tmp_path, ["README.md"], "*.md", run_test="false")
    assert "Lint skipped: all 1 changed files" in summary
    _, _, summary = classify(tmp_path, ["a.py"], "*.md", run_test="false")
    assert "Lint run:" in summary


@needs_bash
def test_one_code_file_runs_and_names_it(tmp_path):
    done, output, summary = classify(tmp_path, ["README.md", "src/app.py"], "*.md")
    assert done.returncode == 0, done.stderr
    assert output == "code=true\n"
    assert "`src/app.py` matches no skip-paths pattern" in summary
    # The file name is chosen by the pull request's author, so it is not printed to the log.
    assert "src/app.py" not in done.stdout + done.stderr


@needs_bash
def test_a_rename_out_of_code_counts_its_old_path(tmp_path):
    # The step lists a rename's previous path after its new one; one file, two paths.
    done, output, summary = classify(tmp_path, ["docs/util.md", "src/pkg/util.py"], "*.md\ndocs/*", count=1)
    assert done.returncode == 0, done.stderr
    assert output == "code=true\n"
    assert "`src/pkg/util.py` matches no skip-paths pattern" in summary


@needs_bash
@pytest.mark.skipif(JQ is None, reason="needs jq")
@pytest.mark.parametrize(
    ("entry", "code"),
    [
        ({"filename": "docs/util.md", "previous_filename": "src/pkg/util.py", "status": "renamed"}, "true"),
        ({"filename": "docs/b.md", "previous_filename": "docs/a.md", "status": "renamed"}, "false"),
        ({"filename": "docs/a.md", "status": "modified"}, "false"),
    ],
)
def test_the_real_jq_expression_reads_both_sides_of_a_rename(tmp_path, entry, code):
    done, output, _ = classify(tmp_path, [], "*.md\ndocs/*", compare={"files": [entry]})
    assert done.returncode == 0, done.stderr
    assert output == f"code={code}\n"


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

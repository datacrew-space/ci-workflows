"""The base-branch input (stacked PRs are skipped) and the wiring of the per-commit guard."""

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "claude-code-review.yml"


@pytest.fixture(scope="module")
def doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def jobs(doc) -> dict:
    return doc["jobs"]


def inputs(doc) -> dict:
    # PyYAML reads the key `on` as the boolean True
    return doc[True]["workflow_call"]["inputs"]


def test_the_new_inputs_exist_with_safe_defaults(doc):
    i = inputs(doc)
    assert i["base-branch"]["default"] == "main"
    assert i["protected-paths-extra"]["default"] == ""
    assert i["protected-paths-extra"]["type"] == "string"


def test_a_pr_based_on_another_branch_is_skipped(jobs):
    """The WHOLE expression: a weakened `||` or a dropped clause must fail this test."""
    cond = " ".join(str(jobs["claude-review"]["if"]).split())
    assert cond == (
        "${{ !github.event.pull_request.draft"
        " && github.event.pull_request.head.repo.full_name == github.repository"
        " && github.event.pull_request.base.ref == inputs.base-branch }}"
    )


def test_no_branch_name_is_hardcoded_in_the_workflow():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "origin/main" not in text
    assert "origin/${{ inputs.base-branch }}" in text


def test_the_reviewers_git_forms_follow_the_base_branch(jobs):
    step = next(s for s in jobs["claude-review"]["steps"] if s.get("id") == "claude-review")
    args = " ".join(step["with"]["claude_args"].split())
    for form in (
        "git diff origin/${{ inputs.base-branch }}...HEAD",
        "git diff --stat origin/${{ inputs.base-branch }}...HEAD",
    ):
        assert f"Bash({form})" in args


def test_the_verify_step_gets_the_extra_patterns_and_runs_before_the_push(jobs):
    """The per-commit and endpoint guards run inside the verifier (real bundles: test_verify_fix_bundle.py)."""
    verify = next(s for s in jobs["push"]["steps"] if s.get("id") == "verify")
    assert verify["env"]["PROTECTED_EXTRA"] == "${{ inputs.protected-paths-extra }}"
    ids = [s.get("id") for s in jobs["push"]["steps"]]
    assert ids.index("verify") < ids.index("push")
    assert "PROTECTED_EXTRA" not in str(next(s for s in jobs["push"]["steps"] if s.get("id") == "push"))


def test_the_verifier_hands_the_extra_patterns_to_both_guards():
    text = (WORKFLOW.parents[2] / "scripts" / "verify-fix-bundle.sh").read_text(encoding="utf-8")
    assert 'bash "$here/protected-paths.sh" < "$changed"' in text  # env passes through unchanged
    assert 'bash "$here/guard-commits.sh" "$clean" "$reviewed" refs/heads/fix' in text
    assert "PROTECTED_EXTRA=" not in text  # never reset or narrowed inside the script


def test_the_responder_agent_never_receives_the_extra_patterns(jobs):
    """Nothing the agent reads should help it aim at the edge of the guard; only the push step needs it."""
    for name, job in jobs.items():
        if name == "push":
            continue
        assert "PROTECTED_EXTRA" not in yaml.safe_dump(job), name


def test_base_branch_is_validated_before_it_reaches_an_agent_tool_list(jobs):
    for name in ("claude-review", "respond"):
        steps = jobs[name]["steps"]
        at = next(i for i, s in enumerate(steps) if s.get("name") == "Validate the base-branch input")
        agent = next(i for i, s in enumerate(steps) if "claude-code-action" in str(s.get("uses", "")))
        assert at < agent, name
        assert steps[at]["env"]["BASE"] == "${{ inputs.base-branch }}"
        assert "A-Za-z0-9._/-" in steps[at]["run"] and "exit 1" in steps[at]["run"]


def test_the_agents_are_told_the_base_branch(jobs):
    for job, step_id in (("claude-review", "claude-review"), ("respond", "respond-agent")):
        step = next(s for s in jobs[job]["steps"] if s.get("id") == step_id)
        assert "origin/${{ inputs.base-branch }}" in step["with"]["prompt"], job

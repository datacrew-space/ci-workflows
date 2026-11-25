"""The review report parses and counts the class tag on each finding.

A finding is a list item that starts with `[mechanizable: <check>]`, `[standards-gap]` or
`[judgment]`. A mechanizable finding that does not name its check is counted apart, so the
follow-through (write the guard) stays visible.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "count-finding-tags.sh"
AGENT = Path(__file__).resolve().parents[1] / "examples" / "code-reviewer.md"
PR_TEMPLATE = Path(__file__).resolve().parents[1] / ".github" / "pull_request_template.md"


def count(tmp_path: Path, review: str) -> dict[str, int]:
    f = tmp_path / "review.md"
    f.write_text(review)
    out = subprocess.run(["bash", str(SCRIPT), str(f)], capture_output=True, text=True, check=True).stdout
    return {k: int(v) for k, v in (pair.split("=") for pair in out.split())}


def test_tagged_findings_are_counted_per_class(tmp_path):
    review = """## Standards
- [mechanizable: no-print-in-library] `a.py:3` uses print().
- [mechanizable: ruff T201] `b.py:9` uses print().
- [standards-gap] `README.md` states no rule on retries.
* [judgment] The retry loop could live in the client.
1. [Judgment] Naming is unclear.

Summary: changes requested.
"""
    assert count(tmp_path, review) == {
        "mechanizable": 2,
        "standards-gap": 1,
        "judgment": 2,
        "unnamed-check": 0,
        "unrecognized": 0,
    }


def test_mechanizable_without_a_check_name_is_counted_apart(tmp_path):
    got = count(tmp_path, "- [mechanizable] a.py:1 trailing space.\n- [mechanizable: ] a.py:2 tab.\n")
    assert got["mechanizable"] == 2 and got["unnamed-check"] == 2


def test_unknown_class_is_unrecognized_not_counted(tmp_path):
    got = count(tmp_path, "- [nitpick] a.py:1 style.\n")
    assert got["unrecognized"] == 1 and got["mechanizable"] == got["judgment"] == 0


def test_untagged_review_counts_zero_everywhere(tmp_path):
    """Negative case: an old-style review with no tags must not invent a class."""
    review = "## Standards\n- `a.py:3` uses print().\n- Missing test for the retry path.\n\nNo [tags] here.\n"
    assert set(count(tmp_path, review).values()) == {0}


def test_empty_review_counts_zero(tmp_path):
    assert set(count(tmp_path, "").values()) == {0}


def test_missing_file_fails(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT), str(tmp_path / "nope.md")], capture_output=True, text=True)
    assert r.returncode != 0


def test_reviewer_prompt_asks_for_the_tags_and_the_check_name():
    text = AGENT.read_text()
    for needle in ("[mechanizable: ", "[standards-gap]", "[judgment]", "check that would have caught"):
        assert needle in text


def test_pr_template_asks_for_the_guard_follow_through():
    text = PR_TEMPLATE.read_text()
    assert "guard" in text and "standards" in text and "[mechanizable" in text

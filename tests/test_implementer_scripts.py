"""The two decisions the publish job makes about untrusted agent output, run on awkward input.

`protected-paths.sh` says whether a branch touches a file that grades the agent. It reads paths
separated by NUL (`git diff -z`), because git QUOTES a non-ASCII path in its default output
(`".claude/\\303\\251.sh"`), and a regex anchored on `^\\.claude/` then misses it. `sanitize-pr-text.sh`
makes the agent's PR text safe to publish: it can close no issue and notify nobody.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PROTECTED = SCRIPTS / "implementer-protected-paths.sh"
SANITIZE = SCRIPTS / "sanitize-pr-text.sh"


def run(script: Path, data: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["bash", str(script)], input=data, capture_output=True, check=False)


def nul(*paths: str) -> bytes:
    return b"".join(p.encode("utf-8") + b"\0" for p in paths)


# --- protected-paths.sh ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".agents/agents/ticket-implementer.md",
        ".agents/skills/code-review/SKILL.md",
        ".claude/settings.json",
        "scripts/check_ci_parity.py",
        "AGENTS.md",
        "CLAUDE.md",
        ".pre-commit-config.yaml",
        "ruff.toml",
        ".secrets.baseline",
    ],
)
def test_a_file_that_grades_the_agent_is_refused_and_named(path):
    result = run(PROTECTED, nul("src/ok.py", path))
    assert result.returncode == 1
    assert result.stdout.decode().strip() == path


@pytest.mark.parametrize(
    "path",
    [
        "src/mypackage/forecast.py",
        "tests/test_forecast.py",
        "docs/agents/note.md",
        "docs/decisions/0005-example.md",
        "CONTEXT.md",
        "pyproject.toml",
        "uv.lock",
        ".github-notes/readme.md",
        "scripts/checker.py",
        "docs/AGENTS.md",
    ],
)
def test_ordinary_files_pass(path):
    result = run(PROTECTED, nul(path))
    assert (result.returncode, result.stdout) == (0, b"")


def test_nothing_changed_passes():
    assert run(PROTECTED, b"").returncode == 0


def test_a_non_ascii_protected_path_is_still_refused():
    """The review's hard finding: git prints `".claude/\\303\\251.sh"`, which `^\\.claude/` never matched."""
    result = run(PROTECTED, nul(".claude/é.sh"))
    assert result.returncode == 1


@pytest.mark.parametrize(
    "path",
    [
        '.agents/agents/a "quoted".md',
        ".github/work flows/x.yml",
        ".github/a\nb.yml",
        ".claude/tab\there",
        ".github/back\\slash",
    ],
)
def test_awkward_characters_in_a_protected_path_do_not_hide_it(path):
    assert run(PROTECTED, nul(path)).returncode == 1


def test_a_newline_cannot_forge_a_protected_path_in_an_ordinary_one():
    """One record is one path: `docs/a\\n.github/x` is a file under docs/, not a path under .github/."""
    assert run(PROTECTED, nul("docs/a\n.github/x")).returncode == 0


def test_the_check_is_case_insensitive_so_a_case_folding_checkout_cannot_hide_a_file():
    for path in (".GitHub/Workflows/ci.yml", "agents.md", "AGENTS.MD", ".Agents/Agents/x.md"):
        assert run(PROTECTED, nul(path)).returncode == 1, path


def test_a_real_git_diff_of_a_non_ascii_name_is_refused(tmp_path):
    """End to end with git itself: the exact command the workflow runs, on the review's own example."""

    # Under `git commit` the pre-commit hook inherits GIT_INDEX_FILE, GIT_DIR and friends, which would
    # point this scratch repository's git at the OUTER repository's index (exit 128). Scrub them.
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, check=True, env=clean_env)

    git("init", "-q")
    # Guard: never write config unless git is really working inside the scratch directory. An ambient
    # GIT_DIR once made `git config user.name t` rewrite the real repository's config.
    toplevel = git("rev-parse", "--show-toplevel").stdout.decode().strip()
    assert Path(toplevel).resolve() == tmp_path.resolve(), f"git is not operating on the scratch repo: {toplevel}"
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("branch", "base")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "é.sh").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "agent")
    diff = git("diff", "-z", "--no-renames", "--name-only", "base...HEAD").stdout
    assert b"\xc3\xa9" in diff
    assert run(PROTECTED, diff).returncode == 1


# --- sanitize-pr-text.sh -----------------------------------------------------------------------


def sanitize(text: str) -> str:
    result = run(SANITIZE, text.encode("utf-8"))
    assert result.returncode == 0
    return result.stdout.decode("utf-8")


@pytest.mark.parametrize(
    "line",
    [
        "Closes #12",
        "fixes #3 as well",
        "Resolved #4",
        "this FIXES owner/repo#5",
        "Resolves https://github.com/octocat/hello-world/issues/9",
        "closed https://github.com/o/r/pull/77",
    ],
)
def test_a_line_that_would_close_an_issue_or_pr_is_dropped(line):
    assert sanitize(f"keep me\n{line}\nand me\n") == "keep me\nand me\n"


def test_ordinary_references_and_words_survive():
    text = "See #12 for context.\nClosed the loop on the fixture.\nA fix for the timer.\n"
    assert sanitize(text) == text


def test_an_at_name_is_wrapped_so_it_notifies_nobody():
    assert sanitize("ping @octocat and @org/team\n") == "ping `@octocat` and `@org/team`\n"


def test_an_email_and_an_already_quoted_name_are_left_alone():
    text = "mail a@b.com or `@already`\n"
    assert sanitize(text) == text


def test_empty_text_stays_empty():
    assert sanitize("") == ""


def test_extra_protected_paths_from_the_environment_are_refused_too():
    env = {**os.environ, "EXTRA_PROTECTED_PATHS": "prompts/* docs/policy.md"}
    data = nul("src/ok.py", "Prompts/implementer.md", "docs/policy.md")
    result = subprocess.run(["bash", str(PROTECTED)], input=data, capture_output=True, env=env, check=False)
    assert result.returncode == 1
    assert result.stdout.decode().split() == ["Prompts/implementer.md", "docs/policy.md"]


def test_without_extra_paths_the_same_files_pass():
    assert run(PROTECTED, nul("prompts/implementer.md", "docs/policy.md")).returncode == 0

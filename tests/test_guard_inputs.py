"""PROTECTED_EXTRA (caller-supplied extra protection) and guard-commits.sh (per-commit judgement)."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PROTECTED_PATHS = SCRIPTS / "protected-paths.sh"
GUARD_COMMITS = SCRIPTS / "guard-commits.sh"


def judge(*paths: str, extra: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PROTECTED_EXTRA"}
    if extra is not None:
        env["PROTECTED_EXTRA"] = extra
    data = b"".join(p.encode() + b"\0" for p in paths)
    return subprocess.run(["bash", str(PROTECTED_PATHS)], input=data, capture_output=True, env=env, check=False)


# An example: auth modules inside an otherwise allowed source tree, generated files.
EXTRA = """
# auth modules
*auth*.py

  apps/*/src/manifests/*  
*/generated/*
"""


@pytest.mark.parametrize(
    "path",
    [
        "apps/web/src/pkg/auth_service.py",
        "apps/web/src/pkg/WS_AUTH.PY",  # case-folding
        "apps/web/tests/test_ws_auth.py",
        "apps/bot/src/manifests/app.json",  # the pattern line has surrounding blanks: trimmed
        "apps/web/src/generated/manifest.py",
    ],
)
def test_an_extra_pattern_protects_a_path_inside_an_allowed_directory(path):
    assert judge(path).returncode == 0  # allowed without the extra patterns
    result = judge(path, extra=EXTRA)
    assert result.returncode == 1
    assert path.encode() in result.stdout.splitlines()


def test_a_path_matching_no_extra_pattern_is_unaffected():
    assert judge("apps/web/src/pkg/main.py", extra=EXTRA).returncode == 0


@pytest.mark.parametrize("extra", ["", "\n\n", "# only a comment\n", "   \r\n"])
def test_empty_or_comment_only_extra_changes_nothing(extra):
    assert judge("apps/web/src/pkg/main.py", extra=extra).returncode == 0
    assert judge("deploy.sh", extra=extra).returncode == 1


def test_extra_patterns_can_only_add_protection():
    """No pattern can turn a protected path into an allowed one, including a catch-all."""
    for extra in ("*", "docs/*", "deploy.sh", "!deploy.sh", "apps/*"):
        assert judge("deploy.sh", extra=extra).returncode == 1
        assert judge(".github/workflows/ci.yml", extra=extra).returncode == 1


def test_a_catch_all_extra_refuses_everything():
    assert judge("docs/x.md", extra="*").returncode == 1


# ---- guard-commits.sh -------------------------------------------------------------------------


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "PROTECTED_EXTRA"}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return env


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        text=True,
        check=True,
        env=_env(),
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "deploy.sh").write_text("good\n")
    (tmp_path / "apps/gw/src").mkdir(parents=True)
    (tmp_path / "apps/gw/src/a.py").write_text("a = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def commit(repo: Path, files: dict[str, str], msg: str) -> None:
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def run_guard(repo: Path, base: str, extra: str | None = None) -> subprocess.CompletedProcess:
    env = _env()
    if extra is not None:
        env["PROTECTED_EXTRA"] = extra
    return subprocess.run(
        ["bash", str(GUARD_COMMITS), str(repo), base, "HEAD"], capture_output=True, text=True, env=env, check=False
    )


def test_clean_linear_fixes_pass(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"apps/gw/src/a.py": "a = 2\n"}, "fix 1")
    commit(repo, {"apps/gw/src/b.py": "b = 1\n"}, "fix 2")
    assert run_guard(repo, base).returncode == 0


def test_no_commits_is_a_pass(repo):
    assert run_guard(repo, git(repo, "rev-parse", "HEAD")).returncode == 0


def test_an_intermediate_protected_change_that_is_reverted_is_refused(repo):
    """The end-to-end diff is clean, but the evil commit would land in history on a merge or rebase."""
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"deploy.sh": "evil\n"}, "c1: change a protected file")
    commit(repo, {"deploy.sh": "good\n", "apps/gw/src/a.py": "a = 2\n"}, "c2: put it back, fix something")
    # the endpoint diff alone would pass:
    net = subprocess.run(
        ["git", "-C", str(repo), "diff", "-z", "--name-only", base, "HEAD"], capture_output=True, check=True, env=_env()
    ).stdout
    assert net == b"apps/gw/src/a.py\0"
    result = run_guard(repo, base)
    assert result.returncode == 1
    assert "touches protected files" in result.stderr
    assert b"deploy.sh" in result.stdout.encode()


def test_a_merge_commit_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "-c", "side")
    commit(repo, {"apps/gw/src/side.py": "s = 1\n"}, "side")
    git(repo, "switch", "-q", "main")
    commit(repo, {"apps/gw/src/main.py": "m = 1\n"}, "main work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    result = run_guard(repo, base)
    assert result.returncode == 1
    assert "merge" in result.stderr


def test_extra_patterns_apply_to_every_commit(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"apps/gw/src/auth_x.py": "x = 1\n"}, "touches an auth module")
    assert run_guard(repo, base).returncode == 0
    assert run_guard(repo, base, extra="*auth*.py").returncode == 1


def test_an_unknown_base_fails_closed(repo):
    assert run_guard(repo, "0" * 40).returncode == 1


# ---- review follow-ups ------------------------------------------------------------------------


def test_a_trailing_slash_means_everything_under_the_directory():
    assert judge("apps/web/src/gen/x.py").returncode == 0
    assert judge("apps/web/src/gen/x.py", extra="apps/web/src/gen/").returncode == 1
    assert judge("apps/web/src/other/x.py", extra="apps/web/src/gen/").returncode == 0


def test_a_quoted_pattern_is_taken_literally_and_protects_nothing_extra():
    """Documented: a malformed pattern is not an error and not a fail-closed; the default guard still holds."""
    assert judge("apps/web/src/auth.py", extra='"*auth*.py"').returncode == 0
    assert judge("deploy.sh", extra='"*auth*.py"').returncode == 1


def test_a_symlink_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    # built in the index so the test also runs where creating a symlink needs a privilege
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="../../../deploy.sh",
        capture_output=True,
        text=True,
        check=True,
        env=_env(),
    ).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},apps/gw/src/link.py")
    git(repo, "commit", "-q", "-m", "add a symlink at an allowed path")
    result = run_guard(repo, base)
    assert result.returncode == 1
    assert "symlink" in result.stderr


def test_a_submodule_gitlink_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", "160000", base, "apps/gw/src/sub")
    git(repo, "commit", "-q", "-m", "add a gitlink at an allowed path")
    result = run_guard(repo, base)
    assert result.returncode == 1
    assert "submodule" in result.stderr


def test_a_root_commit_in_the_range_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--orphan", "other")
    git(repo, "rm", "-rq", "--cached", ".")
    commit(repo, {"apps/gw/src/z.py": "z = 1\n"}, "an unrelated root commit")
    assert run_guard(repo, base).returncode == 1


def test_a_missing_sibling_guard_fails_with_a_clear_message(tmp_path):
    lone = tmp_path / "guard-commits.sh"
    lone.write_text(GUARD_COMMITS.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        ["bash", str(lone), str(tmp_path), "a", "b"], capture_output=True, text=True, env=_env(), check=False
    )
    assert result.returncode == 1
    assert "tooling-ref" in result.stderr

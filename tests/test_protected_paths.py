"""The responder may never push a change to a file that grades or deploys the stack.

protected-paths.sh judges a NUL-separated path list from `git diff -z --no-renames --name-only`.
These tests drive it with real git diffs, so a rename out of a protected path, a non-ASCII name
and a case-folded name are exercised the way the push step sees them.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "protected-paths.sh"


def judge(*paths: str) -> subprocess.CompletedProcess:
    data = b"".join(p.encode() + b"\0" for p in paths)
    return subprocess.run(["bash", str(SCRIPT)], input=data, capture_output=True)


PROTECTED = [
    ".github/workflows/ci.yml",
    ".github/workflows/claude-code-review.yml",
    ".github/scripts/protected-paths.sh",
    ".agents/agents/code-reviewer.md",
    ".agents/agents/review-responder.md",
    "deploy.sh",
    "apps/runner/deploy.sh",
    "docker-compose.yml",
    "apps/gateway/docker-compose.yml",
    "apps/worker/docker-compose.override.yml",
    "stack.override.yml",
    "settings.env",
    "apps/proxy/config.yaml",
    "AGENTS.md",
    ".pre-commit-config.yaml",
    ".GitHub/workflows/ci.yml",  # case-folding checkout
    "Deploy.SH",
    # review finding C: these exited 0 under the first, denylist version
    "apps/web/build-and-deploy.sh",
    "apps/web/deploy_task.py",
    "apps/web/verify-image.sh",
    "apps/runner/busy.sh",
    "scripts/lib/bootstrap-env.sh",
    "scripts/with-token",
    "scripts/git-credential-helper",
    "scripts/jobs/start",
    "scripts/jobs",
    ".gitguardian.yaml",
    "apps/runner/runner.service",
    "apps/watchdog/watchdog.service",
    "apps/worker/Dockerfile",
    "apps/web/overlays/Dockerfile",
    "apps/media/auth/Dockerfile",
    "apps/gateway/AGENTS.md",
    "apps/web/AGENTS.md",
    "CLAUDE.md",
    "config.yaml",
    "apps/gateway/config.yaml",
    "apps/agent/profiles/default/config.yaml",
    "apps/proxy/config.yml",
    "apps/proxy/compose.yaml",
    "apps/proxy/compose.yml",
    "apps/photos/docker-compose.yaml",
    "tests/review_loop/test_protected_paths.py",  # the responder cannot weaken the guard
    "tests/review_loop/test_review_workflow.py",
    "tests/review_loop/conftest.py",
    # default deny: anywhere not on the allowlist
    "apps/gateway/app.py",
    "services/manager.py",
    "scripts/check_domain.py",
    "setup/network.sh",
    "apps/gateway/src/gateway/deploy.sh",  # allowed directory, executable name
    "apps/gateway/src/gateway/Dockerfile",
    "apps/gateway/src/../../../deploy.sh",  # no `..` hops
    "docs/../.github/workflows/ci.yml",
    "/etc/passwd",
    ".git/hooks/pre-commit",
    ".GIT/config",
    "docs//x.md",
    # review of #509: `*` crosses `/`, so apps/*/src/* admitted auth and secrets code at any depth
    "apps/web/overlays/server/src/auth/edge-auth.ts",
    "apps/web/overlays/server/src/secrets/secret-provider.ts",
    "apps/web/overlays/ui/src/pages/Secrets.tsx",
    "apps/media/auth/tests/test_auth.py",
    "apps/anything/nested/src/x.py",
    "apps/anything/nested/readme.md",
    # dependency and test-runner manifests: a one-line change runs attacker code in CI
    "apps/web/tests/package.json",
    "apps/web/tests/pnpm-lock.yaml",
    "apps/gateway/tests/requirements-test.txt",
    "apps/gateway/src/uv.lock",
    "apps/gateway/tests/pytest.ini",
    "apps/gateway/tests/setup.cfg",
    "apps/web/tests/vitest.config.ts",
]

ALLOWED = [
    "apps/gateway/src/gateway/main.py",
    "apps/gateway/tests/test_health.py",
    "tests/gateway/test_health.py",
    "apps/gateway/src/gateway/Main.PY",
    "apps/proxy/README.md",
    "docs/infrastructure/notes.md",
    ".agents/adrs/0005-notes.md",
]


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_path_is_refused_and_named(path):
    result = judge("apps/gateway/src/ok.py", path)
    assert result.returncode == 1
    assert path.encode() in result.stdout.splitlines()


@pytest.mark.parametrize("path", ALLOWED)
def test_ordinary_path_passes(path):
    assert judge(path).returncode == 0


def test_empty_list_is_a_pass_so_the_caller_must_check_git_diff_itself():
    assert judge().returncode == 0


def test_newline_in_a_name_cannot_hide_a_protected_path():
    assert judge("docs/x\n.github/workflows/ci.yml").returncode == 0  # one odd file, not a protected one
    assert judge("docs/x\n", ".github/workflows/ci.yml").returncode == 1


def _env() -> dict[str, str]:
    # pre-commit runs tests inside hooks; inherited GIT_* would aim `git -C <tmp>` at the real repo.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "core.quotePath=true",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=_env(),
    ).stdout.strip()


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (
            ["mv", "deploy.sh", "docs/deploy-moved.sh"],
            True,
        ),  # moved out of the protected name
        (["mv", ".github/workflows/gate.yml", "docs/gate.yml"], True),
        (["rm", "settings.env"], True),
        (["mv", "apps/proxy/config.yaml", "docs/swap.yaml"], True),
        (["mv", ".agents/agents/code-reviewer.md", "notes/reviewer.md"], True),
        (["mv", "apps/web/build-and-deploy.sh", "docs/b.txt"], True),
        (["mv", "tests/review_loop/test_protected_paths.py", "docs/t.txt"], True),
        (
            ["mv", "apps/gateway/src/a.py", "apps/gateway/src/b.py"],
            False,
        ),  # an ordinary rename
    ],
)
def test_a_push_touching_a_protected_file_is_refused(tmp_path, change, refused):
    """The same pipeline the push step runs: git diff -z --no-renames --name-only | protected-paths.sh."""
    repo = tmp_path / "clean"
    repo.mkdir()
    _git(repo, "init", "-q")
    files = (
        "deploy.sh",
        "settings.env",
        "apps/proxy/config.yaml",
        ".github/workflows/gate.yml",
        ".agents/agents/code-reviewer.md",
        "apps/gateway/src/a.py",
        "apps/web/build-and-deploy.sh",
        "tests/review_loop/test_protected_paths.py",
    )
    for path in files:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(f"{path}\n" * 20, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "reviewed")
    reviewed = _git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    (repo / "notes").mkdir()
    _git(repo, *change)
    _git(repo, "commit", "-q", "-am", "agent change")
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "-z",
            "--no-renames",
            "--name-only",
            f"{reviewed}...HEAD",
        ],
        capture_output=True,
        check=True,
        env=_env(),
    ).stdout
    result = subprocess.run(["bash", str(SCRIPT)], input=diff, capture_output=True)
    assert (result.returncode == 1) is refused, result.stdout


def test_every_tracked_file_the_review_named_is_protected_in_the_real_tree():
    """Walk the real checkout: every deploy/run script, unit, Dockerfile, compose and config is refused."""
    root = SCRIPT.parents[1]
    tracked = (
        subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
            env=_env(),
        )
        .stdout.decode()
        .split("\0")
    )
    risky = [
        p
        for p in tracked
        if p
        and (
            p.endswith((".service", ".timer", ".sh"))
            or p.rsplit("/", 1)[-1].startswith(("Dockerfile", "docker-compose", "compose."))
            or p.endswith(("config.yaml", "config.yml", "AGENTS.md"))
        )
    ]
    assert risky
    unprotected = [p for p in risky if judge(p).returncode == 0]
    assert unprotected == []


def test_non_ascii_path_is_not_quoted_past_the_pattern(tmp_path):
    """git would print `".github/\303\251.yml"` in text mode; -z keeps the raw path."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "seed").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / ".github").mkdir()
    (repo / ".github" / "é.yml").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "b")
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "-z",
            "--no-renames",
            "--name-only",
            f"{base}...HEAD",
        ],
        capture_output=True,
        check=True,
        env=_env(),
    ).stdout
    assert subprocess.run(["bash", str(SCRIPT)], input=diff, capture_output=True).returncode == 1

"""scripts/promote-deploy.sh, driven with REAL git repositories (a bare origin and a work clone).

Every refusal test also pins the reason text and checks that the remote did not change, so a test
cannot pass because some other check fired or because nothing was pushed for the wrong reason.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "promote-deploy.sh"
IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com",
}


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PROMOTE_", "DEPLOY_", "MAIN_", "TAG_"))}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, **IDENTITY)
    env.update(extra)
    return env


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False, env=_env())
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr.decode()}"
    return proc.stdout.decode().strip()


def commit(work: Path, name: str) -> str:
    (work / name).write_text(name)
    git(work, "add", name)
    git(work, "commit", "-q", "-m", name)
    return git(work, "rev-parse", "HEAD")


class Repo:
    def __init__(self, root: Path):
        self.origin = root / "origin.git"
        self.work = root / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True, env=_env())
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.work)], check=True, env=_env(), capture_output=True)
        git(self.work, "checkout", "-q", "-b", "main")
        self.c1 = commit(self.work, "one")
        self.c2 = commit(self.work, "two")
        self.c3 = commit(self.work, "three")
        git(self.work, "push", "-q", "origin", "main")

    def remote_ref(self, ref: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", "--verify", "-q", ref],
            capture_output=True,
            env=_env(),
            check=False,
        )
        return proc.stdout.decode().strip()

    def remote_refs(self) -> str:
        return git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")

    def promote(self, sha: str, out: Path | None = None, **env: str) -> subprocess.CompletedProcess:
        extra = {"PROMOTE_SHA": sha, "PROMOTE_DATE": "2026-11-13", **env}
        if out is not None:
            extra["GITHUB_OUTPUT"] = str(out)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.work,
            capture_output=True,
            text=True,
            env=_env(**extra),
            check=False,
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_first_promotion_creates_the_deploy_branch_and_an_immutable_tag(repo: Repo, tmp_path: Path):
    out = tmp_path / "out"
    r = repo.promote(repo.c2, out)
    assert r.returncode == 0, r.stderr
    assert repo.remote_ref("refs/heads/deploy") == repo.c2
    tag = f"deploy/2026-11-13-{repo.c2[:7]}"
    assert repo.remote_ref(f"refs/tags/{tag}") == repo.c2
    assert outputs(out) == {"result": "promoted", "sha": repo.c2, "previous": "", "tag": tag}


def test_a_later_promotion_fast_forwards_and_reports_the_previous_sha(repo: Repo, tmp_path: Path):
    assert repo.promote(repo.c1).returncode == 0
    out = tmp_path / "out"
    r = repo.promote(repo.c3, out)
    assert r.returncode == 0, r.stderr
    assert repo.remote_ref("refs/heads/deploy") == repo.c3
    got = outputs(out)
    assert got["result"] == "promoted"
    assert got["previous"] == repo.c1
    assert repo.remote_ref(f"refs/tags/deploy/2026-11-13-{repo.c1[:7]}") == repo.c1  # old tag kept


def test_refuses_a_sha_that_is_not_on_main(repo: Repo):
    git(repo.work, "checkout", "-q", "-b", "side")
    side = commit(repo.work, "side")
    git(repo.work, "push", "-q", "origin", "side")
    before = repo.remote_refs()
    r = repo.promote(side)
    assert r.returncode == 3
    assert "not on main" in r.stderr
    assert repo.remote_refs() == before


def test_refuses_when_deploy_has_diverged_from_the_sha(repo: Repo):
    git(repo.work, "checkout", "-q", "-b", "other", repo.c1)
    other = commit(repo.work, "other")
    git(repo.work, "push", "-q", "origin", f"{other}:refs/heads/deploy")
    before = repo.remote_refs()
    r = repo.promote(repo.c3)
    assert r.returncode == 4
    assert "not a fast-forward" in r.stderr
    assert repo.remote_refs() == before


def test_promoting_the_same_sha_twice_is_a_no_op(repo: Repo, tmp_path: Path):
    assert repo.promote(repo.c2).returncode == 0
    before = repo.remote_refs()
    out = tmp_path / "out"
    r = repo.promote(repo.c2, out)
    assert r.returncode == 0, r.stderr
    assert outputs(out)["result"] == "noop"
    assert repo.remote_refs() == before


def test_an_older_sha_never_moves_deploy_backwards(repo: Repo, tmp_path: Path):
    assert repo.promote(repo.c3).returncode == 0
    before = repo.remote_refs()
    out = tmp_path / "out"
    r = repo.promote(repo.c1, out)
    assert r.returncode == 0, r.stderr
    assert outputs(out)["result"] == "noop"
    assert repo.remote_ref("refs/heads/deploy") == repo.c3
    assert repo.remote_refs() == before


def test_refuses_to_move_an_existing_tag_to_a_different_commit(repo: Repo):
    tag = f"deploy/2026-11-13-{repo.c3[:7]}"
    git(repo.work, "push", "-q", "origin", f"{repo.c1}:refs/tags/{tag}")
    before = repo.remote_refs()
    r = repo.promote(repo.c3)
    assert r.returncode == 5
    assert "already exists" in r.stderr
    assert repo.remote_refs() == before


def test_retry_after_a_partial_run_finishes_the_promotion(repo: Repo):
    tag = f"deploy/2026-11-13-{repo.c3[:7]}"
    git(repo.work, "push", "-q", "origin", f"{repo.c3}:refs/tags/{tag}")  # tag landed, branch did not
    r = repo.promote(repo.c3)
    assert r.returncode == 0, r.stderr
    assert repo.remote_ref("refs/heads/deploy") == repo.c3
    assert repo.remote_ref(f"refs/tags/{tag}") == repo.c3


def test_dry_run_pushes_nothing(repo: Repo):
    before = repo.remote_refs()
    r = repo.promote(repo.c3, DRY_RUN="1")
    assert r.returncode == 0, r.stderr
    assert "dry run" in r.stdout
    assert repo.remote_refs() == before


def test_uses_a_custom_branch_and_tag_prefix(repo: Repo):
    r = repo.promote(repo.c3, DEPLOY_BRANCH="live", TAG_PREFIX="release/")
    assert r.returncode == 0, r.stderr
    assert repo.remote_ref("refs/heads/live") == repo.c3
    assert repo.remote_ref(f"refs/tags/release/2026-11-13-{repo.c3[:7]}") == repo.c3
    assert repo.remote_ref("refs/heads/deploy") == ""


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"DEPLOY_BRANCH": "main"}, "must differ"),
        ({"DEPLOY_BRANCH": "bad branch"}, "not a valid branch name"),
        ({"TAG_PREFIX": "bad..prefix/"}, "not a valid tag"),
        ({"PROMOTE_DATE": "yesterday"}, "PROMOTE_DATE"),
    ],
)
def test_refuses_bad_configuration_before_touching_the_remote(repo: Repo, env: dict[str, str], reason: str):
    before = repo.remote_refs()
    r = repo.promote(repo.c3, **env)
    assert r.returncode == 2
    assert reason in r.stderr
    assert repo.remote_refs() == before


def test_refuses_an_unknown_sha(repo: Repo):
    r = repo.promote("0" * 40)
    assert r.returncode == 2
    assert "PROMOTE_SHA" in r.stderr

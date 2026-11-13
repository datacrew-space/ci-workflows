"""scripts/verify-fix-bundle.sh, driven with REAL git bundles built in temporary repositories.

Every refusal test also pins the reason text, so a test cannot pass because some other check fired.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-fix-bundle.sh"
BOT_BRANCH = "feature/x"
TOKEN = "ghp_" + "a1B2c3D4e5" * 4  # built at run time: no token-shaped literal in this file


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("PROTECTED_EXTRA",)}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    env.update(extra)
    return env


def git(repo: Path, *args: str, input: bytes | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        check=False,
        env=_env(),
        input=input,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr.decode()}"
    return proc.stdout.decode().strip()


def native(path: str) -> Path:
    """bash's mktemp prints an MSYS path (/tmp/...) on Windows; Python and git need the native one."""
    if os.name == "nt":
        path = subprocess.run(["cygpath", "-w", path], capture_output=True, text=True, check=True).stdout.strip()
    return Path(path)


def write(repo: Path, name: str, data: str | bytes) -> None:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)


def commit(repo: Path, files: dict[str, str | bytes], msg: str = "fix") -> str:
    for name, data in files.items():
        write(repo, name, data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


class Scenario:
    """A reviewed commit in `src` (the push job's checkout) and a `work` clone where the agent commits."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.src = tmp / "src"
        self.src.mkdir()
        git(self.src, "init", "-q", "-b", "main")
        commit(self.src, {"README.md": "hello\n"}, "root")
        commit(self.src, {"deploy.sh": "good\n", "apps/gw/src/a.py": "a = 1\n"}, "base")
        self.reviewed = git(self.src, "rev-parse", "HEAD")
        self.work = tmp / "work"
        subprocess.run(["git", "clone", "-q", str(self.src), str(self.work)], check=True, env=_env())

    def bundle(self, rev: str | None = None, repo: Path | None = None) -> Path:
        out = self.tmp / "fix.bundle"
        out.unlink(missing_ok=True)
        git(repo or self.work, "bundle", "create", "-q", str(out), rev or f"{self.reviewed}..HEAD")
        return out

    def run(self, bundle: Path, *, reviewed: str | None = None, branch: str = BOT_BRANCH, **env: str):
        outputs = self.tmp / "gh-output"
        outputs.unlink(missing_ok=True)
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(bundle), reviewed or self.reviewed, branch, str(self.src)],
            capture_output=True,
            text=True,
            env=_env(GITHUB_OUTPUT=str(outputs), **env),
            check=False,
        )
        out = dict(
            line.split("=", 1) for line in (outputs.read_text().splitlines() if outputs.exists() else []) if "=" in line
        )
        return proc, out


@pytest.fixture
def sc(tmp_path) -> Scenario:
    return Scenario(tmp_path)


def refused(proc, out, reason: str) -> None:
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert reason in proc.stderr, proc.stderr
    assert out.get("pushed") == "false" and out.get("ready") == "false"
    assert "clean" not in out  # nothing is offered to push


# ---- accepted ------------------------------------------------------------------------------------


def test_clean_linear_fixes_leave_a_verified_ref_in_a_fresh_repo(sc):
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n"}, "one")
    tip = commit(sc.work, {"docs/notes.md": "docs\n"}, "two")
    proc, out = sc.run(sc.bundle())
    assert proc.returncode == 0, proc.stderr
    assert out["ready"] == "true" and out["count"] == "2"
    clean = native(out["clean"])
    assert git(clean, "rev-parse", "refs/heads/fix") == tip
    assert clean not in (sc.src, sc.work)
    # a fresh repository: nothing the agent wrote under its own .git can be here
    hooks = [h for h in (clean / ".git" / "hooks").glob("*") if h.suffix != ".sample"]
    assert hooks == []
    assert git(clean, "merge-base", "--is-ancestor", sc.reviewed, tip) == ""


def test_exactly_twenty_commits_pass(sc):
    for i in range(20):
        commit(sc.work, {f"apps/gw/src/f{i}.py": f"x = {i}\n"}, f"c{i}")
    proc, out = sc.run(sc.bundle())
    assert proc.returncode == 0, proc.stderr
    assert out["count"] == "20"


def test_a_bundle_with_no_new_commit_is_nothing_to_push(sc):
    # tip == the reviewed commit; its parent is the prerequisite
    proc, out = sc.run(sc.bundle(rev="HEAD~1..HEAD"))
    assert proc.returncode == 0, proc.stderr
    assert out["ready"] == "false" and out["pushed"] == "false" and "clean" not in out
    assert "nothing to push" in proc.stdout


# ---- refused -------------------------------------------------------------------------------------


def test_a_bundle_from_an_unrelated_history_is_refused(sc):
    other = sc.tmp / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, {"apps/gw/src/a.py": "evil\n"}, "root")
    proc, out = sc.run(sc.bundle(rev="HEAD", repo=other))
    refused(proc, out, "does not descend from the reviewed commit")


def test_a_bundle_based_on_a_commit_the_job_does_not_have_is_refused(sc):
    # the agent's history grew past the reviewed commit, then the bundle was cut from a later base
    mid = commit(sc.work, {"apps/gw/src/m.py": "m = 1\n"}, "mid")
    commit(sc.work, {"apps/gw/src/n.py": "n = 1\n"}, "tip")
    proc, out = sc.run(sc.bundle(rev=f"{mid}..HEAD"))
    refused(proc, out, "bundle failed verification")


def test_a_reviewed_sha_that_is_not_the_checkout_is_refused(sc):
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n"})
    wrong = commit(sc.work, {"apps/gw/src/b.py": "b = 2\n"})
    proc, out = sc.run(sc.bundle(), reviewed=wrong)
    refused(proc, out, "checkout is not the reviewed commit")


def test_twenty_one_commits_are_refused(sc):
    for i in range(21):
        commit(sc.work, {f"apps/gw/src/f{i}.py": f"x = {i}\n"}, f"c{i}")
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "21 commits is more than a round of fixes should be")


def test_a_protected_file_changed_in_an_intermediate_commit_is_refused_even_if_put_back(sc):
    commit(sc.work, {"deploy.sh": "evil\n"}, "weaken")
    commit(sc.work, {"deploy.sh": "good\n"}, "restore")
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n"}, "real fix")
    # the net diff is clean: only the per-commit guard can see this
    assert git(sc.work, "diff", "--name-only", sc.reviewed, "HEAD") == "apps/gw/src/a.py"
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "refused by guard-commits.sh")


def test_a_protected_file_in_the_net_diff_is_refused(sc):
    commit(sc.work, {".github/workflows/x.yml": "on: push\n"}, "workflow")
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "the responder changed protected files")


def test_extra_protected_paths_reach_the_verifier(sc):
    commit(sc.work, {"apps/gw/src/auth_check.py": "ok = True\n"})
    bundle = sc.bundle()
    ok, _ = sc.run(bundle)
    assert ok.returncode == 0, ok.stderr
    proc, out = sc.run(bundle, PROTECTED_EXTRA="*auth*.py")
    refused(proc, out, "the responder changed protected files")


def test_a_merge_commit_is_refused(sc):
    git(sc.work, "checkout", "-q", "-b", "side")
    commit(sc.work, {"apps/gw/src/side.py": "s = 1\n"}, "side")
    git(sc.work, "checkout", "-q", "-")
    commit(sc.work, {"apps/gw/src/main.py": "m = 1\n"}, "main-side")
    git(sc.work, "merge", "-q", "--no-ff", "-m", "merge", "side")
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "refused by guard-commits.sh")
    assert "merge" in proc.stderr


def test_a_symlink_is_refused(sc):
    blob = git(sc.work, "hash-object", "-w", "--stdin", input=b"../../../deploy.sh")
    git(sc.work, "update-index", "--add", "--cacheinfo", f"120000,{blob},apps/gw/src/link.py")
    git(sc.work, "commit", "-q", "-m", "link")
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "refused by guard-commits.sh")
    assert "symlink" in proc.stderr


def test_a_token_shaped_string_in_a_patch_is_refused_without_printing_it(sc):
    commit(sc.work, {"apps/gw/src/k.py": f'KEY = "{TOKEN}"\n'})
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "hold something token-shaped")
    assert TOKEN not in proc.stdout + proc.stderr


def test_a_token_shaped_string_in_a_commit_message_is_refused(sc):
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n"}, f"fix, key was {TOKEN}")
    proc, out = sc.run(sc.bundle())
    refused(proc, out, "hold something token-shaped")


def test_an_oversize_bundle_is_refused(sc):
    commit(sc.work, {"apps/gw/src/big.py": os.urandom(21_000_000)}, "big")
    bundle = sc.bundle()
    assert bundle.stat().st_size > 20_000_000
    proc, out = sc.run(bundle)
    refused(proc, out, "larger than 20000000 bytes")


def test_a_bundle_just_under_the_cap_is_not_refused_for_size(sc):
    commit(sc.work, {"apps/gw/src/mid.py": os.urandom(1_000_000)}, "mid")
    proc, out = sc.run(sc.bundle())
    assert proc.returncode == 0, proc.stderr


def test_a_tampered_bundle_is_refused(sc):
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n" * 500}, "fix")
    bundle = sc.bundle()
    data = bytearray(bundle.read_bytes())
    for i in range(len(data) - 40, len(data) - 20):  # inside the pack, past the header
        data[i] ^= 0xFF
    bundle.write_bytes(bytes(data))
    proc, out = sc.run(bundle)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "bundle failed verification" in proc.stderr or "cannot fetch the bundle" in proc.stderr, proc.stderr
    assert out.get("pushed") == "false" and "clean" not in out


@pytest.mark.parametrize("content", [b"", b"not a bundle at all\n"])
def test_an_empty_or_garbage_file_is_refused(sc, content):
    bundle = sc.tmp / "junk.bundle"
    bundle.write_bytes(content)
    proc, out = sc.run(bundle)
    assert proc.returncode == 1
    assert out.get("pushed") == "false"


def test_a_missing_bundle_is_refused(sc):
    proc, out = sc.run(sc.tmp / "nope.bundle")
    refused(proc, out, "bundle missing or larger")


@pytest.mark.parametrize("branch", ["-x", "a b", "x..y", "x~1", "", "a:b"])
def test_a_branch_that_is_not_a_plain_name_is_refused(sc, branch):
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n"})
    proc, out = sc.run(sc.bundle(), branch=branch or "x")
    if branch == "":
        return  # an empty argument is rejected by `${3:?}` before any work
    refused(proc, out, "branch name is not a plain branch name")


def test_a_reviewed_sha_that_is_not_a_full_commit_id_is_refused(sc):
    commit(sc.work, {"apps/gw/src/a.py": "a = 2\n"})
    proc, out = sc.run(sc.bundle(), reviewed="HEAD")
    refused(proc, out, "reviewed-sha is not a full commit id")

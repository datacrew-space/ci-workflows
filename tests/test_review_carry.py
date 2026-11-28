"""The review-verdict is carried to a new head only over clean merges of the base branch."""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review-carry.sh"
# Locally a missing tool skips; in CI (the `CI` env var is set) it fails, so the tests cannot go silent.
for tool in ("jq", "git"):
    if shutil.which(tool) is None:
        if os.environ.get("CI"):
            pytest.fail(f"{tool} is missing on this CI runner: the review-carry tests cannot run", pytrace=False)
        pytestmark = pytest.mark.skip(reason=f"needs {tool} (present on CI runners)")
BOT = "ci-review-orchestrator[bot]"

# A stand-in `gh`: `gh api --paginate <url> --jq .[]` prints the statuses stored for the commit in <url>.
STUB_GH = """#!/usr/bin/env bash
sha=$(printf '%s' "$3" | sed -E 's#.*/commits/([0-9a-f]+)/statuses.*#\\1#')
f="$STATUS_DIR/$sha.json"
[ -f "$f" ] && jq -c '.[]' "$f"
exit 0
"""


class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.statuses = path.parent / "statuses"
        self.statuses.mkdir()
        self.bin = path.parent / "bin"
        self.bin.mkdir()
        stub = self.bin / "gh"
        stub.write_text(STUB_GH)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        self.write("a.txt", "one\ntwo\nthree\n")
        self.write("b.txt", "base\n")
        self.commit("root")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name: str, text: str) -> None:
        (self.path / name).write_text(text)

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def sha(self, ref: str) -> str:
        return self.git("rev-parse", ref)

    def publish_base(self) -> None:
        """Make `origin/main` point at the local main, as a fetch would."""
        self.git("update-ref", "refs/remotes/origin/main", self.sha("main"))

    def move_base(self, name: str) -> None:
        self.git("checkout", "-q", "main")
        self.write(name, "main moved\n")
        self.commit(f"main adds {name}")
        self.publish_base()
        self.git("checkout", "-q", "pr")

    def verdict(self, sha: str, state: str, creator: str = BOT) -> None:
        row = {"id": 1, "context": "review-verdict", "state": state, "created_at": "2026-01-01T00:00:00Z", "creator": {"login": creator}}
        (self.statuses / f"{sha}.json").write_text(json.dumps([row]))

    def decide(self, head: str, attempt: str = "1", login: str = "ci-review-orchestrator") -> tuple[str, str, str]:
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "STATUS_DIR": str(self.statuses), "REPO": "o/r"}
        out = subprocess.run(
            ["bash", str(SCRIPT), str(self.path), head, "origin/main", login, attempt], capture_output=True, text=True, check=True, env=env
        ).stdout
        decision, sha, reason = out.strip().split("|", 2)
        return decision, sha, reason

    def merge_main(self) -> str:
        self.git("merge", "-q", "--no-ff", "--no-edit", "main")
        return self.sha("HEAD")


@pytest.fixture
def repo(tmp_path) -> Repo:
    r = Repo(tmp_path / "repo")
    # The PR branch forks from the root, gets an approved commit, and main then moves on.
    r.git("checkout", "-q", "-b", "pr")
    r.write("pr.txt", "pr work\n")
    r.approved = r.commit("pr work")  # type: ignore[attr-defined]
    r.verdict(r.approved, "success")
    r.git("checkout", "-q", "main")
    r.write("b.txt", "base moved\n")
    r.commit("main moves")
    r.publish_base()
    r.git("checkout", "-q", "pr")
    return r


def test_a_clean_merge_of_the_base_carries(repo):
    head = repo.merge_main()
    decision, sha, reason = repo.decide(head)
    assert (decision, sha) == ("carry", repo.approved), reason
    assert repo.approved[:7] in reason


def test_two_stacked_clean_merges_carry(repo):
    repo.merge_main()
    repo.move_base("c.txt")
    head = repo.merge_main()
    assert repo.decide(head)[:2] == ("carry", repo.approved)


def test_a_merge_with_a_conflict_resolution_runs(repo):
    repo.git("checkout", "-q", "main")
    repo.write("b.txt", "main edit of the line\n")
    repo.commit("main edits b again")
    repo.publish_base()
    repo.git("checkout", "-q", "pr")
    repo.write("b.txt", "pr edit of the same line\n")
    pr_edit = repo.commit("pr edits b")
    repo.verdict(pr_edit, "success")
    subprocess.run(["git", "-C", str(repo.path), "merge", "--no-ff", "--no-edit", "main"], capture_output=True)  # conflicts
    repo.write("b.txt", "resolved by hand\n")
    head = repo.commit("Merge main, resolved")
    assert len(repo.git("rev-list", "--parents", "-n", "1", head).split()) == 3
    decision, _, reason = repo.decide(head)
    assert decision == "run" and "does not merge cleanly" in reason


def test_a_merge_with_an_extra_edit_runs(repo):
    repo.git("merge", "-q", "--no-ff", "--no-commit", "main")
    repo.write("sneaky.txt", "new code the reviewer never saw\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "Merge main")
    decision, _, reason = repo.decide(repo.sha("HEAD"))
    assert decision == "run" and "automatic merge" in reason


def test_a_merge_of_a_branch_that_is_not_the_base_runs(repo):
    repo.git("checkout", "-q", "-b", "other", "main")
    repo.write("other.txt", "other\n")
    repo.commit("other work")
    repo.git("checkout", "-q", "pr")
    repo.git("merge", "-q", "--no-ff", "--no-edit", "other")
    decision, _, reason = repo.decide(repo.sha("HEAD"))
    assert decision == "run" and "other than origin/main" in reason


def test_a_normal_commit_runs(repo):
    repo.write("more.txt", "more pr code\n")
    head = repo.commit("more work")
    assert repo.decide(head)[0] == "run"


def test_a_normal_commit_between_the_merge_and_the_verdict_runs(repo):
    repo.write("more.txt", "more pr code\n")
    repo.commit("more work")
    head = repo.merge_main()
    assert repo.decide(head)[0] == "run"


@pytest.mark.parametrize("state", ["failure", "error", "pending"])
def test_a_verdict_that_is_not_success_is_not_carried(repo, state):
    repo.verdict(repo.approved, state)
    head = repo.merge_main()
    assert repo.decide(head)[0] == "run"


def test_a_failure_in_between_stops_the_walk_before_an_older_success(repo):
    first = repo.merge_main()
    repo.verdict(first, "failure")
    repo.move_base("c.txt")
    assert repo.decide(repo.merge_main())[0] == "run"


def test_a_verdict_set_by_someone_else_is_not_carried(repo):
    repo.verdict(repo.approved, "success", creator="somebody")
    head = repo.merge_main()
    assert repo.decide(head)[0] == "run"
    repo.verdict(repo.approved, "success", creator="ci-review-orchestrator-evil[bot]")
    assert repo.decide(head)[0] == "run"


def test_a_rerun_always_reviews(repo):
    head = repo.merge_main()
    assert repo.decide(head, attempt="2")[0] == "run"


def test_a_merge_of_a_base_commit_that_is_not_in_the_base_ref_runs(repo):
    """The second parent must be reachable from origin/<base>, not just from a local branch."""
    repo.git("checkout", "-q", "main")
    repo.write("c.txt", "unpublished\n")
    repo.commit("main moves, not fetched")
    repo.git("checkout", "-q", "pr")
    head = repo.merge_main()
    assert repo.decide(head)[0] == "run"


def test_a_custom_orchestrator_login_is_honoured(repo):
    repo.verdict(repo.approved, "success", creator="my-orch[bot]")
    head = repo.merge_main()
    assert repo.decide(head, login="my-orch")[0] == "carry"
    assert repo.decide(head)[0] == "run"


def test_bad_input_runs():
    out = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True).stdout
    assert out.startswith("run|")

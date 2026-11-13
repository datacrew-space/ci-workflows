"""check-private-terms.sh and check-commit-identity.sh, against real git repositories.

The terms used here are invented. The property that matters most is that a report never contains
the matched term, line, path or address: CI logs of a public repository are public.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TERMS = SCRIPTS / "check-private-terms.sh"
IDENT = SCRIPTS / "check-commit-identity.sh"

NOREPLY = "someone@users.noreply.github.com"
PERSONAL = "person@example.org"
DENYLIST = "# invented names\nzebra-corp\n(^|[^a-z0-9])hidden-host\\.internal([^a-z0-9]|$)\nsecretpath\n"


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") and k not in ("PRIVATE_TERMS", "PRIVATE_TERMS_FILE")
    }
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, HOME=str(Path.cwd()))
    env.update(extra)
    return env


def git(repo: Path, *args: str, email: str = NOREPLY, **env: str) -> str:
    base = {"GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t"}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_env(**{**base, **env}),
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hello\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def commit(repo: Path, files: dict[str, str], msg: str = "change", **kw) -> str:
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg, **kw)
    return git(repo, "rev-parse", "HEAD")


def run(script: Path, repo: Path, *args: str, terms: str | None = DENYLIST, **env: str) -> subprocess.CompletedProcess:
    e = _env(**env)
    if terms is not None:
        e["PRIVATE_TERMS"] = terms
    return subprocess.run(["bash", str(script), *args], cwd=repo, capture_output=True, text=True, env=e, check=False)


def out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


# ---- check-private-terms.sh ---------------------------------------------------------------


def test_clean_staged_changes_pass(repo):
    (repo / "a.txt").write_text("nothing special\n")
    git(repo, "add", "-A")
    assert run(TERMS, repo, "--staged").returncode == 0


def test_a_staged_match_is_refused_and_reported_without_the_term(repo):
    (repo / "notes.txt").write_text("line one\nsee the Zebra-Corp wiki\n")  # case-insensitive
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged")
    assert r.returncode == 1
    assert "notes.txt:2: matches denylist entry #1" in out(r)
    reported = out(r).split("refused (")[0]  # the entries, before the fixed closing line
    for leaked in ("zebra", "wiki", "see the"):
        assert leaked not in reported.lower()


def test_a_whole_word_pattern_does_not_match_inside_a_longer_word(repo):
    (repo / "a.txt").write_text("not-hidden-host.internal-x is fine?\n")
    (repo / "b.txt").write_text("call hidden-host.internal now\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged")
    assert r.returncode == 1
    assert "b.txt:1: matches denylist entry #2" in out(r)


def test_a_matching_path_is_reported_but_never_printed(repo):
    (repo / "zebra-corp-notes.md").write_text("innocent text\n(zebra-corp inside)\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged")
    assert r.returncode == 1
    assert "a changed path matches denylist entry #1" in out(r)
    assert "zebra" not in out(r).lower()  # neither the path nor the line leaks
    assert "<path hidden>" in out(r)


def test_no_denylist_fails_closed_with_require_and_skips_without(repo):
    (repo / "a.txt").write_text("zebra-corp\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--require", "--staged", terms=None, HOME=str(repo))
    assert r.returncode == 2 and "fail closed" in out(r)
    r = run(TERMS, repo, "--staged", terms=None, HOME=str(repo))
    assert r.returncode == 0 and "skipped" in out(r)


def test_comment_only_denylist_counts_as_none(repo):
    assert run(TERMS, repo, "--require", "--staged", terms="# only a comment\n\n").returncode == 2


def test_an_invalid_pattern_is_an_error_not_a_silent_pass(repo):
    (repo / "a.txt").write_text("zebra-corp\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged", terms="zebra-corp\n(unclosed\n")
    assert r.returncode == 2


def test_the_denylist_can_come_from_a_file(repo, tmp_path):
    f = tmp_path / "terms.txt"
    f.write_text("zebra-corp\n")
    (repo / "a.txt").write_text("zebra-corp\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged", terms=None, PRIVATE_TERMS_FILE=str(f))
    assert r.returncode == 1


def test_commit_message_check(repo, tmp_path):
    clean = tmp_path / "m1"
    clean.write_text("fix a thing\n\n# comment mentioning zebra-corp is ignored\n")
    assert run(TERMS, repo, "--message", str(clean)).returncode == 0
    bad = tmp_path / "m2"
    bad.write_text("subject\n\nbody about zebra-corp\n")
    r = run(TERMS, repo, "--message", str(bad))
    assert r.returncode == 1 and "zebra" not in out(r).lower()


def test_a_term_added_in_one_commit_and_removed_in_the_next_is_still_caught(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "zebra-corp\n"}, "add")
    commit(repo, {"a.txt": "clean now\n"}, "remove")
    assert git(repo, "diff", "--name-only", base, "HEAD") == "a.txt"  # the net diff alone would pass
    r = run(TERMS, repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1
    assert "a.txt:1: matches denylist entry #1" in out(r) and "zebra" not in out(r).lower()


def test_range_scans_messages_and_author_names(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "fine\n"}, "mentions secretpath in the message")
    r = run(TERMS, repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "message" in out(r) and "secretpath" not in out(r).lower()
    base2 = git(repo, "rev-parse", "HEAD")
    commit(repo, {"b.txt": "fine\n"}, "ok", GIT_AUTHOR_NAME="zebra-corp bot")
    r = run(TERMS, repo, "--range", f"{base2}..HEAD")
    assert r.returncode == 1 and "zebra" not in out(r).lower()


def test_a_clean_range_passes(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "fine\n"}, "fine")
    assert run(TERMS, repo, "--range", f"{base}..HEAD").returncode == 0


def test_all_history_finds_an_old_commit(repo):
    commit(repo, {"a.txt": "zebra-corp\n"}, "old")
    commit(repo, {"a.txt": "clean\n"}, "newer")
    r = run(TERMS, repo, "--all-history")
    assert r.returncode == 1 and "zebra" not in out(r).lower()


def test_an_unknown_range_fails_closed(repo):
    assert run(TERMS, repo, "--range", "0" * 40 + "..HEAD").returncode == 2


# ---- check-commit-identity.sh -------------------------------------------------------------


def test_noreply_identities_pass_over_a_range(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "x\n"}, "ok\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    assert run(IDENT, repo, "--range", f"{base}..HEAD").returncode == 0


def test_a_personal_author_email_is_refused_without_printing_it(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "x\n"}, "personal", email=PERSONAL)
    r = run(IDENT, repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1
    assert "author email is not an allowed address" in out(r)
    assert "example.org" not in out(r) and PERSONAL not in out(r)


def test_a_personal_committer_alone_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "x\n"}, "c", GIT_COMMITTER_EMAIL=PERSONAL)
    r = run(IDENT, repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "committer" in out(r)


def test_a_personal_coauthor_trailer_is_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "x\n"}, f"c\n\nCo-authored-by: Pat <{PERSONAL}>")
    r = run(IDENT, repo, "--range", f"{base}..HEAD")
    assert r.returncode == 1 and "Co-authored-by" in out(r) and "example.org" not in out(r)


def test_identity_mode_checks_the_configured_identity(repo):
    names = {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t"}  # CI has no user.name; git var needs one
    assert (
        run(IDENT, repo, "--identity", GIT_AUTHOR_EMAIL=NOREPLY, GIT_COMMITTER_EMAIL=NOREPLY, **names).returncode == 0
    )
    r = run(IDENT, repo, "--identity", GIT_AUTHOR_EMAIL=PERSONAL, GIT_COMMITTER_EMAIL=NOREPLY, **names)
    assert r.returncode == 1 and "example.org" not in out(r)


def test_the_allowed_pattern_can_be_overridden(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {"a.txt": "x\n"}, "org mail", email="dev@example.org")
    assert run(IDENT, repo, "--range", f"{base}..HEAD").returncode == 1
    assert run(IDENT, repo, "--range", f"{base}..HEAD", ALLOWED_EMAIL_REGEX="@example\\.org$").returncode == 0


def test_an_invalid_allowed_pattern_is_an_error(repo):
    assert run(IDENT, repo, "--identity", ALLOWED_EMAIL_REGEX="(unclosed").returncode == 2


def test_all_history_finds_an_old_personal_commit(repo):
    commit(repo, {"a.txt": "x\n"}, "old personal", email=PERSONAL)
    commit(repo, {"a.txt": "y\n"}, "newer")
    assert run(IDENT, repo, "--all-history").returncode == 1

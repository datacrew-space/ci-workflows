# ruff: noqa: F811
"""Detection gaps found in review of check-private-terms.sh and check-commit-identity.sh.

Each test reproduces one gap; the terms are invented.
"""

import shutil
import subprocess
from pathlib import Path

from test_public_repo_guards import IDENT, NOREPLY, PERSONAL, TERMS, commit, git, out, repo, run  # noqa: F401

NAMES = {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t"}


def evil_merge(repo: Path, files: dict[str, str]) -> None:
    """Merge a side branch and add files in the merge commit itself (no patch in a plain git log -p)."""
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, {"side.txt": "side\n"}, "side work")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"main.txt": "main\n"}, "main work")
    git(repo, "merge", "--no-commit", "--no-ff", "side")
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "merge side")


def test_an_added_line_that_starts_with_plus_plus_space_is_scanned(repo):
    (repo / "a.txt").write_text("++ zebra-corp\n")  # the diff line is `+++ zebra-corp`
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged")
    assert r.returncode == 1 and "a.txt:1: matches denylist entry #1" in out(r)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "-q", "-m", "x")
    assert run(TERMS, repo, "--range", f"{base}..HEAD").returncode == 1


def test_all_history_sees_content_added_in_a_merge_commit(repo):
    evil_merge(repo, {"merged.txt": "zebra-corp\n"})
    r = run(TERMS, repo, "--all-history")
    assert r.returncode == 1 and "zebra" not in out(r).lower()


def test_range_sees_content_added_in_a_merge_commit(repo):
    base = git(repo, "rev-parse", "HEAD")
    evil_merge(repo, {"merged.txt": "zebra-corp\n"})
    assert run(TERMS, repo, "--range", f"{base}..HEAD").returncode == 1


def test_binary_content_is_scanned(repo):
    (repo / "blob.bin").write_bytes(b"\x00\x01zebra-corp\x00\x02")
    git(repo, "add", "-A")
    assert run(TERMS, repo, "--staged").returncode == 1


def test_uppercase_patterns_match_case_insensitively(repo):
    (repo / "a.txt").write_text("see alpha-zone here\n")
    git(repo, "add", "-A")
    assert run(TERMS, repo, "--staged", terms="Alpha-ZONE\n").returncode == 1


def test_range_uses_the_merge_base_so_text_main_scrubbed_is_not_reported(repo):
    commit(repo, {"old.txt": "zebra-corp\n"}, "old mention")
    git(repo, "checkout", "-q", "-b", "pr")
    git(repo, "checkout", "-q", "main")
    git(repo, "rm", "-q", "old.txt")
    git(repo, "commit", "-q", "-m", "scrub")
    main_tip = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "pr")
    commit(repo, {"new.txt": "fine\n"}, "pr work")
    r = run(TERMS, repo, "--range", f"{main_tip}..HEAD")
    assert r.returncode == 0, out(r)


def test_awk_stderr_never_leaks_denylist_fragments(repo, tmp_path):
    """An awk that complains about a pattern (gawk can) must not get to print it into a public log."""
    shim = tmp_path / "bin" / "awk"
    shim.parent.mkdir()
    real = shutil.which("awk").replace("\\", "/")
    shim.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in terms=*) cat "${a#terms=}" >&2 ;; esac; done\n'
        f'exec "{real}" "$@"\n'
    )
    shim.chmod(0o755)
    # PATH in the form bash itself uses (POSIX paths and ':' even on Windows).
    posix = subprocess.run(
        ["bash", "-c", 'printf "%s" "$(cd "$1" && pwd):$PATH"', "_", str(shim.parent)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    env = {"PATH": posix}
    (repo / "a.txt").write_text("zebra-corp\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged", **env)
    assert r.returncode == 1 and "zebra" not in out(r).lower()
    m = repo / "msg.txt"
    m.write_text("zebra-corp\n")
    r = run(TERMS, repo, "--message", str(m), **env)
    assert r.returncode == 1 and "zebra" not in out(r).lower()


def test_non_ascii_paths_are_matched_not_octal_quoted(repo):
    (repo / "café.txt").write_text("fine\n")
    git(repo, "add", "-A")
    r = run(TERMS, repo, "--staged", terms="café\n")
    assert r.returncode == 1 and "path matches" in out(r) and "caf" not in out(r).lower()


def test_message_below_the_scissors_line_is_ignored(repo):
    m = repo / "msg.txt"
    m.write_text("clean subject\n# ------------------------ >8 ------------------------\nzebra-corp\n")
    assert run(TERMS, repo, "--message", str(m)).returncode == 0
    m.write_text("zebra-corp subject\n# ------------------------ >8 ------------------------\nx\n")
    assert run(TERMS, repo, "--message", str(m)).returncode == 1


def test_allowed_email_regex_is_case_insensitive(repo):
    e = {"GIT_AUTHOR_EMAIL": PERSONAL, "GIT_COMMITTER_EMAIL": NOREPLY, **NAMES}
    assert run(IDENT, repo, "--identity", **e).returncode == 1
    r = run(IDENT, repo, "--identity", ALLOWED_EMAIL_REGEX="@Example\\.ORG$|@users\\.noreply\\.github\\.com$", **e)
    assert r.returncode == 0, out(r)

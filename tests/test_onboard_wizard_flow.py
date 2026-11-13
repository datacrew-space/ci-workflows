"""Drives scripts/onboard-review-loop.sh end to end with a stub `gh` and scripted answers.

The drift tests in test_onboard_wizard.py read the script; these RUN it, so they fail when a safeguard
is removed: a secret on argv, an unconfirmed overwrite, a lost answer, the loop enabled after a failure.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "onboard-review-loop.sh"

CLIENT_ID = "stub-client-id-value"
CLIENT_SECRET = "stub-client-secret-value"
SHA = "a" * 40

GH_STUB = r"""#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
case "$1 $2" in
  "auth status") exit 0 ;;
  "repo view")
    case "$*" in *viewerPermission*) echo "${STUB_PERM:-ADMIN}" ;; esac
    exit 0 ;;
  "secret list") for n in ${STUB_SECRETS:-}; do echo "$n"; done; exit 0 ;;
  "secret set")
    cat > "$STUB_DIR/stdin.$3"
    if [ -n "${STUB_SET_FAIL:-}" ]; then echo "HTTP 403: Resource not accessible" >&2; exit 1; fi
    exit 0 ;;
  "variable set") exit 0 ;;
esac
case "$*" in
  *%5Bbot%5D*) echo 4242 ;;
  api\ users/*) echo User ;;
esac
exit 0
"""


def run_wizard(cmd: list[str], answers: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Feed the answers as BYTES: text mode would turn each newline into CRLF on Windows."""
    proc = subprocess.run(
        cmd,
        input=("\n".join(answers) + "\n").encode(),
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode(errors="replace"),
        proc.stderr.decode(errors="replace"),
    )


class Run:
    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "stub"
        self.dir.mkdir()
        self.log = self.dir / "calls.log"
        self.log.write_text("", encoding="utf-8")
        self.target = tmp_path / "checkout"
        (self.target / ".git").mkdir(parents=True)
        (self.dir / "gh").write_text(GH_STUB, encoding="utf-8", newline="\n")
        # No browser may open during a test, and Windows would find explorer.exe on PATH.
        for name in ("explorer.exe", "wslview", "xdg-open", "open"):
            (self.dir / name).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        for f in self.dir.iterdir():
            f.chmod(0o755)

    def go(self, answers: list[str], *, secrets: str = "", set_fail: bool = False, perm: str = "ADMIN"):
        env = {k: v for k, v in os.environ.items() if k not in ("SHELLOPTS", "GH_REPO")}
        env.update(
            PATH=f"{self.dir.as_posix()}{os.pathsep}{env['PATH']}",
            STUB_DIR=self.dir.as_posix(),
            STUB_LOG=self.log.as_posix(),
            STUB_SECRETS=secrets,
            STUB_PERM=perm,
        )
        if set_fail:
            env["STUB_SET_FAIL"] = "1"
        return run_wizard(["bash", str(WIZARD)], answers, env)

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8")

    def stdin_of(self, name: str) -> str | None:
        f = self.dir / f"stdin.{name}"
        return f.read_text(encoding="utf-8") if f.exists() else None

    @property
    def caller(self) -> Path:
        return self.target / ".github" / "workflows" / "claude-code-review.yml"


def answers(run: Run, *, installed="y", responder="", overwrite_secrets=None, files=(), enable="y"):
    """The prompts in order. `files` are the answers to the 'exists. Overwrite?' prompts."""
    a = [
        "",  # banner
        "o/r",  # target repository
        "",  # continue
        "https://secrets.example.invalid",
        "proj",
        "",  # env
        "",  # claude folder
        "",  # github folder
        responder,
        installed,  # apps already installed?
    ]
    if installed != "y":
        a += [""] * 3  # one pause per App
    a += [""]  # all seven secrets exist
    a += [CLIENT_ID, CLIENT_SECRET]
    if overwrite_secrets is not None:
        a.append(overwrite_secrets)
    a += [str(run.target), SHA]
    a += list(files)
    a += [""]  # prompts edited
    a += [enable]
    return a


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path)


def test_happy_path_sets_both_secrets_over_stdin_and_never_on_argv(run):
    proc = run.go(answers(run))
    assert proc.returncode == 0, proc.stderr
    assert run.stdin_of("INFISICAL_CLIENT_ID") == CLIENT_ID
    assert run.stdin_of("INFISICAL_CLIENT_SECRET") == CLIENT_SECRET
    assert CLIENT_ID not in run.calls() and CLIENT_SECRET not in run.calls()
    assert CLIENT_ID not in proc.stdout + proc.stderr and CLIENT_SECRET not in proc.stdout + proc.stderr
    assert "gh variable set CLAUDE_REVIEW_ENABLED --body true" in run.calls()
    assert run.caller.is_file()


def test_every_gh_call_targets_the_named_repository(run):
    """GH_REPO is exported, so nothing depends on the current directory (checked through the env)."""
    # The stub cannot see GH_REPO unless exported; make it log it.
    stub = run.dir / "gh"
    stub.write_text(
        stub.read_text(encoding="utf-8").replace('echo "gh $*"', 'echo "[${GH_REPO:-unset}] gh $*"'),
        encoding="utf-8",
        newline="\n",
    )
    assert run.go(answers(run)).returncode == 0
    for line in run.calls().splitlines():
        if " gh secret set" in line or " gh variable set" in line:
            assert line.startswith("[o/r]"), line


def test_existing_secrets_are_not_overwritten_without_a_yes(run):
    proc = run.go(answers(run, overwrite_secrets="n", enable="n"), secrets="INFISICAL_CLIENT_ID OTHER")
    assert proc.returncode == 0, proc.stderr
    assert "gh secret set" not in run.calls()
    assert "gh variable set" not in run.calls()  # not enabled after skipping the secrets
    assert "left as they were" in proc.stdout


def test_existing_secrets_are_overwritten_after_a_yes(run):
    proc = run.go(answers(run, overwrite_secrets="y"), secrets="INFISICAL_CLIENT_ID")
    assert proc.returncode == 0, proc.stderr
    assert run.stdin_of("INFISICAL_CLIENT_ID") == CLIENT_ID


def test_a_failed_secret_shows_gh_s_error_and_does_not_enable_the_loop(run):
    proc = run.go(answers(run), set_fail=True)
    assert proc.returncode == 0, proc.stderr
    assert "HTTP 403" in proc.stdout + proc.stderr
    assert "Not enabling it" in proc.stdout
    assert "gh variable set" not in run.calls()
    assert CLIENT_SECRET not in proc.stdout + proc.stderr


def test_rerun_asks_before_overwriting_the_caller_and_each_answer_goes_to_its_own_prompt(run):
    run.caller.parent.mkdir(parents=True)
    run.caller.write_text("OLD CALLER\n", encoding="utf-8")
    reviewer = run.target / ".agents" / "agents" / "code-reviewer.md"
    reviewer.parent.mkdir(parents=True)
    reviewer.write_text("MY CUSTOM REVIEWER\n", encoding="utf-8")
    # y: replace the caller; n: keep my customised reviewer; the third file does not exist yet.
    proc = run.go(answers(run, files=["y", "n"]))
    assert proc.returncode == 0, proc.stderr
    assert "OLD CALLER" not in run.caller.read_text(encoding="utf-8")
    assert SHA in run.caller.read_text(encoding="utf-8")
    assert reviewer.read_text(encoding="utf-8") == "MY CUSTOM REVIEWER\n"


def test_declining_the_caller_overwrite_keeps_it_and_says_so(run):
    run.caller.parent.mkdir(parents=True)
    run.caller.write_text("OLD CALLER\n", encoding="utf-8")
    proc = run.go(answers(run, files=["n"]))
    assert run.caller.read_text(encoding="utf-8") == "OLD CALLER\n"
    assert "left as it was" in proc.stdout


def test_custom_responder_app_is_written_to_the_caller_with_its_looked_up_id(run):
    proc = run.go(answers(run, responder="acme-responder"))
    assert proc.returncode == 0, proc.stderr
    text = run.caller.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    with_ = doc["jobs"]["review"]["with"]
    assert with_["responder-bot-login"] == "acme-responder"
    assert with_["responder-bot-id"] == "4242"
    # the concurrency group follows the input (the commented hint above still names the default)
    assert "acme-responder[bot]" in doc["concurrency"]["cancel-in-progress"]
    assert "ci-review-responder[bot]" not in doc["concurrency"]["cancel-in-progress"]


def test_default_responder_adds_no_override_to_the_caller(run):
    assert run.go(answers(run)).returncode == 0
    with_ = yaml.safe_load(run.caller.read_text(encoding="utf-8"))["jobs"]["review"]["with"]
    assert "responder-bot-login" not in with_ and "responder-bot-id" not in with_


def test_skipping_app_creation_is_the_default_for_installed_apps(run):
    proc = run.go(answers(run, installed="y"))
    assert "github.com/settings/apps/new" not in proc.stdout
    assert "organizations/" not in proc.stdout


def test_creating_the_apps_walks_all_three(run):
    proc = run.go(answers(run, installed="n"))
    assert proc.returncode == 0, proc.stderr
    for app in ("ci-code-reviewer", "ci-review-responder", "ci-review-orchestrator"):
        assert app in proc.stdout


def test_a_non_admin_login_is_warned_and_can_stop(run):
    proc = run.go(["", "o/r", "n"], perm="READ")
    assert proc.returncode == 1
    assert "not an admin" in proc.stdout
    assert "gh secret set" not in run.calls()


def test_inherited_xtrace_does_not_print_the_pasted_secrets(run):
    env_proc = run_wizard(
        ["bash", "-x", str(WIZARD)],
        answers(run),
        {
            **os.environ,
            "PATH": f"{run.dir.as_posix()}{os.pathsep}{os.environ['PATH']}",
            "STUB_DIR": run.dir.as_posix(),
            "STUB_LOG": run.log.as_posix(),
        },
    )
    assert CLIENT_SECRET not in env_proc.stdout + env_proc.stderr

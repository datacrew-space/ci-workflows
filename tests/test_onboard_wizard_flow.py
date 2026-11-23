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
  "secret list")
    if [ -n "${STUB_LIST_FAIL:-}" ]; then echo "HTTP 500" >&2; exit 1; fi
    for n in ${STUB_SECRETS:-}; do echo "$n"; done; exit 0 ;;
  "secret set")
    cat > "$STUB_DIR/stdin.$3"
    if [ -n "${STUB_SET_FAIL:-}" ]; then echo "HTTP 403: Resource not accessible" >&2; exit 1; fi
    exit 0 ;;
  "variable set") exit 0 ;;
esac
case "$*" in
  *%5Bbot%5D*) if [ -n "${STUB_BOT_MISSING:-}" ]; then echo "HTTP 404" >&2; exit 1; fi; echo 4242 ;;
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

    def go(
        self,
        answers: list[str],
        *,
        secrets: str = "",
        set_fail: bool = False,
        perm: str = "ADMIN",
        list_fail: bool = False,
        bot_missing: bool = False,
    ):
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
        if list_fail:
            env["STUB_LIST_FAIL"] = "1"
        if bot_missing:
            env["STUB_BOT_MISSING"] = "1"
        return run_wizard(["bash", str(WIZARD)], answers, env)

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8")

    def stdin_of(self, name: str) -> str | None:
        f = self.dir / f"stdin.{name}"
        return f.read_text(encoding="utf-8") if f.exists() else None

    @property
    def caller(self) -> Path:
        return self.target / ".github" / "workflows" / "claude-code-review.yml"


def answers(run: Run, *, installed="y", responder="", overwrite_secrets=None, files=(), enable="y", bot_id=None):
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
    if bot_id is not None:
        a.append(bot_id)  # asked only when the bot id lookup fails, after the Apps stage
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
    proc = run.go(answers(run, overwrite_secrets="n", enable="y"), secrets="INFISICAL_CLIENT_ID OTHER")
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
    assert "Infisical base URL" not in proc.stdout  # stopped there, not ten prompts later
    assert "too many" not in proc.stdout
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


def test_a_new_owner_can_create_their_responder_app_before_its_bot_id_exists(run):
    """The bot account exists only after the App does: the id is asked after the Apps stage, not before."""
    proc = run.go(
        answers(run, installed="n", responder="acme-responder", bot_id="777"),
        bot_missing=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "any unique name" in proc.stdout  # the reviewer and orchestrator names are free
    doc = yaml.safe_load(run.caller.read_text(encoding="utf-8"))
    assert doc["jobs"]["review"]["with"]["responder-bot-id"] == "777"
    assert doc["jobs"]["review"]["with"]["responder-bot-login"] == "acme-responder"


def test_when_the_secret_list_fails_the_wizard_asks_instead_of_overwriting(run):
    proc = run.go(answers(run, overwrite_secrets="n", enable="y"), list_fail=True)
    assert proc.returncode == 0, proc.stderr
    assert "gh secret set" not in run.calls()
    assert "left as they were" in proc.stdout


# ---- batch mode -----------------------------------------------------------------------------------


def batch_answers(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "INFISICAL_DOMAIN": "https://secrets.example.invalid",
        "INFISICAL_PROJECT_SLUG": "proj",
        "COMMIT_SHA": SHA,
    }
    values.update(overrides)
    f = tmp_path / "answers.env"
    f.write_text("# one set of answers\n" + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return f


def make_checkout(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    (d / ".git").mkdir(parents=True)
    return d


def run_batch(run: Run, args: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("SHELLOPTS", "GH_REPO")}
    env.update(
        PATH=f"{run.dir.as_posix()}{os.pathsep}{env['PATH']}",
        STUB_DIR=run.dir.as_posix(),
        STUB_LOG=run.log.as_posix(),
    )
    env.update(extra_env or {})
    return run_wizard(["bash", str(WIZARD), "--batch", *args], [], env)


def written(checkout: Path) -> list[str]:
    return sorted(
        p.relative_to(checkout).as_posix() for p in checkout.rglob("*") if p.is_file() and ".git/" not in p.as_posix()
    )


EXPECTED_FILES = [
    ".agents/agents/code-reviewer.md",
    ".agents/agents/review-responder.md",
    ".github/workflows/claude-code-review.yml",
]


def test_batch_writes_the_caller_and_both_prompts_into_every_checkout(run, tmp_path):
    a, b = make_checkout(tmp_path, "one"), make_checkout(tmp_path, "two")
    proc = run_batch(run, [str(batch_answers(tmp_path)), str(a), str(b)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for d in (a, b):
        assert written(d) == EXPECTED_FILES
        text = (d / ".github/workflows/claude-code-review.yml").read_text(encoding="utf-8")
        assert SHA in text and "secrets.example.invalid" in text and "<COMMIT_SHA>" not in text
        yaml.safe_load(text)


def test_batch_refuses_to_overwrite_without_an_explicit_yes(run, tmp_path):
    a, b = make_checkout(tmp_path, "one"), make_checkout(tmp_path, "two")
    mine = a / ".agents/agents/code-reviewer.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("MY CUSTOM REVIEWER\n", encoding="utf-8")
    proc = run_batch(run, [str(batch_answers(tmp_path)), str(a), str(b)])
    assert proc.returncode == 1  # something was left as it was
    assert mine.read_text(encoding="utf-8") == "MY CUSTOM REVIEWER\n"
    assert "left as it was" in proc.stdout + proc.stderr
    # the other files and the other checkout are still written
    assert (a / ".github/workflows/claude-code-review.yml").is_file()
    assert written(b) == EXPECTED_FILES


def test_batch_overwrites_only_with_the_overwrite_flag(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    mine = a / ".agents/agents/code-reviewer.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("MY CUSTOM REVIEWER\n", encoding="utf-8")
    proc = run_batch(run, [str(batch_answers(tmp_path)), "--overwrite", str(a)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert mine.read_text(encoding="utf-8") != "MY CUSTOM REVIEWER\n"


def test_batch_commits_pushes_and_enables_nothing(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    proc = run_batch(run, [str(batch_answers(tmp_path)), str(a)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = run.calls()
    assert "secret set" not in calls and "variable set" not in calls
    assert not list(run.dir.glob("stdin.*"))
    assert not (a / ".git" / "COMMIT_EDITMSG").exists() and not list((a / ".git").iterdir())


def test_batch_refuses_a_secret_in_the_answers_file_and_never_shows_it(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    answers_file = batch_answers(tmp_path, INFISICAL_CLIENT_SECRET=CLIENT_SECRET)
    proc = run_batch(run, [str(answers_file), str(a)])
    assert proc.returncode == 1
    assert CLIENT_SECRET not in proc.stdout + proc.stderr + run.calls()
    assert written(a) == []


def test_batch_does_not_echo_a_malformed_line(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    answers_file = tmp_path / "answers.env"
    answers_file.write_text(f"just {CLIENT_SECRET} words\n", encoding="utf-8")
    proc = run_batch(run, [str(answers_file), str(a)])
    assert proc.returncode == 1
    assert CLIENT_SECRET not in proc.stdout + proc.stderr


def test_batch_writes_nothing_when_one_path_is_not_a_checkout(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    proc = run_batch(run, [str(batch_answers(tmp_path)), str(a), str(tmp_path / "missing")])
    assert proc.returncode == 1
    assert "not a git checkout" in proc.stdout + proc.stderr
    assert written(a) == []


def test_batch_rejects_an_invalid_answer_before_writing(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    proc = run_batch(run, [str(batch_answers(tmp_path, COMMIT_SHA="main")), str(a)])
    assert proc.returncode == 1
    assert "COMMIT_SHA" in proc.stdout + proc.stderr
    assert written(a) == []


def test_batch_custom_responder_uses_the_given_id_without_asking_gh(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    answers_file = batch_answers(tmp_path, RESPONDER_LOGIN="acme-responder", RESPONDER_ID="777")
    proc = run_batch(run, [str(answers_file), str(a)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with_ = yaml.safe_load((a / ".github/workflows/claude-code-review.yml").read_text(encoding="utf-8"))["jobs"][
        "review"
    ]["with"]
    assert with_["responder-bot-login"] == "acme-responder"
    assert with_["responder-bot-id"] == "777"
    assert run.calls().strip() == ""


def test_batch_custom_responder_without_an_id_looks_it_up_read_only(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    proc = run_batch(run, [str(batch_answers(tmp_path, RESPONDER_LOGIN="acme-responder")), str(a)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with_ = yaml.safe_load((a / ".github/workflows/claude-code-review.yml").read_text(encoding="utf-8"))["jobs"][
        "review"
    ]["with"]
    assert with_["responder-bot-id"] == "4242"
    assert all(line.startswith("gh api ") for line in run.calls().splitlines())


def test_batch_with_no_checkouts_prints_usage(run, tmp_path):
    proc = run_batch(run, [str(batch_answers(tmp_path))])
    assert proc.returncode == 2
    assert "usage" in proc.stdout + proc.stderr


def test_batch_does_not_take_a_missing_answer_from_the_environment(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    answers_file = tmp_path / "answers.env"
    answers_file.write_text(
        "INFISICAL_DOMAIN=https://secrets.example.invalid\nINFISICAL_PROJECT_SLUG=proj\n", encoding="utf-8"
    )
    proc = run_batch(run, [str(answers_file), str(a)], {"COMMIT_SHA": SHA})
    assert proc.returncode == 1
    assert "COMMIT_SHA" in proc.stdout + proc.stderr
    assert written(a) == []


def test_batch_ignores_a_stale_responder_id_in_the_environment(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    answers_file = batch_answers(tmp_path, RESPONDER_LOGIN="acme-responder")
    proc = run_batch(run, [str(answers_file), str(a)], {"RESPONDER_ID": "999"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with_ = yaml.safe_load((a / ".github/workflows/claude-code-review.yml").read_text(encoding="utf-8"))["jobs"][
        "review"
    ]["with"]
    assert with_["responder-bot-id"] == "4242"  # looked up, not the 999 in the environment


@pytest.mark.parametrize(
    "key,value",
    [
        ("INFISICAL_DOMAIN", "x: y"),
        ("INFISICAL_PROJECT_SLUG", "has space"),
        ("INFISICAL_ENV_SLUG", "a b"),
        ("INFISICAL_CLAUDE_PATH", "/a b"),
        ("INFISICAL_GITHUB_PATH", "/a;b"),
        ("RESPONDER_LOGIN", "bad login"),
        ("RESPONDER_ID", "12ab"),
        ("COMMIT_SHA", "A" * 40),
    ],
)
def test_batch_rejects_each_invalid_answer_and_writes_nothing(run, tmp_path, key, value):
    a = make_checkout(tmp_path, "one")
    overrides = {key: value}
    if key == "RESPONDER_ID":
        overrides["RESPONDER_LOGIN"] = "acme-responder"
    proc = run_batch(run, [str(batch_answers(tmp_path, **overrides)), str(a)])
    assert proc.returncode != 0
    assert written(a) == []


def test_batch_accepts_an_answers_file_with_a_utf8_bom(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    f = batch_answers(tmp_path)
    f.write_bytes(b"\xef\xbb\xbf" + f.read_bytes())
    proc = run_batch(run, [str(f), str(a)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert written(a) == EXPECTED_FILES


def test_batch_accepts_crlf_line_endings_written_as_bytes(run, tmp_path):
    a = make_checkout(tmp_path, "one")
    f = tmp_path / "answers.env"
    f.write_bytes(
        b"INFISICAL_DOMAIN=https://secrets.example.invalid\r\nINFISICAL_PROJECT_SLUG=proj\r\n"
        + f"COMMIT_SHA={SHA}\r\n".encode()
    )
    proc = run_batch(run, [str(f), str(a)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = (a / ".github/workflows/claude-code-review.yml").read_text(encoding="utf-8")
    assert SHA in text and "\r" not in text

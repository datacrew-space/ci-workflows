"""Second hardening round on the review loop (independent re-review of #509).

Each test pins a property the re-review found missing. They read the workflow file and drive the
scripts; none of them can prove a live run behaves, which is why the pins below are also checked
against the upstream repositories when REVIEW_LOOP_VERIFY_PINS=1 (see test_pins_match_upstream).
"""

import base64
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "claude-code-review.yml"
SCRIPTS = ROOT / "scripts"
SCAN = SCRIPTS / "scan-secrets.sh"
REPORT = SCRIPTS / "review-report.sh"

# ---- the committed pins -------------------------------------------------------------------------
# How they are validated: the workflow must use exactly these commit SHAs (test_*_is_pinned_by_sha),
# and with REVIEW_LOOP_VERIFY_PINS=1 test_pins_match_upstream resolves each tag with `gh api` and
# reads action.yaml at that SHA. To bump a pin: look the tag up with
#   gh api repos/<owner>/<repo>/git/ref/tags/<tag>   (an annotated tag needs one more hop:
#   gh api repos/<owner>/<repo>/git/tags/<object sha>), change the constant, the workflow and the
# `# vX.Y.Z` comment on the `uses:` line together, run this file with REVIEW_LOOP_VERIFY_PINS=1.
#
# Infisical/secrets-action: `secret-name` is read by v1.0.17 onwards (src/index.ts reads
# core.getInput("secret-name")); v1.0.15 and v1.0.16 have no such input in action.yaml and export
# EVERY secret under `secret-path`. The input takes ONE name, so there is one step per secret.
INFISICAL_REPO = "Infisical/secrets-action"
INFISICAL_TAG = "v1.0.18"
INFISICAL_SHA = "d2e351f16c6ca20d17c85e6c992e04bdeb64e87d"
INFISICAL_FIRST_TAG_WITH_SECRET_NAME = (1, 0, 17)
# anthropics/claude-code-action v1.0.275 (the commit the floating `v1` tag pointed to).
CLAUDE_ACTION_REPO = "anthropics/claude-code-action"
CLAUDE_ACTION_TAG = "v1.0.275"
CLAUDE_ACTION_SHA = "4d75e5f390ee76b2e09c1980ecc99ff3a2e088c8"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


@pytest.fixture(scope="module")
def raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def step(job: dict, step_id: str) -> dict:
    return next(s for s in job["steps"] if s.get("id") == step_id)


def named(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name") == name)


def all_steps(jobs: dict):
    for name, job in jobs.items():
        for s in job["steps"]:
            yield name, s


AGENTS = [("claude-review", "claude-review"), ("respond", "respond-agent")]


def rules(job: dict, step_id: str, flag: str) -> list[str]:
    args = step(job, step_id)["with"]["claude_args"]
    match = re.search(rf'{flag} "([^"]*)"', args)
    assert match, flag
    return re.findall(r"[A-Za-z]+(?:\([^)]*\))?", match.group(1))


# ---- 1. pins ------------------------------------------------------------------------------------


def test_every_infisical_step_is_pinned_by_sha_to_a_version_that_reads_secret_name(jobs, raw):
    uses = [(n, s["uses"]) for n, s in all_steps(jobs) if str(s.get("uses", "")).startswith(INFISICAL_REPO)]
    assert len(uses) >= 12
    for _, u in uses:
        assert u == f"{INFISICAL_REPO}@{INFISICAL_SHA}", u
    assert tuple(int(x) for x in INFISICAL_TAG.lstrip("v").split(".")) >= INFISICAL_FIRST_TAG_WITH_SECRET_NAME
    # each `uses:` line names the tag it was resolved from, so a reader can see what the SHA is
    lines = [ln for ln in raw.splitlines() if "uses: Infisical/secrets-action@" in ln]
    assert len(lines) == len(uses)
    for ln in lines:
        m = re.search(r"#\s*v(\d+)\.(\d+)\.(\d+)\s*$", ln)
        assert m, ln
        assert tuple(int(x) for x in m.groups()) >= INFISICAL_FIRST_TAG_WITH_SECRET_NAME


def test_no_infisical_step_is_unfiltered(jobs):
    """Without a name filter the step exports every secret under the path into the agent's env."""
    for job, s in all_steps(jobs):
        if str(s.get("uses", "")).startswith(INFISICAL_REPO):
            name = str(s["with"].get("secret-name", ""))
            assert name and not re.search(r"[\s,*]", name), (job, s.get("name"))
            assert "recursive" not in s["with"] and s["with"].get("export-type", "env") == "env"


def test_claude_code_action_is_pinned_by_sha(jobs):
    for job, step_id in AGENTS:
        assert step(jobs[job], step_id)["uses"] == f"{CLAUDE_ACTION_REPO}@{CLAUDE_ACTION_SHA}"


@pytest.mark.skipif(os.environ.get("REVIEW_LOOP_VERIFY_PINS") != "1", reason="needs network and gh")
def test_pins_match_upstream():
    def gh(*args: str) -> str:
        return subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=True).stdout

    def jget(path: str, *keys: str):
        data = json.loads(gh(path))
        for key in keys:
            data = data[key]
        return data

    for repo, tag, sha in (
        (INFISICAL_REPO, INFISICAL_TAG, INFISICAL_SHA),
        (CLAUDE_ACTION_REPO, CLAUDE_ACTION_TAG, CLAUDE_ACTION_SHA),
    ):
        obj = jget(f"repos/{repo}/git/ref/tags/{tag}", "object")
        if obj["type"] == "tag":  # annotated: one more hop to the commit
            obj = jget(f"repos/{repo}/git/tags/{obj['sha']}", "object")
        assert (obj["type"], obj["sha"]) == ("commit", sha), (repo, tag)
    content = jget(f"repos/{INFISICAL_REPO}/contents/action.yaml?ref={INFISICAL_SHA}", "content")
    inputs = yaml.safe_load(base64.b64decode(content))["inputs"]
    assert "secret-name" in inputs


# ---- 2/4. the agents' permission model ----------------------------------------------------------


@pytest.mark.parametrize(("job", "step_id"), AGENTS)
def test_both_agents_run_in_the_strict_default_permission_mode(jobs, job, step_id):
    """claude-code-action defaults to `auto` (a classifier approves calls outside the allow rules)."""
    args = step(jobs[job], step_id)["with"]["claude_args"]
    assert re.search(r"--permission-mode default(\s|$)", args)
    assert not re.search(r"--permission-mode (auto|acceptEdits|bypassPermissions|dontAsk)", args)
    assert "--dangerously-skip-permissions" not in args


DENIED_READS = [
    "Read(//proc/**)",
    "Read(//root/**)",
    "Read(//etc/**)",
    "Read(//actions-runner/**)",
    "Read(/${{ runner.temp }}/_runner_file_commands/**)",
]
DENIED_BASH = [
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "env",
    "printenv",
    "strings",
    "base64",
    "xxd",
    "od",
]


@pytest.mark.parametrize(("job", "step_id"), AGENTS)
def test_both_agents_cannot_read_the_runner_credentials(jobs, job, step_id):
    """The runner container's PID 1 holds GH_RUNNER_PAT; file commands hold every exported secret."""
    denied = rules(jobs[job], step_id, "--disallowedTools")
    for rule in DENIED_READS:
        assert rule in denied, rule
    for cmd in DENIED_BASH:
        assert f"Bash({cmd}:*)" in denied, cmd
    allowed = rules(jobs[job], step_id, "--allowedTools")
    assert not set(denied) & set(allowed)


@pytest.mark.parametrize(("job", "step_id"), AGENTS)
def test_agents_hold_no_tool_that_executes_repository_code(jobs, job, step_id):
    """Only git and gh: `python -m pytest`, pre-commit, uvx, make... all run code the PR author wrote."""
    bash = [r for r in rules(jobs[job], step_id, "--allowedTools") if r.startswith("Bash(")]
    assert bash
    for rule in bash:
        assert re.match(r"Bash\((git|gh) ", rule), rule
    joined = step(jobs[job], step_id)["with"]["claude_args"]
    for word in (
        "pytest",
        "pre-commit",
        "uvx",
        " uv ",
        "python",
        "ruff",
        "npm",
        "make",
        "bash:",
        "sh:",
    ):
        assert word not in joined, word


def test_the_responder_cannot_post_or_file_issues_itself(jobs):
    """Its text must pass the secret scan first, so the workflow posts it (and files its issues)."""
    allowed = rules(jobs["respond"], "respond-agent", "--allowedTools")
    for banned in ("Bash(gh pr comment:*)", "Bash(gh issue create:*)"):
        assert banned not in allowed
    assert not [r for r in allowed if r.startswith("Bash(gh api")]
    assert any(r.startswith("Write(/${{ runner.temp }}/response/") for r in allowed)
    post = named(jobs["respond"], "Scan and post the responder's comment and issues")
    assert "scan-secrets.sh" in post["run"]
    # the scan script comes from the pinned tooling checkout, not the agent's writable workspace or the PR
    assert "$TOOLING/scripts/scan-secrets.sh" in post["run"]
    assert post["run"].index("scan-secrets") < post["run"].index("gh pr comment")


def test_responder_install_steps_are_gone(jobs):
    """With nothing that runs tests there is nothing to install."""
    uses = [str(s.get("uses", "")) for s in jobs["respond"]["steps"]]
    assert not any(u.startswith("astral-sh/") for u in uses)
    assert not any("uv pip" in s.get("run", "") for s in jobs["respond"]["steps"])


# ---- 3. the push token's job --------------------------------------------------------------------


def test_the_push_job_runs_on_its_own_runner_label_that_has_no_default(jobs):
    push = jobs["push"]
    assert "inputs.push-runs-on" in push["runs-on"]
    assert "inputs.runs-on" not in push["runs-on"] and "ubuntu" not in push["runs-on"]
    assert "self-hosted" not in push["runs-on"]
    cond = " ".join(str(push["if"]).split())
    assert "inputs.push-runs-on != ''" in cond
    assert "inputs.push-runs-on != inputs.runs-on" in cond


def test_the_responder_does_not_even_start_without_a_separate_push_runner(jobs):
    cond = " ".join(str(jobs["respond"]["if"]).split())
    assert "inputs.push-runs-on != ''" in cond
    assert "inputs.push-runs-on != inputs.runs-on" in cond
    assert "needs.claude-review.outputs.outcome == 'changes-requested'" in cond


def test_the_responder_private_key_is_cleared_right_after_minting_in_push(jobs):
    steps = jobs["push"]["steps"]
    mint = next(i for i, s in enumerate(steps) if s.get("id") == "push-token")
    push = next(i for i, s in enumerate(steps) if s.get("id") == "push")
    clearing = [i for i, s in enumerate(steps) if "PRIVATE_KEY=" in s.get("run", "")]
    assert clearing and mint < clearing[0] < push
    run = steps[clearing[0]]["run"]
    assert "CI_REVIEW_RESPONDER_PRIVATE_KEY=" in run and "CI_REVIEW_RESPONDER_APP_ID=" in run


def test_the_push_job_scans_what_it_is_about_to_push_for_secrets(jobs):
    script = step(jobs["push"], "push")["run"]
    assert "scan-secrets.sh" in script
    assert script.rindex("scan-secrets") < script.index('push "$url"')
    assert "$TOOLING/scripts/scan-secrets.sh" in script


def test_report_says_so_when_the_push_runner_is_not_configured(jobs):
    env = named(jobs["report"], "Report the round on the PR")["env"]["PUSHED"]
    assert "'unconfigured'" in env
    assert "inputs.push-runs-on" in env


@pytest.mark.parametrize("state", ["unconfigured"])
def test_review_report_pings_a_human_when_no_push_runner_exists(state):
    out = subprocess.run(
        ["bash", str(REPORT), "changes-requested", "1", state, "0"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    s, label, ping, message = out.split("|", 3)
    assert (s, label, ping) == ("failure", "add", "yes")
    assert "push-runs-on" in message


# ---- 4. nothing token-shaped is posted ----------------------------------------------------------


def _tok(prefix: str, n: int, alphabet: str = "Ab3") -> str:
    # built at run time so no scanner sees a token-shaped literal in the repo
    return prefix + (alphabet * n)[:n]


def scan(*args: str, input_: str | None = None, env: dict | None = None):
    full_env = {k: v for k, v in os.environ.items() if k != "SCAN_ENV_NAMES"}
    full_env.update(env or {})
    return subprocess.run(
        ["bash", str(SCAN), *args],
        input=input_,
        capture_output=True,
        text=True,
        env=full_env,
    )


TOKENS = {
    "ghp": _tok("ghp_", 36),
    "github_pat": _tok("github_pat_", 60, "Ab3_"),
    "ghs": _tok("ghs_", 36),
    "gho": _tok("gho_", 36),
    "ghu": _tok("ghu_", 36),
    "claude-oauth": _tok("sk-ant-" + "oat01-", 80, "Ab3-_"),
    "anthropic-key": _tok("sk-ant-" + "api03-", 80, "Ab3-_"),
    "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEow\n",
    "app-jwt": ".".join(
        [
            "eyJ" + "hbGciOiJSUzI1NiJ9",
            "eyJ" + "pYXQiOjE3MDAwMDAwMDB9",
            "c2lnbmF0dXJlc2lnbmF0dXJl",
        ]
    ),
}


@pytest.mark.parametrize("kind", TOKENS)
def test_scan_refuses_every_token_shape_and_never_echoes_it(tmp_path, kind):
    f = tmp_path / "review.md"
    f.write_text(
        f"## Review\nfound this in the log:\n\n    {TOKENS[kind]}\n\nthe end\n",
        encoding="utf-8",
    )
    result = scan(str(f))
    assert result.returncode == 1
    leaked = TOKENS[kind].splitlines()[0]
    assert leaked not in result.stdout and leaked not in result.stderr
    assert "review.md" in result.stdout + result.stderr


def test_scan_passes_ordinary_review_text(tmp_path):
    f = tmp_path / "review.md"
    f.write_text(
        "## Review\n\n- `ghp_` prefixes are scanned for.\n- mentions github_pat_ and sk-ant- as prefixes only\n"
        "- sha 4d75e5f390ee76b2e09c1980ecc99ff3a2e088c8 and PRIVATE KEY handling are discussed\n",
        encoding="utf-8",
    )
    assert scan(str(f)).returncode == 0


def test_scan_reads_stdin_and_directories(tmp_path):
    assert scan("-", input_="fine\n").returncode == 0
    assert scan("-", input_=f"+ {TOKENS['ghp']}\n").returncode == 1
    d = tmp_path / "review"
    (d / "nested").mkdir(parents=True)
    (d / "ok.md").write_text("fine")
    assert scan(str(d)).returncode == 0
    (d / "nested" / "bad.md").write_text(TOKENS["ghs"])
    result = scan(str(d))
    assert result.returncode == 1 and "bad.md" in result.stdout + result.stderr


def test_scan_matches_the_exact_value_of_named_env_secrets_without_printing_it(
    tmp_path,
):
    f = tmp_path / "x.md"
    odd = "not-shaped-like-any-token-0123456789"
    f.write_text(f"leaked: {odd}\n")
    result = scan(str(f), env={"SCAN_ENV_NAMES": "SOME_SECRET", "SOME_SECRET": odd})
    assert result.returncode == 1
    assert odd not in result.stdout and odd not in result.stderr
    assert "SOME_SECRET" in result.stdout + result.stderr
    assert scan(str(f), env={"SCAN_ENV_NAMES": "SOME_SECRET", "SOME_SECRET": ""}).returncode == 0


def test_scan_fails_closed_on_a_missing_file(tmp_path):
    assert scan(str(tmp_path / "nope.md")).returncode != 0


def test_the_review_job_scans_before_anything_leaves_the_runner(jobs):
    steps = jobs["claude-review"]["steps"]
    names = [s.get("name") for s in steps]
    scan_i = next(i for i, s in enumerate(steps) if s.get("id") == "scan")
    agent_i = next(i for i, s in enumerate(steps) if s.get("id") == "claude-review")
    verdict_i = next(i for i, s in enumerate(steps) if s.get("id") == "verdict")
    upload_i = next(i for i, s in enumerate(steps) if s.get("id") == "upload")
    post_i = next(i for i, s in enumerate(steps) if s.get("id") == "post-review")
    assert agent_i < scan_i < verdict_i < upload_i < post_i, names
    run = steps[scan_i]["run"]
    assert "scan-secrets.sh" in run and "-delete" in run  # a hit removes the files: no artifact, no comment
    assert steps[scan_i]["env"]["SCAN_ENV_NAMES"].split() == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert str(steps[verdict_i]["env"]["LEAKED"]) == "${{ steps.scan.outputs.leaked }}"
    assert "tooling-failure" in steps[verdict_i]["run"] and "LEAKED" in steps[verdict_i]["run"]
    post = steps[post_i]["run"]
    assert "scan-secrets.sh" in post and post.index("scan-secrets") < post.index("gh pr review")


# ---- 5. protected paths: where a scan of text cannot help ---------------------------------------


def judge(*paths: str) -> subprocess.CompletedProcess:
    data = b"".join(p.encode() + b"\0" for p in paths)
    return subprocess.run(["bash", str(SCRIPTS / "protected-paths.sh")], input=data, capture_output=True)


NEWLY_PROTECTED = [
    "Containerfile",
    "apps/x/Containerfile.gpu",
    "Makefile",
    "apps/x/GNUmakefile",
    "justfile",
    "docs/Justfile",
    "scripts/run.bash",
    "docs/setup.ps1",
    "apps/x/foo.path",
    "apps/x/data.mount",
    "compose.prod.yaml",
    "apps/x/compose.prod.yml",
    "apps/x/compose.gpu.yaml",
    "apps/x/compose-extra.yml",
    "docker-compose.prod.yaml",
    "requirements.txt",
    "apps/x/requirements-dev.txt",
    "apps/x/src/requirements.txt",
    "package.json",
    "apps/x/package.json",
    "apps/x/package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "apps/x/tests/uv.lock",
    ".npmrc",
    "apps/x/.npmrc",
    "pytest.ini",
    "apps/x/tests/pytest.ini",
    "tox.ini",
    "setup.cfg",
    "setup.py",
    "tests/__init__.py",
    "apps/x/tests/__init__.py",
    "tests/sitecustomize.py",
    "apps/x/src/sitecustomize.py",
    "apps/x/src/usercustomize.py",
    "apps/x/src/evil.pth",
    "tests/evil.pth",
    "apps/x/foo.service.d/override.conf",
    "apps/x/src/foo.service.d/x.conf",
    "apps/x/src/app.conf",
    "apps/x/src/run.sh.",  # trailing dot: some filesystems read it as run.sh
    "apps/x/src/run.sh ",  # trailing space
    "apps/x/src/deploy.sh. ",
    "docs/ok.md.",
    "apps/x/src/...",
    # `apps/*/src/*` matched at any depth
    "apps/web/overlays/server/src/auth/login.ts",
    "apps/web/overlays/server/src/index.ts",
    "apps/x/vendor/src/lib.py",
    "apps/x/overlays/tests/test_a.py",
    "apps/x/deep/readme.md",
    "apps/x/src",  # the directory entry itself
]


@pytest.mark.parametrize("path", NEWLY_PROTECTED)
def test_newly_protected_path_is_refused_and_named(path):
    result = judge("docs/ok.md", path)
    assert result.returncode == 1
    assert path.encode() in result.stdout.splitlines()


STILL_ALLOWED = [
    "apps/gateway/src/gateway/main.py",
    "apps/gateway/src/main.py",
    "apps/gateway/tests/test_health.py",
    "apps/gateway/tests/unit/test_health.py",
    "tests/gateway/test_health.py",
    "docs/infrastructure/notes.md",
    "apps/proxy/README.md",
    ".agents/adrs/0005-x.md",
    ".agents/guides/x.md",
    ".agents/plans/x.md",
    ".agents/tests/test_x.py",
]


@pytest.mark.parametrize("path", STILL_ALLOWED)
def test_the_intended_directories_stay_editable(path):
    assert judge(path).returncode == 0

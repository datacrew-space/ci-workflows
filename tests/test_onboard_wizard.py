"""Drift guards for scripts/onboard-review-loop.sh.

The wizard cannot be run in CI (it opens browsers and waits for a person), so these tests read it
and the reusable workflow and fail when the two disagree: every secret, variable, folder and App
permission the wizard names must be one the workflow or the caller really uses.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "scripts" / "onboard-review-loop.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "claude-code-review.yml"
CALLER = ROOT / "examples" / "caller.yml"

SCRIPT = WIZARD.read_text(encoding="utf-8")
WORKFLOW_DOC = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
JOBS = WORKFLOW_DOC["jobs"]
# PyYAML reads the key `on` as the boolean True.
ON = WORKFLOW_DOC.get("on", WORKFLOW_DOC.get(True))


def all_steps():
    for job in JOBS.values():
        yield from job.get("steps", [])


def fetched_secret_names() -> set[str]:
    return {s["with"]["secret-name"] for s in all_steps() if "secret-name" in s.get("with", {})}


def stages_part() -> str:
    return SCRIPT.split("# STAGES:", 1)[1]


def wizard_perms() -> dict[str, set[str]]:
    """Run the wizard's own app_perms function, so the test reads what the wizard prints."""
    body = re.search(r"^app_perms\(\) \{.*?^\}", SCRIPT, re.S | re.M).group(0)
    out = {}
    for app in re.findall(r"^APPS=\(([^)]*)\)", SCRIPT, re.M)[0].split():
        res = subprocess.run(
            ["bash", "-c", f'{body}\napp_perms "$1"', "_", app], capture_output=True, text=True, check=True
        )
        out[app] = set(res.stdout.split())
    return out


def test_bash_syntax_is_valid():
    subprocess.run(["bash", "-n", str(WIZARD)], check=True)


def test_every_secret_the_workflow_fetches_is_covered_by_the_wizard():
    names = fetched_secret_names()
    assert names, "no secret-name found: the workflow layout changed"
    apps = re.findall(r"^APPS=\(([^)]*)\)", SCRIPT, re.M)[0].split()
    prefixes = {a.upper().replace("-", "_") for a in apps}
    covered = {f"{p}_APP_ID" for p in prefixes} | {f"{p}_PRIVATE_KEY" for p in prefixes}
    covered.add(re.search(r'^CLAUDE_TOKEN_SECRET="([A-Z_]+)"', SCRIPT, re.M).group(1))
    assert covered == names
    # the wizard builds the App names from the prefix helper, so pin that it really does.
    assert 'app_prefix() { local p="${1^^}"; echo "${p//-/_}"; }' in SCRIPT
    assert '"${_p}_APP_ID' in SCRIPT and '"${_p}_PRIVATE_KEY' in SCRIPT


def test_repo_secrets_set_by_the_wizard_are_the_ones_the_workflow_requires():
    required = set(ON["workflow_call"]["secrets"])
    set_by_wizard = set(re.findall(r"^\s*set_secret (\w+) ", stages_part(), re.M))
    assert set_by_wizard == required
    caller = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    passed = set(caller["jobs"]["review"]["secrets"])
    assert set_by_wizard == passed


def test_repo_variable_is_the_one_the_caller_gates_on():
    caller = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    gate = caller["jobs"]["review"]["if"]
    var = re.search(r'^VARIABLE_NAME="(\w+)"', SCRIPT, re.M).group(1)
    assert f"vars.{var} == 'true'" in gate
    assert re.search(r'set_var "\$VARIABLE_NAME" "true"', SCRIPT)


def test_variable_is_set_after_both_secrets_and_the_caller_is_written():
    s = stages_part()
    secrets = [m.start() for m in re.finditer(r"^\s*set_secret ", s, re.M)]
    var = s.index('set_var "$VARIABLE_NAME"')
    caller = s.index('_write_file "$_dest_caller"')
    assert len(secrets) == 2
    assert max(secrets) < caller < var
    assert s.count("set_var ") == 1
    # and behind a confirmation
    assert re.search(r'(if|elif) confirm "Set \$VARIABLE_NAME=true[^\n]*\n\s*set_var', s)


def test_folder_defaults_match_the_workflow_inputs():
    inputs = ON["workflow_call"]["inputs"]
    assert f"INFISICAL_CLAUDE_PATH:-{inputs['infisical-claude-path']['default']}" in SCRIPT
    assert f"INFISICAL_GITHUB_PATH:-{inputs['infisical-github-path']['default']}" in SCRIPT
    assert f"INFISICAL_ENV_SLUG:-{inputs['infisical-env-slug']['default']}" in SCRIPT
    assert f'DEFAULT_RESPONDER_LOGIN="{inputs['responder-bot-login']['default']}"' in SCRIPT
    assert f'DEFAULT_RESPONDER_ID="{inputs['responder-bot-id']['default']}"' in SCRIPT
    # the optional lines the wizard adds to the caller are real workflow inputs
    for name in re.findall(r"\\n {6}(infisical-[a-z-]+): ", SCRIPT):
        assert name in inputs
    for name in ("infisical-domain", "infisical-project-slug"):
        assert inputs[name]["required"] is True


def test_caller_placeholders_exist_in_the_example():
    caller = CALLER.read_text(encoding="utf-8")
    for var in ("PH_SHA", "PH_DOMAIN", "PH_SLUG"):
        placeholder = re.search(rf"^{var}='([^']+)'", SCRIPT, re.M).group(1)
        assert placeholder in caller, f"{placeholder} is gone from examples/caller.yml"


def test_agent_prompt_templates_exist_and_default_paths_match():
    inputs = ON["workflow_call"]["inputs"]
    for fname in ("code-reviewer.md", "review-responder.md"):
        assert (ROOT / "examples" / fname).is_file()
        assert f'"$_dest_agents/{fname}"' in SCRIPT
    assert re.search(r'^_dest_agents="\$TARGET_DIR/(.+)"', SCRIPT, re.M).group(1) == ".agents/agents"
    assert inputs["reviewer-agent"]["default"] == ".agents/agents/code-reviewer.md"
    assert inputs["responder-agent"]["default"] == ".agents/agents/review-responder.md"


# ---- App permissions ----------------------------------------------------------------------------


def minted_permissions() -> dict[str, set[str]]:
    """Union of the permission-* inputs over every create-github-app-token step, per App secret."""
    out: dict[str, set[str]] = {}
    for step in all_steps():
        w = step.get("with", {})
        if "private-key" not in w:
            continue
        app_id = re.search(r"env\.(\w+)_APP_ID", w["app-id"]).group(1)
        perms = {f"{k.removeprefix('permission-')}:{v}" for k, v in w.items() if k.startswith("permission-")}
        out.setdefault(app_id, set()).update(perms)
    return out


@pytest.mark.parametrize("app", ["ci-code-reviewer", "ci-review-responder", "ci-review-orchestrator"])
def test_app_permissions_in_the_wizard_equal_what_the_workflow_mints(app):
    prefix = app.upper().replace("-", "_")
    minted = minted_permissions()[prefix]
    # Per permission, the App needs the strongest level any of its tokens asks for.
    need: dict[str, str] = {}
    for p in minted:
        name, level = p.split(":")
        if need.get(name) != "write":
            need[name] = level
    granted = wizard_perms()[app]
    assert granted == {f"{n}:{lv}" for n, lv in need.items()}


def test_every_app_with_minted_tokens_is_in_the_wizard():
    assert set(minted_permissions()) == {a.upper().replace("-", "_") for a in wizard_perms()}


# ---- Secret hygiene -----------------------------------------------------------------------------


def test_no_shell_tracing():
    for line in SCRIPT.splitlines():
        code = line.split("#", 1)[0]
        assert not re.search(r"\bset\s+-\w*x", code), line
        assert "xtrace" not in code, line
        assert "BASH_XTRACEFD" not in code, line


def test_secret_values_are_only_ever_passed_to_gh_over_stdin():
    for name in ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET"):
        for line in stages_part().splitlines():
            code = line.split("#", 1)[0]
            if f"${name}" not in code and f"${{{name}" not in code:
                continue
            assert code.strip().startswith("set_secret "), f"secret value used outside set_secret: {line}"
    # entered hidden
    assert re.search(r"^need_secret INFISICAL_CLIENT_SECRET ", stages_part(), re.M)
    assert re.search(r"^need_secret INFISICAL_CLIENT_ID ", stages_part(), re.M)
    # no echo of them, and no dotenv write of them
    assert not re.search(r"write_env\s+INFISICAL_CLIENT", SCRIPT)
    assert not re.search(r"(echo|printf)[^\n]*INFISICAL_CLIENT", SCRIPT)
    # never on a command line, where ps would show it
    assert not re.search(r"gh secret set [^|\n]*--body", SCRIPT)


def test_the_wizard_never_reads_a_private_key_file():
    code = "\n".join(line.split("#", 1)[0] for line in stages_part().splitlines())
    assert not re.search(r"\bcat\b[^\n]*\.pem|<[^\n]*\.pem", code)


def test_the_wizard_writes_only_inside_the_chosen_checkout():
    s = stages_part()
    assert "ENV_FILE=/dev/null" in s
    assert not re.search(r"\bgit\s+(push|commit|add)\b", s)
    assert not re.search(r"gh\s+pr\s+merge", SCRIPT)

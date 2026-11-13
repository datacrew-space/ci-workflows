"""Guards on .github/workflows/claude-code-review.yml that no real run can cheaply check.

Each assertion pins a property a reviewer found missing or a rule of the review flow:
the responder's pushes must not be refused as a bot actor, agents must never hold write power
over the verdict or the branch, and every non-green end state must reach a human.
"""

import re
from pathlib import Path

import pytest
import yaml

CALLER = Path(__file__).resolve().parents[1] / "examples" / "caller.yml"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "claude-code-review.yml"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def step(job: dict, step_id: str) -> dict:
    return next(s for s in job["steps"] if s.get("id") == step_id)


def test_review_step_accepts_the_responder_as_a_bot_actor(jobs):
    """Without this the round-2 review fails on the responder's own push and never escalates."""
    allowed = step(jobs["claude-review"], "claude-review")["with"]["allowed_bots"]
    assert "ci-review-responder" in allowed.split(",")


@pytest.mark.parametrize(("job", "step_id"), [("claude-review", "agent-token"), ("respond", "agent-token")])
def test_agent_tokens_cannot_write_contents_or_statuses(jobs, job, step_id):
    perms = {k for k, v in step(jobs[job], step_id)["with"].items() if k.startswith("permission-")}
    assert "permission-statuses" not in perms or step(jobs[job], step_id)["with"]["permission-statuses"] == "read"
    assert step(jobs[job], step_id)["with"].get("permission-contents") == "read"


def test_only_the_push_token_can_write_contents(jobs):
    writers = [
        (name, s["id"])
        for name, job in jobs.items()
        for s in job["steps"]
        if s.get("with", {}).get("permission-contents") == "write"
    ]
    assert writers == [("push", "push-token")]


def test_agent_steps_never_see_the_writer_tokens(jobs):
    """The verdict writer and push tokens come after the agent step and are not passed to it."""
    for job_name, agent_id in (
        ("claude-review", "claude-review"),
        ("respond", "respond-agent"),
    ):
        if job_name == "respond":  # the push token lives in another job entirely
            assert not any("push-token" in str(x) or "writer-token" in str(x) for x in jobs[job_name]["steps"])
            continue
        steps = jobs[job_name]["steps"]
        agent_index = next(i for i, s in enumerate(steps) if s.get("id") == agent_id)
        later_ids = {s.get("id") for s in steps[agent_index + 1 :]}
        earlier_ids = {s.get("id") for s in steps[:agent_index]}
        assert not ({"writer-token", "push-token"} & earlier_ids)
        assert {"writer-token", "push-token"} & later_ids
        agent_with = steps[agent_index]["with"]
        assert "writer-token" not in str(agent_with) and "push-token" not in str(agent_with)


@pytest.mark.parametrize("job_name", ["claude-review", "respond"])
def test_keys_are_cleared_before_the_agent_runs(jobs, job_name):
    steps = jobs[job_name]["steps"]
    agent_index = next(i for i, s in enumerate(steps) if s.get("id") in ("claude-review", "respond-agent"))
    clearing = [i for i, s in enumerate(steps[:agent_index]) if "PRIVATE_KEY" in s.get("run", "")]
    assert clearing, "no step clears the app keys before the agent step"


def test_respond_only_runs_for_a_round_one_changes_requested(jobs):
    cond = " ".join(str(jobs["respond"]["if"]).split())
    assert cond == (
        "${{ needs.claude-review.outputs.outcome == 'changes-requested'"
        " && inputs.push-runs-on != ''"
        " && inputs.push-runs-on != inputs.runs-on }}"
    )


def test_report_runs_after_every_review_that_ran(jobs):
    """Every run ends with one orchestrator report: approved, pending, tooling failure or needs-human."""
    assert " ".join(str(jobs["report"]["if"]).split()) == (
        "${{ !cancelled() && needs.claude-review.result != 'skipped' }}"
    )
    assert jobs["report"]["needs"] == ["claude-review", "respond", "push"]
    assert "escalate" not in jobs


def test_only_the_report_job_holds_the_orchestrator_key(jobs):
    """The orchestrator writes the status and the label, so no agent may share its job."""
    holders = [
        name for name, job in jobs.items() for s in job["steps"] if "CI_REVIEW_ORCHESTRATOR" in str(s.get("with", {}))
    ]
    assert set(holders) == {"report"}  # the fetch and mint steps are all in the job with no agent
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in jobs["report"]["steps"])


def test_only_the_orchestrator_writes_the_verdict_label_and_loop_comment(jobs):
    """One identity runs the loop: no other job sets a final status, edits labels or posts round comments."""
    for name, job in jobs.items():
        for s in job["steps"]:
            run = s.get("run", "")
            if name == "report":
                continue
            assert "needs-human" not in run, (name, s.get("name"))
            assert "Review loop" not in run, (name, s.get("name"))
            if "statuses/" in run:
                assert "state=pending" in run and "github.token" in str(s.get("env", {})), (name, s.get("name"))


def _report_script(jobs: dict) -> str:
    return next(s for s in jobs["report"]["steps"] if s.get("name") == "Report the round on the PR")["run"]


def test_the_orchestrator_posts_one_new_comment_per_run(jobs):
    """Each run's line sits right after that run's review, so the timeline reads in workflow order.

    No editing an earlier comment (it stayed where it was first posted, above every later review).
    """
    script = _report_script(jobs)
    assert script.count('"repos/$REPO/issues/$PR_NUMBER/comments" -f body=') == 1
    assert "-X PATCH" not in script
    assert "issues/comments/" not in script


def test_the_mention_rides_in_that_same_comment(jobs):
    """A needs-human run pings in its own round comment, not in a second one."""
    script = _report_script(jobs)
    assert '[ "$ping" = yes ]' in script
    assert "@${OWNER}" in script
    assert script.index("@${OWNER}") < script.index('"repos/$REPO/issues/$PR_NUMBER/comments" -f body=')


def test_the_review_job_cannot_write_pull_requests_with_github_token(jobs):
    assert jobs["claude-review"]["permissions"] == {
        "contents": "read",
        "statuses": "write",
    }


def test_a_responder_that_never_reached_its_push_is_reported_as_failed(jobs):
    """An empty `pushed` would read as "pushed nothing" and wrongly ping a human.

    The push step always sets `pushed` when it runs, so after a changes-requested verdict an empty
    value means `respond` crashed, or never started because the review job failed: tooling.
    """
    env = next(s for s in jobs["report"]["steps"] if s.get("name") == "Report the round on the PR")["env"]
    assert env["PUSHED"] == (
        "${{ (needs.claude-review.outputs.outcome == 'changes-requested'"
        " && (inputs.push-runs-on == '' || inputs.push-runs-on == inputs.runs-on)"
        " && 'unconfigured') || needs.push.outputs.pushed || needs.respond.outputs.pushed"
        " || (needs.claude-review.outputs.outcome == 'changes-requested' && 'failed' || '') }}"
    )


def test_a_failed_post_does_not_fail_the_review_job(jobs):
    """The verdict is already set; a posting error must not skip the responder."""
    assert step(jobs["claude-review"], "post-review").get("continue-on-error") is True


def test_round_two_only_follows_a_responder_push(jobs):
    prior = step(jobs["claude-review"], "verdict")["env"]["PRIOR"]
    assert prior == "${{ github.event.sender.login == 'ci-review-responder[bot]' && '1' || '0' }}"


def test_the_responders_push_does_not_cancel_the_run_that_pushed(jobs):
    """Otherwise the new run cancels the old one before its report job logs the push."""
    # A reusable workflow cannot set workflow-level concurrency; the example caller owns it.
    caller = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    assert caller["concurrency"]["cancel-in-progress"] == (
        "${{ github.event.sender.login != 'ci-review-responder[bot]' }}"
    )


def test_reviewer_agent_cannot_post_or_approve_reviews(jobs):
    """Read-only on pull requests, and no `gh pr review` tool: it cannot run `--approve`."""
    assert step(jobs["claude-review"], "agent-token")["with"]["permission-pull-requests"] == "read"
    tools = step(jobs["claude-review"], "claude-review")["with"]["claude_args"]
    assert "gh pr review" not in tools


def test_the_workflow_posts_the_review_as_a_comment_with_the_report_link(jobs):
    """The agent writes the review to a file; a later step posts it, comment-only, link included."""
    steps = jobs["claude-review"]["steps"]
    ids = [s.get("id") for s in steps]
    assert ids.index("post-review") > ids.index("claude-review")
    assert ids.index("post-review") > ids.index("upload")  # the artifact URL exists by then
    post = step(jobs["claude-review"], "post-review")
    assert post["env"]["GH_TOKEN"] == "${{ steps.writer-token.outputs.token }}"
    assert "--comment" in post["run"]
    assert "--approve" not in post["run"] and "--request-changes" not in post["run"]
    assert "REPORT_URL" in post["run"]
    runs = " ".join(s.get("run", "") for s in steps)
    assert "gh pr comment" not in runs
    assert "reviews/$id" not in runs  # no editing a posted review (it duplicated the link on re-runs)


def test_encoded_push_token_is_masked_before_use(jobs):
    """create-github-app-token masks the raw token only; its base64 form must be masked too."""
    script = step(jobs["push"], "push")["run"]
    assert 'echo "::add-mask::$b64"' in script
    assert script.index("::add-mask::") < script.index("extraheader")


def test_push_step_never_forces_and_pushes_from_the_verified_fresh_repo(jobs):
    """What the guards, size cap and scans do is tested with real bundles in test_verify_fix_bundle.py."""
    script = step(jobs["push"], "push")["run"]
    assert "--force" not in script and " -f " not in script and "+refs" not in script
    assert "core.hooksPath=/dev/null" in script  # nothing the agent wrote under .git/ runs with the token
    assert 'git -C "$CLEAN"' in script and "refs/heads/fix:refs/heads/$BRANCH" in script
    assert step(jobs["push"], "push")["env"]["CLEAN"] == "${{ steps.verify.outputs.clean }}"
    checkout = next(s for s in jobs["push"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False


def test_the_verifier_comes_from_the_pinned_tooling_not_the_agents_workspace(jobs):
    """The agent edits the workspace; reading the verifier or guards from there would let it disarm them."""
    verify = step(jobs["push"], "verify")
    assert verify["run"].strip() == (
        'bash "$TOOLING/scripts/verify-fix-bundle.sh" "$BUNDLE" "$REVIEWED_SHA" "$BRANCH" "$GITHUB_WORKSPACE"'
    )
    assert verify["env"]["BUNDLE"] == "${{ runner.temp }}/in/fix.bundle"
    assert verify["env"]["BRANCH"] == "${{ github.event.pull_request.head.ref }}"
    assert verify["env"]["REVIEWED_SHA"] == "${{ github.event.pull_request.head.sha }}"
    for s in jobs["push"]["steps"]:
        assert 'bash "$GITHUB_WORKSPACE' not in s.get("run", "")


def test_nothing_is_pushed_unless_the_verify_step_passed_and_said_ready(jobs):
    """The verify step may not be soft-failed, and it runs before any token exists in the job."""
    steps = jobs["push"]["steps"]
    ids = [s.get("id") for s in steps]
    verify = step(jobs["push"], "verify")
    assert "continue-on-error" not in verify and "if" not in verify
    assert "|| true" not in verify["run"] and "set +e" not in verify["run"]
    assert ids.index("verify") < ids.index("push-token") < ids.index("push")
    assert step(jobs["push"], "push")["if"] == "${{ steps.verify.outputs.ready == 'true' }}"


def test_the_responder_cannot_run_docker_or_deploy(jobs):
    """AGENTS.md: never bare `docker compose up`; deploy only through deploy.sh. The agent gets neither."""
    for job, step_id in (
        ("claude-review", "claude-review"),
        ("respond", "respond-agent"),
    ):
        tools = step(jobs[job], step_id)["with"]["claude_args"]
        assert "docker" not in tools and "deploy" not in tools and "curl" not in tools


def test_review_is_opt_in_and_skips_drafts_and_forks(jobs):
    cond = jobs["claude-review"]["if"]
    # Opt-in is the caller's gate (the example caller), not this workflow's.
    caller = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    assert "vars.CLAUDE_REVIEW_ENABLED == 'true'" in caller["jobs"]["review"]["if"]
    assert "!github.event.pull_request.draft" in cond
    assert "github.event.pull_request.head.repo.full_name == github.repository" in cond


def test_exactly_one_workflow_reviews_pull_requests():
    """The generic code-review plugin run was replaced, not kept beside this one."""
    workflows = WORKFLOW.parent
    reviewers = [
        w.name
        for w in sorted(workflows.glob("*.yml"))
        if "code-review@claude-code-plugins" in w.read_text(encoding="utf-8")
    ]
    assert reviewers == []


def test_reviewer_token_can_read_ci_but_not_write_statuses(jobs):
    """`gh pr checks` needs checks, actions and statuses read; none of them may be write."""
    w = step(jobs["claude-review"], "agent-token")["with"]
    assert w["permission-checks"] == "read"
    assert w["permission-actions"] == "read"
    assert w["permission-statuses"].split()[0] == "read"


def test_no_literal_backslash_n_in_run_scripts(jobs):
    r"""A literal `\n` typed where a line break was meant turns `git ... \n push` into `git ... n push`.

    Only printf format strings may contain it.
    """
    backslash_n = "\\" + "n"
    for name, job in jobs.items():
        for s in job["steps"]:
            for line in s.get("run", "").splitlines():
                assert backslash_n not in line or "printf" in line, (
                    name,
                    s.get("name"),
                    line,
                )


# ---- review-loop hardening (independent review of #508) -------------------------------------------


def _rules(job: dict, step_id: str, flag: str = "--allowedTools") -> list[str]:
    args = step(job, step_id)["with"]["claude_args"]
    match = re.search(rf'{flag} "([^"]*)"', args)
    assert match, flag
    return re.findall(r"[A-Za-z]+(?:\([^)]*\))?", match.group(1))


def _matches(rule: str, command: str) -> bool:
    """Claude Code's documented Bash matching: no `*` is an exact command, `*` is any text (spaces too)."""
    inner = rule[len("Bash(") : -1]
    if inner.endswith(":*"):
        inner = inner[:-2] + " *"
    return re.fullmatch(".*".join(re.escape(part) for part in inner.split("*")), command) is not None


ATTACKS = [
    "git fetch --upload-pack='sh -c \"echo PWNED > pwned.txt\" #' .",
    "git fetch origin main",
    "git diff --output=out.txt",
    "git diff origin/main...HEAD --output=out.txt",
    "git diff --ext-diff",
    "git diff -c core.pager=sh",
    "git log --output=x",
    "git log --exec-path=/tmp",
    "git show --output=x HEAD",
    "git status --output=x",
    "git show HEAD",
]


@pytest.mark.parametrize(
    ("job", "step_id"),
    [("claude-review", "claude-review"), ("respond", "respond-agent")],
)
@pytest.mark.parametrize("attack", ATTACKS)
def test_no_allowlist_rule_admits_a_git_flag_injection(jobs, job, step_id, attack):
    """`git diff:*` admitted `--output=FILE` and `git fetch:*` admitted `--upload-pack=CMD` (command execution)."""
    bash_rules = [r for r in _rules(jobs[job], step_id) if r.startswith("Bash(")]
    assert [r for r in bash_rules if _matches(r, attack)] == []


@pytest.mark.parametrize(
    ("job", "step_id"),
    [("claude-review", "claude-review"), ("respond", "respond-agent")],
)
def test_git_read_rules_are_exact_commands_and_fetch_is_gone(jobs, job, step_id):
    for rule in _rules(jobs[job], step_id):
        if re.match(r"Bash\(git (diff|log|show|status|fetch|pull|push|config|remote)", rule):
            assert "*" not in rule, rule
        assert not rule.startswith(("Bash(git fetch", "Bash(git show", "Bash(git push", "Bash(git config"))


def test_the_only_wildcard_git_rules_are_the_pathspec_and_message_forms(jobs):
    wild = [r for r in _rules(jobs["respond"], "respond-agent") if r.startswith("Bash(git") and "*" in r]
    assert sorted(wild) == ["Bash(git add -- *)", "Bash(git commit -m *)"]
    assert not [r for r in _rules(jobs["claude-review"], "claude-review") if r.startswith("Bash(git") and "*" in r]


def test_responder_writes_are_scoped_to_the_workspace_and_never_touch_git_internals(
    jobs,
):
    """Bare `Write`/`Edit` could reach $GITHUB_ENV, $GITHUB_PATH, runner temp, the home dir and .git/hooks."""
    allowed = _rules(jobs["respond"], "respond-agent")
    assert "Write" not in allowed and "Edit" not in allowed
    assert "Write(/${{ github.workspace }}/**)" in allowed and "Edit(/${{ github.workspace }}/**)" in allowed
    denied = _rules(jobs["respond"], "respond-agent", "--disallowedTools")
    assert "Write(/${{ github.workspace }}/.git/**)" in denied and "Edit(/${{ github.workspace }}/.git/**)" in denied


def test_reviewer_writes_stay_scoped_to_the_review_folder(jobs):
    allowed = _rules(jobs["claude-review"], "claude-review")
    assert "Write" not in allowed and "Edit" not in allowed
    scoped = [r for r in allowed if r.startswith(("Write(", "Edit("))]
    assert scoped and all("/review/**" in r for r in scoped)


def test_the_push_runs_in_its_own_job_with_no_agent(jobs):
    """The contents:write token must not be minted in the job (and env) where an agent ran code."""
    assert "push" in jobs and jobs["push"]["needs"] == "respond"
    assert not any("claude-code-action" in str(s.get("uses", "")) for s in jobs["push"]["steps"])
    for name, job in jobs.items():
        if name != "push":
            assert not any(s.get("id") == "push-token" for s in job["steps"])
    assert jobs["push"]["permissions"] == {"contents": "read"}


def test_the_push_job_gets_the_agents_commits_only_as_a_validated_bundle(jobs):
    """Artifact in, validated, never executed: the push job never checks out or runs agent files."""
    respond_runs = " ".join(s.get("run", "") for s in jobs["respond"]["steps"])
    assert "bundle create" in respond_runs and "core.hooksPath=/dev/null" in respond_runs
    uploads = [s for s in jobs["respond"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert uploads and all(s["with"]["path"].endswith(".bundle") for s in uploads)
    steps = jobs["push"]["steps"]
    assert any(str(s.get("uses", "")).startswith("actions/download-artifact") for s in steps)
    ids = [s.get("id") for s in steps]
    download = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/download-artifact"))
    assert download < ids.index("verify")
    script = step(jobs["push"], "verify")["run"]
    assert 'bash "$GITHUB_WORKSPACE' not in script


# Every secret the loop is allowed to hold. A new name must be added here on purpose.
KNOWN_SECRETS = {
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CI_CODE_REVIEWER_APP_ID",
    "CI_CODE_REVIEWER_PRIVATE_KEY",
    "CI_REVIEW_RESPONDER_APP_ID",
    "CI_REVIEW_RESPONDER_PRIVATE_KEY",
    "CI_REVIEW_ORCHESTRATOR_APP_ID",
    "CI_REVIEW_ORCHESTRATOR_PRIVATE_KEY",
}


def test_every_infisical_fetch_names_exactly_one_known_secret(jobs):
    """A path fetch exports every secret under /github or /claude; a new one would land in the agent env."""
    fetched = []
    for name, job in jobs.items():
        for s in job["steps"]:
            if str(s.get("uses", "")).startswith("Infisical/secrets-action"):
                secret = s["with"].get("secret-name")
                assert secret, (
                    name,
                    s.get("name"),
                    "fetches a whole path; use secret-name",
                )
                assert "recursive" not in s["with"]
                fetched.append(secret)
    assert fetched
    assert set(fetched) <= KNOWN_SECRETS


def test_agent_jobs_fetch_only_what_their_agent_needs(jobs):
    for job, app in (
        ("claude-review", "CI_CODE_REVIEWER"),
        ("respond", "CI_REVIEW_RESPONDER"),
    ):
        agent_at = next(i for i, s in enumerate(jobs[job]["steps"]) if "claude-code-action" in str(s.get("uses", "")))
        before = [
            s["with"]["secret-name"]
            for s in jobs[job]["steps"][:agent_at]
            if str(s.get("uses", "")).startswith("Infisical")
        ]
        assert sorted(before) == sorted(["CLAUDE_CODE_OAUTH_TOKEN", f"{app}_APP_ID", f"{app}_PRIVATE_KEY"])
        clearing = next(s for s in jobs[job]["steps"][:agent_at] if "PRIVATE_KEY" in s.get("run", ""))["run"]
        assert f"{app}_PRIVATE_KEY=" in clearing and f"{app}_APP_ID=" in clearing
        # The named clears AND a pattern clear: a secret an action leaves in the env by mistake
        # (finding 1 of the #509 review) must still be blanked before the agent runs.
        assert "compgen -e" in clearing and "_(PRIVATE_KEY|APP_ID)$" in clearing


def test_secret_name_is_only_used_with_an_action_version_that_supports_it(jobs):
    """secrets-action ignores `secret-name` before v1.0.17 and silently exports the whole path."""
    pins = {
        s["uses"]
        for job in jobs.values()
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("Infisical/secrets-action")
    }
    assert pins
    for uses in pins:
        _, ref = uses.split("@")
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"pin the action by commit SHA: {uses}"
    text = WORKFLOW.read_text(encoding="utf-8")
    versions = re.findall(r"Infisical/secrets-action@[0-9a-f]{40} # v(\d+)\.(\d+)\.(\d+)", text)
    assert versions and all(tuple(map(int, v)) >= (1, 0, 17) for v in versions)


def test_the_push_job_never_runs_on_the_agents_runner(jobs):
    """A persistent self-hosted runner container persists between jobs: an agent could plant git or a gitconfig.

    So the push job's label is its own input with no default (set it to
    '"ubuntu-latest"' for GitHub-hosted, or to a dedicated runner). It may never fall back to `runs-on`.
    """
    runs_on = jobs["push"]["runs-on"]
    assert "inputs.push-runs-on" in runs_on and "inputs.runs-on" not in runs_on, runs_on


def test_the_orchestrator_key_is_still_only_in_the_report_job(jobs):
    names = {n for n, job in jobs.items() for s in job["steps"] if "ORCHESTRATOR" in str(s.get("with", {}))}
    assert names == {"report"}

"""Structure of the lib-release reusable workflow and its caller example.

Assertions compare whole parsed values, not substrings, so a flipped clause fails a test.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "lib-release.yml"
CALLER = ROOT / "examples" / "lib-release-caller.yml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


DOC = load(WF)
CALL = (DOC.get("on") or DOC.get(True))["workflow_call"]  # YAML 1.1 parses the bare key `on` as True
JOBS = DOC["jobs"]
RELEASED = "needs.release.outputs.released == 'true'"


def step(job: str, name: str) -> dict:
    (found,) = [s for s in JOBS[job]["steps"] if s.get("name") == name]
    return found


def all_steps():
    for job, body in JOBS.items():
        for s in body["steps"]:
            yield job, s


def test_jobs_are_release_then_build_publish_and_image():
    assert list(JOBS) == ["release", "build-python", "publish-python", "image"]


def test_inputs_and_defaults():
    inputs = CALL["inputs"]
    assert inputs["tooling-ref"]["required"] is True
    assert {k: v["default"] for k, v in inputs.items() if "default" in v} == {
        "runs-on": '"ubuntu-latest"',
        "release-type": "python",
        "config-file": "",
        "manifest-file": "",
        "main-branch": "main",
        "required-check": "ci-ok",
        "python-version": "3.13",
        "publish-python": True,
        "pypi-environment": "pypi",
        "pypi-repository-url": "",
        "image-name": "",
        "dockerfile": "Dockerfile",
        "context": ".",
        "platforms": "linux/amd64",
    }


def test_the_only_secret_is_an_optional_release_token():
    assert list(CALL["secrets"]) == ["RELEASE_TOKEN"]
    assert CALL["secrets"]["RELEASE_TOKEN"]["required"] is False


def test_workflow_default_permissions_grant_nothing():
    assert DOC["permissions"] == {}


def test_each_job_gets_only_the_permissions_it_needs():
    assert JOBS["release"]["permissions"] == {"contents": "write", "pull-requests": "write", "checks": "read"}
    assert JOBS["build-python"]["permissions"] == {"contents": "read"}
    # Only the job that uploads may mint the OIDC token; the job that runs the build code may not.
    assert JOBS["publish-python"]["permissions"] == {"id-token": "write"}
    assert JOBS["image"]["permissions"] == {"contents": "read", "packages": "write"}
    for job, body in JOBS.items():
        if job != "publish-python":
            assert "id-token" not in body["permissions"]


def test_release_runs_only_on_the_main_branch():
    assert JOBS["release"]["if"] == "github.ref_name == inputs.main-branch"


def test_build_and_publish_run_only_when_a_release_was_created():
    assert JOBS["build-python"]["if"] == f"{RELEASED} && inputs.publish-python"
    assert JOBS["publish-python"]["if"] == f"{RELEASED} && inputs.publish-python"
    assert JOBS["image"]["if"] == f"{RELEASED} && inputs.image-name != ''"
    for job in ("build-python", "publish-python", "image"):
        assert "release" in JOBS[job]["needs"]
    assert JOBS["publish-python"]["needs"] == ["release", "build-python"]


def test_publish_uses_the_named_environment_and_the_default_registry_when_unset():
    job = JOBS["publish-python"]
    assert job["environment"] == "${{ inputs.pypi-environment }}"
    publish = step("publish-python", "Publish to the package index")
    assert publish["with"]["repository-url"] == "${{ inputs.pypi-repository-url || 'https://upload.pypi.org/legacy/' }}"
    assert publish["with"]["packages-dir"] == "dist/"


def test_the_green_check_is_verified_for_the_release_commit_before_anything_publishes():
    verify = step("release", "Require the check to be green on the release commit")
    assert verify["if"] == "steps.rp.outputs.release_created == 'true'"
    assert verify["env"]["SHA"] == "${{ steps.rp.outputs.sha }}"
    assert "$TOOLING/scripts/check_required_green.py" in verify["run"]
    assert 'if [ "$rc" -ne 2 ]; then exit "$rc"; fi' in verify["run"]
    # Outputs are set only after the check passed, so a red commit publishes nothing.
    assert JOBS["release"]["outputs"]["released"] == "${{ steps.done.outputs.released }}"
    names = [s.get("name") for s in JOBS["release"]["steps"]]
    assert names.index("Require the check to be green on the release commit") < names.index("Record the release")


def test_builds_use_the_release_commit_and_keep_no_credentials():
    for job in ("build-python", "image"):
        checkout = JOBS[job]["steps"][0]
        assert checkout["uses"] == "actions/checkout@v4"
        assert checkout["with"] == {"ref": "${{ needs.release.outputs.sha }}", "persist-credentials": False}


def test_image_name_must_be_a_lowercase_ghcr_name():
    run = step("image", "Check the image name")["run"]
    assert 'ghcr.io/*) ;;' in run
    assert 'tr -d' not in run  # validation, not silent rewriting
    assert step("image", "Check the image name")["env"] == {"IMAGE": "${{ inputs.image-name }}"}


def test_image_is_tagged_by_version_and_commit_never_latest():
    tags = step("image", "Build and push")["with"]["tags"].strip()
    assert tags == "${{ inputs.image-name }}:${{ needs.release.outputs.version }}\n${{ inputs.image-name }}:${{ needs.release.outputs.sha }}"
    assert ":latest" not in WF.read_text()  # a moving tag would defeat pinning by digest


def test_third_party_actions_are_pinned_to_a_full_commit_sha():
    for job, s in all_steps():
        uses = s.get("uses")
        if not uses or uses.startswith("actions/"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), f"{job}: {uses} is not pinned by sha"


def test_no_expression_is_interpolated_into_a_run_script():
    for job, s in all_steps():
        assert "${{" not in s.get("run", ""), f"{job}: step {s.get('name')!r} interpolates into run"


def test_the_release_token_is_used_in_exactly_one_place():
    holders = [(j, s["name"]) for j, s in all_steps() if "RELEASE_TOKEN" in str(s)]
    assert holders == [("release", "Release PR and tag")]
    assert WF.read_text().count("secrets.RELEASE_TOKEN") == 1


def test_scripts_come_from_the_pinned_tooling_ref():
    assert step("release", "Fetch the release tooling")["env"] == {"TOOLING_REF": "${{ inputs.tooling-ref }}"}


def test_outputs_forward_from_the_jobs():
    assert sorted(CALL["outputs"]) == ["image-digest", "released", "sha", "tag", "version"]
    assert CALL["outputs"]["image-digest"]["value"] == "${{ jobs.image.outputs.digest }}"
    for key in ("released", "sha", "tag", "version"):
        assert CALL["outputs"][key]["value"] == f"${{{{ jobs.release.outputs.{key} }}}}"


def test_the_only_hosts_named_are_public_registries_and_github():
    hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", WF.read_text() + CALLER.read_text()))
    assert hosts == {"github.com", "upload.pypi.org"}


def test_caller_example_gates_on_ci_ok_and_passes_the_pinned_ref():
    job = load(CALLER)["jobs"]["release"]
    assert job["needs"] == "ci-ok"
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert job["uses"].startswith("datacrew-space/ci-workflows/.github/workflows/lib-release.yml@")
    assert job["with"]["tooling-ref"] == "<COMMIT_SHA>"
    assert job["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "checks": "read",
        "id-token": "write",
        "packages": "write",
    }

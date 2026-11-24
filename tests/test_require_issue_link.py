"""Named assertions for scripts/require_issue_link.py, each with a negative case."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples" / "hooks"))

from require_issue_link import check_command  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "examples" / "hooks" / "require_issue_link.py"


def test_pr_create_without_an_issue_link_is_blocked():
    assert check_command('gh pr create --title "x" --body "Adds a thing"')


def test_closes_in_the_body_passes():
    assert check_command('gh pr create --title "x" --body "Adds a thing. Closes #4"') is None


def test_refs_also_passes_for_partial_work():
    assert check_command('gh pr create --body "Part of the status page. Refs #8"') is None


def test_closes_in_a_body_file_passes(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("## Summary\nCloses #4\n", encoding="utf-8")

    assert check_command(f'gh pr create --title x --body-file "{body}"') is None


def test_body_file_without_a_link_is_blocked(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("## Summary\nNo link here\n", encoding="utf-8")

    assert check_command(f"gh pr create --title x --body-file {body}")


def test_an_unreadable_body_file_is_blocked_not_waved_through(tmp_path):
    assert check_command(f"gh pr create --body-file {tmp_path / 'missing.md'}")


def test_other_commands_are_not_checked():
    assert check_command("git push") is None
    assert check_command("gh pr view 25") is None
    assert check_command("gh issue list --state open") is None


def test_a_hash_number_that_is_not_an_issue_link_does_not_pass():
    assert check_command('gh pr create --body "Fixes the typo on line #4"')


def run_hook(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True, text=True, check=False)


def test_hook_exits_2_with_the_reason_to_block_and_0_to_allow():
    blocked = run_hook('gh pr create --body "no link"')
    allowed = run_hook('gh pr create --body "Closes #4"')

    assert blocked.returncode == 2
    assert "ready-for-agent" in blocked.stderr
    assert allowed.returncode == 0


def test_hook_fails_closed_on_unparseable_input():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], input="not json", capture_output=True, text=True, check=False
    )

    assert result.returncode == 2


def test_the_phrase_inside_a_quoted_string_is_not_a_pr_create():
    assert check_command('git commit -m "docs: mention gh pr create in the guide"') is None
    assert check_command("echo 'run gh pr create later'") is None


def test_the_phrase_inside_a_heredoc_is_not_a_pr_create():
    command = "python - <<'EOF'\nnote = 'the hook blocks gh pr create'\nEOF\ngh pr view 26"

    assert check_command(command) is None


def test_a_real_pr_create_after_other_commands_is_still_checked():
    assert check_command('cd repo && gh pr create --title x --body "no link"')
    assert check_command('cd repo && gh pr create --title x --body "Closes #4"') is None


def test_a_pr_create_wrapped_in_bash_c_is_still_checked():
    wrapped = "python -I scripts/with_token.py run GH_PAT -- bash -c 'gh pr create --title x --body \"no link\"'"
    linked = "python -I scripts/with_token.py run GH_PAT -- bash -c 'gh pr create --title x --body \"Closes #4\"'"

    assert check_command(wrapped)
    assert check_command(linked) is None


def test_a_pr_create_fed_to_a_shell_heredoc_is_still_checked():
    assert check_command("bash <<'EOF'\ngh pr create --title x\nEOF")


def test_a_link_in_a_different_command_does_not_count():
    assert check_command('echo "Closes #4"; gh pr create --title x')
    assert check_command('gh pr create --title x && echo "Closes #4"')


def test_the_link_must_be_in_the_pr_create_command_itself():
    assert check_command('gh pr create --title x --body "Closes #4"; echo done') is None
    assert check_command('gh pr create --title x --body "Part one; Closes #4"') is None


def test_text_only_commands_that_mention_it_are_still_allowed():
    assert check_command('echo "run gh pr create later"; git status') is None
    assert check_command('printf "%s" "gh pr create"') is None
    assert check_command('git commit -m "docs: mention gh pr create"') is None

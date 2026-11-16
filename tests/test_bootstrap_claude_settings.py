"""Named assertions for scripts/bootstrap_claude_settings.py, one per acceptance item of the issue."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bootstrap_claude_settings.py"
BASE = json.loads((ROOT / "configs" / "claude" / "settings.base.json").read_text(encoding="utf-8"))
HOOK_FILES = ("compact-instructions.md", "after-compact.md")

sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap_claude_settings as bcs  # noqa: E402

OTHER_HOOK = {"hooks": [{"type": "command", "command": "echo mine"}]}


def run(claude_dir: Path | None, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), *(["--claude-dir", str(claude_dir)] if claude_dir else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)


def write_settings(claude_dir: Path, data) -> Path:
    claude_dir.mkdir(parents=True, exist_ok=True)
    path = claude_dir / "settings.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def backups(claude_dir: Path) -> list[Path]:
    return sorted(claude_dir.glob("settings.json.*"))


def unrelated() -> dict:
    return {"model": "opus", "permissions": {"allow": ["Bash(ls)"]}, "hooks": {"SessionStart": [OTHER_HOOK]}}


def test_dry_run_prints_diff_and_leaves_file_byte_identical(tmp_path):
    path = write_settings(tmp_path, unrelated())
    before = path.read_bytes()
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" in result.stdout
    assert result.stdout.count("\n+") > 3 and "--- " in result.stdout
    assert path.read_bytes() == before
    assert backups(tmp_path) == []
    assert not (tmp_path / "hooks").exists()


def test_apply_adds_base_keeps_unrelated_and_leaves_backup(tmp_path):
    path = write_settings(tmp_path, unrelated())
    before = path.read_bytes()
    result = run(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    merged = load(path)
    assert merged["env"] == BASE["env"]
    assert merged["model"] == "opus"
    assert merged["permissions"] == {"allow": ["Bash(ls)"]}
    assert merged["hooks"]["PreCompact"] == BASE["hooks"]["PreCompact"]
    assert merged["hooks"]["SessionStart"] == [OTHER_HOOK, *BASE["hooks"]["SessionStart"]]
    assert list(merged)[:3] == ["model", "permissions", "hooks"]
    found = backups(tmp_path)
    assert len(found) == 1 and found[0].read_bytes() == before
    for name in HOOK_FILES:
        assert (tmp_path / "hooks" / name).read_bytes() == (ROOT / "configs" / "claude" / "hooks" / name).read_bytes()


def test_second_apply_reports_no_changes_and_writes_no_backup(tmp_path):
    path = write_settings(tmp_path, unrelated())
    assert run(tmp_path, "--apply").returncode == 0
    after_first = path.read_bytes()
    result = run(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    assert "No changes" in result.stdout
    assert path.read_bytes() == after_first
    assert len(backups(tmp_path)) == 1


def test_differing_value_is_kept_and_reported_force_replaces_it(tmp_path):
    path = write_settings(tmp_path, {"env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "90k"}})
    result = run(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    assert load(path)["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "90k"
    assert "env.CLAUDE_CODE_AUTO_COMPACT_WINDOW" in result.stdout and "kept" in result.stdout
    forced = run(tmp_path, "--apply", "--force")
    assert forced.returncode == 0, forced.stderr
    assert load(path)["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "250k"


def test_invalid_json_exits_nonzero_and_writes_nothing(tmp_path):
    path = write_settings(tmp_path, "{ not json")
    before = path.read_bytes()
    result = run(tmp_path, "--apply")
    assert result.returncode != 0
    assert "not valid JSON" in result.stderr
    assert path.read_bytes() == before
    assert backups(tmp_path) == []
    assert not (tmp_path / "hooks").exists()


def test_non_object_top_level_is_rejected(tmp_path):
    path = write_settings(tmp_path, "[1, 2]")
    result = run(tmp_path, "--apply")
    assert result.returncode != 0
    assert "JSON object" in result.stderr
    assert path.read_text(encoding="utf-8") == "[1, 2]"


def test_missing_settings_file_is_created_from_base(tmp_path):
    claude_dir = tmp_path / "fresh" / ".claude"
    result = run(claude_dir, "--apply")
    assert result.returncode == 0, result.stderr
    assert load(claude_dir / "settings.json") == BASE
    assert backups(claude_dir) == []
    assert (claude_dir / "hooks" / "after-compact.md").is_file()


def test_overlay_entries_are_merged(tmp_path):
    claude_dir = tmp_path / "c"
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps({"env": {"EXTRA": "1"}, "hooks": {"Stop": [OTHER_HOOK]}}), encoding="utf-8")
    result = run(claude_dir, "--apply", "--overlay", str(overlay))
    assert result.returncode == 0, result.stderr
    merged = load(claude_dir / "settings.json")
    assert merged["env"] == {**BASE["env"], "EXTRA": "1"}
    assert merged["hooks"]["Stop"] == [OTHER_HOOK]
    assert merged["hooks"]["PreCompact"] == BASE["hooks"]["PreCompact"]


def test_invalid_overlay_exits_nonzero_and_writes_nothing(tmp_path):
    claude_dir = tmp_path / "c"
    overlay = tmp_path / "overlay.json"
    overlay.write_text("nope", encoding="utf-8")
    result = run(claude_dir, "--apply", "--overlay", str(overlay))
    assert result.returncode != 0
    assert "not valid JSON" in result.stderr
    assert not claude_dir.exists()


def test_existing_unrelated_hooks_under_same_event_are_preserved(tmp_path):
    existing = {"hooks": {"PreCompact": [OTHER_HOOK]}}
    path = write_settings(tmp_path, existing)
    assert run(tmp_path, "--apply").returncode == 0
    assert load(path)["hooks"]["PreCompact"] == [OTHER_HOOK, *BASE["hooks"]["PreCompact"]]


def test_hook_file_conflict_is_kept_and_reported_force_replaces_it(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "after-compact.md").write_text("mine\n", encoding="utf-8")
    result = run(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    assert (hooks / "after-compact.md").read_text(encoding="utf-8") == "mine\n"
    assert "after-compact.md" in result.stdout and "kept" in result.stdout
    assert run(tmp_path, "--apply", "--force").returncode == 0
    assert (hooks / "after-compact.md").read_bytes() == (ROOT / "configs/claude/hooks/after-compact.md").read_bytes()


def test_merge_is_pure_and_reports_conflicts():
    user = {"env": {"A": "1"}, "list": [1]}
    merged, conflicts = bcs.merge_settings(user, {"env": {"A": "2", "B": "3"}, "list": [2]}, force=False)
    assert user == {"env": {"A": "1"}, "list": [1]}
    assert merged == {"env": {"A": "1", "B": "3"}, "list": [1]}
    assert sorted(conflicts) == ["env.A", "list"]
    forced, none = bcs.merge_settings(user, {"env": {"A": "2"}}, force=True)
    assert forced["env"]["A"] == "2" and none == []


def test_invalid_json_raises_named_exception(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(bcs.SettingsFileError):
        bcs.load_json_object(bad)


# --- review follow-up (issue #33) ---

WINDOW = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"


def write_overlay(tmp_path: Path, data: dict, name: str = "overlay.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def hook_group(command: str, matcher: str | None = None) -> dict:
    group = {"hooks": [{"type": "command", "shell": "bash", "command": command}]}
    return {"matcher": matcher, **group} if matcher is not None else group


def test_overlay_value_wins_over_base_on_fresh_dir_without_kept_line(tmp_path):
    claude_dir = tmp_path / "c"
    overlay = write_overlay(tmp_path, {"env": {WINDOW: "90k"}})
    result = run(claude_dir, "--apply", "--overlay", str(overlay))
    assert result.returncode == 0, result.stderr
    assert load(claude_dir / "settings.json")["env"][WINDOW] == "90k"
    assert "kept your value" not in result.stdout


def test_users_own_value_is_kept_whichever_fragment_names_the_key(tmp_path):
    claude_dir = tmp_path / "c"
    path = write_settings(claude_dir, {"env": {WINDOW: "10k", "EXTRA": "mine"}})
    overlay = write_overlay(tmp_path, {"env": {WINDOW: "90k", "EXTRA": "theirs"}})
    result = run(claude_dir, "--apply", "--overlay", str(overlay))
    assert result.returncode == 0, result.stderr
    assert load(path)["env"] == {WINDOW: "10k", "EXTRA": "mine"}
    assert "kept your value for env.EXTRA" in result.stdout
    assert f"kept your value for env.{WINDOW}" in result.stdout
    assert run(claude_dir, "--apply", "--force", "--overlay", str(overlay)).returncode == 0
    assert load(path)["env"] == {WINDOW: "90k", "EXTRA": "theirs"}


def test_overlay_hook_group_for_same_hook_file_replaces_base_group(tmp_path):
    overlay = write_overlay(tmp_path, {"hooks": {"PreCompact": [hook_group("cat hooks/compact-instructions.md # x")]}})
    plan = bcs.build_plan(tmp_path / "c", [overlay], force=False)
    groups = plan.merged["hooks"]["PreCompact"]
    assert len(groups) == 1 and groups[0]["hooks"][0]["command"].endswith("# x")
    assert plan.conflicts == []


def test_dry_run_with_non_ascii_settings_and_redirected_stdout_under_non_utf8_locale(tmp_path):
    claude_dir = tmp_path / "c"
    write_settings(claude_dir, {"note": "café 日本語"})
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "LANG", "LC_ALL")}
    env.update(PYTHONUTF8="0", LC_ALL="C", PYTHONCOERCECLOCALE="0")
    out = tmp_path / "out.txt"
    overlay = write_overlay(tmp_path, {"n": "日"})
    with out.open("wb") as handle:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--claude-dir", str(claude_dir), "--overlay", str(overlay)],
            stdout=handle,
            stderr=subprocess.PIPE,
            env=env,
        )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "日" in out.read_bytes().decode("utf-8")


def test_apply_on_symlinked_settings_updates_target_and_keeps_link(tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    claude_dir = tmp_path / "c"
    claude_dir.mkdir()
    link = claude_dir / "settings.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    result = run(claude_dir, "--apply")
    assert result.returncode == 0, result.stderr
    assert link.is_symlink()
    assert load(real)["env"] == BASE["env"] and load(real)["model"] == "opus"
    assert list(real.parent.glob("settings.json.*.bak")) and not list(claude_dir.glob("settings.json.*"))


def test_default_claude_dir_follows_claude_config_dir(tmp_path):
    target = tmp_path / "cfg"
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(target)}
    result = run(None, "--apply", env=env)
    assert result.returncode == 0, result.stderr
    assert load(target / "settings.json") == BASE
    assert (target / "hooks" / "after-compact.md").is_file()
    assert bcs.default_claude_dir({"CLAUDE_CONFIG_DIR": ""}) == Path("~/.claude").expanduser()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("with_var", [True, False])
def test_hook_commands_read_from_config_dir_or_home_default(tmp_path, with_var):
    home = tmp_path / "home"
    cfg = tmp_path / "cfg"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    env["HOME"] = str(home)
    where = cfg if with_var else home / ".claude"
    if with_var:
        env["CLAUDE_CONFIG_DIR"] = str(cfg)
    assert run(where, "--apply", env=env).returncode == 0
    commands = [g["hooks"][0]["command"] for groups in BASE["hooks"].values() for g in groups]
    assert commands and all("${CLAUDE_CONFIG_DIR:-$HOME/.claude}" in c for c in commands)
    for command in commands:
        done = subprocess.run(["bash", "-c", command], capture_output=True, env=env)
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip()


OLD_PRE = hook_group("cat ~/.claude/hooks/compact-instructions.md")
OLD_START = hook_group("cat ~/.claude/hooks/after-compact.md", matcher="compact")


def test_differing_shared_hook_group_is_kept_and_reported_force_replaces_in_place(tmp_path):
    mine = {"hooks": {"PreCompact": [OTHER_HOOK, OLD_PRE], "SessionStart": [OLD_START, OTHER_HOOK]}}
    path = write_settings(tmp_path, mine)
    result = run(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    assert load(path)["hooks"] == mine["hooks"]
    assert "kept your value for hooks.PreCompact" in result.stdout
    assert "kept your value for hooks.SessionStart" in result.stdout
    assert run(tmp_path, "--apply", "--force").returncode == 0
    hooks = load(path)["hooks"]
    assert hooks["PreCompact"] == [OTHER_HOOK, *BASE["hooks"]["PreCompact"]]
    assert hooks["SessionStart"] == [*BASE["hooks"]["SessionStart"], OTHER_HOOK]
    assert run(tmp_path, "--apply").stdout.count("kept your value") == 0


def test_hook_group_with_different_matcher_is_not_the_shared_group(tmp_path):
    other = hook_group("cat ~/.claude/hooks/after-compact.md", matcher="startup")
    path = write_settings(tmp_path, {"hooks": {"SessionStart": [other]}})
    result = run(tmp_path, "--apply")
    assert result.returncode == 0 and "kept your value" not in result.stdout
    assert load(path)["hooks"]["SessionStart"] == [other, *BASE["hooks"]["SessionStart"]]


def test_unusable_hooks_destination_exits_nonzero_and_leaves_settings_byte_identical(tmp_path):
    path = write_settings(tmp_path, unrelated())
    before = path.read_bytes()
    (tmp_path / "hooks").write_text("i am a file", encoding="utf-8")
    result = run(tmp_path, "--apply")
    assert result.returncode != 0
    assert "hooks" in result.stderr
    assert path.read_bytes() == before
    assert backups(tmp_path) == []
    with pytest.raises(bcs.SettingsFileError):
        bcs.build_plan(tmp_path, [], force=False)


def test_hook_file_path_that_is_a_directory_stops_the_run_before_any_write(tmp_path):
    path = write_settings(tmp_path, unrelated())
    before = path.read_bytes()
    (tmp_path / "hooks" / "after-compact.md").mkdir(parents=True)
    assert run(tmp_path, "--apply").returncode != 0
    assert path.read_bytes() == before
    assert not (tmp_path / "hooks" / "compact-instructions.md").exists()


def test_settings_with_bom_is_accepted_and_written_without_bom(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"model": "opus"}).encode("utf-8"))
    result = run(tmp_path, "--apply")
    assert result.returncode == 0, result.stderr
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert load(path)["model"] == "opus"


def test_crlf_settings_stay_crlf_and_dry_run_diff_shows_only_real_changes(tmp_path):
    path = tmp_path / "settings.json"
    text = json.dumps({"model": "opus", "env": {WINDOW: "250k"}}, indent=2) + "\n"
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    dry = run(tmp_path)
    assert dry.returncode == 0, dry.stderr
    changed = [line for line in dry.stdout.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert changed and not any('"model"' in line or "250k" in line for line in changed)
    assert run(tmp_path, "--apply").returncode == 0
    raw = path.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") > 3


def test_directory_at_settings_path_gives_clear_error(tmp_path):
    (tmp_path / "settings.json").mkdir()
    result = run(tmp_path, "--apply")
    assert result.returncode == 1
    assert "settings.json" in result.stderr and "Traceback" not in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_existing_file_mode_is_preserved(tmp_path):
    path = write_settings(tmp_path, unrelated())
    path.chmod(0o640)
    assert run(tmp_path, "--apply").returncode == 0
    assert path.stat().st_mode & 0o777 == 0o640


def test_plan_builder_checks_everything_without_writing(tmp_path):
    claude_dir = tmp_path / "c"
    plan = bcs.build_plan(claude_dir, [], force=False)
    assert plan.settings_changed and not claude_dir.exists()
    assert {p.name for p in plan.hook_plans} == set(HOOK_FILES)

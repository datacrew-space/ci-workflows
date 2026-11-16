"""Named assertions for scripts/bootstrap_claude_settings.py, one per acceptance item of the issue."""

from __future__ import annotations

import json
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


def run(claude_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--claude-dir", str(claude_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


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
    assert load(path)["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "150k"


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

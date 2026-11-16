"""Merge the shared Claude Code settings fragment into a user's Claude settings file.

Standard library only, so it runs on a machine with nothing else installed.

  - reads `configs/claude/settings.base.json` (and any `--overlay` files) and merges them into
    `<claude-dir>/settings.json`; copies `configs/claude/hooks/*` to `<claude-dir>/hooks/`
  - dry run by default: prints a unified diff and one line per hook file, writes nothing
  - `--apply` writes: backs the settings file up to a timestamped sibling, then replaces it atomically;
    it does nothing when nothing would change, so a second run is a no-op
  - keys the fragments do not name are never touched
  - a scalar or list the user already has with a different value is kept and reported;
    `--force` takes the fragment's value. Under `hooks`, each event's list gets the fragment's
    matcher-groups appended when an equal group is not already there (never replaced).

Usage:  python scripts/bootstrap_claude_settings.py [--claude-dir DIR] [--apply] [--force] [--overlay FILE ...]
Invalid JSON in the settings file or an overlay exits 1 with a message on stderr and writes nothing.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_FILE = REPO_ROOT / "configs" / "claude" / "settings.base.json"
HOOKS_SRC = REPO_ROOT / "configs" / "claude" / "hooks"


class SettingsFileError(Exception):
    """A settings or overlay file cannot be read as a JSON object."""


def load_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsFileError(f"{path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise SettingsFileError(f"{path} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsFileError(f"{path} must hold a JSON object at the top level")
    return data


def merge_settings(user: dict, fragment: dict, force: bool) -> tuple[dict, list[str]]:
    """Return (merged copy of `user`, dotted paths where the user's value was kept over the fragment's)."""
    merged = copy.deepcopy(user)
    conflicts: list[str] = []
    _merge_into(merged, fragment, (), force, conflicts)
    return merged, conflicts


def _merge_into(target: dict, fragment: dict, path: tuple[str, ...], force: bool, conflicts: list[str]) -> None:
    for key, value in fragment.items():
        here = (*path, key)
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_into(target[key], value, here, force, conflicts)
        elif len(here) == 2 and here[0] == "hooks" and isinstance(target[key], list) and isinstance(value, list):
            target[key].extend(copy.deepcopy(group) for group in value if group not in target[key])
        elif target[key] != value:
            if force:
                target[key] = copy.deepcopy(value)
            else:
                conflicts.append(".".join(here))


@dataclass(frozen=True)
class HookFilePlan:
    name: str
    action: str  # "copy", "same", "kept" (differs, left alone) or "replace" (differs, --force)
    content: bytes


def plan_hook_files(src_dir: Path, dest_dir: Path, force: bool) -> list[HookFilePlan]:
    plans = []
    for src in sorted(p for p in src_dir.iterdir() if p.is_file()):
        content = src.read_bytes()
        dest = dest_dir / src.name
        if not dest.exists():
            action = "copy"
        elif dest.read_bytes() == content:
            action = "same"
        else:
            action = "replace" if force else "kept"
        plans.append(HookFilePlan(src.name, action, content))
    return plans


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _backup_path(settings: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = settings.with_name(f"{settings.name}.{stamp}.bak")
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = settings.with_name(f"{settings.name}.{stamp}-{counter}.bak")
    return candidate


def run(claude_dir: Path, overlays: list[Path], apply: bool, force: bool, out) -> int:
    settings_path = claude_dir / "settings.json"
    # Read and validate every input before writing anything.
    existing_text = settings_path.read_bytes().decode("utf-8") if settings_path.exists() else None
    current = load_json_object(settings_path) if existing_text is not None else {}
    fragments = [load_json_object(BASE_FILE), *(load_json_object(o) for o in overlays)]

    merged: dict = current
    conflicts: list[str] = []
    for fragment in fragments:
        merged, found = merge_settings(merged, fragment, force)
        conflicts.extend(c for c in found if c not in conflicts)
    hook_plans = plan_hook_files(HOOKS_SRC, claude_dir / "hooks", force)

    settings_changed = existing_text is None or merged != current
    new_text = render(merged)
    if settings_changed:
        diff = difflib.unified_diff(
            (existing_text or "").splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(settings_path) if existing_text is not None else "/dev/null",
            tofile=str(settings_path),
        )
        out.write("".join(diff))
    for conflict in conflicts:
        out.write(f"kept your value for {conflict} (differs from the fragment; use --force to replace)\n")
    verbs = {
        "copy": "copied" if apply else "would copy",
        "same": "unchanged",
        "replace": "replaced" if apply else "would replace",
    }
    for plan in hook_plans:
        if plan.action == "kept":
            out.write(f"hooks/{plan.name}: kept your file (differs from the shared one; use --force to replace)\n")
        else:
            out.write(f"hooks/{plan.name}: {verbs[plan.action]}\n")

    hooks_changed = any(p.action in ("copy", "replace") for p in hook_plans)
    if not settings_changed and not hooks_changed:
        out.write("No changes: settings are already up to date.\n")
        return 0
    if not apply:
        out.write("Dry run: nothing written. Re-run with --apply to write these changes.\n")
        return 0

    if settings_changed:
        if existing_text is not None:
            backup = _backup_path(settings_path)
            _atomic_write(backup, existing_text.encode("utf-8"))
            out.write(f"backup: {backup}\n")
        _atomic_write(settings_path, new_text.encode("utf-8"))
        out.write(f"wrote {settings_path}\n")
    for plan in hook_plans:
        if plan.action in ("copy", "replace"):
            _atomic_write(claude_dir / "hooks" / plan.name, plan.content)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--claude-dir", type=Path, default=Path("~/.claude"), help="default: ~/.claude")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--force", action="store_true", help="replace differing values and hook files")
    parser.add_argument("--overlay", type=Path, action="append", default=[], help="extra fragment, merged after base")
    args = parser.parse_args(argv)
    try:
        return run(args.claude_dir.expanduser(), args.overlay, args.apply, args.force, sys.stdout)
    except SettingsFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f"error: a settings file is not valid UTF-8: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

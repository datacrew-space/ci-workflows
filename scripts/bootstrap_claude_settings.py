"""Merge the shared Claude Code settings fragment into a user's Claude settings file.

Standard library only, so it runs on a machine with nothing else installed.

  - reads `configs/claude/settings.base.json` (and any `--overlay` files) and merges them into
    `<claude-dir>/settings.json`; copies `configs/claude/hooks/*` to `<claude-dir>/hooks/`
  - the base and the overlays are composed into one fragment first (a later fragment wins over an
    earlier one), and only that fragment is merged into the user's file
  - dry run by default: prints a unified diff and one line per hook file, writes nothing
  - `--apply` writes: backs the settings file up to a timestamped sibling, then replaces it atomically;
    it does nothing when nothing would change, so a second run is a no-op
  - keys the fragments do not name are never touched
  - a scalar or list the user already has with a different value is kept and reported;
    `--force` takes the fragment's value. Under `hooks`, a group of the user's counts as the shared
    one when it has the same `matcher` and prints the same `hooks/<file>.md`; a differing one is a
    conflict (kept, or replaced in place by `--force`). Other groups are never touched, and a group
    the user lacks is appended.
  - a symlinked settings file is written through to its target; line endings and file mode are kept
  - a plan is built and every precondition checked first, then applied (hook files, then settings);
    any failed precondition exits 1 with nothing written

Usage:  python scripts/bootstrap_claude_settings.py [--claude-dir DIR] [--apply] [--force] [--overlay FILE ...]
The default directory is $CLAUDE_CONFIG_DIR when set, else ~/.claude.
Invalid JSON in the settings file or an overlay exits 1 with a message on stderr and writes nothing.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_FILE = REPO_ROOT / "configs" / "claude" / "settings.base.json"
HOOKS_SRC = REPO_ROOT / "configs" / "claude" / "hooks"


class SettingsFileError(Exception):
    """A settings, overlay or hooks path cannot be used."""


def load_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SettingsFileError(f"{path} is not valid JSON: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SettingsFileError(f"{path} is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise SettingsFileError(f"{path} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsFileError(f"{path} must hold a JSON object at the top level")
    return data


def default_claude_dir(environ=os.environ) -> Path:
    configured = environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path("~/.claude").expanduser()


_HOOK_FILE = re.compile(r"hooks/([\w.-]+\.md)")


def _hook_files(group) -> set[str]:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return set()
    found: set[str] = set()
    for hook in group["hooks"]:
        command = hook.get("command") if isinstance(hook, dict) else None
        if isinstance(command, str):
            found.update(_HOOK_FILE.findall(command))
    return found


def _same_slot(a, b) -> bool:
    """Two hook groups are the same shared group: same matcher (absent equals absent) and a common hook file."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    return a.get("matcher") == b.get("matcher") and bool(_hook_files(a) & _hook_files(b))


def _merge_hook_groups(target: list, groups: list, label: str, force: bool, conflicts: list[str]) -> None:
    for group in groups:
        if group in target:
            continue
        slots = [i for i, existing in enumerate(target) if _same_slot(existing, group)]
        if not slots:
            target.append(copy.deepcopy(group))
        elif force:
            target[slots[0]] = copy.deepcopy(group)
            for i in reversed(slots[1:]):
                del target[i]
        else:
            name = "/".join(sorted(_hook_files(group)))
            conflicts.append(f"{label} (group for hooks/{name})")


def compose_fragments(fragments: list[dict]) -> dict:
    """Layer fragments: a later one wins for scalars and lists, dicts merge, hook groups pair up by shared file."""
    composed: dict = {}
    for fragment in fragments:
        _layer(composed, fragment, ())
    return composed


def _layer(target: dict, fragment: dict, path: tuple[str, ...]) -> None:
    for key, value in fragment.items():
        here = (*path, key)
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            _layer(target[key], value, here)
        elif len(here) == 2 and here[0] == "hooks" and isinstance(target.get(key), list) and isinstance(value, list):
            _merge_hook_groups(target[key], value, ".".join(here), True, [])
        else:
            target[key] = copy.deepcopy(value)


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
            _merge_hook_groups(target[key], value, ".".join(here), force, conflicts)
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


def _check_writable_dir(path: Path, what: str) -> None:
    """`path` must be a directory, or creatable: its nearest existing ancestor is a writable directory."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not probe.is_dir():
        raise SettingsFileError(f"{what} {path} is not usable: {probe} exists but is not a directory")
    if not os.access(probe, os.W_OK):
        raise SettingsFileError(f"{what} {path} is not usable: {probe} is not writable")


def plan_hook_files(src_dir: Path, dest_dir: Path, force: bool) -> list[HookFilePlan]:
    _check_writable_dir(dest_dir, "hooks directory")
    plans = []
    for src in sorted(p for p in src_dir.iterdir() if p.is_file()):
        content = src.read_bytes()
        dest = dest_dir / src.name
        if dest.is_dir():
            raise SettingsFileError(f"hooks file {dest} is a directory")
        if not dest.exists():
            action = "copy"
        elif dest.read_bytes() == content:
            action = "same"
        else:
            action = "replace" if force else "kept"
        plans.append(HookFilePlan(src.name, action, content))
    return plans


@dataclass(frozen=True)
class Plan:
    settings_path: Path  # the real target, symlinks resolved
    existing_bytes: bytes | None
    old_text: str  # existing content, BOM dropped, LF newlines ("" when absent)
    new_text: str  # LF newlines
    newline: str
    merged: dict
    conflicts: list[str]
    hook_plans: list[HookFilePlan]
    hooks_dir: Path
    settings_changed: bool

    @property
    def hooks_changed(self) -> bool:
        return any(p.action in ("copy", "replace") for p in self.hook_plans)


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def build_plan(claude_dir: Path, overlays: list[Path], force: bool) -> Plan:
    """Read and check every input and precondition; touch nothing."""
    settings_path = (claude_dir / "settings.json").resolve()
    existing_bytes = None
    current: dict = {}
    if settings_path.is_dir():
        raise SettingsFileError(f"{settings_path} is a directory, not a settings file")
    if settings_path.exists():
        try:
            existing_bytes = settings_path.read_bytes()
        except OSError as exc:
            raise SettingsFileError(f"{settings_path} cannot be read: {exc}") from exc
        current = load_json_object(settings_path)
    fragment = compose_fragments([load_json_object(BASE_FILE), *(load_json_object(o) for o in overlays)])
    merged, conflicts = merge_settings(current, fragment, force)
    _check_writable_dir(settings_path.parent, "settings directory")
    hooks_dir = claude_dir / "hooks"
    hook_plans = plan_hook_files(HOOKS_SRC, hooks_dir, force)

    if existing_bytes is None:
        old_text, newline = "", "\n"
    else:
        old_text = existing_bytes.decode("utf-8-sig").replace("\r\n", "\n")
        newline = "\r\n" if b"\r\n" in existing_bytes else "\n"
    return Plan(
        settings_path=settings_path,
        existing_bytes=existing_bytes,
        old_text=old_text,
        new_text=render(merged),
        newline=newline,
        merged=merged,
        conflicts=conflicts,
        hook_plans=hook_plans,
        hooks_dir=hooks_dir,
        settings_changed=existing_bytes is None or merged != current,
    )


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None and os.name == "posix":
            os.chmod(tmp, mode)
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


def report(plan: Plan, apply: bool, out) -> None:
    if plan.settings_changed:
        diff = difflib.unified_diff(
            plan.old_text.splitlines(keepends=True),
            plan.new_text.splitlines(keepends=True),
            fromfile=str(plan.settings_path) if plan.existing_bytes is not None else "/dev/null",
            tofile=str(plan.settings_path),
        )
        out.write("".join(diff))
    for conflict in plan.conflicts:
        out.write(f"kept your value for {conflict} (differs from the fragment; use --force to replace)\n")
    verbs = {
        "copy": "copied" if apply else "would copy",
        "same": "unchanged",
        "replace": "replaced" if apply else "would replace",
    }
    for hook in plan.hook_plans:
        if hook.action == "kept":
            out.write(f"hooks/{hook.name}: kept your file (differs from the shared one; use --force to replace)\n")
        else:
            out.write(f"hooks/{hook.name}: {verbs[hook.action]}\n")


def apply_plan(plan: Plan, out) -> None:
    """Hook files first, settings last, so a failure never leaves settings pointing at missing files."""
    for hook in plan.hook_plans:
        if hook.action in ("copy", "replace"):
            _atomic_write(plan.hooks_dir / hook.name, hook.content)
    if plan.settings_changed:
        mode = None
        if plan.existing_bytes is not None:
            backup = _backup_path(plan.settings_path)
            _atomic_write(backup, plan.existing_bytes)
            out.write(f"backup: {backup}\n")
            mode = stat.S_IMODE(plan.settings_path.stat().st_mode)
        _atomic_write(plan.settings_path, plan.new_text.replace("\n", plan.newline).encode("utf-8"), mode)
        out.write(f"wrote {plan.settings_path}\n")


def run(claude_dir: Path, overlays: list[Path], apply: bool, force: bool, out) -> int:
    plan = build_plan(claude_dir, overlays, force)
    report(plan, apply, out)
    if not plan.settings_changed and not plan.hooks_changed:
        out.write("No changes: settings are already up to date.\n")
        return 0
    if not apply:
        out.write("Dry run: nothing written. Re-run with --apply to write these changes.\n")
        return 0
    apply_plan(plan, out)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--claude-dir", type=Path, default=None, help="default: $CLAUDE_CONFIG_DIR, else ~/.claude")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--force", action="store_true", help="replace differing values, hook groups and hook files")
    parser.add_argument("--overlay", type=Path, action="append", default=[], help="extra fragment, merged after base")
    args = parser.parse_args(argv)
    claude_dir = args.claude_dir.expanduser() if args.claude_dir else default_claude_dir()
    try:
        return run(claude_dir, args.overlay, args.apply, args.force, sys.stdout)
    except SettingsFileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

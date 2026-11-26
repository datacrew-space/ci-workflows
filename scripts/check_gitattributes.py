#!/usr/bin/env python3
"""Require a root .gitattributes that stores text with LF line endings.

Passes when `.gitattributes` in the current directory (pre-commit runs hooks at the repository root)
has a `*` rule carrying both `text=auto` and `eol=lf`. Without it, every Windows checkout warns
"LF will be replaced by CRLF" on each commit, and files can land with mixed endings.

Exit codes: 0 present, 1 missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

REQUIRED = {"text=auto", "eol=lf"}
RULE = "* text=auto eol=lf"


def has_rule(path: Path) -> bool:
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        fields = raw.split("#", 1)[0].split()
        if fields and fields[0] == "*" and REQUIRED <= set(fields[1:]):
            return True
    return False


def main() -> int:
    if has_rule(Path(".gitattributes")):
        return 0
    print(
        f"gitattributes-lf: add '{RULE}' to .gitattributes at the repository root. Before committing it, "
        "run `git add --renormalize .` and check that `git status` shows nothing new; if it does, commit "
        "the renormalization on its own.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

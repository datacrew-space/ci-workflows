#!/usr/bin/env python3
"""Refuse internal references in public documentation sources.

Checks each file given (pre-commit passes the staged files that match the hook's `files`) against a
pattern file: one Python regular expression per line, `#` comments and blank lines ignored. A line
that carries the allow marker is skipped.

The report names the file, the line and the pattern's entry number, never the matched text: public
CI logs would otherwise publish what the check exists to keep out.

Exit codes: 0 clean, 1 a match, 2 a configuration error (missing pattern file, invalid pattern).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ALLOW_MARKER = "internal-ref: allow"


class ConfigError(Exception):
    """The hook is configured wrongly; nothing was checked."""


def load_patterns(path: Path, flags: int) -> list[tuple[int, re.Pattern[str]]]:
    if not path.is_file():
        raise ConfigError(f"pattern file not found: {path}")
    patterns = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry = len(patterns) + 1
        try:
            patterns.append((entry, re.compile(line, flags)))
        except re.error as exc:
            raise ConfigError(f"{path}: pattern #{entry} (line {n}) is invalid: {exc.msg}") from exc
    if not patterns:
        raise ConfigError(f"pattern file has no patterns: {path}")
    return patterns


def scan(path: Path, patterns: list[tuple[int, re.Pattern[str]]], allow_marker: str) -> list[str]:
    hits = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if allow_marker and allow_marker in line:
            continue
        for entry, pattern in patterns:
            if pattern.search(line):
                hits.append(f"{path}:{lineno}: matches internal pattern #{entry}")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patterns", required=True, type=Path, help="pattern file, one regex per line")
    parser.add_argument("--ignore-case", action="store_true", help="match patterns case-insensitively")
    parser.add_argument("--allow-marker", default=DEFAULT_ALLOW_MARKER, help="text that exempts a line")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    try:
        patterns = load_patterns(args.patterns, re.IGNORECASE if args.ignore_case else 0)
    except ConfigError as exc:
        print(f"docs-internal-refs: {exc}", file=sys.stderr)
        return 2
    hits = [hit for f in args.files for hit in scan(f, patterns, args.allow_marker)]
    for hit in hits:
        print(hit)
    if hits:
        print(
            f"docs-internal-refs: {len(hits)} internal reference(s) in public docs. Remove them, or add "
            f"'{args.allow_marker}' to a line that must keep one.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

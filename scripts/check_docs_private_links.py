#!/usr/bin/env python3
"""Refuse links from public documentation to issue trackers its readers cannot open.

Flags, in each file given:
- a GitHub issue, pull request or discussion URL, or an `owner/repo#N` reference, whose `owner/repo`
  fully matches a private-repository pattern (`--private-repo`, repeatable, or `--private-repos-file`,
  one regex per line; matched case-insensitively, because GitHub names are);
- with `--bare-refs`, a bare `#N` outside code. On a published site a bare number points nowhere a
  reader can follow, and a "fix tracked in #N" caveat outlives its issue.

Exit codes: 0 clean, 1 a finding, 2 a configuration error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull|discussions)/\d+", re.IGNORECASE)
SHORT_REF = re.compile(r"(?<![\w/.-])([\w.-]+)/([\w.-]+)#\d+\b")
BARE_REF = re.compile(r"(?<![\w/&#])#\d+\b")
INLINE_CODE = re.compile(r"(`+).*?\1")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


class ConfigError(Exception):
    """The hook is configured wrongly; nothing was checked."""


def load_private(patterns: list[str], file: Path | None) -> list[re.Pattern[str]]:
    raw = list(patterns)
    if file is not None:
        if not file.is_file():
            raise ConfigError(f"private repos file not found: {file}")
        raw += [s for s in (line.strip() for line in file.read_text(encoding="utf-8").splitlines()) if s and not s.startswith("#")]
    compiled = []
    for entry, p in enumerate(raw, start=1):
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            raise ConfigError(f"private repo pattern #{entry} is invalid: {exc.msg}") from exc
    return compiled


def is_private(owner: str, repo: str, private: list[re.Pattern[str]]) -> bool:
    name = f"{owner}/{repo.removesuffix('.git')}"
    return any(p.fullmatch(name) for p in private)


def scan(path: Path, private: list[re.Pattern[str]], bare_refs: bool) -> list[str]:
    hits = []
    fence = None
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        m = FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        for rx in (URL, SHORT_REF):
            for owner, repo in rx.findall(line):
                if is_private(owner, repo, private):
                    hits.append(f"{path}:{lineno}: links to a private repository's tracker")
        if bare_refs and fence is None and not m and BARE_REF.search(INLINE_CODE.sub("", line)):
            hits.append(f"{path}:{lineno}: bare issue reference (#N) readers cannot follow")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private-repo", action="append", default=[], help="regex for a private owner/repo")
    parser.add_argument("--private-repos-file", type=Path, help="file of owner/repo regexes, one per line")
    parser.add_argument("--bare-refs", action="store_true", help="also refuse bare #N references outside code")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    try:
        private = load_private(args.private_repo, args.private_repos_file)
        if not private and not args.bare_refs:
            raise ConfigError("nothing to check: give --private-repo, --private-repos-file or --bare-refs")
    except ConfigError as exc:
        print(f"docs-private-links: {exc}", file=sys.stderr)
        return 2
    hits = [hit for f in args.files for hit in scan(f, private, args.bare_refs)]
    for hit in hits:
        print(hit)
    if hits:
        print(
            f"docs-private-links: {len(hits)} link(s) public readers cannot follow. Remove the reference, "
            "or describe the limitation without pointing at a private tracker.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

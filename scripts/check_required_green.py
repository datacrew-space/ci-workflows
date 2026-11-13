#!/usr/bin/env python3
"""Decide whether a named check is green for a commit.

Usage:
    check_required_green.py --fetch OWNER/REPO SHA <check-name>   # reads GITHUB_TOKEN, GITHUB_API_URL
    <check-run JSON lines on stdin> | check_required_green.py <check-name>

--fetch lists the commit's check runs from the GitHub API (all pages). Without it the check runs
are read from stdin, one JSON object per line. When a check was re-run, only the newest run counts.

Exit: 0 green (newest run completed with success), 1 not green (completed with any other
conclusion), 2 cannot decide (no such check, still running, bad input, no name given). A caller
treats 2 as "not yet", never as a pass. Standard library only.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request


REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def fetch_runs(repo: str, sha: str) -> list[dict]:
    """All check runs of a commit, following the API's pagination. Raises OSError on any failure."""
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    token = os.environ.get("GITHUB_TOKEN", "")
    url: str | None = f"{base}/repos/{repo}/commits/{sha}/check-runs?per_page=100"
    runs: list[dict] = []
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (https API, validated repo)
            runs.extend(json.load(resp).get("check_runs", []))
            match = NEXT_RE.search(resp.headers.get("Link", ""))
            url = match.group(1) if match else None
    return runs


def main(argv: list[str], stdin) -> int:
    args = argv[1:]
    runs: list[dict]
    if args[:1] == ["--fetch"]:
        if len(args) != 4 or not REPO_RE.match(args[1]) or not SHA_RE.match(args[2]) or not args[3]:
            print("usage: check_required_green.py --fetch OWNER/REPO <40-hex-sha> <check-name>", file=sys.stderr)
            return 2
        name = args[3]
        try:
            runs = fetch_runs(args[1], args[2])
        except (OSError, ValueError) as exc:  # URLError, HTTPError, timeouts and bad JSON all land here
            print(f"could not list check runs: {type(exc).__name__}", file=sys.stderr)
            return 2
    else:
        if len(args) != 1 or not args[0]:
            print("usage: check_required_green.py <check-name>  (check runs as JSON lines on stdin)", file=sys.stderr)
            return 2
        name = args[0]
        text = stdin.read()
        if not text.strip():
            print("no check runs on stdin", file=sys.stderr)
            return 2
        try:
            runs = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            print("stdin is not valid JSON lines", file=sys.stderr)
            return 2
        if not all(isinstance(r, dict) for r in runs):
            print("stdin is not a list of check-run objects", file=sys.stderr)
            return 2

    named = [r for r in runs if r.get("name") == name]
    if not named:
        print(f"no check named '{name}' on this commit yet", file=sys.stderr)
        return 2
    newest = max(named, key=lambda r: (r.get("started_at") or "", r.get("id") or 0))

    status = newest.get("status")
    conclusion = newest.get("conclusion")
    if status != "completed":
        print(f"check '{name}' is {status}; not decided yet", file=sys.stderr)
        return 2
    if conclusion == "success":
        print(f"check '{name}' is green")
        return 0
    print(f"check '{name}' concluded '{conclusion}'; not green", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))

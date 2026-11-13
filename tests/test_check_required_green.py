"""scripts/check_required_green.py against check-run JSON lines shaped like the GitHub API's."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_required_green.py"


def run(runs: list[dict], name: str = "ci-ok") -> subprocess.CompletedProcess:
    stdin = "\n".join(json.dumps(r) for r in runs)
    return subprocess.run([sys.executable, str(SCRIPT), name], input=stdin, capture_output=True, text=True, check=False)


def cr(name="ci-ok", status="completed", conclusion="success", started="2026-11-13T10:00:00Z", id=1):
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started, "id": id}


def test_green_when_the_check_succeeded():
    r = run([cr(), cr(name="lint", conclusion="failure")])
    assert r.returncode == 0
    assert "green" in r.stdout


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", "neutral", None])
def test_not_green_for_any_other_conclusion(conclusion):
    r = run([cr(conclusion=conclusion)])
    assert r.returncode == 1
    assert "not green" in r.stderr


def test_a_running_check_cannot_be_decided():
    r = run([cr(status="in_progress", conclusion=None)])
    assert r.returncode == 2
    assert "not decided yet" in r.stderr


def test_a_missing_check_cannot_be_decided():
    r = run([cr(name="lint")])
    assert r.returncode == 2
    assert "no check named" in r.stderr


def test_only_the_newest_rerun_counts():
    red_then_green = [cr(conclusion="failure", started="2026-11-13T10:00:00Z", id=1), cr(started="2026-11-13T10:05:00Z", id=2)]
    assert run(red_then_green).returncode == 0
    green_then_red = [cr(started="2026-11-13T10:00:00Z", id=1), cr(conclusion="failure", started="2026-11-13T10:05:00Z", id=2)]
    assert run(green_then_red).returncode == 1


def test_bad_input_and_missing_name_cannot_be_decided():
    assert subprocess.run([sys.executable, str(SCRIPT), "ci-ok"], input="", capture_output=True, text=True).returncode == 2
    assert subprocess.run([sys.executable, str(SCRIPT), "ci-ok"], input="not json", capture_output=True, text=True).returncode == 2
    assert subprocess.run([sys.executable, str(SCRIPT)], input="{}", capture_output=True, text=True).returncode == 2


# --fetch mode, against a local HTTP server that speaks the check-runs API with two pages.
import http.server  # noqa: E402
import threading  # noqa: E402

SHA = "a" * 40


@pytest.fixture
def api():
    pages = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.server.seen.append((self.path, self.headers.get("Authorization")))
            key = self.path
            if key not in pages:
                self.send_response(404)
                self.end_headers()
                return
            body, link = pages[key]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if link:
                self.send_header("Link", f'<{link}>; rel="next"')
            self.end_headers()
            self.wfile.write(json.dumps({"check_runs": body}).encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.seen = []
    base = f"http://127.0.0.1:{server.server_port}"
    path = f"/repos/o/r/commits/{SHA}/check-runs?per_page=100"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield pages, base, path, server
    server.shutdown()


def fetch(base: str, name: str = "ci-ok", sha: str = SHA, repo: str = "o/r") -> subprocess.CompletedProcess:
    env = {**os.environ, "GITHUB_API_URL": base, "GITHUB_TOKEN": "t0ken"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--fetch", repo, sha, name], capture_output=True, text=True, env=env, check=False
    )


def test_fetch_follows_pagination_and_sends_the_token(api):
    pages, base, path, server = api
    pages[path] = ([cr(name="lint")], f"{base}{path}&page=2")
    pages[f"{path}&page=2"] = ([cr()], None)
    r = fetch(base)
    assert r.returncode == 0, r.stderr
    assert [p for p, _ in server.seen] == [path, f"{path}&page=2"]
    assert {auth for _, auth in server.seen} == {"Bearer t0ken"}


def test_fetch_api_failure_cannot_be_decided(api):
    _, base, _, _ = api
    r = fetch(base)  # the server answers 404
    assert r.returncode == 2
    assert "could not list check runs: HTTPError" in r.stderr  # pinned: not some other failure


def test_fetch_rejects_a_malformed_repo_or_sha_without_a_request(api):
    _, base, _, server = api
    assert fetch(base, repo="o/r/../x").returncode == 2
    assert fetch(base, sha="main").returncode == 2
    assert server.seen == []

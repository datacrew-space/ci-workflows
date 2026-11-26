"""Behaviour of the docs hooks: internal references, private tracker links, doc-sample imports, .gitattributes."""

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(script: str, *args, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)], cwd=cwd, capture_output=True, text=True
    )


# --- docs-internal-refs -------------------------------------------------------------------------


def internal(tmp_path, text, patterns="ADR-\\d+\n\\.agents/\n", *extra):
    (tmp_path / "patterns.txt").write_text("# internal references\n\n" + patterns)
    (tmp_path / "page.md").write_text(text)
    return run("check_docs_internal_refs.py", "--patterns", "patterns.txt", *extra, "page.md", cwd=tmp_path)


def test_internal_ref_fails_and_names_file_line_and_entry_without_the_match(tmp_path):
    r = internal(tmp_path, "Intro\nSee ADR-062 for why.\n")
    assert r.returncode == 1
    assert "page.md:2: matches internal pattern #1" in r.stdout
    assert "ADR-062" not in r.stdout + r.stderr


def test_clean_page_passes(tmp_path):
    assert internal(tmp_path, "Nothing internal here.\n").returncode == 0


def test_allow_marker_skips_the_line(tmp_path):
    assert internal(tmp_path, "The .agents/ folder <!-- internal-ref: allow -->\n").returncode == 0


def test_patterns_are_case_sensitive_unless_asked(tmp_path):
    assert internal(tmp_path, "adr-1\n").returncode == 0
    assert internal(tmp_path, "adr-1\n", "ADR-\\d+\n", "--ignore-case").returncode == 1


def test_missing_pattern_file_is_a_config_error(tmp_path):
    (tmp_path / "page.md").write_text("x\n")
    r = run("check_docs_internal_refs.py", "--patterns", "nope.txt", "page.md", cwd=tmp_path)
    assert r.returncode == 2


def test_invalid_pattern_is_a_config_error_naming_its_entry(tmp_path):
    r = internal(tmp_path, "x\n", "ok\n(unclosed\n")
    assert r.returncode == 2
    assert "#2" in r.stderr


# --- docs-private-links -------------------------------------------------------------------------


def links(tmp_path, text, *args):
    (tmp_path / "page.md").write_text(text)
    return run("check_docs_private_links.py", *args, "page.md", cwd=tmp_path)


def test_link_to_a_private_repo_issue_fails(tmp_path):
    r = links(tmp_path, "Fix tracked in https://github.com/acme-private/app/issues/12.\n", "--private-repo", "acme-private/.*")
    assert r.returncode == 1
    assert "page.md:1" in r.stdout


def test_short_ref_to_a_private_repo_fails(tmp_path):
    assert links(tmp_path, "See acme-private/app#12.\n", "--private-repo", "acme-private/app").returncode == 1


def test_link_to_a_public_repo_passes(tmp_path):
    text = "See https://github.com/acme/public-lib/pull/3 and acme/public-lib#4.\n"
    assert links(tmp_path, text, "--private-repo", "acme-private/.*").returncode == 0


def test_private_repo_names_come_from_a_file_too(tmp_path):
    (tmp_path / "private.txt").write_text("# private repos\nacme-private/.*\n")
    r = links(tmp_path, "https://github.com/ACME-PRIVATE/app/pull/9\n", "--private-repos-file", "private.txt")
    assert r.returncode == 1


def test_bare_ref_fails_only_when_asked_and_never_inside_code(tmp_path):
    text = "A caveat: fix tracked in #1810.\n"
    assert links(tmp_path, text, "--private-repo", "x/y").returncode == 0
    assert links(tmp_path, text, "--bare-refs").returncode == 1
    code = "Use `#1810` literally.\n```\nlist[#3]\n```\n&#123; is an entity\n"
    assert links(tmp_path, code, "--bare-refs").returncode == 0


def test_private_links_needs_something_to_check(tmp_path):
    assert links(tmp_path, "x\n").returncode == 2


# --- docs-python-imports ------------------------------------------------------------------------


def imports(tmp_path, text, *args):
    pkg = tmp_path / "samplepkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("class Client:\n    pass\n")
    (tmp_path / "page.md").write_text(text)
    return run("check_docs_python_imports.py", "--path", ".", *args, "page.md", cwd=tmp_path)


def test_resolving_imports_pass(tmp_path):
    text = "```python\nfrom samplepkg.core import Client\nimport samplepkg.core\nfrom samplepkg import core\n```\n"
    r = imports(tmp_path, text)
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_name_fails_with_its_doc_line(tmp_path):
    r = imports(tmp_path, "Intro\n\n```python\nx = 1\nfrom samplepkg.core import Clinet\n```\n")
    assert r.returncode == 1
    assert "page.md:5" in r.stdout
    assert "Clinet" in r.stdout


def test_missing_module_fails(tmp_path):
    r = imports(tmp_path, "```py\nfrom samplepkg.gone import Client\n```\n")
    assert r.returncode == 1


def test_a_block_that_does_not_parse_still_has_its_imports_checked(tmp_path):
    text = "```python\nfrom samplepkg.core import (\n    Client,\n    Nope,\n)\nresult = await client.get(...)  # top-level await\n>>> broken(\n```\n"
    r = imports(tmp_path, text)
    assert r.returncode == 1
    assert "Nope" in r.stdout
    assert "Client" not in r.stdout.replace("samplepkg.core", "")


def test_doctest_prompts_are_stripped(tmp_path):
    assert imports(tmp_path, "```pycon\n>>> from samplepkg.core import Client\n```\n").returncode == 0


def test_skip_markers(tmp_path):
    info = "```python skip-import-check\nfrom samplepkg.core import Nope\n```\n"
    comment = "<!-- skip-import-check -->\n```python\nfrom samplepkg.core import Nope\n```\n"
    assert imports(tmp_path, info).returncode == 0
    assert imports(tmp_path, comment).returncode == 0


def test_package_filter_ignores_other_imports(tmp_path):
    text = "```python\nimport not_installed_anywhere\nfrom samplepkg.core import Client\n```\n"
    assert imports(tmp_path, text, "--package", "samplepkg").returncode == 0
    assert imports(tmp_path, text).returncode == 1


def test_non_python_blocks_are_ignored(tmp_path):
    assert imports(tmp_path, "```bash\nfrom x import y\n```\n").returncode == 0


# --- gitattributes-lf ---------------------------------------------------------------------------


def test_gitattributes_missing_or_without_lf_fails(tmp_path):
    assert run("check_gitattributes.py", cwd=tmp_path).returncode == 1
    (tmp_path / ".gitattributes").write_text("* text=auto\n")
    assert run("check_gitattributes.py", cwd=tmp_path).returncode == 1


def test_gitattributes_with_the_lf_rule_passes(tmp_path):
    (tmp_path / ".gitattributes").write_text("# line endings\n*   text=auto   eol=lf\n*.png binary\n")
    assert run("check_gitattributes.py", cwd=tmp_path).returncode == 0


def test_this_repository_has_the_lf_rule():
    assert run("check_gitattributes.py", cwd=ROOT).returncode == 0


# --- manifest -----------------------------------------------------------------------------------


def test_docs_hooks_are_exported_through_the_python_runner():
    hooks = {h["id"]: h for h in yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text())}
    expected = {
        "docs-internal-refs": "check_docs_internal_refs.py",
        "docs-private-links": "check_docs_private_links.py",
        "docs-python-imports": "check_docs_python_imports.py",
        "gitattributes-lf": "check_gitattributes.py",
    }
    for hook_id, script in expected.items():
        assert hooks[hook_id]["entry"] == f"scripts/run-python.sh scripts/{script}"
        assert hooks[hook_id]["language"] == "script"
        assert (SCRIPTS / script).is_file()
    assert hooks["gitattributes-lf"]["pass_filenames"] is False
    assert hooks["gitattributes-lf"]["always_run"] is True

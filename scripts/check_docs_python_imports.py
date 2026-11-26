#!/usr/bin/env python3
"""Check that the imports in documentation's Python samples resolve.

For each fenced block tagged python, py, python3 or pycon in the files given, every absolute
`import a.b` and `from a.b import X` must work in the current interpreter: the module imports, and X
is an attribute of it or a submodule. Nothing is fetched; the package must already be installed (run
the hook from the project's environment, or pass `--path src`).

A block is skipped when its info string contains `skip-import-check`, or when the line before its
opening fence is `<!-- skip-import-check -->`. A block that does not parse as a whole (top-level
await, `...` placeholders, doctest output) still has its import statements checked one by one.

`--package NAME` (repeatable) limits the check to imports whose top-level module is NAME, so samples
may import third-party libraries the docs environment does not have.

Importing runs the imported modules' top-level code, as the sample itself would.

Exit codes: 0 clean, 1 an import that does not resolve.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LANGS = {"python", "py", "python3", "pycon"}
SKIP = "skip-import-check"
OPEN = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)(.*)$")
IMPORT_START = re.compile(r"^\s*(from\s+[\w.]+\s+import\b|import\s+[\w.])")


@dataclass
class Block:
    first_line: int  # doc line number of the block's first code line
    lines: list[str]


def blocks(text: str) -> list[Block]:
    out: list[Block] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        indent, fence, lang, rest = m.groups()
        prev = next((lines[j].strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
        start = i + 1
        j = start
        while j < len(lines) and not (lines[j].strip().startswith(fence[0] * len(fence)) and not lines[j].strip().strip(fence[0])):
            j += 1
        skip = SKIP in rest or SKIP in lang or prev == f"<!-- {SKIP} -->"
        if lang.lower() in LANGS and not skip:
            body = [ln[len(indent):] if ln.startswith(indent) else ln.lstrip() for ln in lines[start:j]]
            out.append(Block(first_line=start + 1, lines=strip_prompts(body)))
        i = j + 1
    return out


def strip_prompts(body: list[str]) -> list[str]:
    first = next((ln.lstrip() for ln in body if ln.strip()), "")
    if not first.startswith(">>>"):  # a doctest session, not code that mentions >>> later
        return body
    kept = []
    for ln in body:
        s = ln.lstrip()
        if s.startswith(">>> ") or s == ">>>":
            kept.append(s[4:])
        elif s.startswith("... ") or s == "...":
            kept.append(s[4:])
        else:
            kept.append("")  # doctest output; keep the line so numbers stay aligned
    return kept


def import_nodes(block: Block) -> list[tuple[int, ast.stmt]]:
    """(doc line, node) for each import statement in the block."""
    source = "\n".join(block.lines)
    try:
        tree = ast.parse(source)
        nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        return [(block.first_line + n.lineno - 1, n) for n in nodes]
    except SyntaxError:
        pass
    found = []
    i = 0
    while i < len(block.lines):
        if not IMPORT_START.match(block.lines[i]):
            i += 1
            continue
        stmt, j = block.lines[i].strip(), i
        while (stmt.count("(") > stmt.count(")") or stmt.endswith("\\")) and j + 1 < len(block.lines):
            j += 1
            stmt = stmt.rstrip("\\") + " " + block.lines[j].strip()
        try:
            found += [(block.first_line + i, n) for n in ast.parse(stmt).body if isinstance(n, (ast.Import, ast.ImportFrom))]
        except SyntaxError:
            pass  # not a statement after all (prose in a comment, a partial line)
        i = j + 1
    return found


class Resolver:
    def __init__(self, packages: set[str]):
        self.packages = packages
        self.cache: dict[str, str | None] = {}

    def wanted(self, module: str) -> bool:
        return not self.packages or module.split(".")[0] in self.packages

    def module_error(self, module: str) -> str | None:
        if module not in self.cache:
            try:
                importlib.import_module(module)
                self.cache[module] = None
            except Exception as exc:  # noqa: BLE001  any failure to import is the finding itself
                self.cache[module] = f"{type(exc).__name__}: {exc}"
        return self.cache[module]

    def check(self, node: ast.stmt) -> list[str]:
        errors = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                if self.wanted(alias.name) and (err := self.module_error(alias.name)):
                    errors.append(f"import {alias.name}: {err}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and self.wanted(node.module):
            if err := self.module_error(node.module):
                return [f"from {node.module} import ...: {err}"]
            mod = sys.modules[node.module]
            for alias in node.names:
                if alias.name == "*" or hasattr(mod, alias.name):
                    continue
                if self.module_error(f"{node.module}.{alias.name}") is not None:
                    errors.append(f"from {node.module} import {alias.name}: no such name or submodule")
        return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package", action="append", default=[], help="only check this top-level package")
    parser.add_argument("--path", action="append", default=[], help="prepend a directory to sys.path")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    for p in reversed(args.path):
        sys.path.insert(0, str(Path(p).resolve()))
    resolver = Resolver(set(args.package))
    failures = []
    for f in args.files:
        for block in blocks(f.read_text(encoding="utf-8", errors="replace")):
            for line, node in import_nodes(block):
                failures += [f"{f}:{line}: {msg}" for msg in resolver.check(node)]
    for failure in failures:
        print(failure)
    if failures:
        print(
            f"docs-python-imports: {len(failures)} import(s) in doc samples do not resolve. Fix the sample, "
            f"or mark a block that is meant not to run with <!-- {SKIP} -->.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

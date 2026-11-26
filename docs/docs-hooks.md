# Docs hooks

Four pre-commit hooks for repositories that publish documentation. Each one catches a problem
that would otherwise surface late, in CI on the built site or in a reader's browser.

| Hook | What it refuses | Configure with |
| --- | --- | --- |
| `docs-internal-refs` | lines in public docs that match an internal-reference pattern (ADR numbers, agent folders, internal hostnames, secret-store names) | `--patterns FILE` (required), `--ignore-case`, `--allow-marker TEXT` |
| `docs-private-links` | issue, pull request or discussion links (and `owner/repo#N`) into private repositories; with `--bare-refs`, any bare `#N` outside code | `--private-repo REGEX` (repeatable), `--private-repos-file FILE`, `--bare-refs` |
| `docs-python-imports` | `import` / `from … import` lines in fenced Python samples that do not resolve | `--package NAME` (repeatable), `--path DIR` (repeatable) |
| `gitattributes-lf` | a repository without `* text=auto eol=lf` in its root `.gitattributes` | none |

Scope the first three to the published docs with the hook's `files` key. They read Markdown and text
files, and pre-commit passes only the staged files that match.

## Adopt them

Pin `rev` to a commit SHA you have reviewed:

```yaml
repos:
  - repo: https://github.com/datacrew-space/ci-workflows
    rev: <commit-sha>
    hooks:
      - id: gitattributes-lf
      - id: docs-internal-refs
        files: '^docs/'
        args: [--patterns, .docs-internal-patterns.txt]
      - id: docs-private-links
        files: '^docs/'
        args: [--private-repos-file, .docs-private-repos.txt, --bare-refs]
      - id: docs-python-imports
        files: '^docs/'
        args: [--package, your_package]
```

The scaffold in `examples/python-uv-repo/` carries this block commented out, plus a `.gitattributes`.

## Where the Python comes from

All four run through `scripts/run-python.sh`, which picks the interpreter in this order:
1. `$DOCS_HOOKS_PYTHON`;
2. the active virtualenv (`$VIRTUAL_ENV`);
3. `python3`;
4. `python`.

`docs-python-imports` imports your package, so run pre-commit from the project's environment (for a uv
project, `uv run pre-commit run`), or point `DOCS_HOOKS_PYTHON` at the environment's interpreter. A
package that isn't installed can be found with `--path src`.

## The hooks in more detail

### docs-internal-refs

- The pattern file holds one Python regular expression per line. Lines starting with `#` and blank
  lines are ignored.
- The file lives in the calling repository. In a **public** repository, don't list private names in
  it, because the file itself would publish them. Use the `private-terms` guard ([guards.md](guards.md))
  for those, and keep this file to structural patterns such as `ADR-\d+` or `\.agents/`.
- The report names the file, the line and the pattern's entry number, never the matched text.
- A line containing `internal-ref: allow` (for example in an HTML comment) is skipped.
- A missing or empty pattern file, or an invalid pattern, exits 2. A misconfigured hook never passes
  silently.

### docs-private-links

- Owner and repository names are matched case-insensitively and in full against each pattern, so
  `acme-private/.*` covers every repository of that owner.
- `--bare-refs` also refuses `#N` outside fenced code and inline code. Use it on a site whose readers
  can't follow a bare number to an issue.

### docs-python-imports

- It checks blocks tagged `python`, `py`, `python3` or `pycon`. Absolute imports only.
- For `from a import X`, the module must import, and `X` must be an attribute of it or a submodule.
- A block that doesn't parse as a whole (top-level `await`, `...` placeholders) still has its import
  statements checked one by one. Doctest prompts are stripped.
- To skip a block, add `skip-import-check` to its info string, or put `<!-- skip-import-check -->`
  on the line before its opening fence.
- Importing runs the imported modules' top-level code, just as running the sample would. Nothing is
  fetched from the network by the hook itself.

### gitattributes-lf

Before you first add the rule to an existing repository, run `git add --renormalize .`, then check
`git status`:
- If nothing changed, commit `.gitattributes` alone.
- If files changed, commit the renormalization as its own commit, so reviewers can see it moves line
  endings only.

#!/usr/bin/env bash
# Usage: git diff -z --no-renames --name-only <base>...<work> | protected-paths.sh
#
# Reads changed paths separated by NUL and exits 1, naming each offender on stdout, when any of
# them is NOT on the explicit allowlist below. Exits 0 when every path is allowed.
#
# The review-responder agent pushes to PR branches. It must never change the gate that grades it
# (workflows, agent prompts, this script and its tests, repo instructions, lint config) or anything
# that decides what runs in CI or with which secrets (deploy and run scripts, Dockerfiles,
# compose files, systemd units, config files, secret-handling scripts, env files). A human reads
# those in review.
#
# Inverted logic (default deny): a denylist has to name every dangerous file, and a new deploy
# script is protected only after somebody remembers to add it. An earlier denylist version let
# deploy scripts and build helpers through for exactly that reason. Here a path is allowed only when
# it is under a directory the responder is meant to edit AND it is not an executable, deploy or
# config artifact by name. A file in a new place is protected until a human widens the allowlist.
# Cost: a fix to service code outside apps/<name>/src/ and apps/<name>/tests/ (one level, not any
# depth) goes back to a human. That is the safe side.
#
# NUL, not newline, and never git's default text output: git QUOTES a path that has a non-ASCII
# byte (core.quotePath), so `.github/é.yml` prints as `".github/\303\251.yml"`. With -z every path
# arrives raw, one record each, and a newline inside a name cannot start a second record.
#
# The caller must run `git diff` ON ITS OWN and refuse to push when it fails: this script only
# judges what it is given, and an empty list is a pass. (tests/review_loop/test_protected_paths.py)
#
# Matching is case-insensitive so a case-folding checkout cannot hide `.GitHub/` or `Deploy.sh`.
set -uo pipefail

found=0
shopt -s extglob
while IFS= read -r -d '' path; do
  lower="${path,,}"
  base="${lower##*/}"
  verdict=protected

  # A segment that ends in a dot or a space (`run.sh.`, `run.sh `, `...`) is read as `run.sh` by
  # some filesystems and tools, and nobody commits one on purpose: refuse before any matching.
  case "/$lower" in
    *[.\ ]/* | *[.\ ])
      printf '%s\n' "$path"
      found=1
      continue
      ;;
  esac

  # Step 1: names that are protected wherever they sit, even inside an allowed directory.
  case "$base" in
    agents.md | claude.md | dockerfile* | containerfile* | *.dockerfile | *.containerfile | \
      *.service | *.timer | *.socket | *.path | *.mount | *.conf | *.sh | *.bash | *.ps1 | \
      *.env | .env* | config.yaml | config.yml | .gitguardian.yaml | .gitguardian.yml | \
      docker-compose* | compose*.y*ml | *.override.yml | *.override.yaml | \
      makefile | gnumakefile | justfile | *.mk | \
      git-credential-* | deploy* | build-and-deploy* | \
      .pre-commit-config.yaml | ruff.toml | .secrets.baseline | pyproject.toml | conftest.py | \
      pytest.ini | tox.ini | setup.cfg | setup.py | sitecustomize.py | usercustomize.py | *.pth | \
      requirements*.txt | constraints*.txt | package.json | package-lock.json | npm-shrinkwrap.json | \
      yarn.lock | pnpm-lock.yaml | *.lock | bun.lockb | pipfile | \
      .npmrc | .yarnrc | .yarnrc.yml | .pnpmrc | .pypirc | \
      tsconfig*.json | vitest.config.* | jest.config.*)
      verdict=protected
      ;;
    *)
      # Step 2: the allowlist. Anything else stays protected.
      case "$lower" in
        # Never a `..` hop, an absolute path or an empty segment: refuse before matching.
        /* | ../* | */../* | */.. | .. | */./* | ./* | */ | *//*) verdict=protected ;;
        # The guard's own tests, wherever they would be reached from.
        tests/review_loop/* | tests/review_loop) verdict=protected ;;
        .github/* | .claude/* | .git/* | .agents/agents/* | .agents/skills/*) verdict=protected ;;
        # Package init files of a test tree run on every collection, before any test.
        tests/__init__.py | apps/+([!/])/tests/__init__.py) verdict=protected ;;
        tests/* | .agents/tests/* | docs/* | .agents/adrs/* | .agents/plans/* | .agents/guides/*)
          verdict=allowed
          ;;
        # `apps/<name>/` plus exactly `src/` or `tests/`: a bare `apps/*/src/*` also matches
        # apps/<name>/overlays/server/src/..., because `*` crosses slashes in a case pattern.
        apps/+([!/])/src/* | apps/+([!/])/tests/*) verdict=allowed ;;
        apps/+([!/])/readme.md) verdict=allowed ;;
      esac
      ;;
  esac

  if [ "$verdict" = protected ]; then
    printf '%s\n' "$path"
    found=1
  fi
done

exit "$found"

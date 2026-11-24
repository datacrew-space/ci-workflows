# Library and package releases

Libraries, packages and apps have consumers, so they use semantic versions. A release is cut from
conventional commits, tagged `vX.Y.Z`, and published as an immutable artifact that other repositories
pin. (Infrastructure repositories do not version this way; see [`infra-deploy.md`](infra-deploy.md).)

```
conventional commits on main -> release PR (release-please) -> merge -> tag vX.Y.Z + GitHub release
   -> required check green on the release commit -> publish package and/or image
```

## What the reusable workflow does

[`lib-release.yml`](../.github/workflows/lib-release.yml) runs as a job of the repository's own CI,
after the aggregate check.

1. **release**: release-please opens and updates a release PR from the commits since the last tag. When
   that PR is merged it tags the release. The job then verifies the required check (default `ci-ok`) is
   green on the release commit, with [`scripts/check_required_green.py`](../scripts/check_required_green.py),
   and only then reports `released=true`. A red commit publishes nothing.
2. **build-python** and **publish-python** (on by default): build the distributions with no credentials,
   then upload them from a separate job that alone holds the OIDC token, in a named environment, using
   trusted publishing. No index token is stored anywhere.
3. **image** (when `image-name` is set): build and push a container image to GHCR, tagged by version and by
   commit. There is no moving tag; the digest is an output, and consumers pin by digest.

## Commit discipline

The version bump comes from commit messages: `fix:` is a patch, `feat:` a minor, and a `!` or a
`BREAKING CHANGE:` footer a major. A wrong prefix gives a wrong version, so squash-merge with a PR title
that follows the convention. Commits without a releasable prefix (`docs:`, `chore:`, `test:`) do not
release.

## Set up a repository

1. Add the `release` job from [`examples/lib-release-caller.yml`](../examples/lib-release-caller.yml) to
   the repository's CI, pinning both refs to one reviewed commit SHA.
2. Pass `RELEASE_TOKEN`, a fine-grained or GitHub App token that can write contents and pull requests.
   Pull requests opened with the run's own `GITHUB_TOKEN` do not start other workflows, so without it the
   release PR never runs CI and cannot meet a required check.
3. For a package: create the environment named by `pypi-environment` (add required reviewers if you want a
   manual gate), then register the repository, the workflow file name of the **calling** workflow and the
   environment as a trusted publisher on the package index. Rehearse first by setting
   `pypi-repository-url` to the test index.
4. For an image: set `image-name` to a lowercase `ghcr.io/<owner>/<name>`. The image job needs a container
   engine, so use a hosted runner (the default).

A repository on a personal account can call this workflow; `secrets: inherit` does not cross owners, so
the token is passed by name.

## Why release-please

The org roadmap left the release tool open. release-please was chosen because it needs no
configuration for a single package, works from the commit history you already write, keeps the
release as a reviewable pull request (so a release is a merge, not a command), and handles a
monorepo through a manifest when a repository needs one. It is a small, reversible choice: the tags
and GitHub releases it creates do not depend on it.

## Out of scope

Publishing to registries other than PyPI and GHCR, signing, SBOMs and changelog customisation. Each
can be added as an input without changing the flow.

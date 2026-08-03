---
title: Releases
description: How Buzz CLI and server versions, changelogs, and publication are managed.
---

Buzz uses Release Please to version two packages independently: the `@infomiho/buzz-cli` npm package and the server Docker image. Commits are routed by path, so changes under `cli/` release the CLI and changes under `server/` release the server.

## Choose A Commit Type

Commits merged into `main` use [Conventional Commits](https://www.conventionalcommits.org/). The type determines the next version of the package the commit touches:

| Change | Example | Version |
| --- | --- | --- |
| Backward-compatible bug fix | `fix: reject invalid server URLs` | Patch |
| Backward-compatible feature | `feat: add deployment token listing` | Minor |
| Breaking change | `feat!: replace the saved config format` | Major |

Use the type that describes the change. Do not label a change as `fix` or `feat` only to force a release.

Pull requests are squash-merged and the pull request title becomes the commit on `main`, so give the pull request a conventional-commit title. Keep each pull request scoped to one package; a title like `feat:` on a pull request that touches both `cli/` and `server/` releases both.

## Verify A Release Change

Before merging a CLI change, run:

```bash
cd cli
npm ci
npm test
npm run build
```

Before merging a server change, run:

```bash
cd server
uv run pytest tests/ -v
```

The Server workflow additionally builds the Docker image and boots it against `/health` on every pull request touching `server/`.

Update user guides when behavior changes. The generated [CLI reference](../../reference/cli/) reads the same Commander definitions as the executable.

## Publish The CLI

The release workflow runs after a push to `main`:

1. Release Please opens or updates a single release pull request covering every package with unreleased changes; merging it releases only those packages.
2. The release pull request updates the package version, release manifest, and `cli/CHANGELOG.md`.
3. Review the version and changelog, then merge the release pull request.
4. Release Please creates the GitHub release and the `buzz-cli-v<version>` tag.
5. The `publish-cli` job runs `npm ci` and `npm publish --provenance --access public` from `cli/`.

npm publication uses GitHub Actions trusted publishing through OpenID Connect. The workflow does not use an npm access token.

## Publish The Server Image

Server releases follow the same flow:

1. Release Please includes the server in the same release pull request when `server/` has unreleased changes.
2. The release pull request updates `server/pyproject.toml`, `server/src/server/__init__.py`, the release manifest, and `server/CHANGELOG.md`.
3. Review the version and changelog, then merge the release pull request.
4. Release Please creates the GitHub release and the `server-v<version>` tag.
5. The `build-server-image` jobs build the image natively for `linux/amd64` and `linux/arm64`, and the `publish-server-image` job assembles the multi-arch manifest.

The image is published to `ghcr.io/infomiho/buzzstatic` with `<major>.<minor>.<patch>`, `<major>.<minor>`, and `latest` tags. The rolling `<major>` tag is added once the server reaches 1.0.

If publication fails, inspect the failed job for that release. Fix the underlying workflow, package, or registry configuration rather than changing the released tag.

The authoritative release history is [`cli/CHANGELOG.md`](https://github.com/infomiho/buzzstatic/blob/main/cli/CHANGELOG.md) for the CLI and [`server/CHANGELOG.md`](https://github.com/infomiho/buzzstatic/blob/main/server/CHANGELOG.md) for the server.

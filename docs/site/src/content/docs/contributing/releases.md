---
title: Releases
description: How Buzz CLI versions, changelogs, and npm publication are managed.
---

Buzz uses Release Please to version and publish the `@infomiho/buzz-cli` npm package. The checked-in workflow does not create a separate server release.

## Choose A Commit Type

Commits merged into `main` use [Conventional Commits](https://www.conventionalcommits.org/). The type determines the next CLI version when the change belongs in a release:

| Change | Example | Version |
| --- | --- | --- |
| Backward-compatible bug fix | `fix: reject invalid server URLs` | Patch |
| Backward-compatible feature | `feat: add deployment token listing` | Minor |
| Breaking change | `feat!: replace the saved config format` | Major |

Use the type that describes the change. Do not label a change as `fix` or `feat` only to force a release.

## Verify A Release Change

Before merging a CLI change, run:

```bash
cd cli
npm ci
npm test
npm run build
```

Update user guides when behavior changes. The generated [CLI reference](../../reference/cli/) reads the same Commander definitions as the executable.

## Publish The CLI

The release workflow runs after a push to `main`:

1. Release Please opens or updates a release pull request for `cli/`.
2. The release pull request updates the package version, release manifest, and `cli/CHANGELOG.md`.
3. Review the version and changelog, then merge the release pull request.
4. Release Please creates the GitHub release and the `buzz-cli-v<version>` tag.
5. The publish job runs `npm ci` and `npm publish --provenance --access public` from `cli/`.

npm publication uses GitHub Actions trusted publishing through OpenID Connect. The workflow does not use an npm access token.

If publication fails, inspect the `publish` job for that release. Fix the underlying workflow, package, or npm trusted-publisher configuration rather than changing the released tag.

The authoritative release history is [`cli/CHANGELOG.md`](https://github.com/infomiho/buzz-static-hosting/blob/main/cli/CHANGELOG.md).

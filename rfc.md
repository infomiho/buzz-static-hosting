# CLI Deploy Ignore Rules

## Problem Statement

The Buzz CLI deploys everything in the target directory with zero filtering. This means `.git` directories, `.DS_Store` files, `.env` secrets, `node_modules`, and IDE config all get zipped, uploaded, and served publicly. Users deploying from a project root (or a build output that wasn't cleaned) end up with unnecessary, potentially sensitive files in their deployed sites.

## Solution

Add a hardcoded ignore list to the CLI's zip creation step. Before archiving files for upload, the CLI filters out files and directories that should never be part of a static site deployment. No custom ignore file, no `.gitignore` honoring - just sensible, opinionated defaults.

## User Stories

1. As a developer, I want `.git` to be excluded from my deploy, so that my repository history isn't publicly accessible.
2. As a developer, I want `.DS_Store` files excluded, so that macOS metadata doesn't pollute my deployed site.
3. As a developer, I want `.env` and `.env.*` files excluded, so that secrets and environment variables are never accidentally deployed.
4. As a developer, I want `.vscode` and `.idea` directories excluded, so that editor config doesn't end up in my site.
5. As a developer, I want `node_modules` excluded, so that accidental project-root deploys don't upload hundreds of MBs.
6. As a developer, I want my deploy zip to be smaller after filtering, so that uploads are faster.
7. As a developer, I want to see how many files were filtered during deploy, so that I have confidence the ignore rules are working.
8. As a developer, I want normal files (HTML, CSS, JS, images) to always be included, so that filtering never breaks a legitimate deployment.
9. As a developer, I want `.well-known` to be included despite being a dotfile, so that ACME challenges and app associations still work.

## Implementation Decisions

### Default ignore list

The CLI will hardcode these ignore patterns, applied before zipping:

- `.git` - Git repository data
- `.DS_Store` - macOS Finder metadata
- `.env`, `.env.*` - Environment/secret files
- `.vscode` - VS Code config
- `.idea` - JetBrains IDE config
- `node_modules` - npm/yarn dependencies

### Key architectural decisions

- **CLI-side filtering only.** Files are excluded before zipping, not on the server. This reduces upload size and keeps the server simple. This is consistent with how Railway, Surge, and Vercel all work.
- **No `.gitignore` honoring.** Buzz deploys build output directories (e.g., `./dist`). Gitignore rules target source trees and would incorrectly filter out built assets that are gitignored. Surge, Vercel CLI, and Netlify all skip `.gitignore` for the same reason.
- **No custom ignore file (`.buzzignore`).** Keep it simple. Users can clean their build output before deploying. If demand emerges, this can be added later without breaking changes.
- **`.well-known` is explicitly preserved.** It's the only dotdir that legitimately needs to be served (SSL verification, app associations, etc.).
- **Server stays unchanged.** The server's `SiteStore.deploy()` extracts whatever zip it receives. No server-side filtering needed.

### Module to modify: `createZipBuffer` in the CLI

The `createZipBuffer` function currently uses `archive.directory(dir, false)` which includes everything. It needs to be modified to use archiver's glob-based file selection with ignore patterns, or to walk the directory manually with filtering before adding files to the archive.

The ignore list should be defined as a constant array that's easy to find and modify.

## Testing Decisions

Good tests for `createZipBuffer` verify external behavior: given a directory with specific files (including ones that should be ignored), the resulting zip buffer contains only the expected files. Tests should not depend on how the filtering is implemented internally (glob patterns vs. manual walk vs. archiver options).

**Module to test: `createZipBuffer`**

Tests:
- Normal files (HTML, CSS, JS) are included in the zip
- `.git` directory and its contents are excluded
- `.DS_Store` files are excluded
- `.env` and `.env.local` files are excluded
- `.vscode` and `.idea` directories are excluded
- `node_modules` directory is excluded
- `.well-known` directory and its contents are included
- Nested ignored patterns work (e.g., `subdir/.DS_Store`)
- Empty directories after filtering are handled gracefully

**Prior art:** `cli/src/deploy.test.ts` tests the deploy upload flow with mock zip buffers. The new tests should create real directories with `fs.mkdtemp`, run `createZipBuffer`, and inspect the resulting zip contents.

## Out of Scope

- Custom ignore file (`.buzzignore` or similar)
- `.gitignore` honoring
- Server-side filtering
- User-configurable ignore patterns via CLI flags
- Retroactive cleaning of already-deployed sites

## Further Notes

- The ignore patterns are intentionally kept as a short, specific list rather than broad wildcards like `.*` (all dotfiles). This avoids accidentally excluding legitimate dotfiles like `.well-known`, `.nojekyll`, or `.htaccess`.
- If a user needs to deploy a dotfile that's in the ignore list, they currently can't override it. This is an acceptable trade-off for simplicity. A future `.buzzignore` with negation support (`!.env.example`) could solve this.

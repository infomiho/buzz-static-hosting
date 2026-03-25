# Step 8: CLI Deploy Ignore Filtering

## Parent PRD

See `rfc.md` in project root.

## What to build

Modify the CLI's `createZipBuffer` function to filter out files that should never be part of a static site deployment. Add a hardcoded ignore list and apply it before zipping so that ignored files never leave the user's machine.

The ignore list: `.git`, `.DS_Store`, `.env`, `.env.*`, `.vscode`, `.idea`, `node_modules`.

Exception: `.well-known` must be preserved (ACME challenges, app associations).

## Acceptance criteria

- [ ] `.git` directory and its contents are excluded from the deploy zip
- [ ] `.DS_Store` files are excluded
- [ ] `.env` and `.env.*` (e.g., `.env.local`) files are excluded
- [ ] `.vscode` and `.idea` directories are excluded
- [ ] `node_modules` directory is excluded
- [ ] `.well-known` directory and its contents are included
- [ ] Nested ignored patterns work (e.g., `subdir/.DS_Store`)
- [ ] Normal files (HTML, CSS, JS, images) are included
- [ ] Deploy zip is smaller when ignored files are present in the source directory
- [ ] Tests cover all ignore patterns and the `.well-known` exception

## Blocked by

None - can start immediately.

## User stories addressed

- User story 1: .git excluded
- User story 2: .DS_Store excluded
- User story 3: .env files excluded
- User story 4: IDE config excluded
- User story 5: node_modules excluded
- User story 6: Smaller zip after filtering
- User story 7: Confidence filtering works
- User story 8: Normal files always included
- User story 9: .well-known preserved

# Step 5: Site Detail Page with File Tree

## Parent RFC

See `rfc.md` in project root.

## What to build

A server-rendered detail page for individual sites at `/dashboard/sites/{name}`. The page shows site metadata (name, live URL, created date, total size) and an indented file tree listing all deployed files with their sizes. This requires a new `SiteStore.list_files()` method that walks the site directory on disk, a new dashboard route, and a new Jinja2 template.

The file tree is always fully expanded (no collapse/expand), with directories shown as distinct entries and files indented under their parent directories. Files are read-only (no click actions). Directories are sorted before files, alphabetically within each group.

## Acceptance criteria

- [ ] `SiteStore.list_files(name, owner_id)` returns sorted file entries with relative path, size, and is_dir flag
- [ ] `list_files()` raises `NotFound` for nonexistent sites and `Forbidden` for wrong owner
- [ ] `GET /dashboard/sites/{name}` renders the detail page with site metadata and file tree
- [ ] File tree shows indented directory structure (directories first, then files, alphabetical)
- [ ] Each file entry shows its relative path and human-readable size
- [ ] Directories are visually distinct from files
- [ ] Page includes a back link to the dashboard
- [ ] Unit tests for `SiteStore.list_files()` covering: correct paths/sizes, nested directories, not found, forbidden, empty site
- [ ] Page returns 404 for nonexistent sites

## Blocked by

None - can start immediately.

## User stories addressed

- User story 1: See which files are deployed
- User story 2: See size of each file
- User story 3: Files organized by directory with indentation
- User story 6: Site metadata on detail page
- User story 8: Navigate back to dashboard
- User story 9: Directories as distinct entries
- User story 10: Page loads quickly

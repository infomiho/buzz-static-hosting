# Site Detail Page & Dashboard Table Refinements

## Problem Statement

The Buzz dashboard shows a flat table of deployed sites with name, date, and size. Users have no way to inspect what files are inside a deployed site without SSH-ing into the server. The site name in the table is a direct link to the live site, but there's no way to navigate to a management view for a specific site. Users need visibility into their deployments and a dedicated place to manage individual sites.

## Solution

Add a site detail page at `/dashboard/sites/{name}` that shows site metadata and an indented file tree. Refine the dashboard table so the site name navigates to this detail page, with a separate external-link icon to visit the live site. The detail page also hosts the delete action, giving each site a proper management view.

## User Stories

1. As a site owner, I want to see which files are deployed in my site, so that I can verify a deployment contains the right assets.
2. As a site owner, I want to see the size of each individual file, so that I can identify unexpectedly large assets.
3. As a site owner, I want to see files organized by directory with indentation, so that I can understand the structure of my deployment.
4. As a site owner, I want to click a site name in the dashboard table to go to a detail page, so that I can manage that specific site.
5. As a site owner, I want an external-link icon next to the site name, so that I can quickly visit the live site without leaving the dashboard context.
6. As a site owner, I want to see site metadata (name, URL, created date, total size) on the detail page, so that I have full context about the deployment.
7. As a site owner, I want to delete a site from its detail page, so that site management actions are consolidated in one place.
8. As a site owner, I want to navigate back to the dashboard from the detail page, so that I can manage other sites.
9. As a site owner, I want the file tree to show directories as distinct entries, so that I can distinguish folders from files at a glance.
10. As a site owner, I want the detail page to load quickly, so that inspecting files doesn't feel sluggish even for large deployments.

## Implementation Decisions

### Modules to build/modify

**SiteStore.list_files() (new method)**
- Add a `list_files(name, owner_id)` method to `SiteStore` that walks the site directory on disk.
- Returns a sorted list of file entries, each with: relative path, size in bytes, and whether it's a directory.
- Validates ownership (same pattern as `delete()`). Raises `NotFound` or `Forbidden` as appropriate.
- Directories are included as entries (size 0) so the template can render the indented tree.
- Files are sorted: directories first, then files, alphabetically within each group. Nested items appear under their parent.

**Dashboard route (new endpoint)**
- `GET /dashboard/sites/{name}` - server-rendered via Jinja2.
- Uses `SiteStore` to fetch the site record and file list.
- Passes site metadata + file entries to the template.
- Requires session auth (same `require_user` dependency as other dashboard routes).

**Site detail template (new: `site_detail.html`)**
- Extends `base.html`.
- Metadata header section: site name, external link to live URL, created date, total size.
- File tree rendered as an indented list (always expanded, no collapse/expand). Indentation based on directory depth. Directories shown with folder styling, files with their size.
- Files are read-only (no click action).
- Delete site button with the existing confirmation dialog pattern.
- Back link to dashboard.

**Dashboard table (modify JS in `dashboard.html`)**
- Site name becomes a link to `/dashboard/sites/{name}` (the detail page), styled as plain bold text.
- Add a small external-link icon next to the name that opens the live site URL in a new tab.
- Remove the site URL from the name link itself.

### Architectural decisions

- The file list is read from disk at request time (not cached, not stored in DB). Sites are small static bundles so directory walking is fast.
- Server-rendered template (not client-side fetch) for the detail page. This avoids needing a new JSON API endpoint and is consistent with how the main dashboard shell works.
- The detail page URL is `/dashboard/sites/{name}`, namespaced under `/dashboard` to distinguish from API routes.
- The delete action is duplicated on both the dashboard table (hover trash icon) and the detail page. Both use the same `DELETE /sites/{name}` API endpoint.

## Testing Decisions

A good test for `SiteStore.list_files()` tests the external behavior: given a site directory with specific files and folders, does it return the correct entries with the right paths, sizes, and directory flags? Tests should not depend on internal implementation details like sort algorithm or walk strategy.

**Module to test: `SiteStore.list_files()`**

Tests (following the existing `test_site_store.py` pattern with `tmp_path` fixtures and in-memory SQLite):
- Returns files with correct relative paths and sizes after a deployment
- Returns directories as entries with `is_dir=True`
- Handles nested directory structures with correct indentation-ready ordering
- Raises `NotFound` for nonexistent site
- Raises `Forbidden` when owner_id doesn't match
- Returns empty list for a site with no files (edge case: directory exists but is empty)

**Prior art:** `server/tests/test_site_store.py` uses the exact same pattern (in-memory DB via `make_db()`, `tmp_path` for disk, `make_zip()` helper for deployments).

## Out of Scope

- File content preview (viewing file contents inline or rendering images)
- Clickable files (opening individual files in browser)
- Deployment history / rollback
- File upload or editing from the dashboard
- Collapsible/expandable directory tree (tree is always fully expanded)

## Further Notes

- The dashboard table already has hover-to-reveal trash icons from the recent UI redesign. The external-link icon should use a similar subtle style.
- The detail page should use the same warm amber theme, DM Sans typography, and Basecoat card components established in the recent redesign.
- For very large sites (thousands of files), consider a future enhancement to paginate or lazy-load the file tree, but this is not needed for the initial implementation.
